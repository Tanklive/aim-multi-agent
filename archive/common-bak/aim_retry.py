#!/usr/bin/env python3
"""
AIM RetryManager — 消息重试组件（指数退避 + 最大重试限制）

用途：
  Agent 发送消息失败时自动重试，支持：
  - 指数退避（exponential backoff）
  - 最大重试次数限制
  - 分类型重试策略（发送/订阅/连接）
  - 回调通知（重试成功/最终失败）
  - 可追溯的失败记录

用法：
  rm = RetryManager(agent_id="ZS0002")
  
  # 自动重试
  result = await rm.retry(
      label="send_dm_to_ZS0001",
      fn=lambda: client.send_private_message("ZS0001", "hello"),
      max_retries=3,
  )
  
  # 手动管理
  task_id = rm.register("connect", connect_fn)
  ok, err = await rm.execute(task_id)
"""

import asyncio
import json
import logging
import math
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class RetryAttempt:
    """一次重试的记录"""
    attempt: int           # 第几次重试（0=首次）
    started_at: float      # 开始时间
    duration: float        # 耗时（秒）
    error: str             # 错误信息
    traceback: str         # 堆栈


@dataclass
class RetryTask:
    """重试任务的状态"""
    task_id: str
    label: str
    fn: Callable
    max_retries: int
    base_delay: float
    max_delay: float
    status: str = "pending"  # pending | running | success | failed
    attempts: List[RetryAttempt] = field(default_factory=list)
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    last_attempt_at: float = 0.0


class RetryManager:
    """
    消息重试管理器 — 指数退避 + 最大重试限制
    
    默认策略（发送消息）：
      - 最多重试 3 次
      - 初始延迟 1s，最大 30s
      - 退避公式: delay = min(base_delay * 1.5^attempt, max_delay)
    
    高级策略（可覆盖）：
      - 连接重试：最多 5 次，初始 2s，最大 60s
      - 订阅重试：最多 3 次，初始 1s，最大 15s
    """

    # ── 默认策略 ──────────────────────────
    DEFAULT_STRATEGIES = {
        "default":   {"max_retries": 3, "base_delay": 1.0, "max_delay": 30.0},
        "send":      {"max_retries": 3, "base_delay": 1.0, "max_delay": 30.0},
        "connect":   {"max_retries": 5, "base_delay": 2.0, "max_delay": 60.0},
        "subscribe": {"max_retries": 3, "base_delay": 1.0, "max_delay": 15.0},
        "request":   {"max_retries": 2, "base_delay": 0.5, "max_delay": 10.0},
    }

    def __init__(
        self,
        agent_id: str,
        log: logging.Logger = None,
        strategies: Dict[str, dict] = None,
        history_path: str = None,
    ):
        self.agent_id = agent_id
        self.log = log or self._default_log()
        self.strategies = {**self.DEFAULT_STRATEGIES, **(strategies or {})}

        # 任务存储
        self._tasks: Dict[str, RetryTask] = {}

        # 历史记录
        history_path = history_path or str(Path.home() / ".hermes" / "aim" / "data" / f"retry_{agent_id}.jsonl")
        Path(history_path).parent.mkdir(parents=True, exist_ok=True)
        self._history_path = history_path

        # 统计
        self.stats = {
            "total_retries": 0,
            "successful": 0,
            "failed": 0,
            "total_attempts": 0,
        }

    def _default_log(self) -> logging.Logger:
        log = logging.getLogger(f"retry-{self.agent_id}")
        log.setLevel(logging.INFO)
        return log

    # ── 延迟计算 ──────────────────────────

    @staticmethod
    def calc_delay(
        attempt: int,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: float = 0.1,
    ) -> float:
        """
        计算指数退避延迟
        delay = min(base * 1.5^attempt, max) + jitter
        """
        delay = min(base_delay * (1.5 ** attempt), max_delay)
        # 增加随机抖动，避免 thundering herd
        if jitter > 0:
            delay += delay * jitter * (math.sin(attempt * 7) * 0.5 + 0.5)
        return delay

    # ── 核心重试逻辑 ──────────────────────

    async def retry(
        self,
        label: str,
        fn: Callable,
        max_retries: int = None,
        base_delay: float = None,
        max_delay: float = None,
        strategy: str = "default",
        on_success: Callable = None,
        on_failure: Callable = None,
        timeout: float = None,
    ) -> Tuple[bool, Any]:
        """
        执行并重试
        
        参数：
          label: 任务标签（用于日志和追踪）
          fn: 异步函数
          max_retries: 最大重试次数（覆盖策略默认值）
          base_delay: 初始延迟（覆盖策略默认值）
          max_delay: 最大延迟（覆盖策略默认值）
          strategy: 策略名称（查找预定义策略中的参数）
          on_success: 成功回调(result)
          on_failure: 最终失败回调(error)
          timeout: 每次调用的超时时间（秒）
        
        返回：
          (success, result_or_error)
        """
        # 解析策略参数
        strat = self.strategies.get(strategy, self.strategies["default"])
        max_retries = max_retries if max_retries is not None else strat["max_retries"]
        base_delay = base_delay if base_delay is not None else strat["base_delay"]
        max_delay = max_delay if max_delay is not None else strat["max_delay"]

        # 创建任务
        task_id = f"retry_{uuid.uuid4().hex[:8]}"
        task = RetryTask(
            task_id=task_id,
            label=label,
            fn=fn,
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
        )
        self._tasks[task_id] = task

        # 开始执行
        task.status = "running"
        attempts = 0

        while attempts <= max_retries:
            attempt_start = time.time()
            try:
                # 设置超时
                if timeout:
                    result = await asyncio.wait_for(fn(), timeout=timeout)
                else:
                    result = await fn()

                # 成功
                task.status = "success"
                task.result = result
                task.last_attempt_at = time.time()
                self.stats["successful"] += 1
                self.stats["total_retries"] += attempts
                self.stats["total_attempts"] += attempts + 1

                duration = time.time() - attempt_start
                attempt_record = RetryAttempt(
                    attempt=attempts,
                    started_at=attempt_start,
                    duration=duration,
                    error="",
                    traceback="",
                )
                task.attempts.append(attempt_record)

                # 保存历史
                self._save_history(task)

                if on_success:
                    try:
                        on_success(result)
                    except Exception:
                        pass

                return True, result

            except asyncio.TimeoutError as e:
                err = f"超时 ({timeout}s)"
                tb = ""
            except Exception as e:
                err = str(e)
                tb = traceback.format_exc()

            duration = time.time() - attempt_start
            attempt_record = RetryAttempt(
                attempt=attempts,
                started_at=attempt_start,
                duration=duration,
                error=err,
                traceback=tb,
            )
            task.attempts.append(attempt_record)
            task.last_attempt_at = time.time()

            # 判断是否继续重试
            if attempts < max_retries:
                delay = self.calc_delay(attempts, base_delay, max_delay)
                self.log.warning(
                    f"[Retry:{self.agent_id}] {label} "
                    f"第{attempts + 1}/{max_retries + 1}次尝试失败: {err} "
                    f"(当前延迟={delay:.1f}s)"
                )
                await asyncio.sleep(delay)
                attempts += 1
            else:
                # 所有重试耗尽
                task.status = "failed"
                task.error = err
                self.stats["failed"] += 1
                self.stats["total_retries"] += attempts
                self.stats["total_attempts"] += attempts + 1

                self.log.error(
                    f"[Retry:{self.agent_id}] {label} "
                    f"在{max_retries + 1}次尝试后最终失败: {err}"
                )

                # 保存历史
                self._save_history(task)

                if on_failure:
                    try:
                        on_failure(err)
                    except Exception:
                        pass

                return False, err

        return False, task.error

    # ── 快捷方法 ──────────────────────────

    async def retry_send(self, label: str, fn: Callable) -> Tuple[bool, Any]:
        """发送消息的重试（默认策略）"""
        return await self.retry(label, fn, strategy="send")

    async def retry_connect(self, label: str, fn: Callable) -> Tuple[bool, Any]:
        """连接的重试（更激进）"""
        return await self.retry(label, fn, strategy="connect")

    async def retry_request(self, label: str, fn: Callable) -> Tuple[bool, Any]:
        """请求/响应重试（更快失败）"""
        return await self.retry(label, fn, strategy="request")

    # ── 历史记录 ──────────────────────────

    def _save_history(self, task: RetryTask):
        """保存重试记录到历史文件"""
        try:
            record = {
                "agent_id": self.agent_id,
                "task_id": task.task_id,
                "label": task.label,
                "status": task.status,
                "max_retries": task.max_retries,
                "attempts_count": len(task.attempts),
                "error": task.error[:200] if task.error else "",
                "duration": task.last_attempt_at - task.created_at if task.last_attempt_at else 0,
                "ts": time.time(),
            }
            with open(self._history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def get_history(self, limit: int = 10) -> List[dict]:
        """获取最近的重试历史"""
        records = []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except FileNotFoundError:
            pass
        return records[-limit:]

    # ── 管理接口 ──────────────────────────

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self.stats,
            "active_tasks": len([t for t in self._tasks.values() if t.status == "running"]),
            "total_tasks": len(self._tasks),
        }

    def get_task(self, task_id: str) -> Optional[RetryTask]:
        """获取某个任务的详情"""
        return self._tasks.get(task_id)

    def get_failed_tasks(self) -> List[RetryTask]:
        """获取所有失败的任务"""
        return [t for t in self._tasks.values() if t.status == "failed"]

    def clear_tasks(self):
        """清空任务缓存"""
        self._tasks.clear()


# ── 自测 ──────────────────────────────────


async def _self_test():
    """基本功能自测"""
    print("=" * 50)
    print("AIM RetryManager 自测")
    print("=" * 50)

    rm = RetryManager(agent_id="TEST", history_path="/tmp/aim_retry_test.jsonl")
    passed = 0
    failed = 0

    def check(name, condition):
        nonlocal passed, failed
        if condition:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}")
            failed += 1

    # Test 1: 成功任务不重试
    async def ok_fn():
        return "ok"
    success, result = await rm.retry("test_ok", ok_fn, max_retries=2)
    check("成功任务返回正确", success and result == "ok")

    # Test 2: 失败后重试
    attempt_count = [0]

    async def fail_then_ok():
        attempt_count[0] += 1
        if attempt_count[0] < 2:
            raise ValueError("临时错误")
        return "recovered"

    success, result = await rm.retry("test_recover", fail_then_ok, max_retries=3, base_delay=0.01)
    check("失败后重试成功", success and result == "recovered")
    check("实际尝试了2次", attempt_count[0] == 2)

    # Test 3: 超过最大重试次数后彻底失败
    async def always_fail():
        raise RuntimeError("永久错误")

    success, result = await rm.retry("test_always_fail", always_fail, max_retries=2, base_delay=0.01)
    check("超过重试次数后失败", not success)

    # Test 4: 延迟计算递增
    delays = [rm.calc_delay(i, base_delay=1.0, max_delay=30.0, jitter=0) for i in range(4)]
    check("延迟递增", all(delays[i] <= delays[i+1] for i in range(len(delays)-1)))
    check("延迟不超过上限", all(d <= 30.0 for d in delays))

    # Test 5: 超时控制
    async def slow_fn():
        await asyncio.sleep(10)

    success, result = await rm.retry("test_timeout", slow_fn, max_retries=1, base_delay=0.01, timeout=0.1)
    check("超时后失败", not success)

    # Test 6: 统计
    stats = rm.get_stats()
    check("统计有成功记录", stats["successful"] >= 1)
    check("统计有失败记录", stats["failed"] >= 1)

    # Test 7: 历史记录
    history = rm.get_history()
    check("历史记录不为空", len(history) > 0)

    print(f"\n  结果: {passed}/{passed+failed} 通过")
    return passed, failed


if __name__ == "__main__":
    asyncio.run(_self_test())
