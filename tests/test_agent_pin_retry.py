#!/usr/bin/env python3
"""
AIM NATS Agent — Pin + RetryManager 集成测试

测试内容：
  1. Pin 消息去重（同一 msg_id 不重复处理）
  2. RetryManager 重试 + 退避
  3. 两个组件配合工作

使用方法:
  # 先确保 NATS Server 运行中
  nats-server -p 4222 &
  python3 test_agent_pin_retry.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# 颜色
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

passed = 0
failed = 0

def log_test(n: int, name: str):
    print(f"\n{'='*60}")
    print(f"  集成测试 #{n}: {name}")
    print(f"{'='*60}")

def log_pass(msg: str = "PASS"):
    global passed
    passed += 1
    print(f"  {GREEN}✓ {msg}{RESET}")

def log_fail(msg: str):
    global failed
    failed += 1
    print(f"  {RED}✗ {msg}{RESET}")


# ── 测试 1: Pin 消息去重 ────────────────

async def test_pin_dedup():
    """测试 Pin 去重：同一 msg_id 的重复消息只处理一次"""
    log_test(1, "Pin 消息去重")

    from aim_pin import AIMPin
    import nats

    pin = AIMPin(agent_id="TEST_PIN", ttl=60, db_dir="/tmp/aim_pin_test")

    nc = await nats.connect("nats://127.0.0.1:4222")

    processed_msgs = []

    async def handler(msg):
        data = json.loads(msg.data)
        msg_id = data.get("msg_id", "")

        # Pin 去重
        if await pin.is_duplicate(msg_id):
            return

        await pin.mark(msg_id)
        processed_msgs.append(data["content"])

    # 订阅
    sub = await nc.subscribe("test.pin.dedup", cb=handler)
    await nc.flush()

    # 发 3 条消息，其中 2 条 msg_id 相同
    common_id = str(uuid.uuid4())
    unique_id = str(uuid.uuid4())

    # msg1 — 正常消息
    await nc.publish("test.pin.dedup", json.dumps({
        "msg_id": common_id, "content": "msg1"
    }).encode())

    # msg2 — 重复 msg_id
    await nc.publish("test.pin.dedup", json.dumps({
        "msg_id": common_id, "content": "msg2"
    }).encode())

    # msg3 — 新的 msg_id
    await nc.publish("test.pin.dedup", json.dumps({
        "msg_id": unique_id, "content": "msg3"
    }).encode())

    await nc.flush()
    await asyncio.sleep(1)

    # 只有 msg1 和 msg3 被处理
    assert "msg1" in processed_msgs, "msg1 应被处理"
    assert "msg2" not in processed_msgs, "msg2 是重复的，应被去重"
    assert "msg3" in processed_msgs, "msg3 应被处理"
    assert len(processed_msgs) == 2, f"预期2条被处理，实际{len(processed_msgs)}条"

    await sub.unsubscribe()
    await nc.close()
    await pin.clear()
    log_pass(f"Pin 去重正确: {len(processed_msgs)}/{3} 条处理 (2条去重)")


# ── 测试 2: RetryManager 消息发送重试 ────

async def test_retry_send():
    """测试 RetryManager：发送失败后自动重试"""
    log_test(2, "RetryManager 发送重试")

    from aim_retry import RetryManager
    import nats

    rm = RetryManager(agent_id="TEST_RETRY", history_path="/tmp/aim_retry_integration.jsonl")

    nc = await nats.connect("nats://127.0.0.1:4222")

    # 模拟 send 函数：前 2 次失败，第 3 次成功
    attempt_counter = [0]

    async def flaky_send():
        attempt_counter[0] += 1
        if attempt_counter[0] < 3:
            raise ConnectionError(f"模拟发送失败 (第{attempt_counter[0]}次)")
        # 第3次成功
        await nc.publish("test.retry.result", json.dumps({"result": "ok"}).encode())
        await nc.flush()
        return "sent"

    success, result = await rm.retry(
        label="flaky_send",
        fn=flaky_send,
        max_retries=4,
        base_delay=0.01,
    )

    assert success, "最终应该成功"
    assert attempt_counter[0] == 3, f"预期3次尝试，实际{attempt_counter[0]}次"

    await nc.close()
    log_pass(f"RetryManager 重试正确: {attempt_counter[0]}次尝试后成功")


# ── 测试 3: RetryManager 彻底失败 ────────

async def test_retry_exhaustion():
    """测试 RetryManager：所有重试耗尽后正确报告失败"""
    log_test(3, "RetryManager 彻底失败")

    from aim_retry import RetryManager

    rm = RetryManager(agent_id="TEST_FAIL", history_path="/tmp/aim_retry_fail.jsonl")

    attempt_count = [0]

    async def always_fail():
        attempt_count[0] += 1
        raise RuntimeError("永久失败")

    success, result = await rm.retry(
        label="always_fail",
        fn=always_fail,
        max_retries=2,  # 共 3 次尝试
        base_delay=0.01,
    )

    assert not success, "应返回失败"
    assert attempt_count[0] == 3, f"预期3次尝试，实际{attempt_count[0]}次"
    assert "永久失败" in str(result), f"错误信息应传递: {result}"

    log_pass(f"RetryManager 正确报告失败: {attempt_count[0]}次尝试后放弃")


# ── 测试 4: Pin + Retry 联合工作 ─────────

async def test_pin_and_retry_together():
    """测试 Pin 和 RetryManager 配合：去重后再重试"""
    log_test(4, "Pin + RetryManager 联合工作")

    from aim_pin import AIMPin
    from aim_retry import RetryManager
    import nats

    pin = AIMPin(agent_id="TEST_COMBO", ttl=60, db_dir="/tmp/aim_combo_test")
    rm = RetryManager(agent_id="TEST_COMBO", history_path="/tmp/aim_retry_combo.jsonl")

    nc = await nats.connect("nats://127.0.0.1:4222")

    # 模拟完整流程：收到消息 → Pin 去重 → 处理(可能重试)
    processed = []

    async def process_message(msg_data: dict):
        """模拟处理一条消息"""
        msg_id = msg_data.get("msg_id", "")
        content = msg_data.get("content", "")

        # 1. Pin 去重
        if await pin.is_duplicate(msg_id):
            return {"status": "deduped", "content": None}

        await pin.mark(msg_id)

        # 2. RetryManager 包裹处理逻辑
        async def do_process():
            # 模拟 AI 处理
            await asyncio.sleep(0.05)
            processed.append(content)
            return f"processed: {content}"

        success, result = await rm.retry(
            label=f"process_{msg_id[:8]}",
            fn=do_process,
            max_retries=2,
            base_delay=0.01,
        )

        return {"status": "success" if success else "failed", "content": result}

    # 测试：3条消息，其中1条重复
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    # 发 4 次消息（2次重复 msg_id_b）
    for content, mid in [("A", id_a), ("B", id_b), ("B_dup", id_b), ("A_dup", id_a)]:
        result = await process_message({
            "msg_id": mid,
            "content": content,
        })
        print(f"   消息 {content} → {result['status']}")

    assert len(processed) == 2, f"预期2条被处理，实际{len(processed)}条: {processed}"
    assert "A" in processed
    assert "B" in processed

    await nc.close()
    await pin.clear()
    log_pass(f"Pin + Retry 联合工作正常: 处理{len(processed)}条 (去重2条)")


# ── 测试 5: 持久化 Pin 重启后去重 ────────

async def test_pin_persistence():
    """测试 Pin 持久化：重启后仍能去重之前处理过的消息"""
    log_test(5, "Pin 持久化重启去重")

    from aim_pin import AIMPin

    # 第一次使用 Pin
    pin1 = AIMPin(agent_id="TEST_PERSIST", ttl=300, db_dir="/tmp/aim_persist_test")
    await pin1.clear()

    msg_id = str(uuid.uuid4())

    # 检查 → 标记 → 刷入
    dup1 = await pin1.is_duplicate(msg_id)
    assert not dup1, "新消息不应重复"
    await pin1.mark(msg_id)
    await pin1.flush()  # 刷入 DB

    # 模拟重启：创建新的 Pin 实例
    pin2 = AIMPin(agent_id="TEST_PERSIST", ttl=300, db_dir="/tmp/aim_persist_test")

    dup2 = await pin2.is_duplicate(msg_id)
    assert dup2, "重启后应检测为重复"

    # 统计
    stats = pin2.get_stats()
    assert stats["hits"] >= 1, f"持久化命中应 >= 1: {stats}"

    await pin1.clear()
    log_pass("Pin 持久化重启后去重正确")


# ── 主入口 ──────────────────────────────

async def main():
    print(f"\n{'='*60}")
    print(f"  AIM Agent — Pin + RetryManager 集成测试")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 先检查 NATS 是否运行
    try:
        import nats
        nc = await nats.connect("nats://127.0.0.1:4222")
        if nc.is_connected:
            await nc.close()
            print(f"{GREEN}✓ NATS Server 运行中{RESET}")
        else:
            print(f"{RED}✗ NATS Server 未运行{RESET}")
            print(f"  请先启动: nats-server -p 4222 &")
            return 1
    except Exception as e:
        print(f"{RED}✗ NATS Server 连接失败: {e}{RESET}")
        print(f"  请先启动: nats-server -p 4222 &")
        return 1

    tests = [
        test_pin_dedup,
        test_retry_send,
        test_retry_exhaustion,
        test_pin_and_retry_together,
        test_pin_persistence,
    ]

    for test in tests:
        await test()

    global passed, failed
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  总结: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {RED}{failed} 失败{RESET}")
    else:
        print(f", {GREEN}全部通过!{RESET}")
    print(f"{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
