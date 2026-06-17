# AIM V2 Server - Unit Tests
# Author: 呱呱 (ZS0001)
# Date: 2026-06-05

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from aim_server_v2 import (
    AIMServer,
    Connection,
    ConnectionManager,
    ConnType,
    MsgState,
    PendingMessage,
    MAX_PLATFORM_CONNECTIONS,
    SYSTEM_CHANNELS,
)


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    """ConnectionManager 核心逻辑测试"""

    async def asyncSetUp(self):
        self.cm = ConnectionManager()

    # ---------- 平台级连接计数 ----------

    async def test_system_channel_not_counted(self):
        """系统级 channel 不计入连接上限"""
        ws = MagicMock()

        for i in range(MAX_PLATFORM_CONNECTIONS):
            conn = await self.cm.register(ws, f"web_{i}", "agent1", time.time())
            self.assertIsNotNone(conn)

        conn = await self.cm.register(ws, "main", "agent1", time.time())
        self.assertIsNotNone(conn)
        self.assertEqual(conn.conn_type, ConnType.SYSTEM)

    async def test_platform_limit_enforced(self):
        """平台级连接达到上限后拒绝"""
        ws = MagicMock()
        for i in range(MAX_PLATFORM_CONNECTIONS):
            await self.cm.register(ws, f"web_{i}", "agent1", time.time())

        conn = await self.cm.register(ws, "web_extra", "agent1", time.time())
        self.assertIsNone(conn)

    async def test_different_agents_independent(self):
        """不同 agent 的连接计数独立"""
        ws = MagicMock()
        for i in range(MAX_PLATFORM_CONNECTIONS):
            await self.cm.register(ws, f"web_{i}", "agent1", time.time())

        conn = await self.cm.register(ws, "web_0", "agent2", time.time())
        self.assertIsNotNone(conn)

    # ---------- Tiebreaker ----------

    async def test_tiebreak_main_channel_first(self):
        """main channel 优先"""
        ws = MagicMock()
        t = time.time()

        await self.cm.register(ws, "web", "agent1", t)
        await self.cm.register(ws, "main", "agent1", t + 1)

        selected = self.cm.select_target("agent1")
        self.assertEqual(selected.channel, "main")

    async def test_tiebreak_term_higher_wins(self):
        """term 大的优先"""
        ws = MagicMock()
        t = time.time()

        await self.cm.register(ws, "web", "agent1", t)
        await self.cm.register(ws, "web", "agent1", t)

        selected = self.cm.select_target("agent1", channel="web")
        self.assertEqual(selected.term, 1)

    # ---------- Unregister ----------

    async def test_unregister_cleans_up(self):
        """注销后连接被清理"""
        ws = MagicMock()
        conn = await self.cm.register(ws, "main", "agent1", time.time())
        self.assertIsNotNone(conn)

        await self.cm.unregister(conn.conn_id, "agent1")
        stats = self.cm.get_connection_count("agent1")
        self.assertEqual(stats["total"], 0)

    # ---------- Connection Stats ----------

    async def test_get_connection_count(self):
        """连接统计正确"""
        ws = MagicMock()
        await self.cm.register(ws, "main", "agent1", time.time())
        await self.cm.register(ws, "web", "agent1", time.time())

        stats = self.cm.get_connection_count("agent1")
        self.assertEqual(stats["system"], 1)
        self.assertEqual(stats["platform"], 1)
        self.assertEqual(stats["total"], 2)


class TestMessageAckStateMachine(unittest.TestCase):
    """消息确认状态机测试"""

    def test_pending_message_states(self):
        """PendingMessage 状态机基本测试"""
        pending = PendingMessage(
            msg_id="test_001",
            payload={"text": "hello"},
            target_agent="agent2",
            target_channel="main",
        )
        self.assertEqual(pending.state, MsgState.SENT)
        self.assertEqual(pending.retry_count, 0)

    def test_msg_state_enum(self):
        """MsgState 枚举值正确"""
        self.assertEqual(MsgState.SENT.value, "sent")
        self.assertEqual(MsgState.RECEIVED.value, "received")
        self.assertEqual(MsgState.ACKED.value, "acked")
        self.assertEqual(MsgState.NACKED.value, "nacked")
        self.assertEqual(MsgState.TIMEOUT.value, "timeout")
        self.assertEqual(MsgState.FAILED.value, "failed")


class TestGracefulShutdown(unittest.IsolatedAsyncioTestCase):
    """Graceful Shutdown 测试"""

    async def test_disconnect_method_exists(self):
        """disconnect 方法存在且可调用"""
        cm = ConnectionManager()
        self.assertTrue(hasattr(cm, 'disconnect'))
        self.assertTrue(hasattr(cm, 'shutdown_all'))


class TestServerIntegration(unittest.TestCase):
    """服务器集成测试（结构验证）"""

    def test_server_has_handler(self):
        server = AIMServer()
        self.assertTrue(hasattr(server, 'handler'))
        self.assertTrue(callable(server.handler))

    def test_server_has_run(self):
        server = AIMServer()
        self.assertTrue(hasattr(server, 'run'))

    def test_server_default_config(self):
        server = AIMServer()
        self.assertEqual(server.host, "127.0.0.1")
        self.assertEqual(server.port, 18900)


if __name__ == "__main__":
    unittest.main()
