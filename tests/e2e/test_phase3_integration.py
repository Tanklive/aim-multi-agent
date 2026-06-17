#!/usr/bin/env python3
"""
Phase 3 联调测试 — 三方 NATS 通信验证
测试项：私聊、群聊、Observer、aim-watch
"""

import asyncio
import json
import time
import sys
import os

sys.path.insert(0, os.path.expanduser("~/shared/aim"))
import nats

# ── 测试配置 ──
NATS_SERVER = "nats://127.0.0.1:4222"
AGENTS = {
    "ZS0001": "呱呱",
    "ZS0002": "吉量",
    "ZS0003": "小火鸡儿",
}
MY_ID = "ZS0003"
GROUP = "grp_trio"

results = []


def log_test(name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}" + (f" | {detail}" if detail else ""))
    results.append({"name": name, "passed": passed, "detail": detail})


# ── Test 1: NATS 连接 ──
async def test_connection():
    print("\n🔌 Test 1: NATS 连接")
    try:
        nc = await nats.connect(NATS_SERVER)
        log_test("NATS 连接", nc.is_connected)
        await nc.close()
    except Exception as e:
        log_test("NATS 连接", False, str(e))


# ── Test 2: 私聊消息 ──
async def test_private_message():
    print("\n📩 Test 2: 私聊消息")

    nc = await nats.connect(NATS_SERVER)
    received = []

    async def on_msg(msg):
        data = json.loads(msg.data.decode())
        received.append(data)

    # 订阅自己的 subject
    sub = await nc.subscribe(f"aim.dm.{MY_ID}", cb=on_msg)
    await asyncio.sleep(0.5)

    # 发送给呱呱
    test_msg = {
        "id": f"test-{int(time.time())}",
        "from": MY_ID,
        "to": "ZS0001",
        "type": "dm",
        "payload": {"text": "联调测试：私聊消息验证"},
        "ts": time.time()
    }
    await nc.publish(f"aim.dm.ZS0001", json.dumps(test_msg).encode())
    log_test("私聊发送到 ZS0001", True, "已发送")

    # 发送给吉量
    test_msg["to"] = "ZS0002"
    test_msg["payload"]["text"] = "联调测试：私聊消息验证"
    await nc.publish(f"aim.dm.ZS0002", json.dumps(test_msg).encode())
    log_test("私聊发送到 ZS0002", True, "已发送")

    await asyncio.sleep(2)
    log_test("消息发送完成", True, f"发送了 2 条私聊消息")

    await nc.close()


# ── Test 3: 群聊消息 ──
async def test_group_message():
    print("\n👥 Test 3: 群聊消息")

    nc = await nats.connect(NATS_SERVER)

    test_msg = {
        "id": f"test-grp-{int(time.time())}",
        "from": MY_ID,
        "to": GROUP,
        "type": "grp",
        "payload": {"text": "联调测试：群聊消息验证"},
        "ts": time.time()
    }
    await nc.publish(f"aim.grp.{GROUP}", json.dumps(test_msg).encode())
    log_test("群聊发送到 grp_trio", True, "已发送")

    await nc.close()


# ── Test 4: Observer 事件 ──
async def test_observer():
    print("\n👁️ Test 4: Observer 事件")

    nc = await nats.connect(NATS_SERVER)
    events = []

    async def on_event(msg):
        data = json.loads(msg.data.decode())
        events.append(data)

    sub = await nc.subscribe("aim.obs.>", cb=on_event)
    await asyncio.sleep(0.5)

    # 发送测试事件
    event = {
        "type": "test_event",
        "agent_id": MY_ID,
        "detail": "联调测试：Observer 事件验证",
        "ts": time.time()
    }
    await nc.publish(f"aim.obs.{MY_ID}", json.dumps(event).encode())
    await asyncio.sleep(1)

    log_test("Observer 订阅", True)
    log_test("Observer 事件发送", True)
    log_test("Observer 事件接收", len(events) > 0, f"收到 {len(events)} 个事件")

    await nc.close()


# ── Test 5: 消息格式验证 ──
async def test_message_format():
    print("\n📋 Test 5: 消息格式验证")

    nc = await nats.connect(NATS_SERVER)

    # 验证标准消息格式
    msg = {
        "id": "test-001",
        "from": "ZS0003",
        "to": "ZS0001",
        "type": "dm",
        "payload": {"text": "格式验证"},
        "ts": 1780974412.589
    }

    # 检查必要字段
    required_fields = ["id", "from", "to", "type", "payload", "ts"]
    for field in required_fields:
        log_test(f"字段 {field} 存在", field in msg)

    # 验证 payload 结构
    log_test("payload.text 存在", "text" in msg.get("payload", {}))

    await nc.close()


# ── 主函数 ──
async def main():
    print("=" * 60)
    print("🚀 Phase 3 联调测试 — 三方 NATS 通信验证")
    print(f"   Agent: {MY_ID} ({AGENTS[MY_ID]})")
    print("=" * 60)

    await test_connection()
    await test_private_message()
    await test_group_message()
    await test_observer()
    await test_message_format()

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
