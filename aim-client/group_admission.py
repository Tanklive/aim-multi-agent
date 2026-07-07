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

    SUBJ_CREATE  = "aim.groups.create"
    SUBJ_JOIN    = "aim.groups.join"
    SUBJ_APPROVE = "aim.groups.approve"
    SUBJ_MEMBERS = "aim.groups.members"
    SUBJ_LIST    = "aim.groups.list"
    SUBJ_MY      = "aim.groups.my"

    # ── 群 ID / 命名工具 ──

    @staticmethod
    def generate_group_id() -> str:
        """生成全局唯一群 ID: grp_<timestamp_ms>_<random4>

        格式: grp_1712345678_a3f2
        保证分布式安全: 同毫秒内不同 Agent 创建的群不会冲突。
        """
        ts = int(time.time() * 1000)
        rand = secrets.token_hex(4)[:4]
        return f"grp_{ts}_{rand}"

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
        await self.nc.subscribe(self.SUBJ_MEMBERS, cb=self._handle_members)
        await self.nc.subscribe(self.SUBJ_LIST, cb=self._handle_list)
        await self.nc.subscribe(self.SUBJ_MY, cb=self._handle_my)

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
            logger.info(f"✅ {agent_id} 加入 {group_id}")
            self._respond(msg, {"status": "approved", "group_id": group_id, "agent_id": agent_id})
        elif action == "reject":
            grp.pending_joins[agent_id] = JoinStatus.REJECTED
            self._respond(msg, {"status": "rejected", "group_id": group_id, "agent_id": agent_id})
        
        await self._save_to_kv(group_id, grp)

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


# ── CLI ───────────────────────────────────────────────────────
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="AIM Group Admission v1.0")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222")
    args = parser.parse_args()

    ga = GroupAdmission(nats_url=args.nats_url)

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
