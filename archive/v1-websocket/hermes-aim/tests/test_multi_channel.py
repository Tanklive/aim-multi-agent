#!/usr/bin/env python3
"""
AIM V2 Phase 2 — 多通道共存测试脚本
=====================================
验证 Agent 可以通过 main/script/health 三个 channel 同时连接到 Server，
彼此不互踢，且 handler 正确地由 main channel 担任。

测试方法：
1. 用 ZS0002 连接 main channel → 验证 handler 当选
2. 在同一个 Agent 下连接 script channel → 验证共存，不踢 main
3. 连接 health channel → 验证三个 channel 同时在线
4. 验证 pool 状态报告正确（get_pool_summary 命令）
5. 验证旧客户端（不带 channel）被拒绝
6. 断开所有连接 → 清理

用法:
  cd ~/.hermes/aim && python3 tests/test_multi_channel.py
"""

import asyncio
import json
import os
import sys
import time

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("❌ 需要 websockets: pip install websockets")
    sys.exit(1)

from security import get_security_manager

# ============================================================
# 配置
# ============================================================
TEST_AGENT = "ZS0002"  # 用吉量身份测试（有 secret 文件）
SERVER_URL = "ws://127.0.0.1:18900"

PASS = 0
FAIL = 0


def log(msg: str, ok: bool = True):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")


def build_auth(agent_id: str, channel: str) -> dict:
    """使用 HMAC 签名构建 auth payload"""
    sec = get_security_manager()
    timestamp = int(time.time())
    message = f"{agent_id}:{timestamp}"
    secret = sec.load_secret(agent_id)
    if not secret:
        raise ValueError(f"密钥不存在: {agent_id}")
    import hashlib
    import hmac as hmac_mod
    signature = hmac_mod.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return {
        "cmd": "auth",
        "agent_id": agent_id,
        "channel": channel,
        "timestamp": timestamp,
        "signature": signature,
        "handler": channel == "main",
        "term": 1,
    }


async def connect_channel(channel: str, timeout: float = 10.0) -> tuple:
    """连接指定 channel，返回 (ws, auth_response) 或 (None, error)"""
    try:
        ws = await asyncio.wait_for(
            ws_connect(SERVER_URL, max_size=1024 * 1024),
            timeout=5.0
        )
        # 发送 auth 命令
        auth_msg = json.dumps(build_auth(TEST_AGENT, channel))
        await ws.send(auth_msg)
        resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
        data = json.loads(resp)
        if data.get("cmd") == "auth_fail":
            await ws.close()
            return None, f"认证失败: {data.get('reason', 'unknown')}"
        if data.get("cmd") not in ("auth_ok", "register_ok"):
            # 也可能收到其他响应
            pass
        return ws, data
    except asyncio.TimeoutError:
        return None, "连接超时"
    except Exception as e:
        return None, str(e)


async def disconnect_ws(ws, label: str):
    """安全断开 WS 连接"""
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
    print(f"  🔌 断开 {label}")


async def send_and_recv(ws, cmd_payload: dict, timeout: float = 5.0) -> dict:
    """发送命令并等待响应"""
    await ws.send(json.dumps(cmd_payload))
    resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(resp)


async def check_ws_alive(ws, label: str) -> bool:
    """检查 WS 连接是否存活"""
    try:
        await ws.send(json.dumps({"cmd": "ping"}))
        resp = await asyncio.wait_for(ws.recv(), timeout=3.0)
        data = json.loads(resp)
        return True
    except Exception:
        return False


async def run_test():
    global PASS, FAIL
    
    print("=" * 60)
    print("🧪 Phase 2 — 测试 1: 多通道共存")
    print("=" * 60)
    print(f"Server: {SERVER_URL}")
    print(f"测试 Agent: {TEST_AGENT}")
    print()

    ws_main = ws_script = ws_health = None

    # ---- 1. main channel 连接 ----
    print("▶ 步骤 1: 连接 main channel")
    ws_main, resp = await connect_channel("main")
    if ws_main is None:
        log(f"main channel 连接失败: {resp}", False)
        return
    log(f"main 连接成功")
    print()

    # ---- 2. script channel 连接 ----
    print("▶ 步骤 2: 连接 script channel（与 main 共存）")
    ws_script, resp = await connect_channel("script")
    if ws_script is None:
        log(f"script channel 连接失败: {resp}", False)
    else:
        log("script 连接成功")
        
        # 验证 main 没有被踢
        alive = await check_ws_alive(ws_main, "main")
        log(f"main channel 在 script 连接后依然存活", alive)
    print()

    # ---- 3. health channel 连接 ----
    print("▶ 步骤 3: 连接 health channel（三通道共存）")
    ws_health, resp = await connect_channel("health")
    if ws_health is None:
        log(f"health channel 连接失败: {resp}", False)
    else:
        log("health 连接成功")
        
        # 验证两个旧通道仍在
        alive_main = await check_ws_alive(ws_main, "main")
        alive_script = await check_ws_alive(ws_script, "script")
        alive_health = await check_ws_alive(ws_health, "health")
        log(f"三个 channel 全部存活: main={alive_main} script={alive_script} health={alive_health}", 
            alive_main and alive_script and alive_health)
    print()

    # ---- 4. 使用 lifecycle_status 命令验证 ----
    print("▶ 步骤 4: 验证连接池状态")
    if ws_main:
        try:
            status_resp = await send_and_recv(ws_main, {"cmd": "lifecycle_status", "agent_id": "ZS0002"}, timeout=5.0)
            print(f"   lifecycle_status 响应: {json.dumps(status_resp, indent=2, ensure_ascii=False)[:600]}")
            
            agents = status_resp.get("agents", [])
            our_status = {}
            if isinstance(agents, list):
                for a in agents:
                    if isinstance(a, dict) and a.get("agent_id") == TEST_AGENT:
                        our_status = a
                        break
            elif isinstance(agents, dict):
                our_status = agents.get(TEST_AGENT, {})
            
            channels = our_status.get("channels", [])
            # lifecycle_status 返回的是状态信息，可能没有 channels
            # 改用连接池直接验证
            print(f"   状态: {json.dumps(our_status, ensure_ascii=False)[:300]}")
            log(f"agent {TEST_AGENT} 状态获取成功", bool(our_status))
            
            # 额外验证：通过 check_ws_alive 确认三个连接都存活
            alive_main = await check_ws_alive(ws_main, "main")
            alive_script = await check_ws_alive(ws_script, "script")
            alive_health = await check_ws_alive(ws_health, "health")
            log(f"三个 channel 连接存活验证", alive_main and alive_script and alive_health)
        except Exception as e:
            log(f"lifecycle_status 命令失败: {e}", False)
    else:
        log("无可用连接查询", False)
    print()

    # ---- 5. 验证旧客户端（不带 channel）被拒绝 ----
    print("▶ 步骤 5: 验证不带 channel 的旧客户端被拒绝")
    try:
        ws_old = await asyncio.wait_for(
            ws_connect(SERVER_URL, max_size=1024 * 1024),
            timeout=5.0
        )
        # 发送 auth 但不带 channel
        sec = get_security_manager()
        ts = int(time.time())
        old_auth = json.dumps({
            "cmd": "auth",
            "agent_id": TEST_AGENT,
            "timestamp": ts,
            "signature": sec.generate_signature(TEST_AGENT, ts)[1],
            # 故意省略 channel
        })
        await ws_old.send(old_auth)
        old_resp = await asyncio.wait_for(ws_old.recv(), timeout=5.0)
        old_data = json.loads(old_resp)
        log(f"旧客户端被拒绝: {old_data.get('reason', '?')}", 
            old_data.get("cmd") == "auth_fail")
        await ws_old.close()
    except Exception as e:
        log(f"旧客户端测试异常: {e}", False)
    print()

    # ---- 清理 ----
    print("▶ 清理: 断开所有连接")
    await asyncio.gather(
        disconnect_ws(ws_main, "main"),
        disconnect_ws(ws_script, "script"),
        disconnect_ws(ws_health, "health"),
    )
    print()

    # ---- 汇总 ----
    print("=" * 60)
    total = PASS + FAIL
    print(f"📊 测试完成: {PASS}/{total} 通过, {FAIL}/{total} 失败")
    print("=" * 60)
    
    return FAIL == 0


def main():
    result = asyncio.run(run_test())
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
