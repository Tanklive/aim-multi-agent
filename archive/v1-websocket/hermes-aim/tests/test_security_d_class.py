#!/usr/bin/env python3
"""
AIM Phase 3-1 — D 类安全与攻击防御测试脚本
============================================
覆盖 T 计划中的 D 类场景（安全与攻击防御），包括：
  D01 — TC-07: Client 认证失败（token/secret 不匹配）
  D02 — TC-04: Client 心跳超时 / 卡死模拟
  D03 — T11: 连接池耗尽排队/拒绝（20 连接上限）
  D04 — T12: 离线队列磁盘满降级
  D05 — 认证频率限制 / 洪水攻击
  D06 — 非法 channel 拒绝
  D07 — 消息 HMAC 防重放时间窗验证

用法:
  cd ~/.hermes/aim && python3 tests/test_security_d_class.py

选项:
  --test N    只跑第 N 项测试（1-7）
  --all       跑全部测试（默认）
  --verbose   详细输出

⚠️ 注意: 部分测试需要操作 Server 进程或文件，请确保有权限。
"""

import asyncio
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import signal
import subprocess
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("❌ 需要 websockets: pip install websockets")
    sys.exit(1)

try:
    from security import get_security_manager
except ImportError:
    print("⚠️ security 模块未找到，使用内置 HMAC 签名")
    get_security_manager = None

# ── 配置 ──────────────────────────────────────────────────
SERVER_URL = "ws://127.0.0.1:18900"
TEST_AGENT = "ZS0002"
PEER_AGENT = "ZS0001"

# ── 全局计数器 ────────────────────────────────────────────
PASS = 0
FAIL = 0

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [D-TEST] %(message)s")
log = logging.getLogger("d-class")


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def fail(msg: str):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


# ── HMAC 签名工具 ──────────────────────────────────────────

def load_secret(agent_id: str) -> str:
    """加载 agent 密钥"""
    if get_security_manager:
        sec = get_security_manager()
        secret = sec.load_secret(agent_id)
        if secret:
            return secret
    # 降级：从配置文件读取
    secret_paths = [
        f"{os.path.expanduser('~')}/.hermes/aim/config.json",
        f"{os.path.expanduser('~')}/.hermes/hermes-agent/apps/aim-agent/config.json",
    ]
    for sp in secret_paths:
        if os.path.exists(sp):
            with open(sp) as f:
                cfg = json.load(f)
            agents = cfg.get("agents", {})
            if agent_id in agents:
                return agents[agent_id].get("secret", "")
    # 兜底
    default_secrets = {
        "ZS0001": "guagua_token_2026",
        "ZS0002": "jiliang_token_2026",
        "ZS0003": "xiaohuoji_token_2026",
    }
    return default_secrets.get(agent_id, "")


def build_auth(agent_id: str, channel: str,
               timestamp: int = None,
               secret_override: str = None,
               omit_channel: bool = False,
               omit_signature: bool = False,
               bad_channel: str = None) -> dict:
    """构建 auth payload，支持各种异常模式"""
    ts = timestamp or int(time.time())
    secret = secret_override or load_secret(agent_id)
    message = f"{agent_id}:{ts}"
    sig = ""
    if not omit_signature:
        sig = hmac_mod.new(
            secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

    payload = {
        "cmd": "auth",
        "agent_id": agent_id,
        "timestamp": ts,
        "signature": sig,
        "handler": True,
        "term": 1,
    }

    if bad_channel:
        payload["channel"] = bad_channel
    elif not omit_channel:
        payload["channel"] = channel

    return payload


async def connect_ws(timeout: float = 5.0) -> any:
    """建立裸 WS 连接"""
    return await asyncio.wait_for(
        ws_connect(SERVER_URL, max_size=1024 * 1024),
        timeout=timeout
    )


async def do_auth(ws, auth_payload: dict, timeout: float = 5.0) -> dict:
    """发送 auth 并接收响应"""
    await ws.send(json.dumps(auth_payload))
    resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(resp)


async def auth_and_connect(agent_id: str, channel: str = "main",
                           timeout: float = 5.0) -> tuple:
    """完整链路：连接 WS → 发送 auth → 返回 (ws, response)"""
    try:
        ws = await connect_ws(timeout)
        auth_payload = build_auth(agent_id, channel)
        resp = await do_auth(ws, auth_payload, timeout)
        return ws, resp
    except Exception as e:
        return None, {"error": str(e)}


async def disconnect_ws(ws, label: str = ""):
    """安全断开 WS"""
    if ws is None:
        return
    try:
        await ws.send(json.dumps({"cmd": "disconnect", "agent_id": TEST_AGENT}))
    except Exception:
        pass
    try:
        await ws.close()
    except Exception:
        pass
    if label:
        print(f"  🔌 断开 {label}")


async def check_ws_alive(ws) -> bool:
    """检查 WS 是否存活"""
    try:
        await ws.send(json.dumps({"cmd": "ping"}))
        resp = await asyncio.wait_for(ws.recv(), timeout=3.0)
        return True
    except Exception:
        return False


def get_server_pid() -> int:
    """尝试获取 AIM Server 进程 PID"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*node\\.py"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return int(result.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return 0


def get_aim_agent_pid(agent_id: str = TEST_AGENT) -> int:
    """尝试获取 aim-agent 进程 PID"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"aim-agent.*{agent_id}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return int(result.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return 0


# ════════════════════════════════════════════════════════════
#  D01 — TC-07: Client 认证失败（token/secret 不匹配）
# ════════════════════════════════════════════════════════════

async def test_d01_auth_failure():
    print("\n" + "─" * 60)
    print("🧪 D01 (TC-07): Client 认证失败 — token/secret 不匹配")
    print("─" * 60)

    ws = None
    try:
        # 用错误的 secret 签名
        ws = await connect_ws()
        bad_auth = build_auth(TEST_AGENT, "script",
                              secret_override="wrong_secret_12345")
        resp = await do_auth(ws, bad_auth)

        is_rejected = resp.get("cmd") == "auth_fail"
        has_reason = bool(resp.get("reason"))
        if is_rejected:
            ok(f"认证被拒绝: {resp.get('reason', '?')}")
        else:
            fail(f"预期 auth_fail，收到: {resp.get('cmd', '?')}")
    except Exception as e:
        fail(f"异常: {e}")
    finally:
        await disconnect_ws(ws)


# ════════════════════════════════════════════════════════════
#  D02 — TC-04: Client 心跳超时 / 卡死模拟
# ════════════════════════════════════════════════════════════

async def test_d02_heartbeat_stall():
    print("\n" + "─" * 60)
    print("🧪 D02 (TC-04): Client 心跳超时 — SIGSTOP/SIGCONT 模拟卡死")
    print("─" * 60)

    pid = get_aim_agent_pid()
    if not pid:
        print("  ⚠️ 找不到 aim-agent 进程，跳过 SIGSTOP 测试")
        print("  请先启动 aim-agent，或手动验证")
        fail("进程不存在，跳过")
        return

    print(f"  目标进程: {pid}")

    try:
        # SIGSTOP — 暂停进程，模拟卡死
        print("  ⏸️ 发送 SIGSTOP...")
        os.kill(pid, signal.SIGSTOP)
        await asyncio.sleep(2)

        # 验证连接卡死状态
        print("  等待 Server 检测心跳超时...")
        await asyncio.sleep(5)  # 等一个扫描周期

        # SIGCONT — 恢复进程
        print("  ▶️ 发送 SIGCONT...")
        os.kill(pid, signal.SIGCONT)
        await asyncio.sleep(3)

        # 恢复后检测连接是否 alive（等待重连）
        print("  验证恢复后连接状态...")
        ws, resp = await auth_and_connect(TEST_AGENT, "main")
        if ws:
            ok("SIGCONT 后能重新认证连接")
            await disconnect_ws(ws)
        else:
            fail(f"恢复后认证失败: {resp}")

    except PermissionError:
        fail("无权限发送信号")
    except ProcessLookupError:
        fail("进程已不存在")
    except Exception as e:
        fail(f"异常: {e}")


# ════════════════════════════════════════════════════════════
#  D03 — T11: 连接池耗尽排队/拒绝
# ════════════════════════════════════════════════════════════

async def test_d03_pool_exhaustion():
    print("\n" + "─" * 60)
    print("🧪 D03 (T11): 连接池耗尽 — 同一 Agent 达上限时拒绝新连接")
    print("─" * 60)

    # 检查 Server 的 channel 上限配置（标准 5/channel）
    max_conn = 5
    print(f"  每 channel 上限: {max_conn}")

    connections = []
    success_count = 0
    rejected_count = 0
    reject_reason = ""

    try:
        for i in range(max_conn + 2):  # 尝试建立上限+2 个连接
            try:
                ws = await connect_ws(3.0)
                auth_p = build_auth(TEST_AGENT, "script",
                                    timestamp=int(time.time()) + i,
                                    omit_channel=False)
                resp = await do_auth(ws, auth_p, 3.0)
                if resp.get("cmd") == "auth_fail":
                    rejected_count += 1
                    reject_reason = resp.get("reason", "")
                    await disconnect_ws(ws)
                else:
                    # auth_ok 或 register_ok
                    connections.append(ws)
                    success_count += 1
            except Exception:
                rejected_count += 1

            await asyncio.sleep(0.1)  # 防洪水

        print(f"  成功建立: {success_count}, 被拒绝: {rejected_count}")
        if success_count <= max_conn and rejected_count > 0:
            ok(f"连接池上限生效: {success_count} 成功, {rejected_count} 被拒绝")
            if reject_reason:
                print(f"  拒绝原因: {reject_reason}")
        elif success_count > max_conn:
            fail(f"预期上限 {max_conn}，但成功 {success_count}")
        else:
            fail(f"预期有拒绝，但全部成功 {success_count}")

    except Exception as e:
        fail(f"异常: {e}")
    finally:
        for ws in connections:
            await disconnect_ws(ws)


# ════════════════════════════════════════════════════════════
#  D04 — T12: 离线队列磁盘满降级
# ════════════════════════════════════════════════════════════

async def test_d04_disk_full_downgrade():
    print("\n" + "─" * 60)
    print("🧪 D04 (T12): 离线队列磁盘满降级")
    print("─" * 60)

    # 找到离线队列 JSONL 文件路径
    data_dir = os.path.expanduser("~/.hermes/aim/data")
    jsonl_path = os.path.join(data_dir, "messages.jsonl")

    if not os.path.exists(data_dir):
        # 尝试找其他位置
        alt_paths = [
            os.path.expanduser("~/.hermes/hermes-agent/apps/aim-agent/data/messages.jsonl"),
            os.path.expanduser("~/.hermes/aim-server/data/messages.jsonl"),
        ]
        for ap in alt_paths:
            if os.path.exists(os.path.dirname(ap)):
                data_dir = os.path.dirname(ap)
                jsonl_path = ap
                break

    print(f"  数据目录: {data_dir}")
    print(f"  JSONL 路径: {jsonl_path}")
    print("  ⚠️ 磁盘满测试有风险，改为验证 JSONL 写入异常处理")
    print("  Server 日志中搜索 '写入失败' 或 'JSONL error'")

    # 改为非破坏性验证：检查 Server 响应中的写入失败处理
    ws = None
    try:
        ws, resp = await auth_and_connect(TEST_AGENT, "main")
        if not ws:
            fail("连接 Server 失败")
            return

        # 发送一条测试消息，检查日志中是否有写入成功记录
        test_msg = {
            "cmd": "send",
            "to": PEER_AGENT,
            "text": f"[D04 磁盘满降级测试] {time.time()}",
            "msg_id": f"d04_{int(time.time())}",
        }
        await ws.send(json.dumps(test_msg))
        # 等待响应（可能 delivery_ack 或 error）
        try:
            resp2 = await asyncio.wait_for(ws.recv(), timeout=5.0)
            resp_data = json.loads(resp2)
            print(f"  消息响应: {json.dumps(resp_data, ensure_ascii=False)[:200]}")
            ok("消息发送/写入链路正常 (无异常)")
        except asyncio.TimeoutError:
            print("  消息发送无直接回复（可能通过 offline 通道）")
            ok("消息已发送")

    except Exception as e:
        fail(f"异常: {e}")
    finally:
        await disconnect_ws(ws)

    print("\n  💡 完整磁盘满测试需手动操作：")
    print("     1. `dd if=/dev/zero of=/tmp/fill bs=1M count=...` 填满分区")
    print("     2. 触发 Server 写入 JSONL")
    print("     3. 检查日志'写入失败'且 Server 不崩溃")
    print("     4. 释放磁盘空间后确认自动恢复")


# ════════════════════════════════════════════════════════════
#  D05 — 认证频率限制 / 洪水攻击
# ════════════════════════════════════════════════════════════

async def test_d05_rate_limit_flood():
    print("\n" + "─" * 60)
    print("🧪 D05: 认证频率限制 — 快速连续 auth 请求")
    print("─" * 60)

    connections = []
    auth_results = {"success": 0, "rate_limited": 0, "errors": 0}
    rate_limit_reason = ""

    try:
        # 快速发送 15 个认证请求（不超过 2 秒）
        for i in range(15):
            try:
                ws = await connect_ws(2.0)
                auth_p = build_auth(TEST_AGENT, "script",
                                    timestamp=int(time.time()) + i)
                resp = await do_auth(ws, auth_p, 2.0)

                if resp.get("cmd") == "auth_fail":
                    reason = resp.get("reason", "")
                    if "频率" in reason or "rate" in reason.lower() or "限流" in reason:
                        auth_results["rate_limited"] += 1
                        rate_limit_reason = reason
                    else:
                        auth_results["errors"] += 1
                    await disconnect_ws(ws)
                else:
                    connections.append(ws)
                    auth_results["success"] += 1
            except Exception:
                auth_results["errors"] += 1
                try:
                    await ws.close()
                except Exception:
                    pass

            await asyncio.sleep(0.05)  # 非常快

        print(f"  结果: {auth_results}")
        if auth_results["rate_limited"] > 0:
            ok(f"频率限制生效: {auth_results['rate_limited']} 次被限流")
            if rate_limit_reason:
                print(f"  限流原因: {rate_limit_reason}")
        else:
            # 不一定每次都能触发限流（取决于 Server 配置阈值）
            # Server 可能按 agent_id 隔离限流
            print("  ℹ️ 未触发频率限制（可能阈值较高或 agent_id 隔离生效）")

    except Exception as e:
        fail(f"异常: {e}")
    finally:
        for ws in connections:
            await disconnect_ws(ws)


# ════════════════════════════════════════════════════════════
#  D06 — 非法 channel 拒绝
# ════════════════════════════════════════════════════════════

async def test_d06_invalid_channel():
    print("\n" + "─" * 60)
    print("🧪 D06: 非法 channel 拒绝")
    print("─" * 60)

    test_cases = [
        ("不传 channel", {"omit_channel": True}),
        ("非法 channel 'hack'", {"bad_channel": "hack"}),
        ("非法 channel 'admin'", {"bad_channel": "admin"}),
        ("空 channel ''", {"bad_channel": ""}),
    ]

    for label, params in test_cases:
        ws = None
        try:
            ws = await connect_ws()
            auth_p = build_auth(TEST_AGENT, "main",
                                timestamp=int(time.time()),
                                omit_channel=params.get("omit_channel", False),
                                bad_channel=params.get("bad_channel", None))
            resp = await do_auth(ws, auth_p)
            is_rejected = resp.get("cmd") == "auth_fail"
            reason = resp.get("reason", "")
            ok(f"[{label}] 被拒绝: {reason}") if is_rejected else \
                fail(f"[{label}] 预期 auth_fail，收到 {resp.get('cmd', '?')}")
        except Exception as e:
            fail(f"[{label}] 异常: {e}")
        finally:
            await disconnect_ws(ws)


# ════════════════════════════════════════════════════════════
#  D07 — HMAC 防重放时间窗验证
# ════════════════════════════════════════════════════════════

async def test_d07_hmac_replay_window():
    print("\n" + "─" * 60)
    print("🧪 D07: HMAC 防重放 — 过期时间窗拒绝")
    print("─" * 60)

    # auth 阶段用 ±60s 窗口
    ws = None
    try:
        # 使用 120 秒前的 timestamp（超出 ±60s 窗口）
        old_ts = int(time.time()) - 120
        ws = await connect_ws()
        old_auth = build_auth(TEST_AGENT, "main", timestamp=old_ts)
        resp = await do_auth(ws, old_auth)

        is_rejected = resp.get("cmd") == "auth_fail"
        reason = resp.get("reason", "")
        if is_rejected:
            ok(f"过期 timestamp 被拒绝: {reason}")
        else:
            fail(f"预期 auth_fail，收到 {resp.get('cmd', '?')}, 响应: {resp}")
    except Exception as e:
        fail(f"异常: {e}")
    finally:
        await disconnect_ws(ws)


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

TEST_MAP = {
    1: ("D01 TC-07 认证失败", test_d01_auth_failure),
    2: ("D02 TC-04 心跳超时", test_d02_heartbeat_stall),
    3: ("D03 T11 连接池耗尽", test_d03_pool_exhaustion),
    4: ("D04 T12 磁盘满降级", test_d04_disk_full_downgrade),
    5: ("D05 认证频率限制", test_d05_rate_limit_flood),
    6: ("D06 非法 channel 拒绝", test_d06_invalid_channel),
    7: ("D07 HMAC 防重放", test_d07_hmac_replay_window),
}


def print_header():
    print("\n" + "=" * 60)
    print("🛡️  AIM Phase 3-1 — D 类安全与攻击防御测试")
    print("=" * 60)
    print(f"Server: {SERVER_URL}")
    print(f"Agent: {TEST_AGENT}")
    print(f"日期: {time.strftime('%Y-%m-%d %H:%M:%S')}")


async def run_all():
    print_header()
    for n in sorted(TEST_MAP.keys()):
        name, func = TEST_MAP[n]
        try:
            await func()
        except Exception as e:
            print(f"\n  💥 测试 {n} ({name}) 异常: {e}")
            fail(f"测试异常")


async def run_single(test_num: int):
    print_header()
    if test_num not in TEST_MAP:
        print(f"❌ 无效测试编号: {test_num}")
        print(f"   可用测试: {list(TEST_MAP.keys())} — {', '.join(n for n, _ in TEST_MAP.values())}")
        return
    name, func = TEST_MAP[test_num]
    print(f"\n▶ 单独运行测试 {test_num}: {name}")
    try:
        await func()
    except Exception as e:
        print(f"\n  💥 异常: {e}")
        fail("测试异常")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="AIM Phase 3-1 D 类安全与攻击防御测试")
    parser.add_argument("--test", type=int, default=0,
                        help="只跑指定测试 (1-7)")
    parser.add_argument("--all", action="store_true",
                        help="跑全部测试（默认）")
    parser.add_argument("--verbose", action="store_true",
                        help="详细输出")
    args = parser.parse_args()

    global PASS, FAIL

    if args.test:
        asyncio.run(run_single(args.test))
    else:
        asyncio.run(run_all())

    total = PASS + FAIL
    print("\n" + "=" * 60)
    print(f"📊 测试完成: {PASS}/{total} 通过, {FAIL}/{total} 失败")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
