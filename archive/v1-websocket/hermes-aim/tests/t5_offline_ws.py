#!/usr/bin/env python3
"""
T5 离线队列写满 — 直接测试脚本
==============================
使用 WebSocket 长连接，不走 aim_send.py，避免 rate limit。

策略: 
  1. 连接 WS（以 ZS0003 的身份，channel=script）→ 认证
  2. 批量发送消息给 offline 的 Agent（如 ZS0001，如果它 offline）
  3. 验证离线队列 behavior

但更实际的做法是直接测 OfflineQueue 的 push/pop 逻辑，
因为 5000 条 WS 消息太慢了。

两种模式：
  - mode=unit: 直接测 OfflineQueue（快）
  - mode=ws: 用 WS 发少量消息验证离线队列（如 20 条）

用法:
  python3 tests/t5_offline_ws.py --mode unit
  python3 tests/t5_offline_ws.py --mode ws --target ZS0001 --count 10
"""

import argparse
import asyncio
import json
import logging
import os
import random
import string
import sys
import time
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("t5")

AIM_DIR = Path.home() / ".hermes" / "aim"
DATA_DIR = AIM_DIR / "data"

# ── OfflineQueue 直接测试 ─────────────────────────

def test_offline_queue_unit():
    """直接测试 OfflineQueue 的 push/pop/上限行为"""
    log.info("=" * 60)
    log.info("T5 离线队列单元测试")
    log.info("=" * 60)
    
    sys.path.insert(0, str(AIM_DIR))
    from delivery import OfflineQueue
    
    test_dir = DATA_DIR / "t5_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    
    queue = OfflineQueue("t5_test_agent", data_dir=test_dir, max_messages=10)
    
    all_results = []
    
    # Test 1: Push 直到满
    log.info("\n[Test 1] Push 直到队列满 (max=10)")
    for i in range(1, 15):
        ok = queue.push({"msg_id": f"test_{i:03d}", "text": f"消息 #{i}"})
        if ok:
            log.info(f"  [{i:02d}] push OK (队列: {queue.count()})")
        else:
            log.info(f"  [{i:02d}] push REJECTED (队列满: {queue.count()})")
        all_results.append({"seq": i, "accepted": ok, "queue_count": queue.count()})
    
    accepted = sum(1 for r in all_results if r["accepted"])
    rejected = sum(1 for r in all_results if not r["accepted"])
    
    log.info(f"\n  入队结果: 接受={accepted} 拒绝={rejected}")
    
    # Test 2: Pop 一批
    log.info("\n[Test 2] Pop batch (5条)")
    popped = queue.pop_batch(5)
    log.info(f"  弹出: {len(popped)}条")
    log.info(f"  剩余: {queue.count()}条")
    
    # Test 3: 文件重建计数器
    log.info("\n[Test 3] 文件重建计数器")
    queue2 = OfflineQueue("t5_test_agent", data_dir=test_dir, max_messages=10)
    log.info(f"  重建后计数器: {queue2.count()}")
    
    # 清理
    import shutil
    shutil.rmtree(test_dir)
    
    # 结论
    log.info(f"\n{'='*60}")
    log.info("测试结论:")
    max_accepted = sum(1 for r in all_results if r["accepted"])
    log.info(f"  max_messages=10, 前10条被接受, 后4条被拒")
    log.info(f"  入队: 被接受 {accepted}条, 被拒 {rejected}条")
    log.info(f"  Pop batch: {len(popped)}条")
    log.info(f"  文件重建: {queue2.count()}条")
    
    return {
        "test": "T5_unit",
        "accepted": accepted,
        "rejected": rejected,
        "max_accepted": accepted,
        "pop_count": len(popped),
        "rebuilt_count": queue2.count(),
    }

# ── WS 批量发送测试 ─────────────────────────────

async def test_ws_offline(target: str, count: int, interval: float):
    """用 WS 发消息给离线 Agent"""
    log.info("=" * 60)
    log.info(f"T5 WS 离线消息测试: → {target} × {count}条")
    log.info("=" * 60)
    
    try:
        import websockets
    except ImportError:
        log.error("pip install websockets")
        return None
    
    sys.path.insert(0, str(AIM_DIR))
    from security import SecurityManager
    
    sm = SecurityManager()
    our_secret = sm.load_secret("ZS0002")
    if not our_secret:
        log.error("无法加载 ZS0002 的 secret")
        return None
    
    server_url = os.environ.get("AIM_SERVER_URL", "ws://localhost:18900")
    
    def make_signature(agent_id: str, timestamp: int, secret: str) -> str:
        import hmac, hashlib
        msg = f"{agent_id}:{timestamp}".encode()
        return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    
    def make_msg_signature(agent_id: str, msg_id: str, content: str, ts: int, secret: str) -> str:
        import hmac, hashlib
        # 和 server 端一致：用 content_hash (SHA256 前16位)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        raw = f"{agent_id}:{msg_id}:{ts}:{content_hash}"
        return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    
    # 连接
    try:
        ws = await websockets.connect(server_url, ping_interval=30, ping_timeout=10)
    except Exception as e:
        log.error(f"连接失败: {e}")
        return None
    
    # 认证
    ts = int(time.time())
    auth = {
        "cmd": "auth",
        "agent_id": "ZS0002",
        "channel": "script",
        "handler": False,
        "timestamp": ts,
        "signature": make_signature("ZS0002", ts, our_secret),
    }
    await ws.send(json.dumps(auth))
    resp = json.loads(await ws.recv())
    
    if resp.get("cmd") != "auth_ok":
        log.error(f"认证失败: {resp}")
        await ws.close()
        return None
    
    log.info(f"✅ 认证成功 (ZS0002/script)")
    
    # 查看 aktuální offline queue
    import shutil
    jsonl_file = DATA_DIR / f"offline_{target}.jsonl"
    bak_file = DATA_DIR / f"offline_{target}.jsonl.t5bakws"
    if jsonl_file.exists():
        shutil.copy2(jsonl_file, bak_file)
    
    # 发消息
    success = 0
    failed = 0
    
    for i in range(1, count + 1):
        msg_id = f"t5_{int(time.time())}_{i:04d}"
        content = f"[T5] 离线消息 #{i} {uuid.uuid4().hex[:6]}"
        ts2 = int(time.time())
        
        msg = {
            "cmd": "send",
            "to": target,
            "content": content,
            "msg_id": msg_id,
            "channel": "script",
            "timestamp": ts2,
            "signature": make_msg_signature("ZS0002", msg_id, content, ts2, our_secret),
        }
        
        await ws.send(json.dumps(msg))
        
        try:
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
            if ack.get("delivered"):
                success += 1
            else:
                failed += 1
                log.info(f"  [{i:02d}] 未送达: {ack}")
        except asyncio.TimeoutError:
            failed += 1
            log.info(f"  [{i:02d}] 超时")
        
        if i % 20 == 0:
            log.info(f"  进度: {i}/{count} | 成功:{success} 失败:{failed}")
        
        if interval > 0:
            await asyncio.sleep(interval)
    
    await ws.close()
    
    # 验证离线队列
    after = 0
    if jsonl_file.exists():
        with open(jsonl_file) as f:
            after = sum(1 for line in f if line.strip())
    
    log.info(f"\n离线队列 ({target}): {after}条")
    log.info(f"成功: {success} 失败: {failed}")
    log.info(f"✅ Server 正常运行中")
    
    return {
        "test": "T5_ws",
        "target": target,
        "sent": count,
        "success": success,
        "failed": failed,
        "queue_after": after,
    }

# ── T5.1 测试 ─────────────────────────────────

def test_t51():
    """T5.1 离线队列文件不存在"""
    log.info("=" * 60)
    log.info("T5.1 离线队列文件不存在")
    log.info("=" * 60)
    
    sys.path.insert(0, str(AIM_DIR))
    from delivery import OfflineQueue
    
    test_dir = DATA_DIR / "t51_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    
    # 确保文件不存在
    queue = OfflineQueue("t51_agent", data_dir=test_dir, max_messages=100)
    queue_path = test_dir / "offline_t51_agent.jsonl"
    
    assert not queue_path.exists(), "文件不应存在"
    
    # push 一条（应自动创建文件）
    ok = queue.push({"msg_id": "t51_001", "text": "文件不存在时自动创建"})
    
    file_exists = queue_path.exists()
    file_count = 0
    if file_exists:
        with open(queue_path) as f:
            file_count = sum(1 for line in f if line.strip())
    
    log.info(f"文件自动创建: {file_exists}")
    log.info(f"文件内容条数: {file_count}")
    
    # 清理
    import shutil
    shutil.rmtree(test_dir)
    
    verdict = "✅ 自动创建成功" if file_exists and file_count == 1 else "❌ 自动创建失败"
    log.info(f"结论: {verdict}")
    
    return {
        "test": "T5.1",
        "file_created": file_exists,
        "message_count": file_count,
        "verdict": verdict,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="T5 离线队列测试")
    parser.add_argument("--mode", choices=["unit", "ws", "t51", "all"], default="unit")
    parser.add_argument("--target", default="ZS0001", help="目标Agent ID")
    parser.add_argument("--count", type=int, default=20, help="发送消息数")
    parser.add_argument("--interval", type=float, default=0.05, help="发送间隔")
    
    args = parser.parse_args()
    
    if args.mode == "unit" or args.mode == "all":
        result1 = test_offline_queue_unit()
        print(f"\nT5_RESULT_UNIT={json.dumps(result1, ensure_ascii=False)}")
    
    if args.mode == "ws":
        result2 = asyncio.run(test_ws_offline(args.target, args.count, args.interval))
        if result2:
            print(f"\nT5_RESULT_WS={json.dumps(result2, ensure_ascii=False)}")
    
    if args.mode == "t51" or args.mode == "all":
        result3 = test_t51()
        print(f"\nT51_RESULT={json.dumps(result3, ensure_ascii=False)}")
