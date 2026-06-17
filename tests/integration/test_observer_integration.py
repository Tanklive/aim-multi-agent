#!/usr/bin/env python3
"""
AIM Observer 接口集成测试

测试目标：
  验证 Observer 通道事件格式与 retry_components 的集成正确性

覆盖场景：
  TC01 — RetryEventEmitter → retry_event 格式校验
  TC02 — RetryEventEmitter → delivery_event(delivered) 格式校验
  TC03 — RetryEventEmitter → delivery_event(expired) 格式校验
  TC04 — RetryEventEmitter → delivery_event(unreachable) 格式校验
  TC05 — RetryEventEmitter → cache_event(recovered) 格式校验
  TC06 — RetryEventEmitter → cache_event(cache_overflow) 格式校验
  TC07 — RetryManager deliver_with_retry → 离线缓存 + cache_event
  TC08 — RetryManager on_agent_recovered → recovered 事件
  TC09 — RetryManager 多次重试 → 事件序列完整性
  TC10 — Observer 消息解析端到端（模拟 WebSocket 帧）
  TC11 — SuspectTracker 状态机集成
  TC12 — SeqDeduplicator 去重集成
  TC07b — 重试过程中目标离线 → unreachable 事件
  TC13 — broadcast_to_observers 事件信封格式

依赖：
  - retry_components.py
  - retry_integration.py
  - aim_observer.py

作者：呱呱 🐸 (ZS0001)
日期：2026-06-08
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from retry_components import (
    RetryPolicy, OfflineCache, RetryEventEmitter,
    SeqReplayBuffer, SuspectTracker, SeqDeduplicator,
)
from retry_integration import RetryManager

# ── 辅助 ──────────────────────────────────────────────

class CollectedEvents:
    """收集 Observer 事件的模拟 Hub"""

    def __init__(self):
        self.events: list = []

    async def broadcast_to_observers(self, target_agent_id: str, data: dict):
        """模拟 Server 的 broadcast_to_observers"""
        # 与 node.py 一致的信封格式
        seq = len(self.events) + 1
        envelope = {
            "cmd": data.get("cmd", "retry_event"),
            "seq": seq,
            "timestamp": time.time(),
            "agent_id": target_agent_id,
            "payload": data.get("event", data.get("payload", data)),
        }
        self.events.append(envelope)

    def clear(self):
        self.events.clear()

    def get_events_by_cmd(self, cmd: str) -> list:
        return [e for e in self.events if e.get("cmd") == cmd]

    def get_last_event(self) -> dict:
        return self.events[-1] if self.events else {}


def validate_envelope(event: dict, expected_cmd: str = None) -> list:
    """校验事件信封格式，返回错误列表"""
    errors = []
    required = ["cmd", "seq", "timestamp", "agent_id", "payload"]
    for field in required:
        if field not in event:
            errors.append(f"缺少字段: {field}")

    if expected_cmd and event.get("cmd") != expected_cmd:
        errors.append(f"cmd 不匹配: expected={expected_cmd}, got={event.get('cmd')}")

    if not isinstance(event.get("payload"), dict):
        errors.append(f"payload 应为 dict, got {type(event.get('payload'))}")

    if not isinstance(event.get("seq"), int):
        errors.append(f"seq 应为 int, got {type(event.get('seq'))}")

    if not isinstance(event.get("timestamp"), (int, float)):
        errors.append(f"timestamp 应为 number, got {type(event.get('timestamp'))}")

    return errors


def parse_observer_frame(raw_json: str) -> dict:
    """模拟 Observer 客户端解析 WebSocket 帧"""
    return json.loads(raw_json)


# ── 测试用例 ──────────────────────────────────────────

async def tc01_retry_event_format():
    """TC01: RetryEventEmitter → retry_event 格式"""
    print("\n=== TC01: retry_event 格式校验 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    await emitter.emit_retry("ZS0001", "msg-001", attempt=1, delay_seconds=10.0, from_agent="ZS0002")

    assert len(hub.events) == 1, f"期望 1 个事件, got {len(hub.events)}"
    event = hub.events[0]

    errors = validate_envelope(event, "retry_event")
    assert not errors, f"信封格式错误: {errors}"

    payload = event["payload"]
    assert payload["type"] == "retry", f"type 不匹配: {payload['type']}"
    assert payload["msg_id"] == "msg-001"
    assert payload["attempt"] == 1
    assert payload["delay_seconds"] == 10.0
    assert "next_retry_at" in payload
    assert payload["target_agent"] == "ZS0001"

    print("  ✅ retry_event 格式正确")
    return True


async def tc02_delivery_delivered_format():
    """TC02: delivery_event(delivered) 格式"""
    print("\n=== TC02: delivery_event(delivered) 格式校验 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    await emitter.emit_delivered("ZS0001", "msg-002", attempt=2, latency_ms=150.5)

    event = hub.events[0]
    errors = validate_envelope(event, "delivery_event")
    assert not errors, f"信封格式错误: {errors}"

    payload = event["payload"]
    assert payload["type"] == "delivered"
    assert payload["msg_id"] == "msg-002"
    assert payload["attempt"] == 2
    assert payload["latency_ms"] == 150.5
    assert payload["target_agent"] == "ZS0001"

    print("  ✅ delivery_event(delivered) 格式正确")
    return True


async def tc03_delivery_expired_format():
    """TC03: delivery_event(expired) 格式"""
    print("\n=== TC03: delivery_event(expired) 格式校验 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    await emitter.emit_expired("ZS0001", "msg-003", reason="max_retries_exceeded", attempts=3, from_agent="ZS0002")

    event = hub.events[0]
    errors = validate_envelope(event, "delivery_event")
    assert not errors, f"信封格式错误: {errors}"

    payload = event["payload"]
    assert payload["type"] == "expired"
    assert payload["reason"] == "max_retries_exceeded"
    assert payload["total_attempts"] == 3
    assert payload["target_agent"] == "ZS0001"

    print("  ✅ delivery_event(expired) 格式正确")
    return True


async def tc04_delivery_unreachable_format():
    """TC04: delivery_event(unreachable) 格式"""
    print("\n=== TC04: delivery_event(unreachable) 格式校验 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    await emitter.emit_unreachable("ZS0001", "msg-004", transition="to_offline_cache")

    event = hub.events[0]
    errors = validate_envelope(event, "delivery_event")
    assert not errors, f"信封格式错误: {errors}"

    payload = event["payload"]
    assert payload["type"] == "unreachable"
    assert payload["transition"] == "to_offline_cache"
    assert payload["target_agent"] == "ZS0001"

    print("  ✅ delivery_event(unreachable) 格式正确")
    return True


async def tc05_cache_recovered_format():
    """TC05: cache_event(recovered) 格式"""
    print("\n=== TC05: cache_event(recovered) 格式校验 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    await emitter.emit_recovered("ZS0001", cached_count=5, cached_msg_ids=["m1","m2","m3","m4","m5"], flush_duration_ms=120.3)

    event = hub.events[0]
    errors = validate_envelope(event, "cache_event")
    assert not errors, f"信封格式错误: {errors}"

    payload = event["payload"]
    assert payload["type"] == "recovered"
    assert payload["target_agent"] == "ZS0001"
    assert payload["cached_count"] == 5
    assert len(payload["cached_msg_ids"]) == 5
    assert payload["flush_duration_ms"] == 120.3

    print("  ✅ cache_event(recovered) 格式正确")
    return True


async def tc06_cache_overflow_format():
    """TC06: cache_event(cache_overflow) 格式"""
    print("\n=== TC06: cache_event(cache_overflow) 格式校验 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    await emitter.emit_cache_overflow("ZS0001", "msg-old-999", cache_size=100)

    event = hub.events[0]
    errors = validate_envelope(event, "cache_event")
    assert not errors, f"信封格式错误: {errors}"

    payload = event["payload"]
    assert payload["type"] == "cache_overflow"
    assert payload["target_agent"] == "ZS0001"
    assert payload["dropped_msg_id"] == "msg-old-999"
    assert payload["cache_size"] == 100

    print("  ✅ cache_event(cache_overflow) 格式正确")
    return True


async def tc07_offline_cache_integration():
    """TC07: RetryManager 离线缓存 + 事件

    注意：初始投递时目标离线 → 直接入缓存，不触发 unreachable 事件。
    unreachable 事件仅在重试过程中发现目标离线时触发（目标从在线变为离线）。
    """
    print("\n=== TC07: 离线缓存集成 ===")
    hub = CollectedEvents()
    delivered = []

    async def mock_deliver(msg, agent_id):
        delivered.append((msg, agent_id))
        return True

    def mock_get_conn(agent_id):
        return None  # 全部离线

    mgr = RetryManager(hub_ref=hub)
    mgr.set_callbacks(do_deliver=mock_deliver, get_connection=mock_get_conn)

    # 发送 3 条消息 → 全部进缓存（初始离线不触发 unreachable）
    for i in range(3):
        result = await mgr.deliver_with_retry(
            {"msg_id": f"msg-{i:03d}", "content": f"hello {i}"},
            "ZS0001",
            from_agent="ZS0002"
        )
        assert result["status"] == "cached", f"期望 cached, got {result['status']}"

    # 验证缓存
    cache_status = mgr.offline_cache.get_queue_size("ZS0001")
    assert cache_status["total"] == 3, f"期望缓存 3 条, got {cache_status['total']}"

    # 初始离线不触发 unreachable 事件（这是正确的设计行为）
    unreachable_events = hub.get_events_by_cmd("delivery_event")
    unreachable_events = [e for e in unreachable_events if e["payload"].get("type") == "unreachable"]
    assert len(unreachable_events) == 0, f"初始离线不应触发 unreachable, got {len(unreachable_events)}"

    print("  ✅ 离线缓存正确（初始离线不触发 unreachable，符合设计）")
    return True


async def tc07b_unreachable_during_retry():
    """TC07b: 重试过程中目标离线 → 触发 unreachable 事件

    注意：_retry_loop 中先检查连接再投递。当连接在重试期间断开时，
    get_connection 返回 None → 走 unreachable 路径。
    """
    print("\n=== TC07b: 重试中离线 → unreachable ===")
    hub = CollectedEvents()
    delivered = []
    conn_available = True

    async def mock_deliver(msg, agent_id):
        delivered.append(msg.get("msg_id", ""))
        # 模拟投递失败（连接已断）
        return conn_available

    def mock_get_conn(agent_id):
        return {"ws": "mock"} if conn_available else None

    # 用极短退避加速测试
    mgr = RetryManager(hub_ref=hub, config={"backoff_seconds": [0.1, 0.2], "jitter": 0.0, "max_retries": 2})
    mgr.set_callbacks(do_deliver=mock_deliver, get_connection=mock_get_conn)

    # 在线投递（初始投递成功）
    result = await mgr.deliver_with_retry({"msg_id": "msg-conn-01", "content": "test"}, "ZS0001")
    assert result["status"] == "delivering"

    # 投递后目标立即离线（在第一次重试前）
    conn_available = False

    # 等待重试循环完成（第一次退避 0.1s + 第二次 0.2s + 余量）
    await asyncio.sleep(1.0)

    # 验证 unreachable 事件
    delivery_events = hub.get_events_by_cmd("delivery_event")
    unreachable = [e for e in delivery_events if e["payload"].get("type") == "unreachable"]
    assert len(unreachable) >= 1, f"重试中离线应触发 unreachable, got {len(unreachable)}"

    print("  ✅ 重试中离线触发 unreachable 事件正确")
    return True


async def tc08_recovered_integration():
    """TC08: Agent 恢复 → recovered 事件"""
    print("\n=== TC08: Agent 恢复集成 ===")
    hub = CollectedEvents()
    delivered = []

    async def mock_deliver(msg, agent_id):
        delivered.append(msg.get("msg_id", ""))
        return True

    def mock_get_conn(agent_id):
        return {"ws": "mock"}  # 在线

    mgr = RetryManager(hub_ref=hub)
    mgr.set_callbacks(do_deliver=mock_deliver, get_connection=mock_get_conn)

    # 先缓存 5 条消息
    mgr_no_conn = RetryManager(hub_ref=hub)
    mgr_no_conn.set_callbacks(do_deliver=mock_deliver, get_connection=lambda a: None)
    for i in range(5):
        await mgr_no_conn.deliver_with_retry(
            {"msg_id": f"cached-{i:03d}", "content": f"msg {i}"},
            "ZS0001"
        )

    # 用 mgr_no_conn 的缓存
    mgr.offline_cache = mgr_no_conn.offline_cache

    hub.clear()

    # 恢复
    result = await mgr.on_agent_recovered("ZS0001")
    assert result["cached_count"] == 5, f"期望 5 条, got {result['cached_count']}"
    assert result["flushed"] == 5, f"期望 flushed 5, got {result['flushed']}"

    # 验证 recovered 事件
    cache_events = hub.get_events_by_cmd("cache_event")
    recovered = [e for e in cache_events if e["payload"].get("type") == "recovered"]
    assert len(recovered) == 1, f"期望 1 个 recovered 事件, got {len(recovered)}"
    assert recovered[0]["payload"]["cached_count"] == 5

    print("  ✅ Agent 恢复 + recovered 事件正确")
    return True


async def tc09_retry_sequence():
    """TC09: 多次重试 → 事件序列完整性"""
    print("\n=== TC09: 重试事件序列 ===")
    hub = CollectedEvents()

    policy = RetryPolicy(backoff_seconds=[0.1, 0.2, 0.3], jitter=0.0, max_retries=3)
    emitter = RetryEventEmitter(hub_ref=hub)

    # 模拟 3 次重试 + 过期
    for i in range(3):
        await emitter.emit_retry("ZS0001", "msg-seq-01", attempt=i+1, delay_seconds=policy.backoff_seconds[i])

    await emitter.emit_expired("ZS0001", "msg-seq-01", reason="max_retries_exceeded", attempts=3)

    # 验证事件序列
    assert len(hub.events) == 4, f"期望 4 个事件, got {len(hub.events)}"

    cmds = [e["cmd"] for e in hub.events]
    types = [e["payload"]["type"] for e in hub.events]

    assert cmds == ["retry_event", "retry_event", "retry_event", "delivery_event"]
    assert types == ["retry", "retry", "retry", "expired"]

    # 验证 seq 递增
    seqs = [e["seq"] for e in hub.events]
    assert seqs == sorted(seqs), f"seq 应递增: {seqs}"
    assert len(set(seqs)) == len(seqs), f"seq 不应重复: {seqs}"

    print("  ✅ 重试事件序列完整且有序")
    return True


async def tc10_observer_frame_parsing():
    """TC10: Observer 消息解析端到端"""
    print("\n=== TC10: Observer 帧解析 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    # 生成各类事件
    await emitter.emit_retry("ZS0001", "msg-parse-01", attempt=1, delay_seconds=10.0)
    await emitter.emit_delivered("ZS0001", "msg-parse-01", attempt=2, latency_ms=50.0)
    await emitter.emit_recovered("ZS0001", cached_count=3)
    await emitter.emit_cache_overflow("ZS0001", "old-msg", cache_size=100)

    # 模拟 WebSocket 帧 → JSON → 解析
    for event in hub.events:
        raw = json.dumps(event, ensure_ascii=False)
        parsed = parse_observer_frame(raw)

        # 校验 roundtrip
        assert parsed["cmd"] == event["cmd"], f"cmd 不匹配"
        assert parsed["seq"] == event["seq"], f"seq 不匹配"
        assert parsed["agent_id"] == event["agent_id"], f"agent_id 不匹配"
        assert isinstance(parsed["payload"], dict), f"payload 应为 dict"

    print(f"  ✅ {len(hub.events)} 个事件 JSON roundtrip 全部正确")
    return True


async def tc11_suspect_tracker_integration():
    """TC11: SuspectTracker 状态机集成"""
    print("\n=== TC11: SuspectTracker 状态机 ===")
    tracker = SuspectTracker(suspect_ttl_ms=500)  # 500ms TTL 加快测试

    # online → suspect
    tracker.mark_unreachable("ZS0001")
    assert tracker.is_suspect("ZS0001")
    assert not tracker.is_suspect("ZS0002")

    # suspect 清除
    duration = tracker.clear_suspect("ZS0001")
    assert duration is not None
    assert duration >= 0
    assert not tracker.is_suspect("ZS0001")

    # suspect → dead (TTL 超时)
    tracker.mark_unreachable("ZS0003")
    await asyncio.sleep(0.6)  # 等待 TTL 过期
    dead = tracker.check_expired()
    assert "ZS0003" in dead, f"ZS0003 应该 dead, got {dead}"

    print("  ✅ SuspectTracker 状态机正确")
    return True


async def tc12_seq_dedup_integration():
    """TC12: SeqDeduplicator 去重集成"""
    print("\n=== TC12: SeqDeduplicator 去重 ===")
    dedup = SeqDeduplicator(window_size=5)

    # 首次不重复
    assert not dedup.is_duplicate("ZS0001", "seq-1")
    dedup.mark_seen("ZS0001", "seq-1")

    # 重复检测
    assert dedup.is_duplicate("ZS0001", "seq-1")
    assert not dedup.is_duplicate("ZS0001", "seq-2")

    # 不同 Agent 独立
    assert not dedup.is_duplicate("ZS0002", "seq-1")

    # 窗口淘汰
    for i in range(2, 7):
        dedup.mark_seen("ZS0001", f"seq-{i}")
    # seq-1 应被淘汰（窗口大小 5）
    assert not dedup.is_duplicate("ZS0001", "seq-1"), "seq-1 应已被淘汰"

    print("  ✅ SeqDeduplicator 去重正确")
    return True


async def tc13_broadcast_envelope_format():
    """TC13: broadcast_to_observers 事件信封格式"""
    print("\n=== TC13: broadcast 信封格式 ===")
    hub = CollectedEvents()
    emitter = RetryEventEmitter(hub_ref=hub)

    # 生成 3 种不同类型的事件
    await emitter.emit_retry("ZS0001", "msg-001", attempt=1, delay_seconds=10.0)
    await emitter.emit_delivered("ZS0001", "msg-001", attempt=1, latency_ms=100.0)
    await emitter.emit_recovered("ZS0001", cached_count=2)

    # 校验每个事件的信封
    for i, event in enumerate(hub.events):
        errors = validate_envelope(event)
        assert not errors, f"事件 {i} 信封错误: {errors}"

        # 信封字段类型
        assert isinstance(event["cmd"], str)
        assert isinstance(event["seq"], int) and event["seq"] > 0
        assert isinstance(event["timestamp"], float) and event["timestamp"] > 0
        assert isinstance(event["agent_id"], str)
        assert isinstance(event["payload"], dict)

    # seq 应递增
    seqs = [e["seq"] for e in hub.events]
    assert seqs == sorted(seqs), f"seq 应递增: {seqs}"

    print(f"  ✅ {len(hub.events)} 个事件信封格式全部正确")
    return True


# ── 主函数 ──────────────────────────────────────────

async def run_all():
    """运行所有测试"""
    tests = [
        tc01_retry_event_format,
        tc02_delivery_delivered_format,
        tc03_delivery_expired_format,
        tc04_delivery_unreachable_format,
        tc05_cache_recovered_format,
        tc06_cache_overflow_format,
        tc07_offline_cache_integration,
        tc07b_unreachable_during_retry,
        tc08_recovered_integration,
        tc09_retry_sequence,
        tc10_observer_frame_parsing,
        tc11_suspect_tracker_integration,
        tc12_seq_dedup_integration,
        tc13_broadcast_envelope_format,
    ]

    passed = 0
    failed = 0
    errors = []

    for test in tests:
        try:
            ok = await test()
            if ok:
                passed += 1
            else:
                failed += 1
                errors.append(f"{test.__name__}: returned False")
        except Exception as e:
            failed += 1
            errors.append(f"{test.__name__}: {e}")
            print(f"  ❌ FAILED: {e}")

    print("\n" + "=" * 60)
    print(f"📊 Observer 集成测试结果: {passed}/{passed + failed} 通过")
    if errors:
        print("\n❌ 失败用例:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("✅ 全部通过！")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_all())
    sys.exit(0 if ok else 1)
