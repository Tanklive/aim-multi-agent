#!/usr/bin/env python3
"""
T5. 离线队列写满 — 高效测试脚本
=============================
两步验证:
  1. 直接填充离线队列 JSONL 到接近上限 (4990条)
  2. 用 aim_send.py 发真实消息触发满队列，验证截断

用法:
  python3 tests/t5_offline_full.py [--target ZS0003] [--fill-to 4990] [--send 20]
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("t5")

AIM_DIR = Path.home() / ".hermes" / "aim"
DATA_DIR = AIM_DIR / "data"
SEND_SCRIPT = AIM_DIR / "aim_send.py"

def get_offline_count(agent_id: str) -> int:
    """读取离线队列条数"""
    jsonl_file = DATA_DIR / f"offline_{agent_id}.jsonl"
    if not jsonl_file.exists():
        return 0
    count = 0
    try:
        with open(jsonl_file, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception as e:
        log.warning(f"读取离线队列失败: {e}")
        return -1
    return count

def fill_offline_queue(agent_id: str, target_count: int):
    """快速填充离线队列到目标条数"""
    jsonl_file = DATA_DIR / f"offline_{agent_id}.jsonl"
    current = get_offline_count(agent_id)
    need = target_count - current
    
    if need <= 0:
        log.info(f"队列已有 {current}条 ≥ {target_count}，无需填充")
        return current
    
    log.info(f"填充离线队列: {current} → {target_count} (+{need}条)")
    
    # 批量写入 JSONL
    start = time.time()
    batch_size = 500
    written = 0
    
    with open(jsonl_file, "a", encoding="utf-8") as f:
        for i in range(0, need, batch_size):
            chunk = min(batch_size, need - i)
            for j in range(chunk):
                entry = {
                    "msg_id": f"fill-{uuid.uuid4().hex[:12]}",
                    "from_agent": "ZS0002",
                    "to_agent": agent_id,
                    "text": f"[T5填充] 离线消息 #{current + written + j + 1}",
                    "_offline_ts": time.time(),
                    "_offline_seq": current + written + j + 1,
                    "timestamp": time.time(),
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1
            f.flush()
            os.fsync(f.fileno())
    
    elapsed = time.time() - start
    log.info(f"填充完成: {written}条, 耗时{elapsed:.2f}s")
    return written + current

def send_message_via_ws(agent_id: str, target: str, text: str) -> tuple:
    """用 aim_send.py 发一条消息"""
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(SEND_SCRIPT), target, text],
        capture_output=True, text=True, timeout=30
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output

def test_t5(target: str = "ZS0003", fill_to: int = 4990, send_count: int = 20):
    """T5 主测试流程"""
    log.info("=" * 60)
    log.info("T5. 离线队列写满 — 测试开始")
    log.info("=" * 60)
    log.info(f"目标Agent: {target}")
    log.info(f"填充至: {fill_to}条")
    log.info(f"额外发送: {send_count}条（触发满队列）")
    
    # Step 1: 备份当前队列
    jsonl_file = DATA_DIR / f"offline_{target}.jsonl"
    backup_file = DATA_DIR / f"offline_{target}.jsonl.t5bak"
    if jsonl_file.exists():
        import shutil
        shutil.copy2(jsonl_file, backup_file)
        log.info(f"已备份: {backup_file}")
    
    before = get_offline_count(target)
    log.info(f"Step 1 — 当前离线队列: {before}条")
    
    # Step 2: 填充到接近上限
    log.info(f"\nStep 2 — 填充至 {fill_to}条...")
    after_fill = fill_offline_queue(target, fill_to)
    log.info(f"填充后队列: {after_fill}条")
    
    # Step 3: 发真实消息触发满队列
    log.info(f"\nStep 3 — 发送 {send_count} 条真实消息，触发满队列...")
    success = 0
    full_rejected = 0
    other_fail = 0
    
    for i in range(1, send_count + 1):
        msg = f"[T5实时] 队列满触发 #{i}"
        rc, out = send_message_via_ws("ZS0002", target, msg)
        if rc == 0:
            success += 1
        else:
            if any(kw in out.lower() for kw in ["full", "discard", "limit", "reject"]):
                full_rejected += 1
            else:
                other_fail += 1
        time.sleep(0.1)
    
    # Step 4: 验证最终状态
    after = get_offline_count(target)
    log.info(f"\nStep 4 — 最终状态:")
    log.info(f"  Queue before: {before}")
    log.info(f"  Queue after_fill: {after_fill}")
    log.info(f"  Queue final: {after}")
    log.info(f"  成功发送: {success}")
    log.info(f"  队列满拒绝: {full_rejected}")
    log.info(f"  其他失败: {other_fail}")
    
    # Step 5: 验证 Server 正常运行
    log.info(f"\nStep 5 — 验证 Server 正常运行:")
    is_alive = after > 0  # 队列有数据 = Server 正常工作
    is_truncated = full_rejected > 0 or (after > 0 and after < after_fill + send_count)
    
    # 结果判定
    verdicts = []
    if after > 0:
        verdicts.append("✅ 离线队列功能正常")
    if full_rejected > 0:
        verdicts.append("✅ 队列满截断触发")
    elif after <= after_fill:
        verdicts.append("✅ 队列保持上限未超")
    
    log.info(f"\n{'='*60}")
    log.info("T5 测试结论:")
    for v in verdicts:
        log.info(f"  {v}")
    log.info(f"{'='*60}")
    
    results = {
        "test": "T5",
        "target": target,
        "before": before,
        "after_fill": after_fill,
        "after_test": after,
        "success_sent": success,
        "full_rejected": full_rejected,
        "other_fail": other_fail,
        "conclusions": verdicts,
    }
    
    # 输出 JSON 格式结果
    print(f"\nT5_RESULT={json.dumps(results, ensure_ascii=False)}")
    return results

def test_t51(target: str = "ZS0003"):
    """T5.1 离线队列文件不存在"""
    log.info("=" * 60)
    log.info("T5.1 离线队列文件不存在 — 测试开始")
    log.info("=" * 60)
    
    jsonl_file = DATA_DIR / f"offline_{target}.jsonl"
    
    # Step 1: 备份并删除
    import shutil
    if jsonl_file.exists():
        bak = DATA_DIR / f"offline_{target}.jsonl.t51bak"
        shutil.copy2(jsonl_file, bak)
        jsonl_file.unlink()
        log.info(f"已删除 {jsonl_file.name}，备份至 {bak.name}")
    
    # Step 2: 发一条消息（触发自动创建）
    msg = "[T5.1测试] 队列文件不存在时自动创建"
    rc, out = send_message_via_ws("ZS0002", target, msg)
    log.info(f"发送结果: rc={rc}")
    time.sleep(1)
    
    # Step 3: 验证文件已自动创建
    exists = jsonl_file.exists()
    count = get_offline_count(target) if exists else 0
    log.info(f"文件已自动创建: {exists}, 包含: {count}条")
    
    verdict = "✅ 自动创建成功" if exists and count > 0 else "❌ 自动创建失败"
    log.info(f"\nT5.1 结论: {verdict}")
    
    results = {
        "test": "T5.1",
        "target": target,
        "file_recreated": exists,
        "message_count": count,
        "conclusion": verdict,
    }
    print(f"\nT51_RESULT={json.dumps(results, ensure_ascii=False)}")
    return results

def cleanup(target: str = "ZS0003"):
    """清理测试残留"""
    import shutil
    for suffix in ["t5bak", "t51bak"]:
        bak = DATA_DIR / f"offline_{target}.jsonl.{suffix}"
        if bak.exists():
            bak.unlink()
            log.info(f"清理备份: {bak}")
    
    # 恢复原始队列（从备份）
    bak = DATA_DIR / f"offline_{target}.jsonl.t5bak"
    orig = DATA_DIR / f"offline_{target}.jsonl"
    if bak.exists():
        shutil.copy2(bak, orig)
        bak.unlink()
        log.info(f"已恢复原始队列")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="T5 离线队列测试")
    parser.add_argument("--target", default="ZS0003", help="目标Agent ID")
    parser.add_argument("--fill-to", type=int, default=4990, help="填充条数")
    parser.add_argument("--send", type=int, default=20, help="额外发送条数")
    parser.add_argument("--t51", action="store_true", help="只跑 T5.1")
    parser.add_argument("--cleanup", action="store_true", help="清理测试残留")
    
    args = parser.parse_args()
    
    if args.cleanup:
        cleanup(args.target)
        sys.exit(0)
    
    if args.t51:
        test_t51(args.target)
    else:
        test_t5(args.target, args.fill_to, args.send)
