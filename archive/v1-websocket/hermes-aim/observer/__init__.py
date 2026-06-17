"""
AIM Observer 增强模块 — 事件过滤、持久化、重连逻辑

本模块是对 aim_observer.py 核心功能的补充和增强扩展。
提供:
  1. EventFilter — 事件类型过滤（控制终端输出内容）
  2. EventPersister — 事件持久化存储到 JSONL 文件
  3. ReconnectStrategy — 带指数退避的重连策略

用法:
  from observer.enhanced import EventFilter, EventPersister, ReconnectStrategy
"""

import json
import os
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("aim_observer.enhanced")

# ──────────────────────────────────────────────
# 1. EventFilter — 事件类型过滤
# ──────────────────────────────────────────────

class EventFilter:
    """事件类型过滤器
    控制哪些 observer 事件显示在终端，哪些静默记录。
    """

    # 默认静默事件类型（不打印终端，但仍记日志）
    DEFAULT_SILENT = frozenset({
        "heartbeat", "heartbeat_ack", "presence", "lifecycle",
    })

    # 用户关注的业务事件（必须显示）
    IMPORTANT_EVENTS = frozenset({
        "status_feedback", "status_update", "message",
        "retry_event", "delivery_event", "cache_event",
        "processing_ack", "replay_done", "error",
    })

    def __init__(self, silent_events: Optional[set] = None, verbose: bool = False):
        self.silent = set(self.DEFAULT_SILENT) | (silent_events or set())
        self.verbose = verbose

    def should_display(self, cmd: str) -> bool:
        """是否应在终端显示此事件"""
        if cmd in self.silent:
            return self.verbose  # 仅在 verbose 模式显示
        return True

    def should_log(self, cmd: str) -> bool:
        """是否应记录此事件到日志"""
        return True  # 所有事件都记日志


# ──────────────────────────────────────────────
# 2. EventPersister — 事件持久化存储
# ──────────────────────────────────────────────

class EventPersister:
    """Observer 事件持久化存储
    将接收到的所有事件写入 JSONL 文件，支持按日期分片和过期清理。
    """

    def __init__(self, log_dir: str = ""):
        if not log_dir:
            log_dir = str(Path.home() / "shared" / "aim" / "logs" / "observer")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_file = None
        self._current_date = None

    def _get_log_file(self) -> Path:
        """获取当前日期对应的日志文件"""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            self._current_file = self.log_dir / f"observer-{today}.jsonl"
        return self._current_file

    def write(self, msg: dict):
        """写入一条事件到日志"""
        entry = {
            "t": time.time(),
            "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "msg": msg,
        }
        try:
            with open(self._get_log_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning(f"写入事件日志失败: {e}")

    def cleanup(self, retention_days: int = 30):
        """清理过期日志文件"""
        if retention_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=retention_days)
        for f in self.log_dir.glob("observer-*.jsonl"):
            try:
                # 从文件名解析日期: observer-2026-06-01.jsonl
                date_str = f.stem.replace("observer-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
                    log.info(f"已清理过期日志: {f.name}")
            except (ValueError, OSError):
                continue

    def replay(self, date_str: str = None, limit: int = 100) -> list:
        """回放指定日期的日志（用于断连后补充查阅）
        
        Args:
            date_str: 日期字符串 "YYYY-MM-DD"，默认当天
            limit: 最多返回条数
        
        Returns:
            事件列表（最新的在前）
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"observer-{date_str}.jsonl"

        if not log_file.exists():
            return []

        events = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as e:
            log.warning(f"读取日志失败: {e}")

        # 按时间倒序返回最新的
        events.sort(key=lambda x: x.get("t", 0), reverse=True)
        return events[:limit]


# ──────────────────────────────────────────────
# 3. ReconnectStrategy — 重连策略
# ──────────────────────────────────────────────

class ReconnectStrategy:
    """带指数退避的重连策略
    用于 observer 断连后自动重连，避免频繁重连导致服务器压力。
    """

    def __init__(self, initial_delay: float = 2.0,
                 max_delay: float = 60.0,
                 backoff: float = 1.5,
                 jitter: float = 0.1):
        """
        Args:
            initial_delay: 初始重连延迟（秒）
            max_delay: 最大重连延迟（秒）
            backoff: 延迟倍数（指数退避）
            jitter: 随机抖动比例（避免多个 observer 同时重连）
        """
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff = backoff
        self.jitter = jitter
        self._attempt = 0

    @property
    def attempt(self) -> int:
        return self._attempt

    def next_delay(self) -> float:
        """计算下一次重连延迟"""
        import random
        delay = self.initial_delay * (self.backoff ** self._attempt)
        delay = min(delay, self.max_delay)
        # 添加随机抖动
        jitter_amount = delay * self.jitter
        delay += random.uniform(-jitter_amount, jitter_amount)
        delay = max(1.0, delay)  # 至少 1 秒
        self._attempt += 1
        return delay

    def reset(self):
        """重置重连计数（成功连接后调用）"""
        self._attempt = 0


# ──────────────────────────────────────────────
# 4. 快捷函数
# ──────────────────────────────────────────────

def get_default_persister() -> EventPersister:
    """获取默认的持久化存储实例"""
    return EventPersister()


def get_default_filter(verbose: bool = False) -> EventFilter:
    """获取默认的事件过滤器实例"""
    return EventFilter(verbose=verbose)


def get_default_reconnect() -> ReconnectStrategy:
    """获取默认的重连策略实例"""
    return ReconnectStrategy()
