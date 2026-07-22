"""
AIM 群聊准入 — Phase 2

功能:
  1. 群组管理: 创建群组 + 设置群主
  2. 成员请求: 用户请求加入群组
  3. 群主审批: 群主 approve/reject
  4. 成员列表: NATS KV 持久化
  5. 群 ID 自动生成: grp_<timestamp_ms> 全局唯一
  6. 群命名默认规则: 未指定时用 "群聊(YYYY-MM-DD HH:MM)"
  7. 入群推送通知: aim.notification.<agent_id>.group.update

Phase 2 范围:
  - grp_trio 默认群聊（所有三方 Agent 自动加入，向后兼容）
  - 新群组自动生成唯一 ID + 默认命名
  - Agent 启动时自动从 KV 发现订阅所有群
  - 入群实时通知
  - NATS KV `aim-kv-groups` 存储

协议:
  创建群: → aim.groups.create  {"group_id":"","owner":"ZS0001","name":""}
          (group_id 留空自动生成 grp_<timestamp>)
  请求加入: → aim.groups.join    {"group_id":"my_grp","agent_id":"ZS0002"}
  审批:     → aim.groups.approve {"group_id":"my_grp","agent_id":"ZS0002","action":"approve"}
  查成员:   → aim.groups.members {"group_id":"my_grp"}
  我的群:   → aim.groups.my       {"agent_id":"ZS0001"}

  群公告:   → aim.groups.announce {"action":"set","group_id":"...","operator":"群主ID","content":"公告内容"}
              aim.groups.announce {"action":"get","group_id":"..."}
  权限: 仅群主可设置公告，所有成员可查看。新成员入群自动推送。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from aim_nats_sdk import load_global_config
from typing import Optional

logger = logging.getLogger("aim.groups")


class JoinStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class GroupInfo:
    """群组元信息"""
    group_id: str
    name: str
    owner: str               # 群主 Agent ID
    created_at: float
    members: list[str] = field(default_factory=list)
    pending_joins: dict[str, JoinStatus] = field(default_factory=dict)
    group_type: str = "chat"  # chat | workspace (Phase 1 预留，P2+ 升级)
    is_default: bool = False  # grp_trio 为默认群，免审批


# ── Group Manager ────────────────────────────────────────────

class GroupAdmission:
    """群聊准入管理器 — NATS 微服务 + 客户端"""

    SUBJ_CREATE   = "aim.groups.create"
    SUBJ_JOIN     = "aim.groups.join"
    SUBJ_APPROVE  = "aim.groups.approve"
    SUBJ_LEAVE    = "aim.groups.leave"
    SUBJ_MEMBERS  = "aim.groups.members"
    SUBJ_LIST     = "aim.groups.list"
    SUBJ_MY       = "aim.groups.my"
    SUBJ_ANNOUNCE = "aim.groups.announce"

    # ── 群 ID / 命名工具 ──

    @staticmethod
    def generate_group_id() -> str:
        """生成全局唯一群 ID: grp_<uuid4>

        格式: grp_a1b2c3d4-e5f6-7890-abcd-ef1234567890
        使用 UUID4 (RFC 4122)，保证跨系统全局唯一，且不暴露创建时间。
        """
        import uuid
        return f"grp_{uuid.uuid4()}"

    @staticmethod
    def default_group_name() -> str:
        """默认群名: 群聊(YYYY-MM-DD HH:MM)

        参考微信群：系统自动生成群名 = 成员名拼接。
        AIM 场景下用创建时间更直观，Agent 可见后随时修改。
        """
        from datetime import datetime
        return f"群聊({datetime.now().strftime('%Y-%m-%d %H:%M')})"

    def __init__(self, nats_url: str = "", credentials: str = ""):
        if not nats_url:
            cfg = load_global_config()
            nats_url = cfg.get("nats_server", "nats://127.0.0.1:4222")
        self.nats_url = nats_url
        self.credentials = credentials
        self.nc = None
        self.js = None
        self.kv = None
        self._groups: dict[str, GroupInfo] = {}
        self._running = False

    # ── 服务端 ────────────────────────────────────────────

    async def start_service(self):
        """启动群聊准入服务"""
        from nats import connect as nats_connect

        opts = {}
        if self.credentials:
            opts["user_credentials"] = self.credentials

        self.nc = await nats_connect(self.nats_url, **opts)
        self.js = self.nc.jetstream()

        try:
            self.kv = await self.js.create_key_value(
                bucket="aim-kv-groups",
                description="AIM Group Workspace",
            )
        except Exception:
            self.kv = await self.js.key_value("aim-kv-groups")

        await self._load_from_kv()

        # 确保默认群 grp_trio 存在
        await self._ensure_default_group()

        # 订阅
        await self.nc.subscribe(self.SUBJ_CREATE, cb=self._handle_create)
        await self.nc.subscribe(self.SUBJ_JOIN, cb=self._handle_join)
        await self.nc.subscribe(self.SUBJ_APPROVE, cb=self._handle_approve)
        await self.nc.subscribe(self.SUBJ_LEAVE, cb=self._handle_leave)
        await self.nc.subscribe(self.SUBJ_MEMBERS, cb=self._handle_members)
        await self.nc.subscribe(self.SUBJ_LIST, cb=self._handle_list)
        await self.nc.subscribe(self.SUBJ_MY, cb=self._handle_my)
        await self.nc.subscribe(self.SUBJ_ANNOUNCE, cb=self._handle_announce)

        self._running = True
        logger.info(f"群聊准入启动 — {len(self._groups)} group(s)")

    async def stop(self):
        self._running = False
        if self.nc:
            await self.nc.drain()

    async def _get_client_nc(self):
        """获取 NATS 连接：优先复用服务端连接，否则新建（2026-06-17 修复）"""
        from nats import connect as _connect
        if self.nc and self.nc.is_connected:
            return self.nc
        return await _connect(self.nats_url)

    async def _client_request(self, subject: str, payload: dict) -> dict:
        """客户端请求封装：复用连接 + 自动清理临时连接"""
        import json as _json
        nc = await self._get_client_nc()
        owned = nc is not self.nc
        try:
            resp = await nc.request(subject, _json.dumps(payload).encode(), timeout=5)
            return _json.loads(resp.data)
        finally:
            if owned:
                await nc.drain()

    async def _ensure_default_group(self):
        """确保默认群存在（从 aim.json 读取）"""
        cfg = load_global_config()
        default_grp = cfg.get("default_group", "grp_trio")
        default_name = cfg.get("default_group_name", "Default Group")
        default_owner = cfg.get("default_group_owner", "ZS0001")
        default_members = cfg.get("trusted_peers", ["ZS0001"])
        if default_grp not in self._groups:
            grp = GroupInfo(
                group_id=default_grp,
                name=default_name,
                owner=default_owner,
                created_at=time.time(),
                members=default_members,
                is_default=True,
            )
            self._groups[default_grp] = grp
            await self._save_to_kv(default_grp, grp)
            logger.info(f"默认群 {default_grp} 已创建")

    async def _load_from_kv(self):
        try:
            keys = await self.kv.keys()
            for key in keys:
                try:
                    entry = await self.kv.get(key)
                    data = json.loads(entry.value.decode())
                    self._groups[key] = GroupInfo(**data)
                except Exception as e:
                    logger.warning(f"KV 记录解析失败 {key}: {e}")
        except Exception:
            pass

    async def _save_to_kv(self, group_id: str, grp: GroupInfo):
        try:
            await self.kv.put(group_id, json.dumps(asdict(grp), default=str).encode())
        except Exception as e:
            logger.error(f"KV 保存失败 {group_id}: {e}")

    def _respond(self, msg, data: dict):
        """Respond helper — 检查 reply subject"""
        if msg.reply:
            asyncio.ensure_future(
                self.nc.publish(msg.reply, json.dumps(data).encode())
            )

    def _notify_group_update(self, agent_id: str, group_id: str, action: str, group_name: str = ""):
        """推送群变更通知给 Agent

        Subject: aim.notification.<agent_id>
        Agent 端已订阅此 subject，收到后重新查询群列表。
        """
        payload = {
            "event": "group.update",
            "action": action,        # added | removed | created
            "group_id": group_id,
            "group_name": group_name or group_id,
            "timestamp": time.time(),
        }
        subject = f"aim.notification.{agent_id}"
        asyncio.ensure_future(
            self.nc.publish(subject, json.dumps(payload, ensure_ascii=False).encode())
        )
        logger.debug(f"📢 群通知 → {agent_id}: {action} {group_id}")

    async def _handle_create(self, msg):
        try:
            req = json.loads(msg.data.decode())
            group_id = req.get("group_id", "")
            owner = req["owner"]
        except Exception:
            self._respond(msg, {"status": "error", "error": "invalid"})
            return

        # v2.0: group_id 留空 → 自动生成
        if not group_id:
            group_id = self.generate_group_id()

        if group_id in self._groups:
            self._respond(msg, {"status": "exists", "group_id": group_id})
            return

        # v2.0: name 留空 → 默认命名 "群聊(2026-07-07 17:28)"
        name = req.get("name", "")
        if not name:
            name = self.default_group_name()

        grp = GroupInfo(
            group_id=group_id,
            name=name,
            owner=owner,
            created_at=time.time(),
            members=[owner],
        )
        self._groups[group_id] = grp
        await self._save_to_kv(group_id, grp)
        logger.info(f"群组创建: {group_id} ({name}) owner={owner}")
        self._notify_group_update(owner, group_id, "added", grp.name)
        self._respond(msg, {"status": "created", "group_id": group_id, "name": name})

    async def _handle_join(self, msg):
        try:
            req = json.loads(msg.data.decode())
            group_id = req["group_id"]
            agent_id = req["agent_id"]
        except Exception:
            self._respond(msg, {"status": "error"})
            return

        grp = self._groups.get(group_id)
        if not grp:
            self._respond(msg, {"status": "not_found", "group_id": group_id})
            return

        if agent_id in grp.members:
            self._respond(msg, {"status": "already_member", "group_id": group_id})
            return

        if grp.is_default:
            # 默认群免审批
            grp.members.append(agent_id)
            await self._save_to_kv(group_id, grp)
            self._notify_group_update(agent_id, group_id, "added", grp.name)
            await self._push_announcement_on_join(agent_id, group_id)
            logger.info(f"{agent_id} 加入默认群 {group_id}")
            self._respond(msg, {"status": "joined", "group_id": group_id})
        else:
            # 需群主审批
            grp.pending_joins[agent_id] = JoinStatus.PENDING
            await self._save_to_kv(group_id, grp)
            logger.info(f"{agent_id} 申请加入 {group_id} (待 {grp.owner} 审批)")
            self._respond(msg, {"status": "pending", "group_id": group_id, "owner": grp.owner})

    async def _handle_approve(self, msg):
        try:
            req = json.loads(msg.data.decode())
            group_id = req["group_id"]
            agent_id = req["agent_id"]
            action = req["action"]  # approve | reject
            requester = req.get("requester", "")
        except Exception:
            self._respond(msg, {"status": "error"})
            return

        grp = self._groups.get(group_id)
        if not grp:
            self._respond(msg, {"status": "not_found"})
            return

        if requester != grp.owner and requester not in grp.members:
            self._respond(msg, {"status": "unauthorized"})
            return

        if action == "approve":
            grp.members.append(agent_id)
            grp.pending_joins.pop(agent_id, None)
            self._notify_group_update(agent_id, group_id, "added", grp.name)
            await self._push_announcement_on_join(agent_id, group_id)
            logger.info(f"✅ {agent_id} 加入 {group_id}")
            self._respond(msg, {"status": "approved", "group_id": group_id, "agent_id": agent_id})
        elif action == "reject":
            grp.pending_joins[agent_id] = JoinStatus.REJECTED
            self._respond(msg, {"status": "rejected", "group_id": group_id, "agent_id": agent_id})
        
        await self._save_to_kv(group_id, grp)

    async def _handle_leave(self, msg):
        """v2.0: 主动退群或被踢出"""
        try:
            req = json.loads(msg.data.decode())
            group_id = req["group_id"]
            agent_id = req["agent_id"]
            requester = req.get("requester", agent_id)  # 谁操作的（群主踢人 vs 自己退）
        except Exception:
            self._respond(msg, {"status": "error", "error": "invalid"})
            return

        grp = self._groups.get(group_id)
        if not grp:
            self._respond(msg, {"status": "not_found"})
            return

        if agent_id not in grp.members:
            self._respond(msg, {"status": "not_member"})
            return

        # 群主不能被踢（只能解散群，Phase 2+）
        if requester != agent_id and agent_id == grp.owner:
            self._respond(msg, {"status": "owner_cannot_be_removed"})
            return

        # 非群主的踢人操作需权限检查
        if requester != agent_id and requester != grp.owner:
            self._respond(msg, {"status": "unauthorized"})
            return

        grp.members.remove(agent_id)
        grp.pending_joins.pop(agent_id, None)
        await self._save_to_kv(group_id, grp)

        # 通知被移除的 agent
        self._notify_group_update(agent_id, group_id, "removed", grp.name)

        action_desc = "退出" if requester == agent_id else f"被 {requester} 踢出"
        logger.info(f"{agent_id} {action_desc} {group_id}")
        self._respond(msg, {"status": "left", "group_id": group_id, "agent_id": agent_id, "action": action_desc})

    async def _handle_members(self, msg):
        try:
            req = json.loads(msg.data.decode())
            group_id = req["group_id"]
        except Exception:
            self._respond(msg, {"status": "error"})
            return

        grp = self._groups.get(group_id)
        if not grp:
            self._respond(msg, {"status": "not_found"})
            return

        self._respond(msg, {
            "status": "ok", "group_id": group_id,
            "members": grp.members,
            "pending": {k: v.value for k, v in grp.pending_joins.items()},
            "owner": grp.owner,
            "group_type": grp.group_type,
        })

    async def _handle_list(self, msg):
        groups = {gid: {"name": g.name, "owner": g.owner, "members": len(g.members), "group_type": g.group_type}
                  for gid, g in self._groups.items()}
        self._respond(msg, {"status": "ok", "groups": groups})

    async def _handle_my(self, msg):
        """v2.0: 查询某 Agent 所属的全部群"""
        try:
            req = json.loads(msg.data.decode())
            agent_id = req["agent_id"]
        except Exception:
            self._respond(msg, {"status": "error", "error": "invalid"})
            return

        my_groups = {}
        for gid, g in self._groups.items():
            if agent_id in g.members:
                my_groups[gid] = {
                    "group_id": gid,
                    "name": g.name,
                    "owner": g.owner,
                    "member_count": len(g.members),
                    "group_type": g.group_type,
                    "is_default": g.is_default,
                }
        self._respond(msg, {"status": "ok", "agent_id": agent_id, "groups": my_groups})

    # ── 群公告 (v2.0 2026-07-15) ──

    async def _get_announce_kv_key(self, group_id: str) -> str:
        return f"{group_id}-announce"

    async def _push_announcement_on_join(self, agent_id: str, group_id: str):
        """新成员入群时自动推送已有公告"""
        try:
            key = await self._get_announce_kv_key(group_id)
            entry = await self.kv.get(key)
            ann = json.loads(entry.value.decode())
            await self._push_announcement_to(agent_id, group_id, ann["content"], ann["set_by"], ann["set_at"])
        except Exception:
            pass  # 无公告则静默

    async def _push_announcement_to(self, agent_id: str, group_id: str, content: str, set_by: str, set_at: float):
        """推送群公告给指定 Agent"""
        payload = {
            "event": "group.announce",
            "group_id": group_id,
            "content": content,
            "set_by": set_by,
            "set_at": set_at,
            "timestamp": time.time(),
        }
        # 同时发通知 + 直接发 DM 公告内容
        subject_notify = f"aim.notification.{agent_id}"
        subject_dm = f"aim.dm.{agent_id}"
        data = json.dumps(payload, ensure_ascii=False).encode()
        asyncio.ensure_future(self.nc.publish(subject_notify, data))
        # DM 公告（让 Agent 一定会看到）
        dm = {
            "ver": "1.0",
            "id": str(secrets.token_hex(6)),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from": "system",
            "type": "dm",
            "payload": {
                "text": f"📢 [群公告] {group_id}\n{content}\n\n— {set_by} 发布于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(set_at))}"
            }
        }
        asyncio.ensure_future(
            self.nc.publish(subject_dm, json.dumps(dm, ensure_ascii=False).encode())
        )
        logger.info(f"📢 群公告推送 → {agent_id}: {group_id}")

    async def _handle_announce(self, msg):
        """群公告：set（群主设置）/ get（成员查看）

        请求:
          {action: "set", group_id: "...", operator: "...", content: "..."}
          {action: "get", group_id: "..."}
        响应:
          {status: "ok", group_id: "...", announcement: {content, set_by, set_at}}
        """
        try:
            req = json.loads(msg.data.decode())
            action = req.get("action", "get")
            group_id = req["group_id"]
        except Exception:
            self._respond(msg, {"status": "error", "error": "invalid"})
            return

        grp = self._groups.get(group_id)
        if not grp:
            self._respond(msg, {"status": "not_found", "group_id": group_id})
            return

        if action == "set":
            operator = req.get("operator", "")
            content = req.get("content", "")
            if operator != grp.owner:
                self._respond(msg, {"status": "unauthorized", "error": "仅群主可设置公告"})
                return
            if not content.strip():
                self._respond(msg, {"status": "error", "error": "公告内容不能为空"})
                return

            ann = {
                "content": content.strip(),
                "set_by": operator,
                "set_at": time.time(),
            }
            key = await self._get_announce_kv_key(group_id)
            await self.kv.put(key, json.dumps(ann, ensure_ascii=False).encode())
            # 推送给所有群成员
            for member_id in grp.members:
                await self._push_announcement_to(member_id, group_id, content, operator, ann["set_at"])
            logger.info(f"📢 群公告已设置 {group_id}: {content[:50]}...")
            self._respond(msg, {"status": "set", "group_id": group_id, "announcement": ann})

        elif action == "get":
            key = await self._get_announce_kv_key(group_id)
            try:
                entry = await self.kv.get(key)
                ann = json.loads(entry.value.decode())
                self._respond(msg, {"status": "ok", "group_id": group_id, "announcement": ann})
            except Exception:
                self._respond(msg, {"status": "ok", "group_id": group_id, "announcement": None})

        else:
            self._respond(msg, {"status": "error", "error": f"未知 action: {action}"})

    # ── 客户端方法 ───────────────────────────────────────

    async def create_group(self, group_id: str = "", owner: str = "", name: str = "") -> dict:
        """创建群组。group_id/name 留空则自动生成。"""
        return await self._client_request(self.SUBJ_CREATE, {
            "group_id": group_id,
            "owner": owner,
            "name": name,
        })

    async def join_group(self, group_id: str, agent_id: str) -> dict:
        return await self._client_request(self.SUBJ_JOIN, {
            "group_id": group_id, "agent_id": agent_id,
        })

    async def get_members(self, group_id: str) -> dict:
        return await self._client_request(self.SUBJ_MEMBERS, {
            "group_id": group_id,
        })

    async def get_my_groups(self, agent_id: str) -> dict:
        """v2.0: 查询某 Agent 所属的全部群"""
        return await self._client_request(self.SUBJ_MY, {
            "agent_id": agent_id,
        })

    async def leave_group(self, group_id: str, agent_id: str) -> dict:
        """v2.0: Agent 主动退群"""
        return await self._client_request(self.SUBJ_LEAVE, {
            "group_id": group_id, "agent_id": agent_id,
        })


# ── CLI ───────────────────────────────────────────────────────
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="AIM Group Admission v1.0")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222")
    parser.add_argument("--credentials", default="", help="NATS credentials file path")
    args = parser.parse_args()

    ga = GroupAdmission(nats_url=args.nats_url, credentials=args.credentials)

    async def run():
        await ga.start_service()
        while ga._running:
            await asyncio.sleep(5)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        asyncio.run(ga.stop())


if __name__ == "__main__":
    main()
