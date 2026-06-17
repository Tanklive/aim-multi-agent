"""Scheduler 三级降级模型集成 UT

覆盖：
  T1: L0 → L1（连续3次 exit=1）
  T2: L1 → L0 自动恢复（health ok + retry_count 清零）
  T3: L0 → L2（health exit=2）
  T4: L2 → L0 自动恢复（health 恢复）
  T5: 探针间隔 L0/L1/L2
  T6: status_summary 含 degrade_level
  T7: on_retry 单次不触发 L1（<3次）
  T8: dispatch 成功清零 retry_count
  T9: L1 保持（health ok 但 retry_count > 0）
"""
import pytest
from aim_client.types import (
    AgentState, DegradeLevel, StateReport,
    evaluate_degrade_level, get_probe_interval,
)
from aim_client.scheduler import Scheduler


def _mk_report(status: AgentState) -> StateReport:
    return StateReport(status=status)


class TestDegradeIntegration:

    # ── T1: L0 → L1（连续3次超时）──

    def test_l0_to_l1_on_3_retries(self):
        s = Scheduler()
        assert s.degrade_level == DegradeLevel.L0
        assert s._retry_count == 0

        for i in range(3):
            s.on_retry()

        assert s.degrade_level == DegradeLevel.L1
        assert s._retry_count == 3

    # ── T2: L1 → L0 自动恢复 ──

    def test_l1_to_l0_recovery(self):
        s = Scheduler()

        # 先触发 L1
        for _ in range(3):
            s.on_retry()
        assert s.degrade_level == DegradeLevel.L1

        # dispatch 成功 → retry 清零
        s.on_dispatch_started()
        assert s._retry_count == 0

        # health 报告 IDLE → 触发恢复评估
        s.update_state(_mk_report(AgentState.IDLE))
        assert s.degrade_level == DegradeLevel.L0

    # ── T3: L0 → L2（health exit=2）──

    def test_l0_to_l2_on_health_exit2(self):
        s = Scheduler()
        s.update_state(_mk_report(AgentState.OFFLINE))
        assert s.degrade_level == DegradeLevel.L2

    # ── T4: L2 → L0 自动恢复 ──

    def test_l2_to_l0_recovery(self):
        s = Scheduler()
        s.update_state(_mk_report(AgentState.OFFLINE))
        assert s.degrade_level == DegradeLevel.L2

        # health 恢复
        s.update_state(_mk_report(AgentState.IDLE))
        assert s.degrade_level == DegradeLevel.L0

    # ── T5: 探针间隔 L0/L1/L2 ──

    def test_probe_interval_l0(self):
        s = Scheduler()
        assert s.get_probe_interval() == 5.0

    def test_probe_interval_l1(self):
        s = Scheduler()
        for _ in range(3):
            s.on_retry()
        assert s.degrade_level == DegradeLevel.L1
        # retry_count=3 → _get_probe_interval(L1, 3) → idx min(3,2)=2 → intervals[2] = 15
        assert s.get_probe_interval() == 15.0

    def test_probe_interval_l2(self):
        s = Scheduler()
        s.update_state(_mk_report(AgentState.OFFLINE))
        assert s.degrade_level == DegradeLevel.L2
        # offline_count = 0 → idx 0 → 5
        assert s.get_probe_interval() == 5.0

        s.update_state(_mk_report(AgentState.OFFLINE))
        # offline_count = 1 → idx 1 → 10
        assert s.get_probe_interval() == 10.0

    # ── T6: status_summary 含 degrade_level ──

    def test_status_summary_includes_degrade_level(self):
        s = Scheduler()
        summary = s.status_summary()
        assert "degrade_level" in summary
        assert summary["degrade_level"] == "normal"
        assert "retry_count" in summary
        assert summary["retry_count"] == 0

    # ── T7: 单次 retry 不触发 L1 ──

    def test_single_retry_does_not_trigger_l1(self):
        s = Scheduler()
        s.on_retry()
        assert s.degrade_level == DegradeLevel.L0
        s.on_retry()
        assert s.degrade_level == DegradeLevel.L0
        # 第三次才触发
        s.on_retry()
        assert s.degrade_level == DegradeLevel.L1

    # ── T8: dispatch 成功清零 retry_count ──

    def test_dispatch_resets_retry_count(self):
        s = Scheduler()
        for _ in range(2):
            s.on_retry()
        assert s._retry_count == 2

        s.on_dispatch_started()
        assert s._retry_count == 0

    # ── T9: L1 保持（health ok 但 retry 还在）──

    def test_l1_persists_with_pending_retries(self):
        s = Scheduler()
        for _ in range(3):
            s.on_retry()
        assert s.degrade_level == DegradeLevel.L1

        # health ok 但 retry_count 还在 → 保持 L1（evaluate 判定）
        s.update_state(_mk_report(AgentState.IDLE))
        assert s.degrade_level == DegradeLevel.L1

        # dispatch 成功清零 + health ok → L0
        s.on_dispatch_started()
        s.update_state(_mk_report(AgentState.IDLE))
        assert s.degrade_level == DegradeLevel.L0

    # ── T10: on_degrade_change 回调触发 ──

    def test_degrade_change_callback(self):
        import asyncio
        callbacks = []

        async def on_change(old, new, reason):
            callbacks.append((old, new, reason))

        async def run():
            s = Scheduler()
            s._on_degrade_change = on_change

            for _ in range(3):
                s.on_retry()

            # 回调是 ensure_future 异步的，给一帧时间
            await asyncio.sleep(0)

            assert len(callbacks) == 1
            assert callbacks[0][0] == DegradeLevel.L0
            assert callbacks[0][1] == DegradeLevel.L1

        asyncio.run(run())
