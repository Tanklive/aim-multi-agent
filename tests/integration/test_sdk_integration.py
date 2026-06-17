#!/usr/bin/env python3
"""
SDK 集成测试 — 测试 aim_nats_sdk.py 的 Pin + RetryManager 子组件
（NATS 连接测试需 NATS Server 运行，此处只测脱机组件的集成）
"""
import asyncio
import logging
import sys
import uuid

logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, ".")
from aim_nats_sdk import AIMPin, RetryManager


async def test_pin_integration():
    """Pin 集成测试（使用 SDK 内嵌的 AIMPin）"""
    print("\n=== Pin 集成测试 ===")
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}")
            failed += 1

    # Test 1: 默认 TTL 120s
    pin = AIMPin(agent_id="TEST_INTEGRATION", ttl=120, db_dir="/tmp/sdk_test_pin")
    check("default TTL = 120", pin.ttl == 120)

    # Test 2: 新消息非重复
    mid = uuid.uuid4().hex[:12]
    check("new msg not dup", not await pin.is_duplicate(mid))

    # Test 3: 标记后重复
    await pin.mark(mid)
    check("marked msg is dup", await pin.is_duplicate(mid))

    # Test 4: 不同 msg 互不影响
    mid2 = uuid.uuid4().hex[:12]
    check("different msg not dup", not await pin.is_duplicate(mid2))

    # Test 5: 持久化后重启仍可去重
    await pin.flush()
    pin2 = AIMPin(agent_id="TEST_INTEGRATION", ttl=120, db_dir="/tmp/sdk_test_pin")
    check("persist restore after flush", await pin2.is_duplicate(mid))

    # Test 6: 统计
    stats = pin.get_stats()
    check("hits count > 0", stats["hits"] >= 1)
    check("misses count > 0", stats["misses"] >= 2)
    check("persisted records > 0", stats["persisted"] > 0)

    await pin.clear()
    print(f"\n  结果: {passed}/{passed + failed} 通过")
    return passed, failed


async def test_retry_integration():
    """RetryManager 集成测试（使用 SDK 内嵌的 RetryManager）"""
    print("\n=== RetryManager 集成测试 ===")
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}")
            failed += 1

    rm = RetryManager(agent_id="TEST_INTEGRATION", history_path="/tmp/sdk_test_retry.jsonl")

    # Test 1: 默认策略的 max_retries 是 5（呱呱建议）
    check("default max_retries = 5", rm.strategies["default"]["max_retries"] == 5)
    check("default base_delay = 1.0", rm.strategies["default"]["base_delay"] == 1.0)
    check("default max_delay = 60.0", rm.strategies["default"]["max_delay"] == 60.0)

    # Test 2: 成功任务
    async def ok_fn():
        return "ok_result"
    success, result = await rm.retry("test_ok", ok_fn, max_retries=2)
    check("success returns result", success and result == "ok_result")

    # Test 3: 失败后恢复
    cnt = [0]
    async def fail_then_ok():
        cnt[0] += 1
        if cnt[0] < 2:
            raise ValueError("temp")
        return "recovered"
    success, result = await rm.retry("test_recover", fail_then_ok, max_retries=3, base_delay=0.01)
    check("recover after retry", success and result == "recovered")
    check("exactly 2 attempts", cnt[0] == 2)

    # Test 4: 超过最大重试
    async def always_fail():
        raise RuntimeError("permanent")
    success, result = await rm.retry("test_permanent", always_fail, max_retries=2, base_delay=0.01)
    check("exhaust retries returns False", not success)

    # Test 5: 2x 指数退避延迟计算
    d0 = rm.calc_delay(0, base_delay=1.0, max_delay=60.0, jitter=0)
    d1 = rm.calc_delay(1, base_delay=1.0, max_delay=60.0, jitter=0)
    d2 = rm.calc_delay(2, base_delay=1.0, max_delay=60.0, jitter=0)
    check("delay0 ≈ 1.0", 0.9 <= d0 <= 1.1)
    check("delay1 ≈ 2.0 (2x)", 1.9 <= d1 <= 2.1)
    check("delay2 ≈ 4.0 (2x^2)", 3.9 <= d2 <= 4.1)
    check("delays monotonic", d0 <= d1 <= d2)

    # Test 6: 超时
    async def slow():
        await asyncio.sleep(10)
    success, result = await rm.retry("test_timeout", slow, max_retries=1, base_delay=0.01, timeout=0.1)
    check("timeout fails", not success)

    # Test 7: 统计和历史
    stats = rm.get_stats()
    check("successful > 0", stats["successful"] >= 1)
    check("failed > 0", stats["failed"] >= 1)
    history = rm.get_history()
    check("history not empty", len(history) > 0)

    # Test 8: 快捷方法
    async def send_fn():
        return "sent"
    success, result = await rm.retry_send("test_send", send_fn)
    check("retry_send works", success and result == "sent")

    async def connect_fn():
        return "connected"
    success, result = await rm.retry_connect("test_connect", connect_fn)
    check("retry_connect works", success)

    async def request_fn():
        return {"reply": "ok"}
    success, result = await rm.retry_request("test_request", request_fn)
    check("retry_request works", success)

    print(f"\n  结果: {passed}/{passed + failed} 通过")
    return passed, failed


async def test_client_status():
    """验证 AIMNATSClient.status() 包含 pin/retry 统计（无连接时）"""
    print("\n=== Client status() 结构检查 ===")
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}")
            failed += 1

    from aim_nats_sdk import AIMNATSClient
    client = AIMNATSClient(agent_id="TEST_UNCONNECTED")

    status = client.status()
    check("status has agent_id", "agent_id" in status)
    check("status has pin stats", "pin" in status)
    check("status has retry stats", "retry" in status)
    check("pin stats has hits", "hits" in status["pin"])
    check("retry stats has successful", "successful" in status["retry"])
    check("connnected is False (no nats)", status["connected"] is False)

    # Pin 和 Retry 的访问器
    pin_stats = client.pin.get_stats()
    check("pin.get_stats() works", isinstance(pin_stats, dict))
    check("pin has ttl field", pin_stats.get("ttl") == 120)  # 默认

    retry_stats = client.retry.get_stats()
    check("retry.get_stats() works", isinstance(retry_stats, dict))

    print(f"\n  结果: {passed}/{passed + failed} 通过")
    return passed, failed


async def main():
    print("=" * 52)
    print("  AIM NATS SDK 集成测试")
    print("=" * 52)

    p1, f1 = await test_pin_integration()
    p2, f2 = await test_retry_integration()
    p3, f3 = await test_client_status()

    total_p = p1 + p2 + p3
    total_f = f1 + f2 + f3
    print(f"\n{'=' * 52}")
    print(f"  总计: {total_p}/{total_p + total_f} 通过")
    if total_f > 0:
        print(f"  失败: {total_f}")
        sys.exit(1)
    else:
        print(f"  ✅ 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
