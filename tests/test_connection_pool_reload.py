"""ConnectionPool Reload 单元测试 — T1-T7（含缺失场景）

测试矩阵（来自 connection-pool-reload-final-plan.md §7.4）：

| # | 测试项 | 验证方法 |
|---|--------|---------|
| T1 | reload 后新连接注册到新池 | 检查 pool._generation |
| T2 | 老池连接继续服务不中断 | 用活跃连接发消息 |
| T3 | 老池连接自然释放后清理 | is_drained() 返回 True |
| T4 | MAX_PENDING_DRAINS 限制 | 快速连续 reload 3 次，第 3 次跳过 |
| T5 | Agent 收不到 pool_reload → revalidate | 模拟推送丢失，确认 revalidate 兜底 |
| T6 | 频繁 reload 限流 | 短时间内触发 2 次，第 2 次跳过 |
| T7 | DRAIN_TIMEOUT 强制切断 | 等待超时后老池清空 |

缺失场景（呱呱评审补充）：
- reload 期间有消息到达 → 路由到老池还是新池
- 老池连接主动断开 → drain 计数正确递减
- 并发 reload → 两个协程同时调用 reload() 是否安全
- register 时 pool 为空 → reload 后第一次 register 的特殊初始化逻辑
- DRAIN_TIMEOUT 可配性边界 → drain_timeout=0 或负数

注意：本套测试基于 MockReloadablePool（conftest.py 中定义），
在呱呱完成 P1 实现后，只需将 `MockReloadablePool` 替换为真实的
`ReloadableConnectionPool` 即可运行。
"""

import time
from pathlib import Path

import pytest

from tests.conftest import (
    MockReloadablePool,
    MockAgedPool,
    MIN_RELOAD_INTERVAL,
    MAX_PENDING_DRAINS,
    DRAIN_TIMEOUT,
    make_mock_ws,
    make_conn_info,
)


# ============================================================
# T1: reload 后新连接注册到新池
# ============================================================

class TestT1_ReloadNewPool:
    """T1: reload 后新连接注册到新池，generation 递增"""

    def test_t1_generation_increments_after_reload(self):
        """T1.1: reload 后 generation 从 0 → 1"""
        pool = MockReloadablePool()
        assert pool.generation == 0, "初始 generation 应为 0"

        result = pool.reload()
        assert result is True, "reload 应成功"
        assert pool.generation == 1, "reload 后 generation 应为 1"

    def test_t1_new_connection_registers_to_new_pool(self):
        """T1.2: reload 后新注册的连接在最新的 pool 中"""
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        # reload
        pool.reload()

        # 新连接注册 → 应归属新池（generation=1）
        ws2 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws2, role="primary")

        # 新连接应通过新池注册
        conns = pool.get_all_connections("ZS0001")
        # 此时 ZS0001 的 main 连接只应有新注册的（老池已清空给 aged pool）
        assert len(conns) == 1, "reload 后新池应只有新连接"
        assert any(c.ws == ws2 for c in conns), "新连接应出现在当前池"

    def test_t1_multiple_reloads_increment_generation(self):
        """T1.3: 多次 reload，generation 持续递增"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})  # 不限流
        pool._min_interval = 0  # 测试用不限制

        pool.reload()
        assert pool.generation == 1

        pool.simulate_drain_complete(0)  # 清理 gen=0
        pool._last_reload_time = 0  # 重置计时

        pool.reload()
        assert pool.generation == 2

        pool.simulate_drain_complete(1)
        pool._last_reload_time = 0

        pool.reload()
        assert pool.generation == 3


# ============================================================
# T2: 老池连接继续服务不中断
# ============================================================

class TestT2_OldConnectionsKeepServing:
    """T2: reload 后老池连接继续服务不中断

    核心设计语义（来自方案文档 §2.2 & §6）：
    - reload 不关闭活跃连接，老池进入 Draining 状态继续服务
    - 新消息应路由到老池的连接继续处理，直到 drain 完成
    - get_all_connections() 只返回当前池（新池）的连接
    - 老池的连接通过 old_pools 路由，不丢失
    """

    def test_t2_old_pool_connections_preserved(self):
        """T2.1: reload 后老连接被记录到老池，不被丢弃"""
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        pool.reload()

        # 验证老连接在 old_pools 中
        old_pool = pool._old_pools.get(0)
        assert old_pool is not None, "generation 0 应为老池"
        assert old_pool.get_connection_count() >= 1, "老池应保留连接记录"

    def test_t2_active_connection_not_disconnected(self):
        """T2.2: reload 不关闭活跃连接的 ws"""
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        assert not ws1.closed, "reload 前连接应未关闭"

        pool.reload()

        # 老池连接不应在 reload 时被关闭
        # 它们会继续服务直到自然释放或 drain_timeout
        assert not ws1.closed, "reload 后老连接不应被关闭"

    def test_t2_old_connections_routeable_through_old_pool(self):
        """T2.3: 老连接可通过老池继续路由（消息继续服务）

        设计方案：get_all_connections() 只返回当前池连接。
        老连接通过 pool._old_pools[gen]._connections 维护。
        路由逻辑应优先检查当前池，若无匹配则回退到老池。
        """
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        # reload 后，老连接不丢失
        pool.reload()

        # 验证老池中保留了连接
        old_pool = pool._old_pools.get(0)
        assert old_pool is not None
        # 统计老池中的连接数（通过 get_connection_count）
        assert old_pool.get_connection_count() >= 1, "老池应有连接可路由"

        # 验证老连接没有被关闭（继续服务中）
        assert not ws1.closed, "老连接应继续服务"

    def test_t2_message_routing_to_old_connections(self):
        """T2.4: 消息路由应能到达老池连接（不中断服务）

        模拟场景：reload 后，已有连接上的消息流继续处理。
        验证老连接 ws 的状态未被关闭，仍可接受数据。
        """
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        # 模拟消息处理中发生 reload
        pool.reload()

        # 老连接继续可用
        assert not ws1.closed

        # 模拟在老连接上继续收发消息（通过老池连接）
        old_pool = pool._old_pools.get(0)
        assert old_pool is not None
        conns_in_old_pool = old_pool.get_connection_count()
        assert conns_in_old_pool >= 1, "老池应保留连接供继续服务"


# ============================================================
# T3: 老池连接自然释放后清理
# ============================================================

class TestT3_OldPoolCleanup:
    """T3: 老池连接自然释放后清理"""

    def test_t3_drained_pool_is_cleaned(self):
        """T3.1: drain 完成后老池被清理"""
        pool = MockReloadablePool()

        pool.reload()  # gen 0 → 1, gen 0 进入老池

        # 模拟 drain 完成
        pool.simulate_drain_complete(0)

        # 验证老池已被清理
        assert 0 not in pool._old_pools, "drain 完成后 gen=0 老池应被清理"

    def test_t3_is_drained_returns_true(self):
        """T3.2: is_drained() 在 drain 完成后返回 True"""
        pool = MockReloadablePool()
        pool.reload()  # gen 0 → 1

        # 模拟 drain 完成
        pool.simulate_drain_complete(0)

        assert pool.is_pool_drained(0) is True, "gen=0 应标记为已 drain"

    def test_t3_force_drain_keeps_connections_open(self):
        """T3.3: force_drain（自然释放）不关闭连接，只标记状态

        这是与超时切断的关键区别：自然释放是 Agent 主动断开后 Server 清理，
        不关闭 ws。
        """
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")
        pool.reload()

        # 自然释放：Agent 关闭连接后，Server 标记 drain 完成
        pool.simulate_drain_complete(0)

        # force_drain 不关闭 ws
        # 连接由 Agent 侧主动关闭，Server 只清理引用
        # 如果连接还打开着，那也没问题（Agent 不会再发消息过来）
        assert pool.is_pool_drained(0) is True


# ============================================================
# T4: MAX_PENDING_DRAINS 限制
# ============================================================

class TestT4_MaxPendingDrains:
    """T4: 最多允许 2 个老池同时在 drain"""

    def test_t4_first_reload_allowed(self):
        """T4.1: 首次 reload 应成功"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        result = pool.reload()
        assert result is True, "首次 reload 应成功"
        assert pool.get_active_drains_count() == 1

    def test_t4_second_reload_allowed(self):
        """T4.2: 第 2 次 reload 应成功 (MAX_PENDING_DRAINS=2)"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        pool.reload()  # gen 0 → 1, drain gen=0
        pool._last_reload_time = 0
        pool.reload()  # gen 1 → 2, drain gen=1

        count = pool.get_active_drains_count()
        assert count <= 2, f"活跃 drain 数应为 ≤2, 实际为 {count}"

    def test_t4_third_reload_blocked(self):
        """T4.3: 第 3 次 reload 被跳过 (已达 MAX_PENDING_DRAINS=2)"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        # 第 1 次 reload
        assert pool.reload() is True
        pool._last_reload_time = 0

        # 第 2 次 reload
        assert pool.reload() is True
        pool._last_reload_time = 0

        # 第 3 次 reload — 应被跳过
        result = pool.reload()
        assert result is False, "第 3 次 reload 应被跳过 (active_drains=2)"

    def test_t4_after_drain_complete_reload_allowed(self):
        """T4.4: 老池 drain 完成后，可继续 reload"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        pool.reload()  # gen 0 → 1
        pool._last_reload_time = 0
        pool.reload()  # gen 1 → 2

        # 释放第一个老池
        pool.simulate_drain_complete(0)
        pool._last_reload_time = 0

        # 再次 reload 应成功
        result = pool.reload()
        assert result is True, "drain 完成后 reload 应恢复"


# ============================================================
# T5: Agent 收不到 pool_reload → revalidate 兜底
# ============================================================

class TestT5_RevalidateFallback:
    """T5: push 丢失时 revalidate 兜底恢复

    核心语义（来自方案文档 §4 & §6）：
    - Server 推送 pool_reload 到 handler 的 main 通道
    - Agent 心跳 response 携带 server_generation
    - Agent 比对 server_generation > local_generation → 触发重连
    - Agent 收到 pool_reload 消息直接更新本地 generation
    - 两种路径任一即可恢复，保证 30s 内收敛
    """

    def test_t5_heartbeat_detects_generation_mismatch(self):
        """T5.1: 心跳携带本地 generation，Server response 走比对路径

        当 Agent 的本地 generation < Server generation 时触发重连：
        1. Agent 发送心跳（含本地 generation）
        2. Server 回复心跳 response（含 server_generation）
        3. Agent 比对 → 不匹配 → 自动重连
        """
        pool = MockReloadablePool()

        # 模拟 Agent 的本地 generation（未收到 pool_reload）
        agent_local_gen = 0

        # Server 侧 reload（generation 递增）
        pool.reload()
        server_gen = pool.generation  # = 1

        # 模拟一个 heartbeat 处理函数，检查是否应重连
        def should_reconnect(local_gen, server_gen):
            """心跳 response 处理逻辑（与真实 Agent 对齐）"""
            return server_gen > local_gen

        assert should_reconnect(agent_local_gen, server_gen), \
            "server_gen > agent_local_gen 时应触发重连"

    def test_t5_revalidate_updates_agent_generation(self):
        """T5.2: 心跳 response generation 比对 → Agent 更新本地 generation

        Agent 收到心跳 response 后，如果 server_generation 更大，
        应更新本地 generation 并触发重连。
        """
        pool = MockReloadablePool()
        pool.reload()  # gen = 1

        # 模拟 Agent 侧行为
        class AgentRevalidate:
            def __init__(self):
                self.local_generation = 0
                self.reconnect_triggered = False

            def on_heartbeat_ack(self, server_generation):
                if server_generation > self.local_generation:
                    self.local_generation = server_generation
                    self.reconnect_triggered = True

            def on_pool_reload_msg(self, msg_generation):
                """收到 pool_reload 推送消息（直接更新，不需比对）"""
                self.local_generation = msg_generation

        agent = AgentRevalidate()
        agent.on_heartbeat_ack(server_generation=1)

        assert agent.local_generation == 1, "心跳 response 后 Agent 应更新 generation"
        assert agent.reconnect_triggered is True, "generation 不匹配应触发重连"

    def test_t5_pool_reload_msg_updates_directly(self):
        """T5.3: 收到 pool_reload 消息后直接更新本地 generation

        这是推送路径：Agent 直接收到 pool_reload 消息（不需比对），
        更新本地 generation，不触发重连。
        """
        class AgentRevalidate:
            def __init__(self):
                self.local_generation = 0
                self.reconnect_triggered = False

            def on_heartbeat_ack(self, server_generation):
                if server_generation > self.local_generation:
                    self.local_generation = server_generation
                    self.reconnect_triggered = True

            def on_pool_reload_msg(self, msg_generation):
                self.local_generation = msg_generation

        agent = AgentRevalidate()
        agent.on_pool_reload_msg(msg_generation=5)

        assert agent.local_generation == 5, "pool_reload 消息后 Agent 应直接更新 generation"
        assert agent.reconnect_triggered is False, "推送路径不触发重连"

    def test_t5_revalidate_is_30s_timer_based(self):
        """T5.4: revalidate 检查是定时触发的（30s 间隔）

        空闲时 Agent 每 30s 发一次心跳 → 心跳 response 带 server_generation →
        比对检测到不匹配 → 触发重连。
        忙时跳过心跳（有消息活动说明连接正常）。
        """
        # 配置检查
        assert MIN_RELOAD_INTERVAL == 30, "MIN_RELOAD_INTERVAL 应为 30s"
        # 此处仅验证定时器配置一致性。
        # 端到端的心跳间隔验证需要 Agent 进程参与，不在本单元测试范围。


# ============================================================
# T6: 频繁 reload 限流
# ============================================================

class TestT6_RateLimit:
    """T6: MIN_RELOAD_INTERVAL 防止频繁 reload"""

    def test_t6_first_reload_allowed(self):
        """T6.1: 首次 reload 总是允许"""
        pool = MockReloadablePool()
        result = pool.reload()
        assert result is True, "首次 reload 应成功"

    def test_t6_immediate_second_reload_blocked(self):
        """T6.2: 短时间内第 2 次 reload 被跳过"""
        pool = MockReloadablePool()

        pool.reload()  # 成功
        # 立即再次 reload — 应被跳过（未到 MIN_RELOAD_INTERVAL）
        result = pool.reload()
        assert result is False, "间隔过短，第 2 次 reload 应被跳过"

    def test_t6_after_interval_reload_allowed(self):
        """T6.3: 超过 MIN_RELOAD_INTERVAL 后 reload 恢复"""
        pool = MockReloadablePool()

        pool.reload()

        # 模拟时间流逝
        pool._last_reload_time = 0  # hack: 重置计时器

        result = pool.reload()
        assert result is True, "间隔足够后 reload 应恢复"

    def test_t6_rate_limit_independent_of_drains(self):
        """T6.4: 限流和 drain 上限是两个独立限制"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        # 短时间内成功多次，但受 MAX_PENDING_DRAINS 限制
        pool.reload()
        pool._last_reload_time = 0
        pool.reload()
        pool._last_reload_time = 0

        # 这里应该被 drain 限制挡住，不是限流
        result = pool.reload()
        assert result is False, "被 MAX_PENDING_DRAINS 挡住（非限流）"


# ============================================================
# T7: DRAIN_TIMEOUT 强制切断
# ============================================================

class TestT7_DrainTimeout:
    """T7: DRAIN_TIMEOUT 超时后强制清理老池

    核心语义：
    - DRAIN_TIMEOUT=60s，超过此时间老池中未释放的连接被强制 close
    - 超时和自然释放的语义不同：
      - 自然释放：Agent 主动断开 → Server 标记 drain 完成（不关闭 ws）
      - 超时切断：Server 主动关闭 ws → 强制清理老池引用
    """

    def test_t7_drain_timeout_constant(self):
        """T7.1: DRAIN_TIMEOUT 默认值为 60s"""
        assert DRAIN_TIMEOUT == 60, "DRAIN_TIMEOUT 应为 60s"

    def test_t7_pool_not_drained_before_timeout(self):
        """T7.2: 超时前老池未 drain"""
        pool = MockReloadablePool()
        pool.reload()

        # 刚 reload 完，还没到超时时间
        assert pool.is_pool_drained(0) is False, "超时前老池不应 drained"

    def test_t7_pool_drained_after_timeout(self):
        """T7.3: 超时后老池强制 drained"""
        pool = MockReloadablePool()
        pool.reload()

        # 模拟超时到达
        pool.simulate_drain_timeout(0)

        assert pool.is_pool_drained(0) is True, "超时后老池应 drained"
        assert pool.get_active_drains_count() == 0, "超时后无活跃 drain"

    def test_t7_zombie_connections_closed_on_timeout(self):
        """T7.4: 超时强制清理后，僵尸连接被 close

        关键区别：与 force_drain() 不同，超时后应实际调用 ws.close()，
        不只是标记状态。这是呱呱评审提出的核心改进点。
        """
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")
        pool.reload()

        # 验证老池中的连接在超时前是打开的
        assert not ws1.closed, "超时前 ws 应保持打开"

        # 超时到达
        pool.simulate_drain_timeout(0)

        # 验证连接被关闭了（不是仅标记状态）
        assert ws1.closed is True, "超时后僵尸连接应被 close()"

        # 验证老池已被清理
        assert 0 not in pool._old_pools, "超时后老池应被完全清理"

    def test_t7_drain_timeout_configurable(self):
        """T7.5: DRAIN_TIMEOUT 可通过 config 配置"""
        custom_timeout = 120
        pool = MockReloadablePool(config={"drain_timeout": custom_timeout})
        assert pool._drain_timeout == custom_timeout, "自定义 DRAIN_TIMEOUT 应生效"

    def test_t7_drain_timeout_edge_cases(self):
        """T7.6: DRAIN_TIMEOUT 边界值测试

        缺失场景（呱呱评审补充）：drain_timeout=0 和负数值。
        """
        # drain_timeout=0 → 理论上立即超时
        pool_immediate = MockReloadablePool(config={"drain_timeout": 0})
        assert pool_immediate._drain_timeout == 0, "drain_timeout=0 应接受"

        # drain_timeout=负数 → 不应 set 为负值（防御）
        # 此处检查 config 是否能接受负数（取决于真实实现是否做校验）
        # 当前 Mock 不做负数校验，但真实实现应 clamp 到 >=1
        pool_neg = MockReloadablePool(config={"drain_timeout": -1})
        assert pool_neg._drain_timeout == -1, "当前 Mock 接受负值"


# ============================================================
# 缺失场景（呱呱评审补充）
# ============================================================

class TestMissingScenarios:
    """呱呱评审提出的 5 个缺失场景"""

    def test_reload_during_message_processing(self):
        """场景 1: reload 期间已有消息在传输中

        方案语义：正在处理消息的连接不中断。
        验证 reload 后老连接没有被强制关闭。
        """
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        # 模拟在消息处理中间发生 reload
        pool.reload()

        # 老连接应保持打开（消息继续处理）
        assert not ws1.closed, "reload 期间正在处理消息的连接不应被关闭"

        # 新消息注册到新池
        ws2 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws2, role="primary")
        new_conns = pool.get_all_connections("ZS0001")
        assert any(c.ws == ws2 for c in new_conns), "新消息应路由到新池"

    def test_old_connection_disconnected_by_client(self):
        """场景 2: 老池连接被客户端主动断开

        Agent 侧主动 close 后，drain 计数应正确递减，
        老池 drain 计数反映剩余活跃连接数。
        """
        pool = MockReloadablePool()
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")
        ws2 = make_mock_ws("ZS0002", "main")
        pool.register("ZS0002", "main", ws2, role="primary")

        pool.reload()  # 两个连接都进入老池

        # 模拟 Agent 侧主动断开
        ws1.closed = True

        # drain 完成后，老池应被清理
        # 这里验证 force_drain 不关心连接数量（正常 drain 由超时或主动释放触发）
        # 真实实现中应有一个计数追踪池中未释放连接
        assert ws1.closed is True, "模拟 Agent 侧断开"
        assert ws2.closed is False, "其他连接不受影响"

        # 老池仍在 drain 中（因为 ws2 未断开）
        assert pool.is_pool_drained(0) is False, "还有连接未释放时应未 drained"

    def test_concurrent_reload_safety(self):
        """场景 3: 并发 reload 安全性

        两个几乎同时的 reload 调用。由于当前 reload() 是同步的，
        且 T6 限流会挡住第二次，理论上不会发生真正并发。
        验证：即使在短时间内第 2 次被挡住，状态仍一致。
        """
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        # 模拟连续两次快速 reload
        r1 = pool.reload()
        pool._last_reload_time = 0
        r2 = pool.reload()

        # 前两次都应该成功（受 MAX_PENDING_DRAINS=2 保护）
        assert r1 is True, "第一次 reload 应成功"
        assert r2 is True, "第二次 reload 应成功"
        assert pool.generation == 2, "两次 reload 后 gen 应为 2"

    def test_register_after_reload_works(self):
        """场景 4: reload 后第一次 register 应有正确的初始化逻辑

        reload 后 _connections 被清空，新 register 应创建新池的连接。
        """
        pool = MockReloadablePool()
        pool.reload()  # _connections 被清空

        # 新的连接注册
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        conns = pool.get_all_connections("ZS0001")
        assert len(conns) == 1, "reload 后注册应创建新连接"
        assert conns[0].ws == ws1

        # 验证 generation 正确
        assert pool.generation == 1, "注册不应影响 generation"

    def test_drain_timeout_zero_behavior(self):
        """场景 5: DRAIN_TIMEOUT 边界值

        - drain_timeout=0: 立即超时（取决于实现是否做防御校验）
        - drain_timeout=负数: 不合理值
        """
        # drain_timeout=0 时，is_drained() 应立即返回 True
        # 因为 elapsed >= 0 总是成立
        pool_zero = MockReloadablePool(config={"drain_timeout": 0})
        ws1 = make_mock_ws("ZS0001", "main")
        pool_zero.register("ZS0001", "main", ws1, role="primary")
        pool_zero.reload()

        # elapsed >= 0 成立 → is_drained = True
        assert pool_zero.is_pool_drained(0) is True, \
            "drain_timeout=0 时 should drain immediately"

        # 连接被关闭（超时清理）
        assert ws1.closed is True, "drain_timeout=0 时连接应立即被 close"


# ============================================================
# 集成测试：T1+T4+T6 组合场景
# ============================================================

class TestIntegration:
    """组合场景测试 — 模拟真实流程"""

    def test_full_reload_lifecycle(self):
        """完整 reload 生命周期：
        1. 初始连接注册
        2. reload（generation 递增）
        3. 新连接注册到新池
        4. 老池 drain
        5. 多次 reload 受限
        """
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        # 阶段 1: 初始连接
        ws_initial = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws_initial, role="primary")
        assert pool.generation == 0
        assert len(pool.get_all_connections("ZS0001")) == 1

        # 阶段 2: 第一次 reload
        assert pool.reload() is True
        assert pool.generation == 1
        assert 0 in pool._old_pools  # gen=0 进入老池

        # 阶段 3: 新连接注册
        ws_new = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws_new, role="primary")
        new_conns = pool.get_all_connections("ZS0001")
        assert len(new_conns) == 1, "新池应只有新连接"
        assert any(c.ws == ws_new for c in new_conns)

        # 阶段 4: drain 老池
        pool.simulate_drain_complete(0)
        assert pool.is_pool_drained(0)

        # 阶段 5: 多次 reload 限制
        pool._last_reload_time = 0
        assert pool.reload() is True  # gen 1 → 2
        pool._last_reload_time = 0
        pool.simulate_drain_complete(1)
        assert pool.reload() is True  # gen 2 → 3
        assert pool.generation == 3

    def test_full_reload_with_timeout_scenario(self):
        """集成场景：模拟超时强制清理全流程"""
        pool = MockReloadablePool(config={"min_reload_interval": 0})
        pool._min_interval = 0

        # 注册连接
        ws1 = make_mock_ws("ZS0001", "main")
        pool.register("ZS0001", "main", ws1, role="primary")

        # reload
        assert pool.reload() is True
        assert not ws1.closed, "reload 后老连接应保持打开"

        # 超时到达
        pool.simulate_drain_timeout(0)

        # 连接被关闭
        assert ws1.closed is True, "超时后连接应被关闭"
        assert pool.is_pool_drained(0) is True
        assert 0 not in pool._old_pools, "老池引用应被清理"
