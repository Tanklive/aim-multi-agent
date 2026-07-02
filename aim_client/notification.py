"""
AIM Notification API — 平台标准新消息提醒接口（类似微信红点）

设计目标:
  - 用户安装→注册→接入→获取身份→AI 沟通协作→有新消息→提醒→触发 AI 正常沟通协作
  - 事件驱动，不阻塞 dispatch 主循环

事件模型 (4 类):
  - message.received    新消息入队（含 from_id / content 摘要 / 群聊标记）
  - message.mentioned   被 @ 提及（高优先级，立即关注）
  - message.processed   AI 处理完成（含回复摘要，通知闭环）
  - message.failed      处理失败（adapter 超时/降级/退避耗尽/人需介入）

三层通道 (config.json notification.channel 数组):
  - "file"          文件通道 → ~/.aim/notifications/{event}.jsonl（轮询兼容）
  - "system_event"  系统事件 → OpenClaw/Gateway SystemEvent 实时推送
  - "webhook"       Webhook  → POST 到外部 URL
  - 默认: ["file"]（不声明则只开文件通道）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiohttp  # type: ignore
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

# ── 事件类型 ──────────────────────────────────────────────

class EventType:
    RECEIVED  = "message.received"
    MENTIONED = "message.mentioned"
    PROCESSED = "message.processed"
    FAILED    = "message.failed"

ALL_EVENTS = (EventType.RECEIVED, EventType.MENTIONED, EventType.PROCESSED, EventType.FAILED)

# ── NotificationHandler ───────────────────────────────────

class NotificationHandler:
    """统一通知处理器——在 dispatch 主循环中非阻塞发射事件"""

    def __init__(
        self,
        agent_id: str,
        channels: Optional[List[str]] = None,
        webhook_url: Optional[str] = None,
        system_event_publisher: Optional[Any] = None,  # Callable[[dict], Awaitable[None]]
        logger: Optional[logging.Logger] = None,
    ):
        self.agent_id = agent_id
        self.logger = logger or logging.getLogger("aim-notification")

        # 通道配置（默认仅 file，生产可按需开启 system_event/webhook）
        self.channels: List[str] = channels or ["file"]
        self.webhook_url = webhook_url

        # 624: system_event 通道的 NATS publisher（由 Transport 注入）
        #      签名为 async def publish(envelope: dict) -> None
        self._system_event_publisher = system_event_publisher

        # 文件通道根目录
        self._notify_dir = Path.home() / ".aim" / "notifications"
        self._notify_dir.mkdir(parents=True, exist_ok=True)

        # 去重：防止同一 msg_id 重复发射（已处理过的消息不再通知）
        self._emitted_ids: set = set()
        self._max_emitted = 2000  # 最多缓存

        # Webhook session (lazy)
        self._session: Any = None

    # ── 公共 API ──

    def set_system_event_publisher(self, publisher: Any) -> None:
        """运行时注入 system_event 通道的 NATS publisher（由 main.py Transport 提供）

        publisher 签名: async def publish(envelope: dict) -> None
        """
        self._system_event_publisher = publisher

    async def emit(self, event: str, payload: Dict[str, Any]) -> None:
        """发射通知事件（fire-and-forget，阻塞主循环）"""

        # 去重（仅对 processed/failed 事件按 msg_id 去重，received/mentioned 不在此去重）
        dedup_id = payload.get("msg_id", "")
        if event in (EventType.PROCESSED, EventType.FAILED) and dedup_id:
            if dedup_id in self._emitted_ids:
                return
            self._add_dedup(dedup_id)

        envelope = {
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "agent_id": self.agent_id,
            "payload": payload,
        }

        # 所有通道并发发射
        tasks = []
        if "file" in self.channels:
            tasks.append(self._emit_file(envelope))
        if "system_event" in self.channels:
            tasks.append(self._emit_system_event(envelope))
        if "webhook" in self.channels and self.webhook_url:
            tasks.append(self._emit_webhook(envelope))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def emit_async(self, event: str, payload: Dict[str, Any]) -> None:
        """异步发射——不阻塞调用方（create_task fire-and-forget）"""
        try:
            asyncio.create_task(self.emit(event, payload))
        except Exception as e:
            self.logger.debug(f"[notification] emit_async 失败: {e}")

    async def received(self, msg_id: str, from_id: str, content_preview: str,
                       is_dm: bool = True, grp_id: str = "") -> None:
        """消息入队通知"""
        await self.emit_async(EventType.RECEIVED, {
            "msg_id": msg_id,
            "from_id": from_id,
            "preview": content_preview[:120],
            "is_dm": is_dm,
            "grp_id": grp_id,
        })

    async def mentioned(self, msg_id: str, from_id: str, content_preview: str,
                        grp_id: str = "") -> None:
        """被 @ 提及通知（高优先级）"""
        await self.emit_async(EventType.MENTIONED, {
            "msg_id": msg_id,
            "from_id": from_id,
            "preview": content_preview[:120],
            "grp_id": grp_id,
            "priority": "high",
        })

    async def processed(self, msg_id: str, from_id: str,
                        reply_preview: str = "", elapsed_ms: int = 0) -> None:
        """处理完成通知"""
        await self.emit_async(EventType.PROCESSED, {
            "msg_id": msg_id,
            "from_id": from_id,
            "reply_preview": reply_preview[:120] if reply_preview else "",
            "elapsed_ms": elapsed_ms,
        })

    async def failed(self, msg_id: str, from_id: str, reason: str,
                     retries: int = 0) -> None:
        """处理失败通知"""
        await self.emit_async(EventType.FAILED, {
            "msg_id": msg_id,
            "from_id": from_id,
            "reason": reason,
            "retries": retries,
        })

    # ── 通道实现 ──

    async def _emit_file(self, envelope: Dict[str, Any]) -> None:
        """文件通道: append JSONL"""
        try:
            event_name = envelope["event"]
            fpath = self._notify_dir / f"{event_name}.jsonl"
            line = json.dumps(envelope, ensure_ascii=False) + "\n"
            with open(fpath, "a") as f:
                f.write(line)
        except Exception as e:
            self.logger.debug(f"[notification] file emit 失败: {e}")

    async def _emit_system_event(self, envelope: Dict[str, Any]) -> None:
        """系统事件通道: 通过注入的 publisher 发布 NATS 事件（供 OpenClaw Gateway 订阅）"""
        if self._system_event_publisher is None:
            self.logger.debug(f"[notification] system_event publisher 未注入，跳过: {envelope['event']}")
            return
        try:
            await self._system_event_publisher(envelope)
        except Exception as e:
            self.logger.debug(f"[notification] system_event emit 失败: {e}")

    async def _emit_webhook(self, envelope: Dict[str, Any]) -> None:
        """Webhook 通道: POST JSON 到外部 URL

        超时: 5s（不阻塞主循环）。
        失败: 写 webhook_failed.jsonl，不重试（避免堆积）。
        """
        if not _HAS_AIOHTTP:
            self.logger.debug("[notification] aiohttp 未安装，webhook 跳过")
            return
        if not self.webhook_url:
            return
        try:
            if self._session is None:
                import ssl
                tls = ssl.create_default_context()
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5),
                    connector=aiohttp.TCPConnector(ssl=tls),
                )
            async with self._session.post(
                self.webhook_url,
                json=envelope,
                headers={"Content-Type": "application/json", "X-AIM-Event": envelope["event"]},
            ) as resp:
                if resp.status >= 400:
                    self._log_webhook_fail(envelope, f"HTTP {resp.status}")
        except asyncio.TimeoutError:
            self._log_webhook_fail(envelope, "timeout")
        except Exception as e:
            self._log_webhook_fail(envelope, f"error: {e}")

    def _log_webhook_fail(self, envelope: Dict[str, Any], reason: str) -> None:
        """Webhook 失败记录到文件，供外部 daemon 感知"""
        try:
            fpath = self._notify_dir / "webhook_failed.jsonl"
            record = {
                "ts": time.time(),
                "reason": reason,
                "event": envelope.get("event", ""),
                "url": self.webhook_url,
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with open(fpath, "a") as f:
                f.write(line)
        except Exception:
            pass  # 写文件都失败了，彻底放弃

    # ── 辅助 ──

    def _add_dedup(self, msg_id: str) -> None:
        self._emitted_ids.add(msg_id)
        if len(self._emitted_ids) > self._max_emitted:
            # FIFO 淘汰
            excess = len(self._emitted_ids) - self._max_emitted
            for _ in range(excess):
                self._emitted_ids.pop()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── 辅助：检测 @ 提及 ──

    @staticmethod
    def detect_mention(content: str, agent_names: List[str]) -> bool:
        """检测消息中是否 @ 了本方 Agent（支持 ID 和昵称）"""
        for name in agent_names:
            if f"@{name}" in content or f"@ {name}" in content:
                return True
        return False
