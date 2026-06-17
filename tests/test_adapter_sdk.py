#!/usr/bin/env python3
"""
自测：AIM Agent NATS Adapter (SDK 版)
测试内容：
1. 导入 adapter 模块
2. 模拟启动 adapter（只初始化，不阻塞）
3. 验证 SDK 依赖就绪
4. 快速启动/停止验证生命周期
"""
import asyncio
import sys
import os
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

print("=" * 60)
print("🧪 AIM Agent NATS Adapter 自测")
print("=" * 60)


# 测试 1: 模块导入
print("\n[1/5] 测试导入 adapter 模块...")
try:
    from aim_agent_nats_adapter import AIMAgentNatsAdapter, setup_logging, MessageArchive
    print("  ✅ adapter 模块导入成功")
except Exception as e:
    print(f"  ❌ 导入失败: {e}")
    sys.exit(1)


# 测试 2: SDK 依赖
print("\n[2/5] 测试 Veritas SDK 导入...")
try:
    from aim_nats_sdk import AIMNATSClient, make_envelope, parse_message, Subjects
    print(f"  ✅ SDK 导入成功")
    print(f"     Subjects.dm('ZS0002') = {Subjects.dm('ZS0002')}")
    print(f"     Subjects.grp('grp_trio') = {Subjects.grp('grp_trio')}")
except Exception as e:
    print(f"  ❌ SDK 导入失败: {e}")
    sys.exit(1)


# 测试 3: adapter 实例化
print("\n[3/5] 测试 adapter 实例化...")
try:
    adapter = AIMAgentNatsAdapter(
        agent_id="ZS0002",
        agent_name="吉量",
        framework="hermes",
        nats_url="nats://127.0.0.1:4222",
        emoji="🐴",
    )
    print(f"  ✅ adapter 实例化成功")
    print(f"     agent_id = {adapter.agent_id}")
    print(f"     client type = {type(adapter.client).__name__}")
    print(f"     log name = {adapter.log.name}")
except Exception as e:
    print(f"  ❌ 实例化失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


# 测试 4: 信封格式转换
print("\n[4/5] 测试信封格式转换...")

# Veritas 信封 → 内部格式
test_envelope = {
    "ver": "1.0",
    "id": "test123",
    "ts": "2026-06-09T01:30:00.000Z",
    "from": "ZS0001",
    "type": "dm",
    "payload": {"text": "你好，吉量"},
}
internal = adapter._envelope_to_internal(test_envelope)
print(f"  源信封: {test_envelope['from']} → {test_envelope['payload']['text']}")
print(f"  内部格式: msg_id={internal['msg_id']}, from={internal['from']}, content={internal['content']}")
assert internal["from"] == "ZS0001"
assert internal["content"] == "你好，吉量"
assert internal["msg_id"] == "test123"
print(f"  ✅ 信封→内部格式转换通过")

# 内部格式 → Veritas 信封（使用 SDK 的 make_envelope）
from aim_nats_sdk import make_envelope
reply_envelope = make_envelope(
    from_id="ZS0002",
    msg_type="dm",
    payload={"text": "收到，呱呱"},
    reply_to="test123",
)
print(f"  回复信封: from={reply_envelope['from']}, payload={reply_envelope['payload']}, meta={reply_envelope.get('meta')}")
assert reply_envelope["from"] == "ZS0002"
assert reply_envelope["payload"]["text"] == "收到，呱呱"
print(f"  ✅ 内部格式→信封转换通过")


# 测试 5: 快速启动/停止（检查连接能力，超时5秒）
print("\n[5/5] 测试 NATS 连接...")

async def test_connect():
    """快速连接测试"""
    try:
        await adapter.client.connect()
        await adapter.client.setup_streams()
        print(f"  ✅ NATS 连接成功: {adapter.client.server}")
        print(f"     连接状态: {adapter.client.is_connected}")
        print(f"     JetStream: {'可用' if adapter.client.js else '不可用'}")

        # 测试发送一条消息给自己（验证 publish）
        await adapter.client.send_dm(
            to_id="ZS0002",
            text="self-test 消息",
            enable_retry=False,
        )
        print(f"  ✅ 消息发送测试通过")

        # 检查状态
        status = adapter.client.status()
        print(f"  📊 Pin 统计: {status.get('pin', {})}")
        print(f"  📊 Retry 统计: {status.get('retry', {})}")

        # 断开
        await adapter.client.close()
        print(f"  ✅ 断开连接成功")
        return True
    except Exception as e:
        print(f"  ❌ NATS 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

result = asyncio.run(test_connect())

print("\n" + "=" * 60)
if result:
    print("🎉 所有测试通过！Adapter 已就绪")
    print("   可执行: python aim_agent_nats_adapter.py --agent-id ZS0002 --agent-name 吉量")
else:
    print("⚠️  部分测试未通过，请检查日志")
print("=" * 60)
