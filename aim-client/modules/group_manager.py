"""
AIM Group Manager — 标准群管理模块

所有 Agent runtime (ZS0001/ZS0002/ZS0003) 通过统一接口调用：

    from modules.group_manager import GroupManager
    gm = GroupManager(nc, agent_id="ZS0001")
    result = await gm.create_group(name="开发组", owner="ZS0001")
    # → {"status": "created", "group_id": "grp_xxx", "name": "开发组"}

特性：
- 统一 NATS 通信 — 复用 GroupAdmission 服务，单一 subject 常量来源
- 输入验证 — 空名拒绝、非法字符过滤、长度限制、格式校验
- 频率限制 — create_group 每分钟 ≤3 次（可配置）
- 意图识别 — 自然语言 → API 调用（从 main.py 迁移）
- 响应格式化 — 人类可读输出（从 main.py 迁移）
- 标准化错误 — 统一 {status, error} 格式
- 所有取值使用 .get() 防止 KeyError
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("aim.group_manager")

# ── NATS Subject 常量（单一来源，替代 main.py GRP_* 和 group_admission.py SUBJ_*） ──

NATS_GROUPS_CREATE  = "aim.groups.create"
NATS_GROUPS_JOIN    = "aim.groups.join"
NATS_GROUPS_APPROVE = "aim.groups.approve"
NATS_GROUPS_LEAVE   = "aim.groups.leave"
NATS_GROUPS_MEMBERS = "aim.groups.members"
NATS_GROUPS_LIST    = "aim.groups.list"
NATS_GROUPS_MY      = "aim.groups.my"

# ── 自然语言意图识别规则 ──

INTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── create ──
    (re.compile(r'^(?:创建|建|拉|新建)(?:个?)(?:群|群聊|群组)\s*(.*?)$'), 'create'),
    (re.compile(r'^/create_group\s*(.*)$'), 'create'),
    # ── join ──
    (re.compile(r'^(?:加入|加)(?:入?)(?:群|群聊|群组)\s+(\S+)$'), 'join'),
    (re.compile(r'^/join_group\s+(\S+)$'), 'join'),
    # ── leave ──
    (re.compile(r'^(?:退出|离开|退)(?:群|群聊|群组)\s+(\S+)$'), 'leave'),
    (re.compile(r'^/leave_group\s+(\S+)$'), 'leave'),
    # ── members ──
    (re.compile(r'^(?:查看|查询)?(?:群成员|成员)\s+(\S+)$'), 'members'),
    (re.compile(r'^/members\s+(\S+)$'), 'members'),
    # ── approve ──
    (re.compile(r'^(?:审批|同意)\s+(\S+)\s+(\S+)$'), 'approve'),
    (re.compile(r'^/approve\s+(\S+)\s+(\S+)$'), 'approve'),
    # ── reject ──
    (re.compile(r'^(?:拒绝)\s+(\S+)\s+(\S+)$'), 'reject'),
    (re.compile(r'^/reject\s+(\S+)\s+(\S+)$'), 'reject'),
    # ── my_groups ──
    (re.compile(r'^(?:我的群|我的群组|查看群组)$'), 'my_groups'),
    (re.compile(r'^/my_groups$'), 'my_groups'),
    # ── list_groups ──
    (re.compile(r'^(?:所有群|群列表|查看所有群)$'), 'list_groups'),
    (re.compile(r'^/list_groups$'), 'list_groups'),
]

# ── 输入验证参数 ──

GROUP_NAME_MAX_LEN = 50
GROUP_NAME_PATTERN = re.compile(r'^[\w\u4e00-\u9fff\uff00-\uffef\s\-_.@#]+$')
GROUP_ID_PATTERN = re.compile(r'^grp_[a-f0-9\-]+$')

# ── 频率限制 ──

DEFAULT_CREATE_RATE_LIMIT = 3     # 每分钟最多 3 个
DEFAULT_CREATE_RATE_WINDOW = 60.0  # 窗口秒数


class GroupManager:
    """群管理标准模块 — 所有 Agent 的群操作统一入口

    架构：
        Agent (ZS0001/ZS0002/ZS0003)
            ↓ GroupManager (本模块)
            ↓ NATS request/reply
            ↓ GroupAdmission 服务 (group_admission.py)
    """

    def __init__(self, nc, agent_id: str = "",
                 rate_limit: int = DEFAULT_CREATE_RATE_LIMIT,
                 rate_window: float = DEFAULT_CREATE_RATE_WINDOW):
        """
        Args:
            nc: NATS connection（必须已连接）
            agent_id: 当前 Agent ID，用于自动填充 owner/requester
            rate_limit: 每分钟最大创建数
            rate_window: 频率限制滑动窗口（秒）
        """
        self.nc = nc
        self.agent_id = agent_id
        self.rate_limit = rate_limit
        self.rate_window = rate_window
        self._create_timestamps: list[float] = []

    # ────── 意图识别 ──────────────────────────────────

    @staticmethod
    def detect_intent(content: str) -> Optional[tuple[str, dict]]:
        """从自然语言文本中检测群操作意图。

        Args:
            content: 用户消息文本

        Returns:
            (intent, params) 或 None

            create  → params {'name': str}
            join    → params {'group_id': str}
            leave   → params {'group_id': str}
            members → params {'group_id': str}
            approve → params {'group_id': str, 'agent_id': str}
            reject  → params {'group_id': str, 'agent_id': str}
            my_groups/list_groups → params {}
        """
        content = content.strip()
        for pattern, intent in INTENT_PATTERNS:
            m = pattern.match(content)
            if m:
                params: dict = {}
                if intent == 'create':
                    params['name'] = m.group(1).strip() if m.group(1) else ''
                elif intent in ('join', 'leave', 'members'):
                    params['group_id'] = m.group(1).strip()
                elif intent in ('approve', 'reject'):
                    params['group_id'] = m.group(1).strip()
                    params['agent_id'] = m.group(2).strip()
                return (intent, params)
        return None

    # ────── 输入验证 ──────────────────────────────────

    @staticmethod
    def validate_group_name(name: str) -> Optional[str]:
        """验证群名。返回 None 表示通过，否则返回错误描述。"""
        if not name or not name.strip():
            return "群名不能为空"
        name = name.strip()
        if len(name) > GROUP_NAME_MAX_LEN:
            return f"群名不超过 {GROUP_NAME_MAX_LEN} 个字符"
        if not GROUP_NAME_PATTERN.match(name):
            return "群名包含非法字符（仅支持中英文、数字、下划线、短横线、空格、@.#）"
        return None

    @staticmethod
    def validate_group_id(group_id: str) -> Optional[str]:
        """验证群 ID 格式。返回 None 表示通过。"""
        if not group_id or not group_id.strip():
            return "群 ID 不能为空"
        if not GROUP_ID_PATTERN.match(group_id.strip()):
            return f"群 ID 格式无效（需为 grp_<uuid> 格式）: {group_id}"
        return None

    # ────── 频率限制 ──────────────────────────────────

    def _check_rate_limit(self) -> Optional[str]:
        """检查创建频率。返回 None 表示通过，否则返回错误描述。"""
        now = time.time()
        self._create_timestamps = [t for t in self._create_timestamps if now - t < self.rate_window]
        if len(self._create_timestamps) >= self.rate_limit:
            oldest = min(self._create_timestamps)
            remain = int(self.rate_window - (now - oldest)) + 1
            return f"创建过于频繁，请 {remain}s 后再试（每分钟 ≤{self.rate_limit}）"
        return None

    def _record_create(self):
        """记录一次创建（成功后调用）"""
        self._create_timestamps.append(time.time())

    # ────── NATS 通信（统一封装） ──────────────────────

    async def _nats_request(self, subject: str, payload: dict, timeout: float = 5.0) -> dict:
        """向 GroupAdmission 服务发送 NATS request。

        统一替代 main.py 的 _group_request 和 group_admission.py 的 _client_request。
        """
        if self.nc is None:
            return {"status": "error", "error": "NATS not connected (nc is None)"}
        try:
            if not self.nc.is_connected:
                return {"status": "error", "error": "NATS not connected"}
        except Exception:
            return {"status": "error", "error": "NATS connection check failed"}

        try:
            resp = await self.nc.request(subject, json.dumps(payload).encode(), timeout=timeout)
            return json.loads(resp.data)
        except asyncio.TimeoutError:
            return {"status": "error", "error": "request timeout"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ────── 群操作 API ────────────────────────────────

    async def create_group(self, name: str = "", owner: str = "",
                           group_id: str = "") -> dict:
        """创建群组。

        Args:
            name: 群名（必填，不可为空）
            owner: 群主 Agent ID（默认当前 Agent）
            group_id: 自定义群 ID（留空自动生成 grp_<uuid>）

        Returns:
            {"status": "created", "group_id": "grp_xxx", "name": "..."}
            {"status": "error", "error": "群名不能为空"}
            {"status": "error", "error": "创建过于频繁..."}
        """
        # 1. 输入验证
        err = self.validate_group_name(name)
        if err:
            return {"status": "error", "error": err}

        # 2. 频率限制
        err = self._check_rate_limit()
        if err:
            return {"status": "error", "error": err}

        # 3. NATS 请求
        owner = owner or self.agent_id
        result = await self._nats_request(NATS_GROUPS_CREATE, {
            "group_id": group_id,
            "owner": owner,
            "name": name.strip(),
        })

        # 4. 成功则记录
        if result.get("status") == "created":
            self._record_create()

        return result

    async def join_group(self, group_id: str, agent_id: str = "") -> dict:
        """加入群组"""
        err = self.validate_group_id(group_id)
        if err:
            return {"status": "error", "error": err}
        return await self._nats_request(NATS_GROUPS_JOIN, {
            "group_id": group_id.strip(),
            "agent_id": agent_id or self.agent_id,
        })

    async def leave_group(self, group_id: str, agent_id: str = "") -> dict:
        """退出群组"""
        err = self.validate_group_id(group_id)
        if err:
            return {"status": "error", "error": err}
        return await self._nats_request(NATS_GROUPS_LEAVE, {
            "group_id": group_id.strip(),
            "agent_id": agent_id or self.agent_id,
        })

    async def get_members(self, group_id: str) -> dict:
        """查询群成员"""
        err = self.validate_group_id(group_id)
        if err:
            return {"status": "error", "error": err}
        return await self._nats_request(NATS_GROUPS_MEMBERS, {
            "group_id": group_id.strip(),
        })

    async def get_my_groups(self, agent_id: str = "") -> dict:
        """查询我的群组"""
        return await self._nats_request(NATS_GROUPS_MY, {
            "agent_id": agent_id or self.agent_id,
        })

    async def list_groups(self) -> dict:
        """列出所有群组"""
        return await self._nats_request(NATS_GROUPS_LIST, {})

    async def approve_member(self, group_id: str, agent_id: str,
                             requester: str = "") -> dict:
        """审批通过入群申请"""
        err = self.validate_group_id(group_id)
        if err:
            return {"status": "error", "error": err}
        return await self._nats_request(NATS_GROUPS_APPROVE, {
            "group_id": group_id.strip(),
            "agent_id": agent_id,
            "action": "approve",
            "requester": requester or self.agent_id,
        })

    async def reject_member(self, group_id: str, agent_id: str,
                            requester: str = "") -> dict:
        """拒绝入群申请"""
        err = self.validate_group_id(group_id)
        if err:
            return {"status": "error", "error": err}
        return await self._nats_request(NATS_GROUPS_APPROVE, {
            "group_id": group_id.strip(),
            "agent_id": agent_id,
            "action": "reject",
            "requester": requester or self.agent_id,
        })

    # ────── 统一命令处理 ──────────────────────────────

    async def handle_command(self, intent: str, params: dict,
                             from_id: str) -> Optional[str]:
        """统一命令处理：意图 → API 调用 → 人类可读回复。

        替代 main.py 的 _handle_group_command + _format_group_response。

        Args:
            intent:  意图类型 (create/join/leave/members/approve/reject/my_groups/list_groups)
            params:  解析出的参数 (dict)
            from_id: 发起者 Agent ID

        Returns:
            人类可读回复文本，或 None（未知 intent）
        """
        # 所有取值使用 .get() — 防止 KeyError
        if intent == 'create':
            result = await self.create_group(
                name=params.get('name', ''),
                owner=from_id,
            )
        elif intent == 'join':
            result = await self.join_group(
                group_id=params.get('group_id', ''),
                agent_id=from_id,
            )
        elif intent == 'leave':
            result = await self.leave_group(
                group_id=params.get('group_id', ''),
                agent_id=from_id,
            )
        elif intent == 'members':
            result = await self.get_members(
                group_id=params.get('group_id', ''),
            )
        elif intent == 'approve':
            result = await self.approve_member(
                group_id=params.get('group_id', ''),
                agent_id=params.get('agent_id', ''),
                requester=from_id,
            )
        elif intent == 'reject':
            result = await self.reject_member(
                group_id=params.get('group_id', ''),
                agent_id=params.get('agent_id', ''),
                requester=from_id,
            )
        elif intent == 'my_groups':
            result = await self.get_my_groups(agent_id=from_id)
        elif intent == 'list_groups':
            result = await self.list_groups()
        else:
            return None

        return self.format_response(intent, result, params)

    # ────── 响应格式化 ────────────────────────────────

    @staticmethod
    def format_response(intent: str, resp: dict, params: dict) -> str:
        """格式化为人类可读消息。

        替代 main.py 的 _format_group_response。
        """
        status = resp.get('status', '')

        if intent == 'create':
            if status == 'created':
                return (
                    f"✅ 群已创建: `{resp.get('group_id', '?')}` — {resp.get('name', '?')}"
                )
            elif status == 'exists':
                return f"⚠️ 群 `{resp.get('group_id', '?')}` 已存在"
            return f"❌ 创建失败: {resp.get('error', resp)}"

        elif intent == 'join':
            if status == 'joined':
                return f"✅ 已加入 `{resp.get('group_id', '?')}`"
            elif status == 'pending':
                return f"⏳ 入群申请已提交，等待群主 `{resp.get('owner', '?')}` 审批"
            elif status == 'already_member':
                return f"⚠️ 已是 `{resp.get('group_id', '?')}` 成员"
            elif status == 'not_found':
                return f"❌ 群 `{resp.get('group_id', '?')}` 不存在"
            return f"❌ 加入失败: {resp.get('error', resp)}"

        elif intent == 'leave':
            if status == 'left':
                return f"✅ 已退出 `{resp.get('group_id', '?')}`"
            elif status == 'not_member':
                return f"⚠️ 不是 `{resp.get('group_id', '?')}` 成员"
            return f"❌ 退群失败: {resp.get('error', resp)}"

        elif intent == 'members':
            if status == 'ok':
                gid = resp.get('group_id', '?')
                members = resp.get('members', [])
                pending = resp.get('pending', {})
                owner = resp.get('owner', '?')
                lines = [f"👥 群成员 `{gid}`:", f"  👑 群主: {owner}"]
                for m in members:
                    lines.append(f"  👤 {m}")
                if pending:
                    lines.append(f"  ⏳ 待审批: {', '.join(pending.keys())}")
                return '\n'.join(lines)
            return f"❌ 查询失败: {resp.get('error', resp)}"

        elif intent in ('approve', 'reject'):
            agent_name = params.get('agent_id', '?') if isinstance(params, dict) else '?'
            gid = resp.get('group_id', '?')
            if status == 'approved':
                return f"✅ {agent_name} 已加入 `{gid}`"
            elif status == 'rejected':
                return f"🚫 已拒绝 {agent_name} 加入 `{gid}`"
            elif status == 'unauthorized':
                return "❌ 无权限：只有群主可以审批"
            return f"❌ 操作失败: {resp.get('error', resp)}"

        elif intent == 'my_groups':
            if status == 'ok':
                groups = resp.get('groups', {})
                if not groups:
                    return "📭 你还没有加入任何群组"
                lines = ["📋 我的群组:"]
                for gid, g in groups.items():
                    default_tag = " [默认]" if g.get('is_default') else ""
                    cnt = g.get('member_count', 0) or len(g.get('members', []))
                    lines.append(
                        f"  🔹 `{gid}` — {g.get('name', gid)}{default_tag} ({cnt}人)"
                    )
                return '\n'.join(lines)
            return f"❌ 查询失败: {resp.get('error', resp)}"

        elif intent == 'list_groups':
            if status == 'ok':
                groups = resp.get('groups', {})
                if not groups:
                    return "📭 暂无群组"
                lines = ["📋 所有群组:"]
                for gid, g in groups.items():
                    members = g.get('members', [])
                    lines.append(
                        f"  🔹 `{gid}` — {g.get('name', gid)} ({len(members)}人)"
                    )
                return '\n'.join(lines)
            return f"❌ 查询失败: {resp.get('error', resp)}"

        return f"❓ 未知响应 [{intent}]: {resp}"

    # ────── 统计与自检 ────────────────────────────────

    def stats(self) -> dict:
        """返回模块统计信息（对标 ChatArchive.stats()）。"""
        now = time.time()
        recent = sum(1 for t in self._create_timestamps if now - t < self.rate_window)
        return {
            "agent_id": self.agent_id,
            "rate_used": f"{recent}/{self.rate_limit} per {int(self.rate_window)}s",
            "rate_remaining": max(0, self.rate_limit - recent),
        }

    def __repr__(self):
        s = self.stats()
        return f"GroupManager(agent={s['agent_id']}, rate={s['rate_used']})"
