#!/usr/bin/env python3
"""
AIM NATS Interop Test
验证两端消息格式一致性，确保 ZS0001 和 ZS0002 能互通
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone, timedelta

import nats

# 配置
NATS_URL = "nats://127.0.0.1:4222"
ZS0001_ID = "ZS0001"
ZS0002_ID = "ZS0002"
TEST_GROUP = "grp_trio"

# 测试结果
results = {
    "msg_format": False,
    "dm_send_receive": False,
    "group_send_receive": False,
    "timestamp_format": False,
    "metadata_fields": False
}


def create_message(from_id: str, to_id: str, content: str, msg_type: str = "dm", group_id: str = None):
    """创建标准格式消息"""
    msg = {
        "msg_id": str(uuid.uuid4()),
        "from": from_id,
        "to": to_id,
        "type": msg_type,
        "content": content,
        "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "metadata": {
            "reply_to": None,
            "priority": 0,
            "ttl": 3600
        }
    }
    if group_id:
        msg["group"] = group_id
    return msg


def validate_message_format(msg: dict) -> bool:
    """验证消息格式是否符合规范"""
    required_fields = ["msg_id", "from", "to", "type", "content", "timestamp"]
    
    # 检查必填字段
    for field in required_fields:
        if field not in msg:
            print(f"  ❌ 缺少必填字段: {field}")
            return False
    
    # 检查类型
    if not isinstance(msg["msg_id"], str):
        print(f"  ❌ msg_id 类型错误，期望 string，实际 {type(msg['msg_id'])}")
        return False
    
    if msg["type"] not in ["dm", "group", "request", "response"]:
        print(f"  ❌ type 值错误: {msg['type']}")
        return False
    
    # 检查 timestamp 格式（ISO 8601）
    try:
        datetime.fromisoformat(msg["timestamp"])
    except ValueError:
        print(f"  ❌ timestamp 格式错误，期望 ISO 8601")
        return False
    
    # 检查 metadata（可选）
    if "metadata" in msg:
        if not isinstance(msg["metadata"], dict):
            print(f"  ❌ metadata 类型错误，期望 dict")
            return False
    
    print("  ✅ 消息格式验证通过")
    return True


async def test_dm_interop():
    """测试私聊消息互通"""
    print("\n=== 测试 1: 私聊消息互通 ===")
    
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    
    received_messages = []
    
    async def on_message(msg):
        data = json.loads(msg.data.decode())
        received_messages.append(data)
        print(f"  📨 收到消息: {data['content']}")
        await msg.ack()
    
    # ZS0002 订阅
    sub = await js.subscribe(f"aim.dm.{ZS0002_ID}", durable="interop-test-dm", cb=on_message)
    
    # ZS0001 发送
    test_msg = create_message(ZS0001_ID, ZS0002_ID, "Interop 测试消息 - ZS0001→ZS0002")
    print(f"  📤 发送消息: {test_msg['content']}")
    
    # 验证发送前格式
    if not validate_message_format(test_msg):
        results["msg_format"] = False
        await sub.unsubscribe()
        await nc.close()
        return
    
    await js.publish(f"aim.dm.{ZS0002_ID}", json.dumps(test_msg).encode())
    
    # 等待接收
    await asyncio.sleep(2)
    
    if received_messages:
        received = received_messages[0]
        # 验证接收格式
        if validate_message_format(received):
            results["msg_format"] = True
            # 验证内容一致性
            if received["content"] == test_msg["content"]:
                results["dm_send_receive"] = True
                print("  ✅ 私聊消息互通测试通过")
            else:
                print(f"  ❌ 内容不一致: 期望 '{test_msg['content']}', 实际 '{received['content']}'")
        else:
            print("  ❌ 接收消息格式验证失败")
    else:
        print("  ❌ 未收到消息")
    
    await sub.unsubscribe()
    await nc.close()


async def test_group_interop():
    """测试群聊消息互通"""
    print("\n=== 测试 2: 群聊消息互通 ===")
    
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    
    received_messages = []
    
    async def on_message(msg):
        data = json.loads(msg.data.decode())
        received_messages.append(data)
        print(f"  📨 收到群聊消息: {data['content']}")
        await msg.ack()
    
    # 订阅群聊
    sub = await js.subscribe(f"aim.grp.{TEST_GROUP}", durable="interop-test-group", cb=on_message)
    
    # ZS0001 发送群聊
    test_msg = create_message(ZS0001_ID, TEST_GROUP, "Interop 群聊测试 - ZS0001", msg_type="group", group_id=TEST_GROUP)
    print(f"  📤 发送群聊消息: {test_msg['content']}")
    
    await js.publish(f"aim.grp.{TEST_GROUP}", json.dumps(test_msg).encode())
    
    # 等待接收
    await asyncio.sleep(2)
    
    if received_messages:
        received = received_messages[0]
        if validate_message_format(received) and received.get("group") == TEST_GROUP:
            results["group_send_receive"] = True
            print("  ✅ 群聊消息互通测试通过")
        else:
            print("  ❌ 群聊消息格式验证失败")
    else:
        print("  ❌ 未收到群聊消息")
    
    await sub.unsubscribe()
    await nc.close()


async def test_timestamp_format():
    """测试时间戳格式一致性"""
    print("\n=== 测试 3: 时间戳格式 ===")
    
    # 测试 ISO 8601 格式
    test_cases = [
        datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "2026-06-09T01:12:00+08:00",
        "2026-06-09T01:12:00.123456+08:00"
    ]
    
    all_valid = True
    for ts in test_cases:
        try:
            parsed = datetime.fromisoformat(ts)
            print(f"  ✅ 有效: {ts}")
        except ValueError:
            print(f"  ❌ 无效: {ts}")
            all_valid = False
    
    results["timestamp_format"] = all_valid
    if all_valid:
        print("  ✅ 时间戳格式测试通过")
    else:
        print("  ❌ 时间戳格式测试失败")


async def test_metadata_fields():
    """测试 Metadata 字段"""
    print("\n=== 测试 4: Metadata 字段 ===")
    
    # 测试完整 metadata
    test_msg = create_message(ZS0001_ID, ZS0002_ID, "Metadata 测试")
    test_msg["metadata"] = {
        "reply_to": "test-msg-id",
        "priority": 5,
        "ttl": 7200,
        "custom_field": "custom_value"
    }
    
    # 验证格式
    if validate_message_format(test_msg):
        metadata = test_msg["metadata"]
        required_metadata = ["reply_to", "priority", "ttl"]
        
        for field in required_metadata:
            if field in metadata:
                print(f"  ✅ metadata.{field}: {metadata[field]}")
            else:
                print(f"  ❌ 缺少 metadata.{field}")
                results["metadata_fields"] = False
                return
        
        results["metadata_fields"] = True
        print("  ✅ Metadata 字段测试通过")
    else:
        print("  ❌ 消息格式验证失败")


async def main():
    """运行所有测试"""
    print("=" * 50)
    print("AIM NATS Interop 测试")
    print("=" * 50)
    
    # 运行测试
    await test_timestamp_format()
    await test_metadata_fields()
    await test_dm_interop()
    await test_group_interop()
    
    # 打印结果
    print("\n" + "=" * 50)
    print("测试结果汇总")
    print("=" * 50)
    
    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")
    
    # 总体结果
    all_passed = all(results.values())
    print(f"\n{'✅ 所有测试通过!' if all_passed else '❌ 部分测试失败'}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
