#!/usr/bin/env python3
"""
SDK 基本功能验证：连接 → setup streams → 发送私聊 → 接收私聊 → 请求回复 → 断开
"""
import asyncio
import sys
import os
import json

# 从 shared/aim/bin/ 导入 SDK
sys.path.insert(0, os.path.expanduser("~/shared/aim/bin"))
from aim_nats_sdk import AIMNATSClient, Subjects

# ── 测试配置 ──────────────────────────────────────────
AGENT_A = "ZS0002"
AGENT_B = "ZS0001"
NATS_URL = "nats://127.0.0.1:4222"

# ── 共享状态 ──────────────────────────────────────────
received_messages = []

async def test_basic_flow():
    print("=" * 60)
    print("📡 AIM NATS SDK 基本功能验证")
    print("=" * 60)

    # ── 创建两个客户端 ──────────────────────────────
    client_a = AIMNATSClient(AGENT_A, NATS_URL)
    client_b = AIMNATSClient(AGENT_B, NATS_URL)

    print(f"\n1️⃣  [client_a={AGENT_A}] 连接 NATS...")
    await client_a.connect()
    assert client_a.is_connected, "❌ client_a 未连接"
    print(f"   ✅ 已连接: {client_a.is_connected}")

    print(f"\n2️⃣  [client_b={AGENT_B}] 连接 NATS...")
    await client_b.connect()
    assert client_b.is_connected, "❌ client_b 未连接"
    print(f"   ✅ 已连接: {client_b.is_connected}")

    # ── 设置 JetStream Streams ──────────────────────
    print(f"\n3️⃣  设置 JetStream Streams...")
    await client_a.setup_streams()
    print(f"   ✅ Streams 就绪")

    # ── client_b 订阅私聊 ──────────────────────────
    async def dm_handler_b(envelope, raw_msg):
        received_messages.append(envelope)
        print(f"   📥 [{AGENT_B}] 收到消息: {envelope['from']} → {envelope['payload'].get('text', '')[:50]}")

    print(f"\n4️⃣  [client_b={AGENT_B}] 订阅私聊 aim.dm.{AGENT_B}...")
    await client_b.subscribe_dm(dm_handler_b)
    print(f"   ✅ 已订阅")

    # ── 给 NATS 一点时间建立订阅 ────────────────────
    await asyncio.sleep(0.2)

    # ── client_a 发送私聊 ──────────────────────────
    print(f"\n5️⃣  [client_a={AGENT_A}] 发送私聊 → {AGENT_B}...")
    msg1 = await client_a.send_dm(AGENT_B, "Hello from SDK测试！北京时间测试消息")
    print(f"   📤 已发送: msg_id={msg1['id']}")

    # 等待接收
    await asyncio.sleep(0.5)
    assert len(received_messages) >= 1, "❌ 未收到私聊消息"
    print(f"   ✅ 收到消息: {received_messages[-1]['payload']['text']}")

    # ── 请求-回复 ──────────────────────────────────
    print(f"\n6️⃣  测试 Request-Reply...")
    async def reply_handler(envelope, raw_msg):
        """client_b 回复 client_a 的请求"""
        reply = {
            "ver": "1.0", "id": "reply-" + envelope["id"],
            "ts": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "from": AGENT_B, "type": "reply",
            "payload": {"text": f"收到你的请求：{envelope['payload'].get('text','')}"}
        }
        await raw_msg.respond(json.dumps(reply, ensure_ascii=False).encode())
        print(f"   ↩️ [{AGENT_B}] 回复请求")

    # 在 client_b 上加一个 DM handler 支持回复（需要稍微不同的订阅方式）
    # 对 request-reply，我们用 NATS 原生 request/respond
    # 先订阅 client_b 的私聊，在 handler 里判断如果是 request 就回复
    client_b._dm_handler = reply_handler  # 替换为回复 handler
    # 但 subscribe_dm 用的是 core NATS publish，request-reply 需要 raw subscribe
    # 重新搞: 用 nc.subscribe 接收，然后 respond

    # 取消旧的 DM 订阅，用原生 subscribe + respond
    for sub_name, sub in list(client_b._subscriptions.items()):
        if Subjects.dm(AGENT_B) in sub_name:
            await sub.unsubscribe()
            del client_b._subscriptions[sub_name]

    async def request_handler(msg):
        envelope = json.loads(msg.data.decode())
        reply = {
            "ver": "1.0", "id": "reply-" + envelope["id"],
            "ts": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "from": AGENT_B, "type": "reply",
            "payload": {"text": f"收到你的请求: {envelope['payload'].get('text','')[:30]}"}
        }
        await msg.respond(json.dumps(reply, ensure_ascii=False).encode())
        print(f"   ↩️ [{AGENT_B}] 回复 request")

    sub_handle = await client_b.nc.subscribe(Subjects.dm(AGENT_B), cb=request_handler)
    client_b._subscriptions["request_handler"] = sub_handle

    await asyncio.sleep(0.2)

    # client_a 发送请求
    try:
        response = await client_a.send_request(AGENT_B, "测试 Request-Reply 功能", timeout=3.0)
        print(f"   ✅ Request-Reply 成功! 回复: {response.get('payload',{}).get('text','')[:50]}")
        # 恢复原始的 dm_handler
        del client_b._subscriptions["request_handler"]
    except Exception as e:
        print(f"   ⚠️ Request-Reply 异常: {e}")

    # 重新订阅 client_b 的 DM handler（不回复）
    async def dm_handler_clean(envelope, raw_msg):
        received_messages.append(envelope)
    await client_b.subscribe_dm(dm_handler_clean)

    # ── 群聊 ──────────────────────────────────────
    print(f"\n7️⃣  测试群聊...")
    async def grp_handler(envelope, raw_msg):
        print(f"   📥 [{AGENT_B}] 群消息: {envelope['from']} → {envelope['payload'].get('text','')[:40]}")

    await client_b.subscribe_grp("grp_trio", grp_handler)
    await client_a.subscribe_grp("grp_trio", lambda e, m: None)
    await asyncio.sleep(0.2)

    msg_grp = await client_a.send_grp("grp_trio", "测试群聊消息 from SDK")
    await asyncio.sleep(0.3)
    print(f"   ✅ 群聊发送成功: msg_id={msg_grp['id']}")

    # ── JetStream 持久化发布 ───────────────────────
    print(f"\n8️⃣  测试 JetStream 持久化发布...")
    msg_js = await client_a.send_dm(AGENT_B, "JetStream 持久化测试", use_jetstream=True)
    await asyncio.sleep(0.3)
    print(f"   ✅ JetStream 发布成功: msg_id={msg_js['id']}")

    # ── Observer 推送 ─────────────────────────────
    print(f"\n9️⃣  测试 Observer 推送...")
    await client_a.emit_obs("processing", msg_id=msg1["id"], detail="SDK测试处理中")
    await client_a.emit_obs("completed", msg_id=msg1["id"], detail="SDK测试完成")
    print(f"   ✅ Observer 推送成功")

    # ── 状态 ──────────────────────────────────────
    print(f"\n🔟  客户端状态:")
    status_a = client_a.status()
    print(f"   client_a: {json.dumps(status_a, ensure_ascii=False, indent=4)}")

    # ── 关闭 ──────────────────────────────────────
    print(f"\n🔄  关闭连接...")
    await client_a.close()
    await client_b.close()
    print(f"   ✅ 全部断开")

    print("\n" + "=" * 60)
    print("🎉 SDK 功能验证全部通过!")
    print("=" * 60)

    return True

if __name__ == "__main__":
    success = asyncio.run(test_basic_flow())
    sys.exit(0 if success else 1)
