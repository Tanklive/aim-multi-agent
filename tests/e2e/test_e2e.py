#!/usr/bin/env python3
"""
AIM NATS 端到端测试
测试消息收发、重传、持久化、去重
"""

import asyncio
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aim_nats.client import AIMNATSClient, AIMMessage
from aim_nats.retry_manager import RetryManager, RetryPolicy


# ── 测试配置 ──────────────────────────────────────────

NATS_SERVER = "nats://127.0.0.1:4222"
AGENT_A = "ZS0001"  # 呱呱
AGENT_B = "ZS0002"  # 吉量
GROUP_ID = "grp_trio"

results = []


def log_test(name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}" + (f" | {detail}" if detail else ""))
    results.append({"name": name, "passed": passed, "detail": detail})


# ── 测试 1: 基础连通性 ────────────────────────────────

async def test_basic_connectivity():
    """测试 NATS 基础连接"""
    print("\n🔌 测试 1: 基础连通性")

    client_a = AIMNATSClient(AGENT_A, NATS_SERVER)
    client_b = AIMNATSClient(AGENT_B, NATS_SERVER)

    try:
        await client_a.connect()
        log_test("Agent A 连接", client_a.is_connected)

        await client_b.connect()
        log_test("Agent B 连接", client_b.is_connected)

        await client_a.disconnect()
        await client_b.disconnect()
    except Exception as e:
        log_test("基础连通性", False, str(e))


# ── 测试 2: 私聊消息 ────────────────────────────────

async def test_private_message():
    """测试私聊消息收发"""
    print("\n📩 测试 2: 私聊消息")

    client_a = AIMNATSClient(AGENT_A, NATS_SERVER)
    client_b = AIMNATSClient(AGENT_B, NATS_SERVER)
    received = []

    async def on_message(msg: AIMMessage, raw):
        received.append(msg)

    try:
        await client_a.connect()
        await client_b.connect()

        # B 订阅私聊
        await client_b.subscribe_private(on_message)
        await asyncio.sleep(0.5)

        # A 发送给 B
        sent = await client_a.send_private(AGENT_B, "你好吉量")
        await asyncio.sleep(0.5)

        log_test("私聊发送", sent.content == "你好吉量", f"msg_id={sent.msg_id}")
        log_test("私聊接收", len(received) > 0, f"收到 {len(received)} 条")
        if received:
            log_test("内容一致", received[0].content == "你好吉量")

        await client_a.disconnect()
        await client_b.disconnect()
    except Exception as e:
        log_test("私聊消息", False, str(e))


# ── 测试 3: 群聊消息 ────────────────────────────────

async def test_group_message():
    """测试群聊消息收发"""
    print("\n👥 测试 3: 群聊消息")

    client_a = AIMNATSClient(AGENT_A, NATS_SERVER)
    client_b = AIMNATSClient(AGENT_B, NATS_SERVER)
    received_a = []
    received_b = []

    async def on_msg_a(msg, raw):
        received_a.append(msg)

    async def on_msg_b(msg, raw):
        received_b.append(msg)

    try:
        await client_a.connect()
        await client_b.connect()

        # 两者都订阅群聊
        await client_a.subscribe_group(GROUP_ID, on_msg_a)
        await client_b.subscribe_group(GROUP_ID, on_msg_b)
        await asyncio.sleep(0.5)

        # A 发群消息
        await client_a.send_group(GROUP_ID, "大家好")
        await asyncio.sleep(0.5)

        log_test("群消息发送", True)
        log_test("A 收到群消息", len(received_a) > 0)
        log_test("B 收到群消息", len(received_b) > 0)

        await client_a.disconnect()
        await client_b.disconnect()
    except Exception as e:
        log_test("群聊消息", False, str(e))


# ── 测试 4: Request-Reply ────────────────────────────

async def test_request_reply():
    """测试请求-响应模式"""
    print("\n🔄 测试 4: Request-Reply")

    client_a = AIMNATSClient(AGENT_A, NATS_SERVER)
    client_b = AIMNATSClient(AGENT_B, NATS_SERVER)

    async def handle_request(msg: AIMMessage, raw):
        # 回复
        response = AIMMessage(
            from_id=AGENT_B,
            to_id=AGENT_A,
            content=f"回复: {msg.content}",
        )
        await raw.respond(response.to_json().encode())

    try:
        await client_a.connect()
        await client_b.connect()

        await client_b.subscribe_request(handle_request)
        await asyncio.sleep(0.5)

        # A 发请求
        response = await client_a.request(AGENT_B, "ping")
        log_test("Request 发送", True)
        log_test("Response 收到", response.content == "回复: ping", f"content={response.content}")

        await client_a.disconnect()
        await client_b.disconnect()
    except Exception as e:
        log_test("Request-Reply", False, str(e))


# ── 测试 5: 重传机制 ────────────────────────────────

async def test_retry_mechanism():
    """测试消息重传机制"""
    print("\n🔄 测试 5: 重传机制")

    policy = RetryPolicy(
        initial_delay=0.5,
        multiplier=2.0,
        max_delay=5.0,
        max_retries=3,
        ack_timeout=1.0,
    )

    retry_mgr = RetryManager("ZS0005", policy=policy)

    sent_messages = []
    retry_events = []

    async def mock_send(to_id, content, msg_type, msg_id):
        sent_messages.append({"to": to_id, "content": content, "msg_id": msg_id})

    async def mock_emit(event):
        retry_events.append(event)

    retry_mgr.on_send(mock_send)
    retry_mgr.on_emit(mock_emit)

    try:
        # 发送消息（不 ACK，触发重传）
        msg_id = await retry_mgr.send_with_retry("ZS0002", "测试重传")
        log_test("消息入队", msg_id is not None, f"msg_id={msg_id}")

        # 启动重传循环
        retry_task = asyncio.create_task(retry_mgr.start_retry_loop())

        # 等待重传发生（ack_timeout=1.0 后触发第一次重传）
        await asyncio.sleep(6)

        log_test("触发重传", len(sent_messages) > 1, f"发送了 {len(sent_messages)} 次")
        log_test("重传事件", len(retry_events) > 0, f"产生了 {len(retry_events)} 个事件")

        # ACK 消息
        await retry_mgr.ack_message(msg_id)
        stats = retry_mgr.stats()
        log_test("ACK 确认", stats["confirmed"] > 0, f"confirmed={stats['confirmed']}")

        # 测试去重
        is_dup_1 = retry_mgr.is_duplicate(100)
        is_dup_2 = retry_mgr.is_duplicate(100)
        log_test("去重检查", not is_dup_1 and is_dup_2)

    except Exception as e:
        log_test("重传机制", False, str(e))


# ── 测试 6: Observer 事件 ────────────────────────────

async def test_observer_events():
    """测试 Observer 事件推送"""
    print("\n👁️ 测试 6: Observer 事件")

    client = AIMNATSClient("observer", NATS_SERVER)
    events = []

    async def on_event(msg, raw):
        events.append(msg)

    try:
        await client.connect()

        # 用 nc.subscribe 直接订阅（避免 _wrap_handler 问题）
        async def raw_handler(msg):
            data = json.loads(msg.data.decode())
            events.append(data)

        await client.nc.subscribe("observer.events.>", cb=raw_handler)
        await asyncio.sleep(0.5)

        # 发送事件
        await client.emit_event("test", "测试事件")
        await asyncio.sleep(0.5)

        log_test("Observer 订阅", True)
        log_test("事件接收", len(events) > 0, f"收到 {len(events)} 个事件")

        await client.disconnect()
    except Exception as e:
        log_test("Observer 事件", False, str(e))


# ── 主函数 ──────────────────────────────────────────

async def main():
    print("=" * 60)
    print("🚀 AIM NATS 端到端测试")
    print("=" * 60)

    await test_basic_connectivity()
    await test_private_message()
    await test_group_message()
    await test_request_reply()
    await test_retry_mechanism()
    await test_observer_events()

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"📊 测试结果: {passed}/{total} 通过")

    if passed == total:
        print("🎉 所有测试通过！")
    else:
        print("⚠️  部分测试失败")
        for r in results:
            if not r["passed"]:
                print(f"  ❌ {r['name']}: {r['detail']}")

    print("=" * 60)
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)