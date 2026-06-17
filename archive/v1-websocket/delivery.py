"""
AIM V2 Phase 2.1 — 消息保达核心模块

功能：
1. PendingDelivery — 单条消息投递追踪 + 异步重传
2. OfflineQueue — 离线消息队列（JSONL持久化、上限、过期、批量推送）
3. DeliveryGuarantee — 统一入口（发送、ACK处理、状态查询）
4. DeliveryState — 完整投递状态（transport + application 层）

作者：呱呱 🐸 (ZS0001)
日期：2026-06-06
基于：吉量 2.1 消息保达设计
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional, List, Callable, Any, Set

# ── 配置 ──────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AIM_DATA_DIR", Path.home() / ".hermes" / "aim" / "data"))

# 重传参数
DEFAULT_RETRY_INTERVAL = 30.0   # 等待 ACK 超时（秒）
DEFAULT_MAX_RETRIES = 3         # 最大重传次数
DEFAULT_TOTAL_TIMEOUT = 120.0   # 总超时（30s + 30s×3）

# 离线队列参数
DEFAULT_OFFLINE_MAX = 5000      # 队列上限
DEFAULT_OFFLINE_TTL = 86400     # 消息过期时间（24h）
DEFAULT_OFFLINE_BATCH = 50       # 上线推送每批条数
DEFAULT_OFFLINE_INTERVAL = 0.2   # 批次间隔（秒，≤500条）
DEFAULT_OFFLINE_INTERVAL_FAST = 0.1  # 批次间隔（秒，>500条加速）

# 去重参数
MAX_SEEN_MSGS = 10000
MAX_PROCESSED_MSGS = 10000

log = logging.getLogger("aim.delivery")


# ── 数据结构 ──────────────────────────────────────

@dataclass
class PendingDelivery:
    """单条消息的投递追踪"""
    msg_id: str
    msg_data: dict          # 序列化后的消息体
    to_agent: str
    from_agent: str
    retries_left: int       # 剩余重试次数
    last_sent_at: float     # 上次发送时间
    next_retry_at: float    # 下次重试时间
    status: str = "pending" # pending | delivered | offlined | failed
    created_at: float = field(default_factory=time.time)
    delivered_at: float = 0.0
    # transport 确认后继续追踪 application 层
    app_state: str = ""     # "" | received | read | replied | timeout
    received_at: float = 0.0
    read_at: float = 0.0
    replied_at: float = 0.0


@dataclass
class DeliveryState:
    """消息的完整投递状态（用于查询）"""
    msg_id: str
    transport_state: str    # pending/delivered/offlined/failed
    app_state: str          # ""/received/read/replied/timeout
    from_agent: str
    to_agent: str
    created_at: float
    delivered_at: float = 0.0
    received_at: float = 0.0
    read_at: float = 0.0
    replied_at: float = 0.0
    retries: int = 0
    content_preview: str = ""


# ── 离线队列 ──────────────────────────────────────

class OfflineQueue:
    """离线消息队列 — 每个 agent 一个
    
    特性：
    - JSONL 追加写（O(1) 入队）
    - 上限 5000 条（硬限制）
    - 24h 自动过期
    - 容量 80% 告警
    - 上线时批量推送
    """

    def __init__(self, agent_id: str, data_dir: Path = None,
                 max_messages: int = DEFAULT_OFFLINE_MAX,
                 max_age: int = DEFAULT_OFFLINE_TTL):
        self.agent_id = agent_id
        self.data_dir = data_dir or DATA_DIR
        self.max_messages = max_messages
        self.max_age = max_age
        self._path = self.data_dir / f"offline_{agent_id}.jsonl"
        self._legacy_path = self.data_dir / f"offline_{agent_id}.json"
        self._log = logging.getLogger(f"aim.offline.{agent_id}")
        # 内存计数器 — 启动时 scan JSONL 重建
        self._msg_count = 0
        self._rebuild_count()
        # 迁移旧格式
        self._migrate_legacy()

    def _rebuild_count(self):
        """从 JSONL 重建内存计数器（启动时调用）"""
        count = 0
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            count += 1
            except Exception as e:
                self._log.warning(f"重建计数器失败: {e}")
        self._msg_count = count

    def _migrate_legacy(self):
        """迁移旧 JSON 格式到 JSONL"""
        if self._legacy_path.exists() and not self._path.exists():
            try:
                with open(self._legacy_path, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                if isinstance(messages, list) and messages:
                    with open(self._path, "a", encoding="utf-8") as f:
                        for msg in messages:
                            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    self._msg_count = len(messages)
                    self._log.info(f"迁移旧离线队列: {len(messages)}条 ({self._legacy_path.name} → {self._path.name})")
                # 不删除旧文件，保留备份
            except Exception as e:
                self._log.warning(f"迁移旧离线队列失败: {e}")

    def push(self, msg_data: dict) -> bool:
        """入队 — 返回 False 表示队列已满"""
        if self._msg_count >= self.max_messages:
            self._log.warning(f"离线队列已满: {self.agent_id} ({self._msg_count}条)")
            return False

        entry = {
            **msg_data,
            "_offline_ts": time.time(),
            "_offline_seq": self._msg_count + 1,
        }

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._msg_count += 1  # 内存计数器递增
        except Exception as e:
            self._log.error(f"入队失败: {e}")
            return False

        # 容量告警
        if self._msg_count >= self.max_messages * 0.8:
            self._log.warning(f"离线队列容量告警: {self.agent_id} ({self._msg_count}/{self.max_messages})")

        return True

    def pop_batch(self, batch_size: int = DEFAULT_OFFLINE_BATCH) -> list:
        """出队（批量） — 返回干净的消息列表（去除内部字段）"""
        if not self._path.exists():
            return []

        messages = []
        remaining = []
        now = time.time()

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # 过期检查
                    ts = msg.get("_offline_ts", 0)
                    if now - ts > self.max_age:
                        continue

                    if len(messages) < batch_size:
                        # 清除内部字段
                        clean = {k: v for k, v in msg.items() if not k.startswith("_offline_")}
                        messages.append(clean)
                    else:
                        remaining.append(line)
        except Exception as e:
            self._log.error(f"出队失败: {e}")
            return []

        # 重写文件（只保留未弹出的）
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                for line in remaining:
                    f.write(line + "\n")
        except Exception as e:
            self._log.error(f"重写队列文件失败: {e}")

        self._msg_count = sum(1 for line in remaining if line.strip())

        return messages

    def cleanup(self) -> int:
        """清理过期消息 — 返回清理条数"""
        if not self._path.exists():
            return 0

        valid = []
        expired_count = 0
        now = time.time()

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        expired_count += 1
                        continue

                    ts = msg.get("_offline_ts", 0)
                    if now - ts > self.max_age:
                        expired_count += 1
                    else:
                        valid.append(line)
        except Exception as e:
            self._log.error(f"清理读取失败: {e}")
            return 0

        if expired_count > 0:
            try:
                with open(self._path, "w", encoding="utf-8") as f:
                    for line in valid:
                        f.write(line + "\n")
                self._log.info(f"清理离线消息: {expired_count}条过期，{len(valid)}条保留")
            except Exception as e:
                self._log.error(f"清理写入失败: {e}")

        self._msg_count = len(valid)

        return expired_count

    def count(self) -> int:
        """获取队列长度（内存计数器，O(1)）"""
        return self._msg_count

    def clear(self):
        """清空队列"""
        if self._path.exists():
            self._path.unlink()
        self._msg_count = 0

    def _count(self) -> int:
        """获取队列长度"""
        if not self._path.exists():
            return 0
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0


# ── ACK 去重 ──────────────────────────────────────

class AckDedup:
    """ACK 去重器 — 防止重传消息被误拦
    
    区分两个集合：
    - _seen_msgs: 已投递的 msg_id（防环）
    - _processed_msgs: 已产生最终状态的 msg_id（防重复处理）
    
    重传的消息可能在 _seen_msgs 中，但不在 _processed_msgs 中，
    这种情况应该允许重传通过。
    """

    def __init__(self):
        self._seen_msgs: Set[str] = set()
        self._processed_msgs: Set[str] = set()
        self._acked_msgs: Set[str] = set()  # 已发送 ACK 的消息

    def is_processed(self, msg_id: str) -> bool:
        """消息是否已产生最终状态（应该丢弃）"""
        return msg_id in self._processed_msgs

    def is_seen(self, msg_id: str) -> bool:
        """消息是否已投递过"""
        return msg_id in self._seen_msgs

    def is_acked(self, msg_id: str) -> bool:
        """是否已发送过 ACK"""
        return msg_id in self._acked_msgs

    def mark_seen(self, msg_id: str):
        """标记为已投递"""
        self._seen_msgs.add(msg_id)
        self._trim_set(self._seen_msgs, MAX_SEEN_MSGS)

    def mark_processed(self, msg_id: str):
        """标记为已处理（最终状态）"""
        self._processed_msgs.add(msg_id)
        self._trim_set(self._processed_msgs, MAX_PROCESSED_MSGS)

    def mark_acked(self, msg_id: str):
        """标记为已发送 ACK"""
        self._acked_msgs.add(msg_id)
        self._trim_set(self._acked_msgs, MAX_SEEN_MSGS)

    def should_deliver(self, msg_id: str, is_retry: bool = False) -> bool:
        """判断是否应该投递此消息
        
        - 首次投递：未处理过 → 投递
        - 重传投递：未处理过 → 投递（即使已 seen）
        - 已处理过：丢弃
        """
        if self.is_processed(msg_id):
            return False
        if is_retry:
            # 重传：只要没最终处理过就投递
            return True
        # 首次：没 seen 过才投递
        return not self.is_seen(msg_id)

    @staticmethod
    def _trim_set(s: Set[str], max_size: int):
        """超出上限时保留最新的一半"""
        if len(s) > max_size:
            # 丢弃前半部分（近似 FIFO）
            to_remove = len(s) - max_size // 2
            for _ in range(to_remove):
                try:
                    s.pop()
                except KeyError:
                    break


# ── 投递保证管理器 ────────────────────────────────

class DeliveryGuarantee:
    """投递保证管理器 — 统一入口
    
    职责：
    1. 发送消息（带重传保证）
    2. 处理 ACK 回调（transport + application 层）
    3. 管理离线队列
    4. 管理重传循环
    5. 状态查询
    """

    def __init__(self, 
                 connection_pool=None,
                 send_fn: Callable = None,
                 peer_send_fn: Callable = None,
                 notify_fn: Callable = None,
                 retry_interval: float = DEFAULT_RETRY_INTERVAL,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 total_timeout: float = DEFAULT_TOTAL_TIMEOUT):
        """
        Args:
            connection_pool: ConnectionPool 实例
            send_fn: async def send_to_agent(agent_id, msg_data) -> bool
            peer_send_fn: async def send_to_peer(peer_ws, msg_data) -> bool
            notify_fn: async def notify_sender(agent_id, notification) -> None
            retry_interval: 重传间隔（秒）
            max_retries: 最大重传次数
            total_timeout: 总超时（秒）
        """
        self.connection_pool = connection_pool
        self.send_fn = send_fn
        self.peer_send_fn = peer_send_fn
        self.notify_fn = notify_fn
        self.retry_interval = retry_interval
        self.max_retries = max_retries
        self.total_timeout = total_timeout
        self._log = logging.getLogger("aim.delivery")

        # 待确认投递
        self._pending: Dict[str, PendingDelivery] = {}
        # 投递状态（含 application 层）
        self._states: Dict[str, DeliveryState] = {}
        # 离线队列（按 agent_id）
        self._offline_queues: Dict[str, OfflineQueue] = {}
        # ACK 去重
        self.dedup = AckDedup()
        # 重传任务
        self._retry_tasks: Dict[str, asyncio.Task] = {}
        # 清理定时器
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """启动管理器"""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._log.info("DeliveryGuarantee 启动")

    async def stop(self):
        """停止管理器"""
        self._running = False
        for task in self._retry_tasks.values():
            if not task.done():
                task.cancel()
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        self._log.info("DeliveryGuarantee 停止")

    # ── 发送 ──────────────────────────────────────

    async def send(self, msg_data: dict, to_agent: str, from_agent: str = "") -> bool:
        """发送消息（带保达保证）
        
        Returns:
            True: 消息已发送（或已入离线队列）
            False: 发送失败且无法入队
        """
        msg_id = msg_data.get("msg_id", "")
        if not msg_id:
            self._log.error("消息缺少 msg_id，无法投递")
            return False

        # 去重检查
        if self.dedup.is_processed(msg_id):
            self._log.debug(f"消息已处理，跳过: {msg_id[:12]}")
            return True

        # 尝试直接发送
        sent = await self._try_send(msg_data, to_agent, from_agent)

        if sent:
            # 注册 pending
            pd = PendingDelivery(
                msg_id=msg_id,
                msg_data=msg_data,
                to_agent=to_agent,
                from_agent=from_agent,
                retries_left=self.max_retries,
                last_sent_at=time.time(),
                next_retry_at=time.time() + self.retry_interval,
            )
            self._pending[msg_id] = pd

            # 更新状态
            self._states[msg_id] = DeliveryState(
                msg_id=msg_id,
                transport_state="pending",
                app_state="",
                from_agent=from_agent,
                to_agent=to_agent,
                created_at=time.time(),
                content_preview=str(msg_data.get("content", ""))[:100],
            )

            # 标记已 seen
            self.dedup.mark_seen(msg_id)

            # 启动重传定时器
            self._retry_tasks[msg_id] = asyncio.create_task(
                self._retry_loop(pd)
            )

            return True
        else:
            # 发送失败 → 存入离线队列
            return self._enqueue_offline(to_agent, msg_data, from_agent)

    async def _try_send(self, msg_data: dict, to_agent: str, from_agent: str) -> bool:
        """尝试发送消息到目标"""
        if not self.send_fn:
            self._log.error("send_fn 未设置")
            return False

        try:
            return await self.send_fn(to_agent, msg_data)
        except Exception as e:
            self._log.error(f"发送失败: {from_agent}→{to_agent}: {e}")
            return False

    # ── 重传 ──────────────────────────────────────

    async def _retry_loop(self, pd: PendingDelivery):
        """重传循环 — 后台任务"""
        try:
            deadline = pd.created_at + self.total_timeout

            while (pd.retries_left > 0 and 
                   time.time() < deadline and 
                   pd.status == "pending"):
                
                # 等待重传间隔
                wait_time = min(
                    pd.next_retry_at - time.time(),
                    deadline - time.time()
                )
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                # 检查是否已收到 ACK
                if pd.status == "delivered":
                    self._log.debug(f"重传取消（已送达）: {pd.msg_id[:12]}")
                    return

                # 检查总超时
                if time.time() >= deadline:
                    break

                # 检查目标是否在线
                if self.connection_pool:
                    status = self.connection_pool.get_status(pd.to_agent)
                    if status.get("connections", 0) == 0:
                        # 目标离线 → 转存离线队列
                        self._log.info(f"目标离线，转存离线队列: {pd.msg_id[:12]}→{pd.to_agent}")
                        self._enqueue_offline(pd.to_agent, pd.msg_data, pd.from_agent)
                        pd.status = "offlined"
                        self._update_state(pd.msg_id, transport_state="offlined")
                        return

                # 重传
                pd.retries_left -= 1
                pd.last_sent_at = time.time()
                pd.next_retry_at = time.time() + self.retry_interval

                retry_num = self.max_retries - pd.retries_left
                self._log.info(f"🔄 重传 {pd.msg_id[:12]}→{pd.to_agent} (第{retry_num}次, 剩余{pd.retries_left}次)")

                sent = await self._try_send(pd.msg_data, pd.to_agent, pd.from_agent)
                if not sent:
                    # 重传失败 → 存离线
                    self._enqueue_offline(pd.to_agent, pd.msg_data, pd.from_agent)
                    pd.status = "offlined"
                    self._update_state(pd.msg_id, transport_state="offlined")
                    return

            # 重传耗尽或超时
            if pd.status == "pending":
                pd.status = "failed"
                self._update_state(pd.msg_id, transport_state="failed")
                self._log.warning(f"投递失败（重传耗尽）: {pd.msg_id[:12]}→{pd.to_agent}")

                # 通知发送方
                if self.notify_fn:
                    try:
                        await self.notify_fn(pd.from_agent, {
                            "cmd": "delivery_failed",
                            "msg_id": pd.msg_id,
                            "to_agent": pd.to_agent,
                            "reason": "max_retries_exhausted",
                            "retries": self.max_retries,
                            "duration_seconds": int(time.time() - pd.created_at),
                            "suggestion": "目标可能离线，消息已入离线队列，上线后自动推送",
                        })
                    except Exception as e:
                        self._log.error(f"通知发送方失败: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log.error(f"重传循环异常: {pd.msg_id[:12]}: {e}")
        finally:
            # 清理（延迟清理，保留状态供查询）
            self._retry_tasks.pop(pd.msg_id, None)

    # ── ACK 处理 ──────────────────────────────────

    def handle_ack(self, data: dict):
        """处理接收到的 ACK
        
        ACK 格式：
        {
            "cmd": "ack",
            "msg_id": "xxx",
            "status": "delivered" | "received" | "read" | "replied",
            "to_agent": "发送方ID",
            "by_agent": "处理方ID",  // application 层
            "ts": 1234567890.0,
            "detail": ""  // replied 时可携带摘要
        }
        """
        msg_id = data.get("msg_id", "")
        status = data.get("status", "")
        ts = data.get("ts", time.time())

        if not msg_id or not status:
            return

        pd = self._pending.get(msg_id)

        if status == "delivered":
            # Transport 层确认
            if pd:
                pd.status = "delivered"
                pd.delivered_at = ts
                self._log.info(f"✅ ACK delivered: {msg_id[:12]}→{pd.to_agent}")
            self._update_state(msg_id, 
                              transport_state="delivered",
                              delivered_at=ts)
            # 延迟清理 pending（300s 后）
            if pd:
                asyncio.create_task(self._cleanup_pending_after(msg_id, 300))

        elif status in ("received", "read", "replied"):
            # Application 层确认
            if pd:
                pd.app_state = status
                if status == "received":
                    pd.received_at = ts
                elif status == "read":
                    pd.read_at = ts
                elif status == "replied":
                    pd.replied_at = ts
                    # replied 是终态，可以清理
                    asyncio.create_task(self._cleanup_pending_after(msg_id, 60))

            self._update_state(msg_id,
                              app_state=status,
                              **{f"{status}_at": ts})

            self._log.info(f"✅ ACK {status}: {msg_id[:12]}")

    async def _cleanup_pending_after(self, msg_id: str, delay: float):
        """延迟清理 pending 记录"""
        await asyncio.sleep(delay)
        self._pending.pop(msg_id, None)

    # ── 离线队列 ──────────────────────────────────

    def _get_offline_queue(self, agent_id: str) -> OfflineQueue:
        """获取离线队列实例"""
        if agent_id not in self._offline_queues:
            self._offline_queues[agent_id] = OfflineQueue(agent_id)
        return self._offline_queues[agent_id]

    def _enqueue_offline(self, to_agent: str, msg_data: dict, from_agent: str = "") -> bool:
        """存入离线队列"""
        queue = self._get_offline_queue(to_agent)
        ok = queue.push(msg_data)

        if ok:
            self._log.info(f"📬 存入离线队列: {from_agent}→{to_agent} (队列:{queue.count()}条)")
            self._update_state(msg_data.get("msg_id", ""),
                              transport_state="offlined")
        else:
            self._log.warning(f"离线队列已满，丢弃消息: {from_agent}→{to_agent}")
            self._update_state(msg_data.get("msg_id", ""),
                              transport_state="failed")

        return ok

    async def push_offline_messages(self, agent_id: str, send_fn: Callable = None) -> int:
        """上线时推送离线消息
        
        Args:
            agent_id: 上线的 agent
            send_fn: async def send(agent_id, msg_data) -> bool
            
        Returns:
            推送条数
        """
        fn = send_fn or self.send_fn
        if not fn:
            self._log.error("send_fn 未设置，无法推送离线消息")
            return 0

        queue = self._get_offline_queue(agent_id)
        total_pushed = 0
        
        # 获取队列长度决定推送间隔
        queue_count = queue.count()
        use_fast = queue_count > 500
        interval = DEFAULT_OFFLINE_INTERVAL_FAST if use_fast else DEFAULT_OFFLINE_INTERVAL
        if use_fast:
            self._log.info(f"离线队列>500条 ({queue_count})，使用快速推送间隔 {interval}s")

        while True:
            batch = queue.pop_batch(batch_size=DEFAULT_OFFLINE_BATCH)
            if not batch:
                break

            for msg_data in batch:
                try:
                    ok = await fn(agent_id, msg_data)
                    if ok:
                        total_pushed += 1
                except Exception as e:
                    self._log.error(f"推送离线消息失败: {e}")
                    # 失败的消息重新入队
                    queue.push(msg_data)

            # 间隔防冲爆
            await asyncio.sleep(interval)

        if total_pushed > 0:
            self._log.info(f"📤 离线推送完成: {agent_id} ({total_pushed}条)")

        return total_pushed

    # ── 状态管理 ──────────────────────────────────

    def _update_state(self, msg_id: str, **kwargs):
        """更新投递状态"""
        if not msg_id:
            return
        state = self._states.get(msg_id)
        if not state:
            return
        for k, v in kwargs.items():
            if hasattr(state, k):
                setattr(state, k, v)

    def get_delivery_state(self, msg_id: str) -> Optional[DeliveryState]:
        """获取消息投递状态"""
        return self._states.get(msg_id)

    def get_pending_count(self) -> int:
        """获取待确认消息数"""
        return len(self._pending)

    def get_pending_summary(self) -> list:
        """获取所有待确认消息摘要"""
        return [
            {
                "msg_id": pd.msg_id,
                "to": pd.to_agent,
                "retries_left": pd.retries_left,
                "status": pd.status,
                "app_state": pd.app_state,
                "age_seconds": int(time.time() - pd.created_at),
            }
            for pd in self._pending.values()
        ]

    # ── 清理 ──────────────────────────────────────

    async def _cleanup_loop(self):
        """定期清理过期状态"""
        while self._running:
            try:
                await asyncio.sleep(3600)  # 每小时

                # 清理离线队列过期消息
                for agent_id, queue in self._offline_queues.items():
                    expired = queue.cleanup()
                    if expired > 0:
                        self._log.info(f"清理 {agent_id} 离线队列: {expired}条过期")

                # 清理已完成的状态（超过1小时）
                now = time.time()
                expired_states = [
                    msg_id for msg_id, state in self._states.items()
                    if (state.transport_state in ("delivered", "failed", "offlined") and
                        state.app_state in ("replied", "timeout", "") and
                        now - state.created_at > 3600)
                ]
                for msg_id in expired_states:
                    self._states.pop(msg_id, None)

                if expired_states:
                    self._log.debug(f"清理过期状态: {len(expired_states)}条")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"清理循环异常: {e}")
