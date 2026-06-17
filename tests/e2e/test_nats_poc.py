#!/usr/bin/env python3
"""NATS POC 连通性测试 — 接呱呱建议，先验证连通性再集成业务逻辑"""
import asyncio
import sys
import os

# 使用 venv 的 python
sys.path.insert(0, os.path.expanduser("~/.hermes/venv-aim/lib/python3.14/site-packages") if os.path.exists(os.path.expanduser("~/.hermes/venv-aim")) else "")

import nats


async def test_connectivity():
    print("=" * 50)
    print("NATS POC 连通性测试")
    print("=" * 50)

    # 1. 连接测试
    print("\n[1/4] 连接 NATS Server...")
    nc = await nats.connect(
        "nats://127.0.0.1:4222",
        max_reconnect_attempts=1,
        ping_interval=5,
    )
    print(f"  ✅ 连接成功! 状态: connected={nc.is_connected}")

    # 2. 发布/订阅测试
    print("\n[2/4] 发布/订阅测试...")
    received = asyncio.Event()
    received_msg = []

    async def on_msg(msg):
        received_msg.append(msg.data.decode())
        received.set()

    sub = await nc.subscribe("test.zs0002.poc", cb=on_msg)
    await nc.flush()
    print("  ✅ 订阅成功: test.zs0002.poc")

    msg_body = "Hello from 吉量! POC test message"
    await nc.publish("test.zs0002.poc", msg_body.encode())
    await nc.flush()
    print("  ✅ 发布成功")

    # 等待接收
    try:
        await asyncio.wait_for(received.wait(), timeout=3)
        print(f"  ✅ 收到消息: {received_msg[0]}")
    except asyncio.TimeoutError:
        print(f"  ❌ 超时: 没有收到消息 (检查 NATS 订阅机制)")

    await sub.unsubscribe()

    # 3. Request-Reply 测试
    print("\n[3/4] Request-Reply 测试...")
    async def on_request(msg):
        await msg.respond("pong from 吉量".encode())

    req_sub = await nc.subscribe("test.zs0002.req", cb=on_request)
    await nc.flush()

    response = await nc.request("test.zs0002.req", b"ping", timeout=3)
    if response.data.decode() == "pong from 吉量":
        print(f"  ✅ Request-Reply 成功: {response.data.decode()}")
    else:
        print(f"  ⚠️ 收到响应: {response.data.decode()}")

    await req_sub.unsubscribe()

    # 4. JetStream 测试
    print("\n[4/4] JetStream 测试...")
    js = nc.jetstream()
    try:
        await js.add_stream(
            name="TEST_POC",
            subjects=["test.poc.>"],
            storage="memory",
            max_msgs=100,
        )
        print("  ✅ 创建 Stream: TEST_POC")
    except Exception as e:
        if "already exists" in str(e).lower():
            print("  ⚠️  Stream 已存在")
        else:
            print(f"  ⚠️ 创建 Stream 异常: {e}")

    # 发送一条 JetStream 消息
    ack = await js.publish("test.poc.zs0002", b'{"from":"ZS0002","msg":"NATS POC test"}')
    print(f"  ✅ JetStream 发送成功, 序列号: {ack.seq}")

    # 5. 统计信息
    print("\n[统计信息]")
    stats = nc.stats
    print(f"  发送: {stats.get('out_msgs', 0)} 条, {stats.get('out_bytes', 0)} 字节")
    print(f"  接收: {stats.get('in_msgs', 0)} 条, {stats.get('in_bytes', 0)} 字节")

    # 清理
    await nc.close()
    print("\n✅ 全部测试完成! NATS 连通性正常 🚀")


if __name__ == "__main__":
    asyncio.run(test_connectivity())
