#!/usr/bin/env python3
"""
AIM 客户端一键安装程序
用法: python3 aim-install.py --framework crewai

安装流程：
  1. 安装客户端文件到 ~/.aim/
  2. 注册到 AIM Server（自动分配 ID + 随机昵称）
  3. 启动守护进程
  4. 设置昵称（用户自定义，每年可改一次）
"""

import argparse
import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

AIM_HOME = Path.home() / ".aim"
AIM_SERVER = "ws://127.0.0.1:18900"
OPERATOR_ID = "OP0001"

# 随机昵称池（三字/四字组合）
NICK_POOL = [
    "春风", "夏雨", "秋月", "冬雪", "流光", "飞羽",
    "星辰", "云海", "山岚", "暮色", "晨曦", "夜歌",
    "追风", "踏雪", "听雨", "观星", "揽月", "抚琴",
    "逐光", "乘风",
]
ADJ_POOL = [
    "闲适的", "从容的", "自在的", "悠然的", "洒脱的",
    "淡然的", "沉静的", "明快的", "率真的", "澄澈的",
]


def random_nickname() -> str:
    """生成随机昵称"""
    adj = random.choice(ADJ_POOL)
    name = random.choice(NICK_POOL)
    return f"{adj}{name}"


def print_step(step: str, msg: str):
    print(f"\n{'='*50}")
    print(f"  [{step}] {msg}")
    print(f"{'='*50}")


def install_files():
    """第1步：安装客户端文件"""
    print_step("1/4", "安装客户端文件")

    AIM_HOME.mkdir(parents=True, exist_ok=True)
    (AIM_HOME / "logs").mkdir(exist_ok=True)
    (AIM_HOME / "secrets").mkdir(exist_ok=True)

    aim_source = Path(__file__).resolve().parent
    std_source = Path.home() / ".hermes" / "aim"
    if std_source.exists() and "aim-agent.py" not in [f.name for f in aim_source.iterdir()]:
        aim_source = std_source

    # 判断框架类型
    cli_framework = args.framework if args.framework != "callback" else "callback"
    agent_name = args.name or "Agent"
    agent_emoji = args.emoji or "🤖"

    required = ["aim-agent.py", "security.py", "models.py", "framework_cli.py",
                "cli_adapter.py", "ai_types.py"]
    for f in required:
        src = aim_source / f
        dst = AIM_HOME / f
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  ✅ {f}")
        else:
            print(f"  ⚠️  {f} 不存在")

    # callback 模式额外安装 msg_dedup.py 和 archive.py 和 handler.sh
    if args.framework == "callback":
        extra = ["msg_dedup.py", "archive.py"]
        for f in extra:
            src = aim_source / f
            dst = AIM_HOME / f
            if src.exists():
                shutil.copy2(src, dst)
                print(f"  ✅ {f}")
            else:
                print(f"  ⚠️  {f} 不存在（非必需）")

        # handler.sh 只装在 agent-{id}/ 下
        handler_src = aim_source / "handler.sh"
        if handler_src.exists():
            handler_dst = AIM_HOME / "handler.sh"
            shutil.copy2(handler_src, handler_dst)
            handler_dst.chmod(0o755)
            print(f"  ✅ handler.sh（可执行）")
        else:
            print(f"  ⚠️  handler.sh 不存在")

    config = {
        "server": AIM_SERVER,
        "operator_id": OPERATOR_ID,
        "agents": {
            "TEMP": {  # 占位，注册后替换
                "name": agent_name,
                "emoji": agent_emoji,
                "framework": cli_framework,
            }
        },
        "cli_paths": {},
        "commands": {},
    }
    with open(AIM_HOME / "config.json", "w") as f:
        json.dump(config, f, indent=2)
    print(f"  ✅ config.json（server: {AIM_SERVER}）")

    try:
        import websockets
        print(f"  ✅ websockets 已安装")
    except ImportError:
        print(f"  ⚠️  安装依赖: pip install websockets")
        subprocess.run([sys.executable, "-m", "pip", "install", "websockets", "-q"])
        print(f"  ✅ websockets 已安装")

    print(f"\n  📂 安装目录: {AIM_HOME}")

    self_path = Path(__file__).resolve()
    dst_script = AIM_HOME / "aim-install.py"
    if self_path != dst_script:
        shutil.copy2(self_path, dst_script)
        print(f"  ✅ aim-install.py（安装脚本已保存）")


def register_agent(framework: str):
    """第2步：注册获取 ID（自动分配随机昵称）"""
    nickname = random_nickname()
    print_step("2/4", f"注册 Agent")

    async def _register():
        import websockets
        async with websockets.connect(AIM_SERVER, open_timeout=10) as ws:
            req = {
                "cmd": "register",
                "agent_name": nickname,
                "framework": framework,
                "operator_id": OPERATOR_ID,
            }
            await ws.send(json.dumps(req))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(raw)

            if resp.get("cmd") != "register_ok":
                print(f"  ❌ 注册失败: {resp.get('reason')}")
                sys.exit(1)

            agent_id = resp["agent_id"]
            agent_secret = resp["agent_secret"]
            print(f"  ✅ agent_id:     {agent_id}")
            print(f"  🏷️  昵称:        {nickname}")
            print(f"  🔑 密钥已保存     {agent_secret[:20]}...")

            # 保存 secret 文件
            secret_file = AIM_HOME / "secrets" / f"{agent_id}.secret"
            secret_file.write_text(agent_secret)
            secret_file.chmod(0o600)

            # 写入 Agent 配置
            agent_config = {
                "agent_id": agent_id,
                "agent_secret": agent_secret,
                "server": AIM_SERVER,
                "nickname": nickname,
                "nickname_set_at": time.time(),
                "framework": framework,
            }
            with open(AIM_HOME / "agent.json", "w") as f:
                json.dump(agent_config, f, indent=2)

            print(f"\n  📝 昵称可在 agent.json 中修改（每年限 1 次）")
            return agent_id, nickname

    return asyncio.run(_register())


def start_daemon(agent_id: str, framework: str):
    """第3步：启动守护进程"""
    print_step("3/4", "启动守护进程")

    cmd = [
        sys.executable, str(AIM_HOME / "aim-agent.py"),
        "--agent-id", agent_id,
        "--framework", framework,
    ]

    log_file = AIM_HOME / "logs" / f"agent-{agent_id}.log"
    err_file = AIM_HOME / "logs" / f"agent-{agent_id}.error.log"

    proc = subprocess.Popen(
        cmd,
        stdout=open(log_file, "a"),
        stderr=open(err_file, "a"),
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )

    print(f"  ✅ PID: {proc.pid}")
    print(f"  📋 日志: {log_file}")
    return proc.pid


def print_summary(agent_id: str, nickname: str):
    """第4步：打印总结"""
    print_step("4/4", "安装完成")
    print(f"""
  ┌─────────────────────────────────────┐
  │       AIM 客户端安装完成            │
  │                                     │
  │    ID:     {agent_id}
  │    昵称:   {nickname}
  │   目录:    {AIM_HOME}
  │   服务端:  {AIM_SERVER}
  │                                     │
  │   昵称修改: 编辑 ~/.aim/agent.json   │
  │   中的 nickname 字段                │
  │   ⚠️ 每年限修改 1 次               │
  │                                     │
  └─────────────────────────────────────┘

  常用命令:
   停止:   pkill -f 'aim-agent.*{agent_id}'
   卸载:   rm -rf {AIM_HOME}
""")


def main():
    parser = argparse.ArgumentParser(description="AIM 客户端一键安装")
    parser.add_argument("--framework", required=True,
                        help="框架名（crewai/hermes/openclaw）")
    parser.add_argument("--nickname", help="自定义昵称（可选，不传则自动生成）")
    args = parser.parse_args()

    print(f"\n  🚀 AIM 客户端安装程序\n")

    install_files()
    agent_id, nickname = register_agent(args.framework)

    # 如果用户传了自定义昵称，覆盖自动生成的
    if args.nickname:
        nickname = args.nickname
        config_path = AIM_HOME / "agent.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            cfg["nickname"] = nickname
            cfg["nickname_set_at"] = time.time()
            config_path.write_text(json.dumps(cfg, indent=2))
        print(f"  🏷️  已设为自定义昵称: {nickname}")

    pid = start_daemon(agent_id, args.framework)
    print_summary(agent_id, nickname)


if __name__ == "__main__":
    main()
