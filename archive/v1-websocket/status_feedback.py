"""
AIM Status Feedback 模块
协议版本: aim-status-v1
功能: 客户端状态回推 + status_log.jsonl 持久化

作者: 呱呱 🐸
日期: 2026-06-07
"""

import json
import time
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Awaitable
import logging

log = logging.getLogger("status_feedback")

# 状态常量
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"

# Step 命名规范
# 快步骤（<3s）: memory_search, db_query - 默认不推
# 长步骤（≥3s）: reasoning, web_fetch, code_exec - 必推
# 关键步骤: task_start, task_end - 始终推
# 保活: still_working - 每30s推一次

# 节流配置
THROTTLE_INTERVAL = 5.0  # 最大静默期（秒）
HEARTBEAT_INTERVAL = 30.0  # 长任务保活间隔（秒）

# 快步骤列表（默认不推）
QUICK_STEPS = {"memory_search", "db_query", "cache_lookup", "file_read"}

# 关键步骤列表（始终推）
KEY_STEPS = {"task_start", "task_end"}

# 长步骤列表（必推）
LONG_STEPS = {"reasoning", "web_fetch", "code_exec", "ai_call", "web_search"}


class StatusFeedback:
    """状态反馈管理器"""
    
    def __init__(self, agent_id: str, ws_sender: Optional[Callable] = None):
        """
        初始化状态反馈管理器
        
        Args:
            agent_id: 客户端 ID (如 ZS0001)
            ws_sender: WebSocket 发送函数 (async)
        """
        self.agent_id = agent_id
        self._ws_sender = ws_sender
        self._last_push_time: dict[str, float] = {}  # session_id -> last_push_time
        self._task_start_time: dict[str, float] = {}  # session_id -> start_time
        self._last_heartbeat: dict[str, float] = {}  # session_id -> last_heartbeat
        self._log_dir = Path.home() / ".hermes" / "aim" / "data" / "status_logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._seq_counter = 0
        
    def _get_log_file(self) -> Path:
        """获取当天的 status_log 文件路径"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._log_dir / f"status_log_{today}.jsonl"
    
    def _write_log(self, record: dict):
        """追加写入 status_log.jsonl"""
        try:
            log_file = self._get_log_file()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            log.error(f"写入 status_log 失败: {e}")
    
    def _should_push(self, session_id: str, step: str) -> bool:
        """判断是否应该推送"""
        # 关键步骤始终推
        if step in KEY_STEPS:
            return True
        
        # 快步骤默认不推
        if step in QUICK_STEPS:
            return False
        
        # 长步骤必推
        if step in LONG_STEPS:
            return True
        
        # 静默兜底：距离上次推送超过 THROTTLE_INTERVAL 秒
        last_push = self._last_push_time.get(session_id, 0)
        if time.time() - last_push >= THROTTLE_INTERVAL:
            return True
        
        return False
    
    async def push(self, session_id: str, step: str, status: str,
                   progress: str = "", duration_ms: Optional[int] = None):
        """
        推送 status_feedback
        
        Args:
            session_id: 关联的原始 msg_id
            step: 当前步骤名
            status: running / completed / error / timeout
            progress: 人类可读的进度描述
            duration_ms: 当前步骤已执行耗时
        """
        # 节流判断
        if not self._should_push(session_id, step):
            return
        
        self._seq_counter += 1
        timestamp = int(time.time())
        
        # 计算 duration_ms
        if duration_ms is None:
            start_time = self._task_start_time.get(session_id)
            if start_time:
                duration_ms = int((time.time() - start_time) * 1000)
        
        feedback = {
            "msg_type": "status_feedback",
            "protocol_version": "aim-status-v1",
            "from": self.agent_id,
            "session_id": session_id,
            "step": step,
            "status": status,
            "progress": progress,
            "duration_ms": duration_ms,
            "timestamp": timestamp,
            "seq": self._seq_counter,
        }
        
        # 1. 写入本地日志
        self._write_log(feedback)
        
        # 2. WS 推送给服务端
        if self._ws_sender:
            try:
                await self._ws_sender(feedback)
                self._last_push_time[session_id] = time.time()
                log.debug(f"📤 Status feedback: {session_id} → {step} ({status})")
            except Exception as e:
                log.error(f"推送 status_feedback 失败: {e}")
    
    async def start_task(self, session_id: str):
        """任务开始"""
        self._task_start_time[session_id] = time.time()
        self._last_heartbeat[session_id] = time.time()
        await self.push(session_id, "task_start", STATUS_RUNNING, "任务开始")
    
    async def end_task(self, session_id: str, success: bool = True, summary: str = ""):
        """任务结束"""
        status = STATUS_COMPLETED if success else STATUS_ERROR
        await self.push(session_id, "task_end", status, summary)
        # 清理
        self._task_start_time.pop(session_id, None)
        self._last_heartbeat.pop(session_id, None)
        self._last_push_time.pop(session_id, None)
    
    async def step_start(self, session_id: str, step: str, progress: str = ""):
        """步骤开始"""
        await self.push(session_id, step, STATUS_RUNNING, progress)
    
    async def step_end(self, session_id: str, step: str, success: bool = True, progress: str = ""):
        """步骤结束"""
        status = STATUS_COMPLETED if success else STATUS_ERROR
        await self.push(session_id, step, status, progress)
    
    async def heartbeat(self, session_id: str, progress: str = ""):
        """长任务保活 heartbeat（每30s）"""
        last_hb = self._last_heartbeat.get(session_id, 0)
        if time.time() - last_hb >= HEARTBEAT_INTERVAL:
            await self.push(session_id, "still_working", STATUS_RUNNING, progress or "仍在处理...")
            self._last_heartbeat[session_id] = time.time()
    
    def get_last_seq(self) -> int:
        """获取当前最大 seq（用于 observer 断连回放）"""
        return self._seq_counter


# 全局实例
_status_feedback: Optional[StatusFeedback] = None


def init_status_feedback(agent_id: str, ws_sender: Optional[Callable] = None) -> StatusFeedback:
    """初始化全局状态反馈管理器"""
    global _status_feedback
    _status_feedback = StatusFeedback(agent_id, ws_sender)
    return _status_feedback


def get_status_feedback() -> Optional[StatusFeedback]:
    """获取全局状态反馈管理器"""
    return _status_feedback
