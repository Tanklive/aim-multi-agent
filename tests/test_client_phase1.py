#!/usr/bin/env python3
"""
NATS Client Phase 1 端到端测试
1. 基础连接
2. DM 收发（呱呱 → 吉量）
3. JetStream 存储验证
4. Request-Reply
"""
import asyncio
import json
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(__file__))
from aim_nats_sdk import AIMNATSClient, Subjects, make_envelope, parse_message

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("test-phase1")

# 测试常量
NATS_SERVER = "nats://127.0.0.1:4222"
ZS0001 = "ZS0001"  # 呱呱
ZS0002 = "ZS0002"  # 吉量

received_messages = []

async def dm_handler(envelope, msg):
    """私聊消息处理器"""
    received_messages.append(envelope)
    text = envelope.get("payload", {}).get("text", "")
    msg_id = envelope.get("id", "")
    from_id = envelope.get("from", "")
    log.info(f"📥 收到 DM [{msg_id}] 来自 {from_id}: {text}")
    # 自动回复（echo）
    return {"status": "received", "msg_id": msg_id}

async def test_basic_connect():
    """测试 1: 基础连接"""
    log.info("=" * 50)
    log.info("测试 1: 基础连接")
    log.info("=" * 50)
    client = AIMNATSClient("ZS0002", server=NATS_SERVER)
    await client.connect()
    assert client.is_connected, "❌ 连接失败"
    log.info(f"✅ 连接成功: {client.status()}")
    await client.close()
    return True

async def test_send_dm():
    """测试 2: DM 发送"""
    log.info("=" * 50)
    log.info("测试 2: DM 发送（吉量 → 呱呱）")
    log.info("=" * 50)
    client = AIMNATSClient("ZS0002", server=NATS_SERVER)
    await client.connect()

    # 先订阅呱呱的 DM，模拟呱呱接收
    guagua_received = []

    async def guagua_handler(envelope, msg):
        guagua_received.append(envelope)
        text = envelope.get("payload", {}).get("text", "")
        log.info(f"🐸 呱呱收到: {text}")

    await client.nc.subscribe(Subjects.dm(ZS0001), cb=client._wrap_coro(guagua_handler))
    await asyncio.sleep(0.3)  # 等订阅生效

    # 发送 DM
    env = await client.send_dm(ZS0001, "呱呱你好！我是吉量，NATS Phase 1 测试 🐴")
    await asyncio.sleep(0.5)  # 等消息到达

    assert len(guagua_received) == 1, f"❌ 呱呱未收到消息: {guagua_received}"
    received_text = guagua_received[0].get("payload", {}).get("text", "")
    assert "呱呱你好" in received_text, f"❌ 消息内容不符: {received_text}"
    log.info(f"✅ DM 发送+接收成功")
    await client.close()
    return True

async def test_jetstream_persistence():
    """测试 3: JetStream 持久化"""
    log.info("=" * 50)
    log.info("测试 3: JetStream 持久化存储")
    log.info("=" * 50)
    client = AIMNATSClient("ZS0001", server=NATS_SERVER)
    await client.connect()
    await client.setup_streams()

    # 用 JetStream 发送一条消息
    env = await client.send_dm(ZS0002, "这是 JetStream 持久化测试消息", use_jetstream=True)
    await asyncio.sleep(0.5)

    # 验证消息已存储在 Stream 中
    try:
        stream_name = "aim-messages"
        info = await client.js.stream_info(stream_name)
        log.info(f"📦 Stream '{stream_name}' 状态:")
        log.info(f"   • 消息数: {info.state.messages}")
        log.info(f"   • 字节数: {info.state.bytes}")
        log.info(f"   • 最后 seq: {info.state.last_seq}")
        assert info.state.messages > 0, "❌ Stream 中无消息"
        log.info(f"✅ JetStream 消息已持久化")
    except Exception as e:
        log.error(f"❌ JetStream 验证失败: {e}")
        raise

    await client.close()
    return True

async def test_request_reply():
    """测试 4: Request-Reply 模式（使用测试专用 subject 避免呱呱 agent 干扰）"""
    log.info("=" * 50)
    log.info("测试 4: Request-Reply 模式")
    log.info("=" * 50)

    guagua = AIMNATSClient("ZS0001", server=NATS_SERVER)
    await guagua.connect()

    # 使用测试专用 subject 避免与呱呱 agent 冲突
    test_subject = "aim.test.request-reply"

    async def guagua_reply_handler(envelope, msg):
        text = envelope.get("payload", {}).get("text", "")
        log.info(f"🐸 呱呱收到 request: {text}")
        reply = make_envelope(
            from_id=ZS0001,
            msg_type="reply",
            payload={"text": f"已收到: {text}", "original_id": envelope["id"]},
        )
        if msg.reply:
            await msg.respond(json.dumps(reply, ensure_ascii=False).encode())
            log.info(f"🐸 呱呱已回复")

    await guagua.nc.subscribe(test_subject, cb=guagua._wrap_coro(guagua_reply_handler))
    await asyncio.sleep(0.3)

    jiliang = AIMNATSClient("ZS0002", server=NATS_SERVER)
    await jiliang.connect()

    request_env = make_envelope(
        from_id=ZS0002, msg_type="request", payload={"text": "Request-Reply 测试"}
    )
    response = await jiliang.nc.request(
        test_subject,
        json.dumps(request_env, ensure_ascii=False).encode(),
        timeout=5,
    )
    reply = parse_message(response.data)
    reply_text = reply.get("payload", {}).get("text", "")
    log.info(f"📥 收到 reply: {reply_text}")
    assert "已收到" in reply_text, f"❌ Reply 内容不符: {reply_text}"
    log.info(f"✅ Request-Reply 成功")

    await jiliang.close()
    await guagua.close()
    return True

async def test_subscribe_unsubscribe():
    """测试 5: 订阅/取消订阅（使用测试专用 subject）"""
    log.info("=" * 50)
    log.info("测试 5: 订阅管理")
    log.info("=" * 50)
    client = AIMNATSClient("ZS0002", server=NATS_SERVER)
    await client.connect()

    test_subject = "aim.test.subscribe"
    sub_received = []
    async def sub_handler(envelope, msg):
        sub_received.append(envelope)
        log.info(f"📥 收到测试消息: {envelope.get('payload', {}).get('text', '')}")

    sub = await client.nc.subscribe(test_subject, cb=client._wrap_coro(sub_handler))
    client._subscriptions[test_subject] = sub
    await asyncio.sleep(0.3)
    assert test_subject in client._subscriptions, "❌ 订阅未注册"
    log.info(f"✅ 订阅注册成功: {list(client._subscriptions.keys())}")

    # 发消息到测试 subject
    env = make_envelope(from_id="ZS0002", msg_type="dm", payload={"text": "自测消息"})
    await client.nc.publish(test_subject, json.dumps(env, ensure_ascii=False).encode())
    await asyncio.sleep(0.5)
    assert len(sub_received) == 1, f"❌ 自测消息未收到: {sub_received}"
    received_text = sub_received[0].get("payload", {}).get("text", "")
    assert "自测" in received_text, f"❌ 消息内容不符: {received_text}"

    # 取消订阅
    if test_subject in client._subscriptions:
        await sub.unsubscribe()
        client._subscriptions.pop(test_subject)
    log.info(f"✅ 取消订阅成功")
    await client.close()
    return True

async def main():
    """运行全部测试"""
    log.info("🚀 NATS Client Phase 1 端到端测试开始")

    tests = [
        ("基础连接", test_basic_connect),
        ("DM 发送/接收", test_send_dm),
        ("JetStream 持久化", test_jetstream_persistence),
        ("Request-Reply", test_request_reply),
        ("订阅管理", test_subscribe_unsubscribe),
    ]

    passed = 0
    failed = 0

    for name, fn in tests:
        log.info(f"\n{'='*60}")
        log.info(f"  测试: {name}")
        log.info(f"{'='*60}")
        try:
            await fn()
            log.info(f"\n✅ {name} 通过")
            passed += 1
        except Exception as e:
            log.error(f"\n❌ {name} 失败: {e}")
            failed += 1
        await asyncio.sleep(0.5)

    log.info(f"\n{'='*60}")
    log.info(f"📊 测试结果: {passed}/{len(tests)} 通过, {failed} 失败")
    log.info(f"{'='*60}")
    return failed == 0

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
