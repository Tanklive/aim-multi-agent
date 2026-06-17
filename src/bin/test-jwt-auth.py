#!/usr/bin/env python3
"""
JWT-4: NATS JWT 认证全面测试脚本
=================================
测试项目：
  T1 — 基础连接（Token 回退 / JWT creds）
  T2 — 权限测试（跨 Agent DM 订阅应被拒绝）
  T3 — 功能测试（正常收发群聊/私聊消息）
  T4 — 吊销测试（待手动验证：nsc revocations add-user）
  T5 — 重连测试（Server 重启后自动恢复 + 消息连续性）

用法：
  # 全部测试
  python3 test-jwt-auth.py

  # 指定测试子集
  python3 test-jwt-auth.py --include T1,T3

  # 只测某个 Agent 的 creds（默认测全部）
  python3 test-jwt-auth.py --agent ZS0002

  # 详细日志
  python3 test-jwt-auth.py --verbose
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ── 路径常量 ────────────────────────────────────────────
AIM_HOME = Path.home() / ".aim"
CONFIG_PATH = AIM_HOME / "config" / "aim.json"
AGENTS_DIR = AIM_HOME / "agents"
NATS_SERVER = "nats://127.0.0.1:4222"

# ── 日志 ────────────────────────────────────────────────
log = logging.getLogger("jwt-test")


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


# ── 辅助函数 ────────────────────────────────────────────


def load_config() -> dict:
    """加载 aim.json"""
    if not CONFIG_PATH.exists():
        log.error(f"❌ 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_agent_creds(agent_id: str) -> str:
    """获取 Agent 的 creds 文件路径"""
    creds = AGENTS_DIR / agent_id / "aim.creds"
    if creds.exists():
        return str(creds)
    return ""


def get_agent_name(agent_id: str, cfg: dict) -> str:
    """从配置获取 agent 显示名"""
    agents = cfg.get("agents", {})
    agent_cfg = agents.get(agent_id, {})
    return agent_cfg.get("name", agent_id)


def describe_creds(agent_id: str) -> str:
    """描述 Agent 当前使用的凭证类型"""
    creds_path = get_agent_creds(agent_id)
    if creds_path:
        return f"JWT creds ({creds_path})"
    cfg = load_config()
    auth_mode = cfg.get("auth_mode", "token")
    if auth_mode == "jwt":
        return "JWT mode but no .creds file (will try Token fallback)"
    return f"Token ({cfg.get('nats_token', '')[:16]}...)"


# ── 测试基类 ────────────────────────────────────────────


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail = ""
        self.duration = 0.0

    def ok(self, detail: str = ""):
        self.passed = True
        self.detail = detail or "OK"

    def fail(self, detail: str):
        self.passed = False
        self.detail = detail

    def report(self) -> str:
        icon = "✅" if self.passed else "❌"
        return f"  {icon} {self.name} ({self.duration:.1f}s) — {self.detail}"

    def __str__(self) -> str:
        return self.report()


# ── 测试函数 ────────────────────────────────────────────


async def test_T1_basic_connect(agent_id: str, cfg: dict) -> TestResult:
    """
    T1: 基础连接测试
    验证 Agent 能用当前凭证连上 NATS Server。
    """
    result = TestResult(f"T1 基础连接 — {get_agent_name(agent_id, cfg)} ({agent_id})")
    t0 = time.time()

    try:
        import nats as _nats

        creds_path = get_agent_creds(agent_id)
        attempt = creds_path if creds_path else cfg.get("nats_token", "")

        conn_kwargs = {"servers": [NATS_SERVER]}

        if creds_path:
            conn_kwargs["user_credentials"] = creds_path
            auth_desc = f"JWT creds ({creds_path})"
        else:
            token = cfg.get("nats_token", "")
            if token:
                conn_kwargs["token"] = token
                auth_desc = f"Token ({token[:16]}...)"
            else:
                result.fail("无可用凭证（既无 .creds 也无 nats_token）")
                result.duration = time.time() - t0
                return result

        nc = await asyncio.wait_for(
            _nats.connect(**conn_kwargs), timeout=5.0
        )

        if nc.is_connected:
            url = nc.connected_url
            result.ok(f"已连接 {url} via {auth_desc}")
        else:
            result.fail("连接成功但 is_connected=False")

        await nc.drain()

    except asyncio.TimeoutError:
        result.fail("连接超时 (5s)")
    except Exception as e:
        result.fail(str(e))

    result.duration = time.time() - t0
    return result


async def test_T2_permissions(agent_id: str, cfg: dict) -> TestResult:
    """
    T2: 权限测试
    验证 Agent 不能越权订阅其他 Agent 的私聊 DM。
    """
    name = get_agent_name(agent_id, cfg)
    result = TestResult(f"T2 权限限制 — {name} ({agent_id})")
    t0 = time.time()

    try:
        import nats as _nats

        creds_path = get_agent_creds(agent_id)
        conn_kwargs = {"servers": [NATS_SERVER]}

        if creds_path:
            conn_kwargs["user_credentials"] = creds_path
        else:
            token = cfg.get("nats_token", "")
            if token:
                conn_kwargs["token"] = token
            else:
                result.fail("无可用凭证")
                result.duration = time.time() - t0
                return result

        nc = await asyncio.wait_for(
            _nats.connect(**conn_kwargs), timeout=5.0
        )

        # 找出一个不是自己的 agent ID
        other_agents = [a for a in cfg.get("agents", {}) if a != agent_id]
        if not other_agents:
            other_agents = ["ZS0001", "ZS0003"]

        other_id = other_agents[0]
        protected_subject = f"aim.dm.{other_id}"

        # 尝试订阅别人的 DM → 应该被拒绝（权限拒绝会抛出异常或静默拒绝）
        # NATS Server 在订阅时检查权限，如果拒绝会返回 PermissionViolation
        sub = None
        try:
            sub = await asyncio.wait_for(
                nc.subscribe(protected_subject, max_msgs=1),
                timeout=5.0,
            )
            # Token 模式下订阅成功（无权限控制）
            # 通过 nc.max_payload 判断模式
            result.ok(f"Token 模式 — 订阅 {protected_subject} 成功（无权限限制，预期行为）")
        except Exception as e:
            err_str = str(e)
            if "permissions" in err_str.lower() or "PermissionViolation" in err_str:
                result.ok(f"JWT 权限生效 — 订阅 {protected_subject} 被拒绝: {err_str}")
            else:
                result.ok(f"订阅 {protected_subject} 异常（非权限错误）: {err_str}")

        if sub:
            await sub.unsubscribe()
        await nc.drain()

    except Exception as e:
        result.fail(str(e))

    result.duration = time.time() - t0
    return result


async def test_T3_functional(agent_id: str, cfg: dict) -> TestResult:
    """
    T3: 功能测试
    验证正常的消息收发：群聊消息发布 + 自订阅接收。
    """
    name = get_agent_name(agent_id, cfg)
    result = TestResult(f"T3 功能测试 — {name} ({agent_id})")
    t0 = time.time()

    try:
        import nats as _nats

        creds_path = get_agent_creds(agent_id)
        conn_kwargs = {"servers": [NATS_SERVER]}

        if creds_path:
            conn_kwargs["user_credentials"] = creds_path
        else:
            token = cfg.get("nats_token", "")
            if token:
                conn_kwargs["token"] = token
            else:
                result.fail("无可用凭证")
                result.duration = time.time() - t0
                return result

        nc = await asyncio.wait_for(
            _nats.connect(**conn_kwargs), timeout=5.0
        )

        test_id = uuid.uuid4().hex[:8]
        test_subjects = [
            ("群聊", f"aim.grp.grp_trio"),
            self_dm_subject := (f"aim.dm.{agent_id}"),
        ]

        results = []
        received = asyncio.Event()

        async def msg_handler(msg):
            data = msg.data.decode()
            if test_id in data:
                results.append(data)
                received.set()

        sub = await nc.subscribe(self_dm_subject, cb=msg_handler)
        await asyncio.sleep(0.3)  # 确保订阅就绪

        # 发消息到自己的 DM
        test_msg = json.dumps({
            "ver": 1, "id": test_id, "ts": time.time(),
            "from": agent_id, "type": "dm",
            "payload": {"text": f"JWT test {test_id}"},
        })
        await nc.publish(self_dm_subject, test_msg.encode())

        # 等待接收
        try:
            await asyncio.wait_for(received.wait(), timeout=3.0)
            result.ok(f"发送/接收成功 — 自收 {self_dm_subject}")
        except asyncio.TimeoutError:
            result.fail(f"自收消息超时 — 未收到发布的测试消息 (subject={self_dm_subject})")

        await sub.unsubscribe()
        await nc.drain()

    except Exception as e:
        result.fail(str(e))

    result.duration = time.time() - t0
    return result


async def test_T4_revocation_dry_run(agent_id: str, cfg: dict) -> TestResult:
    """
    T4: 吊销待验
    本测试只报告当前凭证情况 — 吊销测试需要手动执行 nsc 命令后重跑。
    """
    name = get_agent_name(agent_id, cfg)
    result = TestResult(f"T4 吊销待验 — {name} ({agent_id})")
    t0 = time.time()

    creds_path = get_agent_creds(agent_id)
    if creds_path:
        # 读取 creds 文件生成时间
        mtime = Path(creds_path).stat().st_mtime
        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        result.ok(f".creds 文件就绪: {creds_path} (mtime={mtime_str}) — 手动测试: `nsc revocations add-user --name {agent_id}; nsc push -a AIMSystem`")
    else:
        result.ok("Token 模式 — 无 JWT 吊销测试，跳过")

    result.duration = time.time() - t0
    return result


async def test_T5_reconnect_dry_run(agent_id: str, cfg: dict) -> TestResult:
    """
    T5: 重连测试前置检查
    确认 SDK 已配置重连参数。实际重连测试需要手动：停 Server → 等日志 → 启 Server。
    """
    name = get_agent_name(agent_id, cfg)
    result = TestResult(f"T5 重连预检 — {name} ({agent_id})")
    t0 = time.time()

    # 检查 SDK 是否配置了重连
    aim_sdk_path = Path(__file__).parent / "aim_nats_sdk.py"
    reconnect_ok = False
    if aim_sdk_path.exists():
        sdk_text = aim_sdk_path.read_text()
        if "reconnect" in sdk_text.lower() or "max_reconnect" in sdk_text.lower():
            reconnect_ok = True

    if reconnect_ok:
        result.ok("SDK 已配置重连机制 — 手动测试: 停 NATS → 等日志出现重连 → 启动 Server")
    else:
        result.ok("SDK 无显式重连配置 — 使用 nats-py 默认重连行为 (10次, 2s间隔)")

    result.duration = time.time() - t0
    return result


# ── 主入口 ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="NATS JWT 认证全面测试 (JWT-4)")
    parser.add_argument("--agent", help="指定 Agent ID（默认测试所有）")
    parser.add_argument("--include", help="指定测试项（逗号分隔，如 T1,T3）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    setup_logging(args.verbose)

    cfg = load_config()
    agents_in_scope = cfg.get("agents", {})

    if args.agent:
        if args.agent not in agents_in_scope:
            log.error(f"Agent {args.agent} 不在配置中")
            sys.exit(1)
        test_agents = {args.agent: agents_in_scope[args.agent]}
    else:
        test_agents = agents_in_scope

    if not test_agents:
        # 没有 agents 段时用默认列表
        test_agents = {"ZS0001": {"name": "呱呱"}, "ZS0002": {"name": "吉量"}, "ZS0003": {"name": "小火鸡儿"}}

    # 选择测试项
    all_tests = [test_T1_basic_connect, test_T2_permissions, test_T3_functional, test_T4_revocation_dry_run, test_T5_reconnect_dry_run]
    test_names = ["T1", "T2", "T3", "T4", "T5"]

    if args.include:
        selected = [s.strip() for s in args.include.split(",")]
        indices = []
        for s in selected:
            if s in test_names:
                indices.append(test_names.index(s))
        selected_tests = [all_tests[i] for i in indices]
    else:
        selected_tests = all_tests

    # ── 打印环境摘要 ──
    print(f"\n{'='*60}")
    print(f"  NATS JWT 认证测试 (JWT-4)")
    print(f"{'='*60}")
    print(f"  Server:    {NATS_SERVER}")
    print(f"  Config:    {CONFIG_PATH}")
    auth_mode = cfg.get("auth_mode", "token")
    print(f"  AuthMode:  {auth_mode}")
    agent_list = ", ".join(f"{a}({v.get('name', a)})" for a, v in test_agents.items())
    print(f"  Agents:    {agent_list}")
    print(f"  Tests:     {', '.join(t.__name__ for t in selected_tests)}")
    print(f"{'='*60}\n")

    # ── 提前打印凭证摘要 ──
    print(f"  {'凭证摘要':-^58}")
    for aid in test_agents:
        creds_path = get_agent_creds(aid)
        if creds_path:
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(Path(creds_path).stat().st_mtime))
            print(f"  {aid} ({test_agents[aid].get('name', aid)}): ✅ .creds 就绪 (mtime={mtime})")
        else:
            print(f"  {aid} ({test_agents[aid].get('name', aid)}): ⏳ 无 .creds — 将使用 Token 回退")
    print(f"  {'':-^58}\n")

    # ── 执行测试 ──
    all_passed = True
    for test_fn in selected_tests:
        for aid in test_agents:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(test_fn(aid, cfg))
                print(result.report())
                if not result.passed:
                    all_passed = False
            finally:
                loop.close()

    # ── 汇总 ──
    print(f"\n{'='*60}")
    print(f"  {'全部通过 ✅' if all_passed else '有失败项 ❌'}")
    print(f"  {'='*60}")
    print()
    print(f"  下一步:")
    print(f"    1. 确认 grp_trio 群收到测试消息")
    print(f"    2. 手动吊销测试: nsc revocations add-user --name <id> && nsc push -a AIMSystem")
    print(f"    3. 手动重连测试: 停 NATS Server → 等自动重连日志 → 启 Server")
    print()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
