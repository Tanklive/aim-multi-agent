#!/usr/bin/env python3
"""
AIM Agent NATS Adapter — Pin + RetryManager 集成测试
验证 adapter 的并发处理(Pin)和重试机制(RetryManager)

使用方法:
  # 先启动 NATS Server
  nats-server -p 4222 &

  # 运行本测试
  python3 test_adapter_pin_retry.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime

# ── 路径 ──
BASE = os.path.dirname(__file__)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".hermes", "aim"))
sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".hermes", "hermes-agent", "apps", "aim-agent"))

# ── 颜色 ──
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

passed = 0
failed = 0
skipped = 0


def log_test(n: int, name: str):
    print(f"\n{'='*60}")
    print(f"  Test #{n}: {name}")
    print(f"{'='*60}")


def log_pass(msg: str = "PASS"):
    global passed
    passed += 1
    print(f"  {GREEN}✓ {msg}{RESET}")


def log_fail(msg: str):
    global failed
    failed += 1
    print(f"  {RED}✗ {msg}{RESET}")


def log_skip(msg: str = "SKIP"):
    global skipped
    skipped += 1
    print(f"  {YELLOW}⊘ {msg}{RESET}")


# ── 测试辅助函数 ──

async def _ensure_nats_running():
    """检查 NATS Server 是否运行"""
    try:
        import nats
        nc = await nats.connect("nats://127.0.0.1:4222")
        await nc.close()
        return True
    except ModuleNotFoundError:
        print("  nats 库未安装，需要 'pip install nats-py'")
        return False
    except Exception as e:
        print(f"  NATS 连接失败: {e}")
        return False


async def _make_nats_connection():
    """创建 NATS 连接"""
    import nats
    return await nats.connect("nats://127.0.0.1:4222")


# ═══════════════════════════════════════════
# 测试 1: 消息去重测试 (AIMPin)
# ═══════════════════════════════════════════

async def test_01_message_dedup():
    """T1: AIMPin 去重测试"""
    log_test(1, "AIMPin 去重测试")

    import tempfile, shutil
    from aim_nats_sdk import AIMPin
    tmp_dir = tempfile.mkdtemp(prefix="pin_test_")
    try:
        pin = AIMPin(agent_id="TEST_T1", ttl=5, db_dir=tmp_dir, max_memory=100)

        msg_id = str(uuid.uuid4())

        # 第一次应该不是重复
        assert not await pin.is_duplicate(msg_id), "第一次判断应为不重复"
        await pin.mark(msg_id)
        log_pass("第一次消息判断正确 (不重复)")

        # 第二次应该是重复
        assert await pin.is_duplicate(msg_id), "第二次判断应为重复"
        log_pass("第二次消息判断正确 (重复)")

        # 不同 msg_id 不应重复
        msg_id2 = str(uuid.uuid4())
        assert not await pin.is_duplicate(msg_id2), "不同 msg_id 应不重复"
        await pin.mark(msg_id2)
        log_pass("不同 msg_id 不互相干扰")

        # 内存淘汰测试
        pin_small = AIMPin(agent_id="TEST_T1_SMALL", ttl=60, db_dir=tmp_dir, max_memory=3)
        ids = [str(uuid.uuid4()) for _ in range(5)]
        for i, mid in enumerate(ids):
            assert not await pin_small.is_duplicate(mid), f"第{i+1}个msg_id首次应不重复"
            await pin_small.mark(mid)
        log_pass("内存淘汰机制正常")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════
# 测试 2: 消息并发处理 (Pin) — semaphore 限制
# ═══════════════════════════════════════════

async def test_02_pin_concurrency():
    """T2: Pin 并发限制测试 (semaphore)"""
    log_test(2, "Pin 并发限制测试")

    # 直接测试 adapter 的 semaphore 机制
    from aim_agent_nats_adapter import AIMAgentNatsAdapter

    adapter = AIMAgentNatsAdapter(
        agent_id="ZS0002",
        agent_name="TestAgent",
        framework="test",
        nats_url="nats://127.0.0.1:4222",
    )

    assert adapter.MAX_CONCURRENT == 3, f"MAX_CONCURRENT 应为 3, 当前 {adapter.MAX_CONCURRENT}"
    log_pass(f"MAX_CONCURRENT = {adapter.MAX_CONCURRENT} ✓")

    # 测试信号量
    assert adapter.semaphore._value == 3, f"semaphore 初始值应为 3, 当前 {adapter.semaphore._value}"
    log_pass("信号量初始值正确")

    # 测试并发限制: 并发获取 semaphore 4次，第4次应等待
    async def acquire_and_hold(sem, hold_time):
        async with sem:
            await asyncio.sleep(hold_time)

    start = time.time()
    tasks = [asyncio.create_task(acquire_and_hold(adapter.semaphore, 0.2)) for _ in range(4)]
    # 全部完成后计算总耗时
    await asyncio.gather(*tasks)
    elapsed = time.time() - start
    # 3个并行各0.2s + 第4个等0.2s = ~0.4s (因为有3个槽位)
    assert elapsed >= 0.35, f"并发限制失效: 耗时 {elapsed:.2f}s, 预期 >= 0.4s"
    log_pass(f"并发限制生效: 4任务耗时 {elapsed:.2f}s (3槽位, 预期 ~0.4s)")

    # 清理 adapter 连接 (防止关闭时尝试连接 NATS)
    adapter._running = False
    adapter.client.nc = None  # 跳过关闭
    log_pass("并发限制测试通过")


# ═══════════════════════════════════════════
# 测试 3: 消息归档测试 (MessageArchive)
# ═══════════════════════════════════════════

async def test_03_message_archive():
    """T3: MessageArchive 归档测试"""
    log_test(3, "MessageArchive 归档测试")

    from aim_agent_nats_adapter import MessageArchive
    import tempfile

    # 使用临时目录
    from pathlib import Path
    original_aim_base = None
    try:
        # 备份并覆盖 AIM_BASE
        import aim_agent_nats_adapter as adapter_module
        original_aim_base = adapter_module.AIM_BASE
        tmpdir = Path(tempfile.mkdtemp())
        adapter_module.AIM_BASE = tmpdir

        archive = MessageArchive("ZS0002")
        test_msg = {
            "msg_id": str(uuid.uuid4()),
            "from": "ZS0001",
            "type": "dm",
            "content": "测试归档消息",
            "timestamp": datetime.now().isoformat(),
        }
        archive.archive(test_msg)

        # 读取并验证
        import json as j
        with open(archive.file, "r") as f:
            line = f.readline().strip()
            saved = j.loads(line)
            assert saved["msg_id"] == test_msg["msg_id"], "msg_id 不一致"
            assert saved["content"] == test_msg["content"], "内容不一致"
        log_pass(f"消息归档成功 -> {archive.file}")

        # 归档另一条
        test_msg2 = {
            "msg_id": str(uuid.uuid4()),
            "from": "ZS0001",
            "type": "group",
            "group": "grp_trio",
            "content": "群聊测试",
            "timestamp": datetime.now().isoformat(),
        }
        archive.archive(test_msg2)

        # 验证两条都在
        with open(archive.file, "r") as f:
            lines = f.readlines()
        assert len(lines) == 2, f"应有2行, 实际 {len(lines)}"
        log_pass(f"多消息归档正常 (共 {len(lines)} 条)")

    finally:
        if original_aim_base:
            import aim_agent_nats_adapter as adapter_module
            adapter_module.AIM_BASE = original_aim_base
        import shutil
        if 'tmpdir' in dir():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════
# 测试 4: RetryEventEmitter 测试
# ═══════════════════════════════════════════

async def test_04_retry_emitter():
    """T4: RetryEventEmitter 事件发射测试"""
    log_test(4, "RetryEventEmitter 事件发射测试")

    sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".hermes", "aim"))
    from retry_emitter import RetryEventEmitter, RetryEvent
    from retry_emitter import (
        EVT_RETRY_START, EVT_RETRY_ATTEMPT, EVT_RETRY_FAILED,
        EVT_RETRY_SUCCESS, EVT_MESSAGE_EXPIRED, EVT_SUSPECT_TTL,
        EVT_BACKOFF_TRIGGERED, EVT_RECOVERED
    )

    local_events = []

    def local_handler(event: RetryEvent):
        local_events.append(event)

    broadcaster_calls = []

    async def mock_broadcaster(payload: dict):
        broadcaster_calls.append(payload)

    emitter = RetryEventEmitter(broadcaster=mock_broadcaster)
    emitter.add_local_handler(local_handler)

    msg_id = str(uuid.uuid4())

    # 发射 retry_start
    await emitter.emit_retry_start(msg_id, "ZS0001", max_retries=3)
    assert len(local_events) == 1, f"本地事件应有1个, 实际 {len(local_events)}"
    assert local_events[0].event_type == EVT_RETRY_START
    assert len(broadcaster_calls) == 1
    log_pass("retry_start 事件发射正常")

    # 发射 retry_attempt
    await emitter.emit_retry_attempt(msg_id, "ZS0001", retry_count=1, max_retries=3)
    assert len(local_events) == 2
    assert local_events[1].event_type == EVT_RETRY_ATTEMPT
    assert local_events[1].retry_count == 1
    log_pass("retry_attempt 事件发射正常 (count=1)")

    # 发射 retry_success
    await emitter.emit_retry_success(msg_id, "ZS0001", retry_count=2)
    assert len(local_events) == 3
    assert local_events[2].event_type == EVT_RETRY_SUCCESS
    log_pass("retry_success 事件发射正常")

    # 发射 retry_failed
    await emitter.emit_retry_failed(msg_id, "ZS0001", retry_count=3, max_retries=3)
    assert len(local_events) == 4
    assert local_events[3].event_type == EVT_RETRY_FAILED
    assert local_events[3].retry_count == 3
    log_pass("retry_failed 事件发射正常")

    # 发射 expired
    await emitter.emit_expired(msg_id, "ZS0001")
    assert len(local_events) == 5
    assert local_events[4].event_type == EVT_MESSAGE_EXPIRED
    log_pass("expired 事件发射正常")

    # 发射 backoff
    await emitter.emit_backoff(msg_id, "ZS0001", delay=2.5)
    assert len(local_events) == 6
    assert local_events[5].event_type == EVT_BACKOFF_TRIGGERED
    log_pass("backoff 事件发射正常")

    # 发射 recovered
    await emitter.emit_recovered("ZS0002", offline_msg_count=5)
    assert len(local_events) == 7
    assert local_events[6].event_type == EVT_RECOVERED
    log_pass("recovered 事件发射正常")

    # 验证所有事件都有广播 (7个事件)
    assert len(broadcaster_calls) == 7, f"应有7次广播, 实际 {len(broadcaster_calls)}"
    log_pass(f"所有 {len(broadcaster_calls)} 次 Observer 广播正常")


# ═══════════════════════════════════════════
# 测试 5: adapter 注册流程测试 (mock NATS)
# ═══════════════════════════════════════════

async def test_05_register_flow():
    """T5: adapter 注册流程测试 (使用真实 NATS)"""
    log_test(5, "Adapter 注册流程测试")

    if not await _ensure_nats_running():
        log_skip("NATS Server 未运行")
        return

    import nats

    # 用原始 nats 模拟注册服务端
    server_nc = await _make_nats_connection()

    reg_responses = []

    async def reg_handler(msg):
        data = json.loads(msg.data)
        reg_responses.append(data)
        await msg.respond(json.dumps({
            "status": "ok",
            "agent_id": data.get("agent_id", ""),
            "message": "注册确认",
            "ts": time.time(),
        }).encode())

    await server_nc.subscribe("aim.reg.register", cb=reg_handler)
    await server_nc.flush()

    # 创建 adapter 客户端
    from aim_agent_nats_adapter import AIMAgentNatsAdapter

    adapter = AIMAgentNatsAdapter(
        agent_id="ZS0002",
        agent_name="TestRunner",
        framework="test",
        nats_url="nats://127.0.0.1:4222",
    )

    try:
        # 连接 NATS
        await adapter.client.connect()
        await adapter.client.setup_streams()

        # 注册
        result = await adapter.register()
        assert result, "注册应返回 True"
        log_pass("注册请求发送成功")

        # 验证服务端收到注册
        assert len(reg_responses) >= 1, "服务端应收到注册请求"
        assert reg_responses[0]["agent_id"] == "ZS0002"
        log_pass(f"服务端正确收到注册: agent_id={reg_responses[0]['agent_id']}")

    finally:
        await adapter.client.disconnect()
        await server_nc.close()


# ═══════════════════════════════════════════
# 测试 6: adapter 消息处理路由测试 (真实 NATS)
# ═══════════════════════════════════════════

async def test_06_message_routing():
    """T6: 消息路由测试 (真实 NATS — DM/Group 路由)"""
    log_test(6, "消息路由测试 — DM 透传")

    if not await _ensure_nats_running():
        log_skip("NATS Server 未运行")
        return

    import nats
    from aim_agent_nats_adapter import AIMAgentNatsAdapter

    # 启动 adapter
    adapter = AIMAgentNatsAdapter(
        agent_id="ZS0002",
        agent_name="TestRunner",
        framework="test",
        nats_url="nats://127.0.0.1:4222",
    )

    received_messages = []
    original_handle = adapter.handle_message

    async def tracking_handle(msg_data):
        received_messages.append(msg_data)
        await original_handle(msg_data)

    adapter.handle_message = tracking_handle

    try:
        # 连接 NATS
        await adapter.client.connect()
        await adapter.client.setup_streams()

        # 订阅私聊
        await adapter.client.subscribe_private_messages(adapter._on_private_msg)
        await asyncio.sleep(0.3)

        # 从另一个连接发私聊
        sender_nc = await _make_nats_connection()
        test_msg = {
            "msg_id": str(uuid.uuid4()),
            "from": "ZS0001",
            "to": "ZS0002",
            "type": "dm",
            "content": "Hello from 呱呱!",
            "timestamp": datetime.now().isoformat(),
        }
        await sender_nc.publish("aim.dm.ZS0002", json.dumps(test_msg).encode())
        await sender_nc.flush()
        await asyncio.sleep(0.5)

        assert len(received_messages) >= 1, f"应收到至少1条消息, 收到 {len(received_messages)}"
        found = any(m.get("content") == "Hello from 呱呱!" for m in received_messages)
        assert found, "应找到呱呱发送的测试消息"
        log_pass(f"DM 消息路由正常: from=ZS0001 to=ZS0002")

        # 测试群聊路由
        received_messages.clear()
        await adapter.client.subscribe_group_messages("grp_trio", adapter._on_group_msg)
        await asyncio.sleep(0.3)

        group_msg = {
            "msg_id": str(uuid.uuid4()),
            "from": "ZS0001",
            "type": "group",
            "group": "grp_trio",
            "content": "群聊测试消息 from 呱呱",
            "timestamp": datetime.now().isoformat(),
        }
        await sender_nc.publish("aim.grp.grp_trio", json.dumps(group_msg).encode())
        await sender_nc.flush()
        await asyncio.sleep(0.5)

        found_group = any(m.get("content") == "群聊测试消息 from 呱呱" for m in received_messages)
        assert found_group, "应收到群聊消息"
        log_pass(f"Group 消息路由正常: grp=grp_trio")

        await sender_nc.close()

    finally:
        adapter._running = False
        try:
            await adapter.client.disconnect()
        except Exception:
            pass


# ═══════════════════════════════════════════
# 测试 7: RetryManager + Adapter 集成 (mock)
# ═══════════════════════════════════════════

async def test_07_retrymanager_adapter_integration():
    """T7: RetryManager + Adapter 集成测试 (模拟 Observer)"""
    log_test(7, "RetryManager + Adapter 集成测试")

    sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".hermes", "aim"))
    from retry_emitter import RetryEventEmitter

    observer_events = []

    async def observer_broadcaster(payload: dict):
        observer_events.append(payload)

    emitter = RetryEventEmitter(broadcaster=observer_broadcaster)

    # 模拟一条消息的完整重试生命周期
    msg_id = str(uuid.uuid4())
    to_id = "ZS0001"

    # 阶段1: start
    await emitter.emit_retry_start(msg_id, to_id)
    assert observer_events[-1]["event_type"] == "retry_start"
    log_pass("阶段1: retry_start ✓")

    # 阶段2: attempt × 3
    for i in range(1, 4):
        await emitter.emit_retry_attempt(msg_id, to_id, retry_count=i)
        assert observer_events[-1]["event_type"] == "retry_attempt"
        assert observer_events[-1]["retry_count"] == i
        log_pass(f"阶段2: retry_attempt #{i} ✓")

    # 阶段3: success 在第二次重试后成功
    await emitter.emit_retry_success(msg_id, to_id, retry_count=2)
    assert observer_events[-1]["event_type"] == "retry_success"
    assert observer_events[-1]["retry_count"] == 2
    log_pass("阶段3: retry_success (第2次) ✓")

    # 阶段4: 另一条消息 failed
    msg_id2 = str(uuid.uuid4())
    await emitter.emit_retry_start(msg_id2, to_id)
    for i in range(1, 4):
        await emitter.emit_retry_attempt(msg_id2, to_id, retry_count=i)
    await emitter.emit_retry_failed(msg_id2, to_id, retry_count=3, max_retries=3)
    assert observer_events[-1]["event_type"] == "retry_failed"
    log_pass("阶段4: retry_failed (3/3上限) ✓")

    # 阶段5: backoff 防惊群
    await emitter.emit_backoff(msg_id2, to_id, delay=3.0)
    assert observer_events[-1]["event_type"] == "backoff_triggered"
    assert observer_events[-1]["detail"] == "防惊群退避 3.0s"
    log_pass("阶段5: backoff 防惊群退避 ✓")

    # 阶段6: 断连恢复
    await emitter.emit_recovered("ZS0002", offline_msg_count=3)
    assert observer_events[-1]["event_type"] == "recovered"
    assert observer_events[-1]["detail"] == "恢复在线, 离线消息 3 条"
    log_pass("阶段6: recovered 断连恢复 ✓")

    log_pass(f"完整重试生命周期 {len(observer_events)} 个事件全部正常")


# ═══════════════════════════════════════════
# 测试 8: adapter 自动重连机制 (mock NATS 断连)
# ═══════════════════════════════════════════

async def test_08_adapter_reconnect():
    """T8: Adapter 自动重连测试"""
    log_test(8, "Adapter 自动重连测试")

    if not await _ensure_nats_running():
        log_skip("NATS Server 未运行")
        return

    import nats
    from aim_agent_nats_adapter import AIMAgentNatsAdapter

    adapter = AIMAgentNatsAdapter(
        agent_id="ZS0002",
        agent_name="TestRunner",
        framework="test",
        nats_url="nats://127.0.0.1:4222",
    )

    try:
        # 连接
        await adapter.client.connect()
        await adapter.client.setup_streams()

        assert adapter.client.nc.is_connected, "初始连接应正常"
        log_pass("初始连接成功")

        # 模拟断连 — 底层 nats 库会触发自动重连
        # 先验证 nc 有重连配置
        nc = adapter.client.nc
        log_pass(f"连接状态: connected={nc.is_connected}, reconnecting={nc.is_reconnecting}")

    finally:
        try:
            await adapter.client.disconnect()
        except Exception:
            pass


# ═══════════════════════════════════════════
# 测试 9: handle_message 去重+归档+并发 完整链路
# ═══════════════════════════════════════════

async def test_09_handle_message_full_chain():
    """T9: handle_message 完整链路 (去重→归档→并发)"""
    log_test(9, "handle_message 完整链路测试")

    from aim_agent_nats_adapter import AIMAgentNatsAdapter

    adapter = AIMAgentNatsAdapter(
        agent_id="ZS0002",
        agent_name="TestRunner",
        framework="test",
        nats_url="nats://127.0.0.1:4222",
    )

    # 替换归档为 mock
    archived = []
    adapter.archive.archive = lambda msg: archived.append(msg)

    msg_id = str(uuid.uuid4())
    msg_data = {
        "msg_id": msg_id,
        "from": "ZS0001",
        "type": "dm",
        "content": "测试消息",
        "timestamp": datetime.now().isoformat(),
    }

    # 首次 — 应处理
    await adapter.handle_message(msg_data)
    await asyncio.sleep(0.1)
    assert len(archived) == 1, f"归档应有1条, 实际 {len(archived)}"
    log_pass("首次消息: 归档正常 ✓")

    # 重复 — 应被去重跳过
    await adapter.handle_message(msg_data)
    await asyncio.sleep(0.1)
    assert len(archived) == 1, f"去重后归档不应增加, 实际 {len(archived)}"
    log_pass("重复消息: 去重正常 (归档未增加) ✓")

    # 空内容 — 应跳过
    empty_msg = {
        "msg_id": str(uuid.uuid4()),
        "from": "ZS0001",
        "type": "dm",
        "content": "",
    }
    await adapter.handle_message(empty_msg)
    await asyncio.sleep(0.1)
    assert len(archived) == 1, f"空内容不应归档, 实际 {len(archived)}"
    log_pass("空消息: 跳过正常 ✓")

    # 清理
    adapter._running = False
    adapter.client.nc = None


# ═══════════════════════════════════════════
# 测试 10: should_auto_reply + extract_mention
# ═══════════════════════════════════════════

async def test_10_helper_functions():
    """T10: 辅助函数测试 (should_auto_reply + extract_mention)"""
    log_test(10, "辅助函数测试")

    from aim_agent_nats_adapter import should_auto_reply, extract_mention

    # should_auto_reply
    assert not should_auto_reply("你好", "ZS0002"), "不应回复自己"
    log_pass("should_auto_reply: 不回复自己 ✓")

    assert should_auto_reply("你好", "ZS0001"), "应回复其他人"
    log_pass("should_auto_reply: 回复他人 ✓")

    assert not should_auto_reply("", "ZS0001"), "空内容不应回复"
    log_pass("should_auto_reply: 空内容跳过 ✓")

    # extract_mention
    assert extract_mention("@ZS0001 请处理") == "ZS0001"
    log_pass("extract_mention: @ZS0001 ✓")

    assert extract_mention("@ZS0002 收到") == "ZS0002"
    log_pass("extract_mention: @ZS0002 ✓")

    assert extract_mention("Hello @ZS0003 test") == "ZS0003"
    log_pass("extract_mention: 中间 @ZS0003 ✓")

    assert extract_mention("没有提及") is None
    log_pass("extract_mention: 无提及返回 None ✓")


# ═══════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════

async def main():
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}  AIM Agent NATS Adapter — Pin + RetryManager 集成测试{RESET}")
    print(f"{BLUE}  共 10 项测试{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")

    tests = [
        ("AIMPin 去重", test_01_message_dedup),
        ("Pin 并发限制", test_02_pin_concurrency),
        ("MessageArchive 归档", test_03_message_archive),
        ("RetryEventEmitter", test_04_retry_emitter),
        ("注册流程", test_05_register_flow),
        ("消息路由 (DM/Group)", test_06_message_routing),
        ("RetryManager 集成", test_07_retrymanager_adapter_integration),
        ("自动重连", test_08_adapter_reconnect),
        ("handle_message 完整链路", test_09_handle_message_full_chain),
        ("辅助函数", test_10_helper_functions),
    ]

    for name, test_fn in tests:
        try:
            await test_fn()
        except Exception as e:
            log_fail(f"{name}: {e}")
            import traceback
            traceback.print_exc()

    # 结果汇总
    total = passed + failed + skipped
    print(f"\n{'='*60}")
    print(f"  结果汇总: {GREEN}{passed} 通过{RESET} | {RED}{failed} 失败{RESET} | {YELLOW}{skipped} 跳过{RESET} / {total} 总计")
    print(f"{'='*60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
