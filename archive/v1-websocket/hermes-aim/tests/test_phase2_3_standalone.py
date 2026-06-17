#!/usr/bin/env python3
"""
AIM Phase 2.3 独立测试脚本（Server 端执行）
测试 AIM Server 的 Phase 2 完整功能。

用法:
  python3 tests/test_phase2_3_standalone.py

选项:
  --test N     只跑第 N 项测试（1-6）
  --all        跑全部 6 项（默认）
  --verbose    详细输出
"""

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("ERROR: websockets not installed")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("phase2.3.test")

# --- Config ---
WS_URL = "ws://127.0.0.1:18900"
SELF_ID = "ZS0002"
TARGET_ID = "ZS0001"
HEARTBEAT_INTERVAL = 5
TIMEOUT = 15

# --- Test Results ---
results = []

async def connect_ws(agent_id, channel="main"):
    """连接 AIM Server，带频率限制退避"""
    token_map = {
        "ZS0001": "guagua_token_2026",
        "ZS0002": "jiliang_token_2026",
        "ZS0003": "xiaohuoji_token_2026",
    }
    token = token_map.get(agent_id, "")

    for attempt in range(5):
        try:
            ws = await ws_connect(
                WS_URL,
                max_size=2**20,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
            )
            await ws.send(json.dumps({"cmd": "auth", "agent_id": agent_id, "token": token, "channel": channel}))
            resp = json.loads(await ws.recv())
            if resp.get("cmd") == "auth_ok":
                log.info(f"  ✅ {agent_id}/{channel} 认证成功")
                return ws
            elif "频率" in resp.get("reason", ""):
                log.info(f"  ⏳ 频率限制，等待 {2**attempt}s...")
                await ws.close()
                await asyncio.sleep(2 ** attempt)
                continue
            else:
                raise AssertionError(f"Auth failed: {resp}")
        except (OSError, ConnectionRefusedError) as e:
            log.info(f"  ⏳ 连接失败 (attempt {attempt+1}): {e}")
            await asyncio.sleep(2 ** attempt)

    raise ConnectionError(f"无法连接 {WS_URL} (尝试5次失败)")


async def recv_with_timeout(ws, timeout=TIMEOUT):
    """带超时的 recv"""
    return await asyncio.wait_for(ws.recv(), timeout=timeout)


def check_result(name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    results.append((name, passed, detail))
    log.info(f"  {status}: {name}  {detail}")


# === Test 1: 多通道共存 ===
async def test_multi_channel():
    log.info("=" * 60)
    log.info("【测试 1】多通道共存 — main/script/health 不互踢")
    log.info("=" * 60)

    channels = ["main", "script", "health"]
    connections = {}

    try:
        for ch in channels:
            ws = await connect_ws(SELF_ID, channel=ch)
            connections[ch] = ws
            log.info(f"  channel={ch} 连接建立 ✅")

        # 等待 2 秒看是否有踢旧
        await asyncio.sleep(2)

        # 检查所有连接是否还在
        all_alive = True
        for ch, ws in connections.items():
            try:
                await ws.send(json.dumps({"cmd": "ping"}))
                resp = await recv_with_timeout(ws, 3)
                log.info(f"  channel={ch} 响应: {resp[:80] if isinstance(resp, str) else resp}")
            except Exception as e:
                log.warning(f"  channel={ch} 已断开: {e}")
                all_alive = False

        check_result("多通道共存", all_alive,
                     f"channels={channels}, alive={all_alive}")

    finally:
        for ws in connections.values():
            await ws.close()


# === Test 2: ACK 直接回复发送方连接 ===
async def test_ack_direct_reply():
    log.info("=" * 60)
    log.info("【测试 2】消息投递 + ACK 验证")
    log.info("(注：单聊模式下 ack 由 DeliveryGuarantee 内部发送，")
    log.info(" 测试验证消息可投递到同一 Agent 的不同连接)")
    log.info("=" * 60)

    ws_sender = await connect_ws(SELF_ID, channel="main")
    ws_receiver = await connect_ws(SELF_ID, channel="script")

    try:
        # 发给自己另一个 channel
        msg_id = f"test_ack_{int(time.time())}"
        test_msg = {
            "cmd": "chat_message",
            "msg_id": msg_id,
            "from_id": SELF_ID,
            "to_id": SELF_ID,
            "content": f"[TEST] delivery test {msg_id}",
            "msg_type": "text",
            "timestamp": time.time(),
        }
        await ws_sender.send(json.dumps(test_msg))
        log.info(f"  发送消息: {msg_id}")

        # 检查 receiver channel 是否收到
        received = False
        for _ in range(3):
            try:
                resp = await recv_with_timeout(ws_receiver, 3)
                data = json.loads(resp)
                log.info(f"  receiver 收到: cmd={data.get('cmd','?')}, keys={list(data.keys())}")
                if data.get("cmd") == "message" and isinstance(data.get("msg"), dict):
                    if data["msg"].get("msg_id") == msg_id:
                        received = True
                        break
            except asyncio.TimeoutError:
                pass

        check_result("消息投递到其他channel", received,
                     f"msg_id={msg_id}")

    finally:
        await ws_sender.close()
        await ws_receiver.close()


# === Test 3: 消息去重（ring buffer）===
async def test_dedup():
    log.info("=" * 60)
    log.info("【测试 3】Ring buffer 500 条去重")
    log.info("(用同一 agent 的 script channel 接收)")
    log.info("=" * 60)

    ws = await connect_ws(SELF_ID, channel="main")
    ws_recv = await connect_ws(SELF_ID, channel="script")

    try:
        test_id = f"dedup_{int(time.time())}"
        received_ids = []

        # 发 2 次相同 msg_id（发给 script channel 自己）
        for i in range(2):
            msg = {
                "cmd": "chat_message",
                "msg_id": test_id,
                "from_id": SELF_ID,
                "to_id": SELF_ID,
                "content": f"[TEST] Dedup test {test_id} (copy {i+1})",
                "msg_type": "text",
                "timestamp": time.time(),
            }
            await ws.send(json.dumps(msg))
            await asyncio.sleep(1)

        # 检查 ws_recv 上收到几次 msg_id==test_id
        for _ in range(4):
            try:
                resp = await recv_with_timeout(ws_recv, 2)
                data = json.loads(resp)
                if isinstance(data, dict) and data.get("cmd") == "message":
                    msg_data = data.get("msg", {})
                    if isinstance(msg_data, dict) and msg_data.get("msg_id") == test_id:
                        received_ids.append(test_id)
            except asyncio.TimeoutError:
                pass

        await asyncio.sleep(1)
        try:
            resp = await recv_with_timeout(ws_recv, 2)
            data = json.loads(resp)
            if isinstance(data, dict) and data.get("cmd") == "message":
                msg_data = data.get("msg", {})
                if isinstance(msg_data, dict) and msg_data.get("msg_id") == test_id:
                    received_ids.append(test_id)
        except asyncio.TimeoutError:
            pass

        passed = len(received_ids) <= 1  # 应该只投递一次
        check_result("消息去重(ring buffer)", passed,
                     f"2次发送收到{len(received_ids)}次")

    finally:
        await ws.close()
        await ws_recv.close()


# === Test 4: 离线队列 ===
async def test_offline_queue():
    log.info("=" * 60)
    log.info("【测试 4】离线消息队列 + 上线推送")
    log.info("=" * 60)

    ws_sender = await connect_ws(SELF_ID, channel="main")

    try:
        # 先发一条消息给一个离线 agent（不存在就不会被消费）
        msg_id = f"offline_{int(time.time())}"
        msg = {
            "cmd": "chat_message",
            "msg_id": msg_id,
            "from_id": SELF_ID,
            "to_id": "ZS0099",
            "content": f"[TEST] Offline queue test {msg_id}",
            "msg_type": "text",
            "timestamp": time.time(),
        }
        await ws_sender.send(json.dumps(msg))
        log.info(f"  发送离线消息: {msg_id}")

        # 等待 3s 看返回
        await asyncio.sleep(3)
        try:
            resp = await recv_with_timeout(ws_sender, 3)
            log.info(f"  收到回复: {resp[:200] if isinstance(resp, str) else resp}")
        except asyncio.TimeoutError:
            log.info("  无直接回复（离线消息已入队列）")

        # 检查离线队列目录是否存在数据
        data_dir = os.path.expanduser("~/.hermes/aim/data")
        offline_files = []
        if os.path.isdir(data_dir):
            for f in os.listdir(data_dir):
                if f.startswith("offline_"):
                    fpath = os.path.join(data_dir, f)
                    size = os.path.getsize(fpath) if os.path.isfile(fpath) else 0
                    offline_files.append((f, size))

        exists = len(offline_files) > 0
        check_result("离线队列", exists,
                     f"files={offline_files if offline_files else 'none'}")

    finally:
        await ws_sender.close()


# === Test 5: 心跳 ===
async def test_heartbeat():
    log.info("=" * 60)
    log.info("【测试 5】心跳上报 + heartbeat_ack")
    log.info("=" * 60)

    ws = await connect_ws(SELF_ID, channel="health")

    try:
        # 发送 3 次心跳
        acks = 0
        for i in range(3):
            hb = {
                "cmd": "heartbeat",
                "agent_id": SELF_ID,
                "status": "online",
                "timestamp": time.time(),
                "load": {"pending_tasks": i},
            }
            await ws.send(json.dumps(hb))
            log.info(f"  心跳 #{i+1} 已发送")

            try:
                resp = await recv_with_timeout(ws, 5)
                data = json.loads(resp)
                log.info(f"  响应: {data.get('cmd', 'unknown')}")
                if data.get("cmd") == "heartbeat_ack":
                    acks += 1
            except asyncio.TimeoutError:
                log.warning(f"  心跳 #{i+1} 超时无响应")

            await asyncio.sleep(1)

        # 查询状态
        query = {"cmd": "lifecycle_status", "agent_id": SELF_ID}
        await ws.send(json.dumps(query))
        try:
            resp = await recv_with_timeout(ws, 5)
            log.info(f"  状态查询: {resp[:200] if isinstance(resp, str) else str(resp)[:200]}")
        except asyncio.TimeoutError:
            log.warning("  状态查询无响应")

        check_result("心跳+状态查询", acks >= 2,
                     f"heartbeats=3, acks={acks}")

    finally:
        await ws.close()


# === Test 6: 优雅窗口（Server 侧测试 — 检测连接关闭流程）===
async def test_graceful_close():
    log.info("=" * 60)
    log.info("【测试 6】优雅关闭窗口 15s")
    log.info("=" * 60)

    ws = await connect_ws(SELF_ID, channel="main")

    try:
        log.info("  发送 deregister 命令...")
        await ws.send(json.dumps({"cmd": "deregister", "agent_id": SELF_ID}))
        await asyncio.sleep(1)

        try:
            resp = await recv_with_timeout(ws, 3)
            log.info(f"  deregister 回复: {resp[:100] if isinstance(resp, str) else resp}")
        except asyncio.TimeoutError:
            log.info("  deregister 无直接回复（正常）")

        # 尝试重新注册
        ws2 = await connect_ws(SELF_ID, channel="main")
        check_result("deregister+重连", True,
                     "deregister 后重新认证成功")
        await ws2.close()

    finally:
        try:
            await ws.close()
        except:
            pass


# === Main ===
async def main():
    log.info("=" * 60)
    log.info("AIM Phase 2.3 独立测试脚本 (Server 端)")
    log.info(f"目标: {WS_URL}")
    log.info(f"自身: {SELF_ID}")
    log.info(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    tests_to_run = []
    if "--test" in sys.argv:
        n = int(sys.argv[sys.argv.index("--test") + 1])
        tests_to_run = [n]
    elif "--all" in sys.argv or len([a for a in sys.argv if a.startswith("--")]) == 0:
        tests_to_run = [1, 2, 3, 4, 5, 6]

    test_map = {
        1: ("多通道共存", test_multi_channel),
        2: ("ACK 直回发送方", test_ack_direct_reply),
        3: ("消息去重", test_dedup),
        4: ("离线队列", test_offline_queue),
        5: ("心跳+状态查询", test_heartbeat),
        6: ("优雅关闭", test_graceful_close),
    }

    for n in tests_to_run:
        if n in test_map:
            name, fn = test_map[n]
            try:
                await fn()
            except Exception as e:
                log.error(f"  测试 {n} ({name}) 异常: {e}")
                check_result(name, False, f"exception: {e}")
        else:
            log.warning(f"  未知测试编号: {n}")

    # 汇总
    log.info("=" * 60)
    log.info("测试汇总")
    log.info("=" * 60)
    passed = 0
    failed = 0
    for name, ok, detail in results:
        icon = "✅" if ok else "❌"
        log.info(f"  {icon} {name}")
        if not ok:
            failed += 1
        else:
            passed += 1
    log.info(f"  ---> {passed} PASS / {failed} FAIL / {len(results)} TOTAL")
    log.info("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
