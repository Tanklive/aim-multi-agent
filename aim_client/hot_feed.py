"""
AIM HotFeed — 平台级热冷消息分级机制

v1.0 (2026-07-22)
归属: shared/aim/aim_client/ — AIM 平台级通用模块
安装 AIM client 即获得，零额外配置。

架构:
  PolicyLoader   — NATS KV → 策略表（template + 群级覆盖）
  MessagePoller  — JetStream consumer → 增量拉取
  StageClassifier — 策略匹配 → hot/warm/cold/archive 四级
  DedupGuard     — 静默期短时去重
  ArchiveRouter  — message_type → summarize/raw 自动映射

用法:
  from aim_client.hot_feed import AIMHotFeed
  hf = AIMHotFeed(client)
  report = await hf.check()
  # report.hot / report.warm / report.cold
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger("aim.hotfeed")

# ── 默认策略（fallback，当 NATS KV 不可用时） ──

DEFAULT_POLICY = {
    "version": "1",
    "silence": {
        "hours": [23, 8],
        "mention_penetrate": True,
        "dedup_window_s": 60,
        "max_hot_per_sender": 2,
        "overflow_downgrade_to": "warm",
    },
    "archive": {
        "retention": {"summarize": "7d", "raw": "3d"},
        "queryable": True,
        "auto_mode_map": {
            "mention": "summarize", "dm": "summarize", "task": "summarize",
            "keyword_hit": "summarize", "observer": "raw", "system": "raw",
            "default": "raw",
        },
    },
    "stages": [
        {
            "name": "hot", "label": "🔥 热消息",
            "triggers": {"mention": True, "dm": True,
                         "keywords": ["TASK", "任务", "BUG", "紧急", "URGENT"],
                         "reply_to_me": True},
            "timeout_s": 30, "on_timeout": "escalate", "action": "report_immediately",
        },
        {
            "name": "warm", "label": "🌤️ 温消息",
            "triggers": {"any_group_message": True,
                         "keywords": ["协作", "联调", "测试", "发布", "评审"]},
            "timeout_s": 300, "on_timeout": "escalate_to_cold", "action": "batch_after_task",
        },
        {
            "name": "cold", "label": "❄️ 冷消息",
            "triggers": {"observer_events": True, "system_notice": True, "old_messages": True},
            "on_timeout": "archive", "action": "heartbeat_summary",
        },
        {
            "name": "archive", "label": "📦 归档",
            "action": "auto",
        },
    ],
}

# ── Data classes ──

@dataclass
class HotMessage:
    """分级后的单条消息"""
    msg_id: str
    stage: str                              # hot / warm / cold / archive
    group_id: Optional[str] = None
    from_agent: Optional[str] = None
    text: str = ""
    timestamp: str = ""
    reason: str = ""                        # 命中原因
    deadline: float = 0.0                   # 超时时间戳


@dataclass
class HotFeedReport:
    """HotFeed.check() 返回的结构"""
    hot: List[HotMessage] = field(default_factory=list)
    warm: List[HotMessage] = field(default_factory=list)
    cold: List[HotMessage] = field(default_factory=list)
    timestamp: str = ""

    @property
    def has_hot(self) -> bool:
        return len(self.hot) > 0

    @property
    def total(self) -> int:
        return len(self.hot) + len(self.warm) + len(self.cold)


# ════════════════════════════════════════════════════════════════════

class PolicyLoader:
    """NATS KV → 策略表加载器"""

    HOTFEED_KV = "aim-hotfeed-policy"
    TEMPLATE_KEY = "template"

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._policy_cache: Dict[str, dict] = {}
        self._callbacks: List[Callable] = []
        self._js = None

    def set_js(self, js):
        self._js = js

    def register_callback(self, cb: Callable[[str, dict], None]):
        """注册策略变更回调"""
        self._callbacks.append(cb)

    async def load(self) -> dict:
        """加载当前 Agent 生效的策略（template + 群覆盖已合并到 policy 查询中）"""
        try:
            kv = await self._js.key_value(self.HOTFEED_KV)
            data = await kv.get(self.TEMPLATE_KEY)
            policy = json.loads(data.value)
            self._policy_cache[self.TEMPLATE_KEY] = policy
            log.info(f"[{self.agent_id}] HotFeed policy loaded (v{policy.get('version','?')})")
            return policy
        except Exception:
            log.warning(f"[{self.agent_id}] HotFeed KV not available, using default policy")
            return DEFAULT_POLICY

    async def get_policy(self, group_id: Optional[str] = None) -> dict:
        """获取生效策略（先读 group 覆盖，fallback template）"""
        if group_id:
            try:
                kv = await self._js.key_value(self.HOTFEED_KV)
                data = await kv.get(f"grp_{group_id}")
                return json.loads(data.value)
            except Exception:
                pass
        return self._policy_cache.get(self.TEMPLATE_KEY, DEFAULT_POLICY)

    async def write_template(self, policy: dict):
        """写入全局模板策略"""
        policy.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        kv = await self._ensure_kv()
        await kv.put(self.TEMPLATE_KEY, json.dumps(policy, ensure_ascii=False).encode())
        self._policy_cache[self.TEMPLATE_KEY] = policy
        log.info(f"[{self.agent_id}] HotFeed template policy updated")

    async def write_group_policy(self, group_id: str, policy: dict):
        """写入群级覆盖策略"""
        policy.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        kv = await self._ensure_kv()
        await kv.put(f"grp_{group_id}", json.dumps(policy, ensure_ascii=False).encode())
        log.info(f"[{self.agent_id}] HotFeed group policy updated: {group_id}")

    async def _ensure_kv(self):
        """确保 KV bucket 存在"""
        try:
            return await self._js.key_value(self.HOTFEED_KV)
        except Exception:
            return await self._js.create_key_value(
                bucket=self.HOTFEED_KV,
                description="AIM HotFeed 热冷消息策略存储",
            )


# ════════════════════════════════════════════════════════════════════

class DedupGuard:
    """静默期短时去重"""

    def __init__(self):
        # sender -> list of (timestamp, stage)
        self._hits: Dict[str, List[Tuple[float, str]]] = {}

    def check(self, sender: str, silence_config: dict) -> str:
        """检查是否触发去重降级

        Returns:
            原始 stage 或降级后的 stage
        """
        window = silence_config.get("dedup_window_s", 60)
        max_hot = silence_config.get("max_hot_per_sender", 2)
        downgrade_to = silence_config.get("overflow_downgrade_to", "warm")
        now = time.time()

        # 清理过期记录
        if sender in self._hits:
            self._hits[sender] = [
                (ts, s) for ts, s in self._hits[sender]
                if now - ts < window
            ]
        else:
            self._hits[sender] = []

        hot_count = sum(1 for _, s in self._hits[sender] if s == "hot")

        if hot_count >= max_hot:
            self._hits[sender].append((now, downgrade_to))
            return downgrade_to

        return "hot"  # 不降级

    def record(self, sender: str, stage: str):
        """记录一次命中"""
        if sender not in self._hits:
            self._hits[sender] = []
        self._hits[sender].append((time.time(), stage))


# ════════════════════════════════════════════════════════════════════

class StageClassifier:
    """策略匹配 → 四级分级"""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    def classify(self, msg: dict, policy: dict,
                 now: Optional[float] = None) -> HotMessage:
        """对单条消息分级"""
        if now is None:
            now = time.time()

        stages = policy.get("stages", [])
        msg_text = msg.get("text", "") or msg.get("payload", {}).get("text", "")
        msg_type = msg.get("type", "") or msg.get("msg_type", "grp")
        from_agent = msg.get("from", "") or msg.get("from_agent", "")
        msg_id = msg.get("id", "") or msg.get("msg_id", "")

        # 确定 message_type（用于 archive 映射）
        detected_type = self._detect_message_type(msg, msg_text, msg_type)

        # 按 stages 顺序匹配（hot → warm → cold）
        for stage in stages:
            if stage["name"] == "archive":
                continue
            triggers = stage.get("triggers", {})
            if not triggers:
                continue
            match_result = self._match_triggers(triggers, msg, msg_text,
                                                 msg_type, from_agent)
            if match_result:
                deadline = now + stage.get("timeout_s", 300) if stage.get("timeout_s") else 0
                return HotMessage(
                    msg_id=msg_id,
                    stage=stage["name"],
                    group_id=msg.get("group_id", ""),
                    from_agent=from_agent,
                    text=msg_text[:200],
                    timestamp=msg.get("ts", ""),
                    reason=f"{stage['name']}: {match_result}",
                    deadline=deadline,
                )

        # 未命中任何 stage → archive
        return HotMessage(
            msg_id=msg_id,
            stage="archive",
            reason="no_trigger_match",
        )

    def _match_triggers(self, triggers: dict, msg: dict,
                        msg_text: str, msg_type: str,
                        from_agent: str) -> Optional[str]:
        """检查是否命中触发条件"""
        # @mention 检测
        if triggers.get("mention"):
            if self._is_mentioned(msg_text):
                return "mention"

        # 私聊检测
        if triggers.get("dm") and msg_type == "dm":
            return "dm"

        # 关键词检测
        keywords = triggers.get("keywords", [])
        if keywords:
            for kw in keywords:
                if kw.upper() in msg_text.upper():
                    return f"keyword:{kw}"

        # 回复给我
        if triggers.get("reply_to_me"):
            reply_to = msg.get("reply_to", "")
            if reply_to == self.agent_id:
                return "reply_to_me"

        # 指定发送者
        from_agents = triggers.get("from_agents", [])
        if from_agents and from_agent in from_agents:
            return f"from:{from_agent}"

        # 任意群消息
        if triggers.get("any_group_message") and msg_type == "grp":
            return "any_group_message"

        # observer 事件
        if triggers.get("observer_events") and msg_type in ("obs", "observer"):
            return "observer_event"

        # 系统通知
        if triggers.get("system_notice") and msg_type in ("sys", "system", "notice"):
            return "system_notice"

        return None

    def _is_mentioned(self, text: str) -> bool:
        """检测是否 @了本方 agent"""
        mentions = [f"@{self.agent_id}", f"@呱呱"]
        for m in mentions:
            if m in text:
                return True
        return False

    def _detect_message_type(self, msg: dict, text: str, msg_type: str) -> str:
        """推断消息类型（用于 archive auto_mode_map）"""
        if msg_type == "dm":
            return "dm"
        if self._is_mentioned(text):
            return "mention"
        if msg_type in ("obs", "observer"):
            return "observer"
        if msg_type in ("sys", "system", "notice"):
            return "system"
        keywords = ["TASK", "任务", "BUG", "紧急", "URGENT"]
        for kw in keywords:
            if kw.upper() in text.upper():
                return "task" if kw in ("TASK", "任务") else "keyword_hit"
        return "default"


# ════════════════════════════════════════════════════════════════════

class MessagePoller:
    """JetStream consumer → 增量拉取"""

    STREAM = "aim-messages"

    def __init__(self, agent_id: str, js):
        self.agent_id = agent_id
        self.js = js
        self._consumer_name = f"hotfeed-{agent_id}"
        self._last_seq: int = 0
        self._cursor_file: str = ""

    def set_cursor_file(self, path: str):
        self._cursor_file = path
        self._load_cursor()

    def _load_cursor(self):
        try:
            if self._cursor_file and os.path.exists(self._cursor_file):
                with open(self._cursor_file) as f:
                    data = json.load(f)
                    self._last_seq = data.get("last_seq", 0)
        except Exception:
            self._last_seq = 0

    def _save_cursor(self):
        try:
            if self._cursor_file:
                os.makedirs(os.path.dirname(self._cursor_file), exist_ok=True)
                with open(self._cursor_file, "w") as f:
                    json.dump({"last_seq": self._last_seq,
                               "updated_at": datetime.now(timezone.utc).isoformat()}, f)
        except Exception as e:
            log.warning(f"[{self.agent_id}] HotFeed cursor save failed: {e}")

    async def pull(self, batch_size: int = 50) -> List[dict]:
        """增量拉取新消息"""
        try:
            # 创建/获取 ephemeral consumer
            try:
                sub = await self.js.pull_subscribe(
                    subject="aim.dm.>",
                    stream=self.STREAM,
                    durable=self._consumer_name,
                )
            except Exception:
                # Ephemeral
                sub = await self.js.pull_subscribe(
                    subject="aim.dm.>",
                    stream=self.STREAM,
                )

            # 也订阅群聊
            try:
                sub_grp = await self.js.pull_subscribe(
                    subject="aim.grp.>",
                    stream=self.STREAM,
                )
            except Exception:
                sub_grp = None

            messages = []

            # Pull from dm
            try:
                msgs = await sub.fetch(batch_size, timeout=2)
                for m in msgs:
                    msg_seq = m.metadata.sequence.stream
                    if msg_seq > self._last_seq:
                        try:
                            parsed = json.loads(m.data)
                            parsed["_stream_seq"] = msg_seq
                            messages.append(parsed)
                        except Exception:
                            pass
                if messages:
                    self._last_seq = max(
                        self._last_seq,
                        max((m.get("_stream_seq", 0) for m in messages), default=0)
                    )
            except Exception:
                pass

            # Pull from grp
            if sub_grp:
                try:
                    msgs = await sub_grp.fetch(batch_size, timeout=2)
                    for m in msgs:
                        msg_seq = m.metadata.sequence.stream
                        if msg_seq > self._last_seq:
                            try:
                                parsed = json.loads(m.data)
                                parsed["_stream_seq"] = msg_seq
                                messages.append(parsed)
                            except Exception:
                                pass
                    if messages:
                        self._last_seq = max(
                            self._last_seq,
                            max((m.get("_stream_seq", 0) for m in messages), default=0)
                        )
                except Exception:
                    pass

        except Exception as e:
            log.warning(f"[{self.agent_id}] HotFeed poll failed: {e}")

        # 保存 cursor
        if messages:
            self._save_cursor()

        return messages

    async def get_recent(self, count: int = 20) -> List[dict]:
        """直接拉取最近 N 条消息（用于首次初始化）"""
        try:
            sub = await self.js.pull_subscribe(
                subject="aim.grp.>",
                stream=self.STREAM,
            )
            msgs = await sub.fetch(count, timeout=3)
            result = []
            for m in msgs:
                try:
                    parsed = json.loads(m.data)
                    parsed["_stream_seq"] = m.metadata.sequence.stream
                    result.append(parsed)
                except Exception:
                    pass
            # Track highest seq
            if result:
                self._last_seq = max(
                    self._last_seq,
                    max((m.get("_stream_seq", 0) for m in result), default=0)
                )
                self._save_cursor()
            return result
        except Exception as e:
            log.warning(f"[{self.agent_id}] HotFeed get_recent failed: {e}")
            return []

    async def cleanup(self):
        """清理 consumer"""
        try:
            await self.js.delete_consumer(self.STREAM, self._consumer_name)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════

class ArchiveRouter:
    """archive_mode 自动映射：message_type → summarize/raw"""

    def __init__(self):
        pass

    def route(self, msg_type: str, policy: dict) -> str:
        """根据 message_type 返回 archive_mode"""
        auto_map = policy.get("archive", {}).get("auto_mode_map", {})
        mode = auto_map.get(msg_type, auto_map.get("default", "raw"))
        return mode


# ════════════════════════════════════════════════════════════════════

class AIMHotFeed:
    """AIM 平台级热冷消息分级

    安装 AIM client 即获得，零额外配置。

    用法:
      hf = AIMHotFeed(client)
      await hf.initialize()
      report = await hf.check()
      if report.has_hot:
          for msg in report.hot:
              print(f"🔥 {msg.text}")
    """

    def __init__(self, client: "AIMNATSClient"):  # noqa: F821
        self.client = client
        self.agent_id = client.agent_id
        self.policy_loader = PolicyLoader(self.agent_id)
        self._poller: Optional[MessagePoller] = None
        self.classifier = StageClassifier(self.agent_id)
        self.dedup = DedupGuard()
        self.archive_router = ArchiveRouter()
        self._initialized = False
        self._known_msg_ids: Set[str] = set()
        self._processed_seqs: Set[int] = set()

    async def initialize(self):
        """初始化（在 AIMNATSClient.connect() 后调用）"""
        if not self.client.js:
            raise RuntimeError("NATS JetStream not available. Call client.connect() first.")

        self.policy_loader.set_js(self.client.js)
        await self.policy_loader.load()

        # 初始化 poller
        self._poller = MessagePoller(self.agent_id, self.client.js)
        cursor_path = os.path.expanduser(
            f"~/.aim/agents/{self.agent_id}/hot_feed_cursor.json"
        )
        self._poller.set_cursor_file(cursor_path)

        self._initialized = True
        log.info(f"[{self.agent_id}] AIMHotFeed initialized")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def check(self, context: Optional[dict] = None) -> HotFeedReport:
        """增量拉取 → 分级 → 去重 → 返回报告

        应在每次 AI 回复前调用。
        """
        if not self._initialized or not self._poller:
            return HotFeedReport(timestamp=datetime.now(timezone.utc).isoformat())

        # 加载当前策略
        policy = await self.policy_loader.get_policy()

        # 增量拉取
        raw_messages = await self._poller.pull(batch_size=50)
        if not raw_messages:
            # 首次调用时用 get_recent 做冷启动
            raw_messages = await self._poller.get_recent(count=20)

        report = HotFeedReport(timestamp=datetime.now(timezone.utc).isoformat())
        now = time.time()
        silence_cfg = policy.get("silence", {})
        in_silence = self._is_in_silence(silence_cfg)

        for msg in raw_messages:
            # 去重（已见过的 msg_id）
            msg_id = msg.get("id", "") or msg.get("msg_id", "")
            if msg_id and msg_id in self._known_msg_ids:
                continue

            # 跳过自己发的
            from_agent = msg.get("from", "")
            if from_agent == self.agent_id:
                continue

            # 分级
            classified = self.classifier.classify(msg, policy, now)

            # 静默期处理
            if in_silence and classified.stage == "hot":
                mention_penetrate = silence_cfg.get("mention_penetrate", True)
                if not mention_penetrate:
                    classified.stage = "warm"
                else:
                    # 穿透但去重
                    effective_stage = self.dedup.check(
                        from_agent or "unknown", silence_cfg
                    )
                    if effective_stage != "hot":
                        classified.stage = effective_stage
                        classified.reason += f" (silence_dedup: {effective_stage})"
                    self.dedup.record(from_agent or "unknown", classified.stage)

            # 路由到 report
            stage = classified.stage
            if stage == "hot":
                report.hot.append(classified)
            elif stage == "warm":
                report.warm.append(classified)
            elif stage == "cold":
                report.cold.append(classified)
            # archive 不出报告

            # 记录已处理
            if msg_id:
                self._known_msg_ids.add(msg_id)
            stream_seq = msg.get("_stream_seq")
            if stream_seq is not None:
                self._processed_seqs.add(stream_seq)

            # 清理已知 ID 集合（最多保留 10000 条）
            if len(self._known_msg_ids) > 10000:
                self._known_msg_ids = set(list(self._known_msg_ids)[-5000:])

        return report

    async def mark_read(self, msg_ids: List[str]):
        """标注已读，消息退出热状态"""
        for mid in msg_ids:
            self._known_msg_ids.add(mid)

    async def get_policy(self, group_id: Optional[str] = None) -> dict:
        return await self.policy_loader.get_policy(group_id)

    async def update_policy(self, policy: dict, group_id: Optional[str] = None):
        """更新策略（菜单模式）"""
        if group_id:
            await self.policy_loader.write_group_policy(group_id, policy)
        else:
            await self.policy_loader.write_template(policy)

    async def get_recent_messages(self, count: int = 20) -> List[dict]:
        """原始消息查询（用于首次冷启动或调试）"""
        if not self._poller:
            return []
        return await self._poller.get_recent(count)

    async def cleanup(self):
        """清理资源"""
        if self._poller:
            await self._poller.cleanup()

    # ── helpers ──

    @staticmethod
    def _is_in_silence(silence_cfg: dict) -> bool:
        """检查当前是否在静默时段"""
        hours = silence_cfg.get("hours", [23, 8])
        if not hours or len(hours) < 2:
            return False
        start, end = hours
        now_hour = datetime.now().hour
        if start <= end:
            return start <= now_hour < end
        else:
            # 跨天 [23, 8]
            return now_hour >= start or now_hour < end

    def stats(self) -> dict:
        """自检接口"""
        return {
            "initialized": self._initialized,
            "known_msg_ids": len(self._known_msg_ids),
            "processed_seqs": len(self._processed_seqs),
            "last_cursor": self._poller._last_seq if self._poller else 0,
        }


# ── 兼容层：从 aim_nats_sdk 附加到 AIMNATSClient ──

async def attach_to_client(client: "AIMNATSClient") -> AIMHotFeed:  # noqa: F821
    """将 HotFeed 附加到 AIMNATSClient 实例

    在 client.connect() 后调用:
        await attach_to_client(client)
        report = await client.hot_feed.check()
    """
    hf = AIMHotFeed(client)
    await hf.initialize()
    client.hot_feed = hf
    return hf
