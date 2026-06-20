"""QueuePersist — 消息队列 JSONL 持久化层

设计：
  - JSONL 追加写入，崩溃安全
  - ops: enqueue | ack | nack，按 msg_id 重放，last-op-wins
  - 启动时从文件恢复未 ack 的消息到内存队列
  - 文件 > compact_threshold 时自动压缩

文件格式（一行一条）：
  {"op":"enqueue","msg_id":"...","data":{...},"ts":1234567890}
  {"op":"ack","msg_id":"...","ts":1234567890}
"""
from __future__ import annotations
import json
import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict, Set

from .types import Message

logger = logging.getLogger(__name__)

DEFAULT_PERSIST_DIR = Path.home() / "shared" / "aim" / "data"
DEFAULT_PERSIST_FILE = "queue.jsonl"
COMPACT_THRESHOLD = 50_000  # 50KB，超过就压缩


class QueuePersist:
    """JSONL 持久化追加写入器

    线程安全：单线程 asyncio 模型，无需加锁。
    异步写入：文件操作不阻塞队列入队/出队。
    """

    def __init__(
        self,
        filepath: Optional[Path] = None,
        compact_threshold: int = COMPACT_THRESHOLD,
    ):
        self.filepath = filepath or (DEFAULT_PERSIST_DIR / DEFAULT_PERSIST_FILE)
        self.compact_threshold = compact_threshold
        self._write_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self):
        """启动后台写入循环"""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._writer_task = asyncio.create_task(self._write_loop(), name="queue-persist-writer")
        logger.info(f"QueuePersist 启动: {self.filepath}")

    async def stop(self):
        """停止后台写入，flush 剩余"""
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        # 写入剩余
        while not self._write_queue.empty():
            try:
                entry = self._write_queue.get_nowait()
                await self._append_line(entry)
            except asyncio.QueueEmpty:
                break
        logger.info("QueuePersist 已停止")

    async def _write_loop(self):
        """后台写入循环"""
        while True:
            entry = await self._write_queue.get()
            await self._append_line(entry)

    async def _append_line(self, entry: dict):
        """追加一行 JSON 到文件"""
        try:
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self.filepath, "a") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"QueuePersist 写入失败: {e}")

    # ── 写入 ops ──────────────────────────────────────────

    async def write_enqueue(self, msg: Message):
        """记录入队"""
        entry = {
            "op": "enqueue",
            "msg_id": msg.msg_id,
            "data": _message_to_dict(msg),
            "ts": time.time(),
        }
        await self._write_queue.put(entry)
        await self._maybe_compact()

    async def write_ack(self, msg_id: str):
        """记录 ack"""
        entry = {"op": "ack", "msg_id": msg_id, "ts": time.time()}
        await self._write_queue.put(entry)

    async def write_nack(self, msg_id: str, reason: str = ""):
        """记录 nack"""
        entry = {"op": "nack", "msg_id": msg_id, "ts": time.time(), "reason": reason}
        await self._write_queue.put(entry)

    # ── 恢复 ──────────────────────────────────────────────

    async def restore(self) -> List[Message]:
        """从文件恢复未 ack 的消息

        重放逻辑：
          1. 读出所有 ops
          2. 对于每个 msg_id，找最后一条 op
          3. 最后 op 是 enqueue → 恢复为 pending
          4. 最后 op 是 ack/nack → 已处理，跳过
        """
        if not self.filepath.exists():
            logger.info("无持久化文件，跳过恢复")
            return []

        ops = await self._read_all_ops()
        if not ops:
            return []

        # 找每个 msg_id 的最后一条 op
        last_ops: Dict[str, dict] = {}
        for entry in ops:
            if not isinstance(entry, dict):
                logger.warning(f"QueuePersist 跳过非 dict entry: {type(entry).__name__}")
                continue
            mid = entry.get("msg_id", "")
            if mid:
                last_ops[mid] = entry

        # 恢复 pending 消息
        restored: List[Message] = []
        ack_count = 0
        nack_count = 0
        for msg_id, last_op in last_ops.items():
            if last_op["op"] == "enqueue":
                data = last_op.get("data", {})
                msg = _dict_to_message(data)
                restored.append(msg)
            elif last_op["op"] == "ack":
                ack_count += 1
            elif last_op["op"] == "nack":
                nack_count += 1

        logger.info(
            f"QueuePersist 恢复完成: pending={len(restored)} ack={ack_count} nack={nack_count}"
        )
        return restored

    async def _read_all_ops(self) -> List[dict]:
        """读出文件中所有 ops"""
        ops = []
        try:
            with open(self.filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ops.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning(f"QueuePersist 跳过损坏的行: {line[:100]}")
        except FileNotFoundError:
            pass
        return ops

    # ── 压缩 ──────────────────────────────────────────────

    async def _maybe_compact(self):
        """如果文件超过阈值，触发压缩"""
        try:
            size = self.filepath.stat().st_size
        except FileNotFoundError:
            return
        if size > self.compact_threshold:
            await self.compact()

    async def compact(self):
        """压缩文件：只保留未 ack 的消息

        在 compact 期间写入 ops 暂存到 _write_queue，
        compact 后由 _write_loop 继续写入。
        """
        try:
            ops = await self._read_all_ops()
        except Exception as e:
            logger.error(f"QueuePersist 压缩读取失败: {e}")
            return

        if len(ops) < 2:
            return

        # 找需要保留的 msg_id
        last_ops: Dict[str, dict] = {}
        for entry in ops:
            mid = entry.get("msg_id", "")
            if mid:
                last_ops[mid] = entry

        # 只保留未 ack 的 enqueue
        keep_lines = []
        kept = 0
        for msg_id, last_op in last_ops.items():
            if last_op["op"] == "enqueue":
                keep_lines.append(json.dumps(last_op, ensure_ascii=False) + "\n")
                kept += 1

        # 原子写入
        tmp = self.filepath.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.writelines(keep_lines)
        os.replace(tmp, self.filepath)

        logger.info(f"QueuePersist 压缩完成: {len(ops)}→{kept} 行, {self.filepath.stat().st_size}B")


# ── Message ↔ dict 转换 ──────────────────────────────────

def _message_to_dict(msg: Message) -> dict:
    return {
        "msg_id": msg.msg_id,
        "from_id": msg.from_id,
        "to_id": msg.to_id,
        "grp_id": msg.grp_id,
        "msg_type": msg.msg_type,
        "content": msg.content,
        "received_at": msg.received_at,
        "dequeued_at": msg.dequeued_at,
        "retry_count": msg.retry_count,
    }


def _dict_to_message(data: dict) -> Message:
    return Message(
        msg_id=data.get("msg_id", ""),
        from_id=data.get("from_id", ""),
        to_id=data.get("to_id", ""),
        grp_id=data.get("grp_id", ""),
        msg_type=data.get("msg_type", "dm"),
        content=data.get("content", ""),
        received_at=data.get("received_at", time.time()),
        dequeued_at=data.get("dequeued_at", 0.0),
        retry_count=data.get("retry_count", 0),
    )
