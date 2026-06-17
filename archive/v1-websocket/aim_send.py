#!/usr/bin/env python3
"""
AIM 统一发送工具 — 标准版
跨所有 Agent 框架通用（Hermes / OpenClaw / QwenPaw / 任何能跑 python 的环境）

设计原则：
1. 零依赖：只依赖 websockets 和 Python 标准库
2. 轻量级：不启动 node.py，直接 WS 连 Hub → 认证 → 发消息 → 断开
3. 统一认证：使用 security.py 模块的 HMAC 签名
4. 统一配置：读取 ~/.hermes/aim/config.json 中的 agents 配置

用法:
  python3 aim_send.py ZS0001 "消息内容"          # 私信
  python3 aim_send.py grp_trio "消息内容" --group  # 群消息
  python3 aim_send.py ZS0003 "消息" --timeout 30   # 自定义超时

也可以作为模块导入:
  from aim_send import send_message
  await send_message("ZS0002", "ZS0001", "你好")
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# 设置 no_proxy 环境变量，避免本地连接被代理拦截
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)

# 自动检测 AIM 目录
AIM_DIR = Path(__file__).resolve().parent
if not (AIM_DIR / "config.json").exists():
    AIM_DIR = Path.home() / ".hermes" / "aim"

CONFIG_FILE = AIM_DIR / "config.json"
SECURITY_MODULE = AIM_DIR / "security.py"


def load_config() -> dict:
    """加载 AIM 配置"""
    if not CONFIG_FILE.exists():
        print(f"ERROR: 配置文件不存在: {CONFIG_FILE}")
        print("提示: 确保 config.json 中有 agents 配置")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def get_hub_url(config: dict) -> str:
    """自动发现 Hub 地址"""
    # 找 role=admin 的节点，那是 Hub
    for aid, info in config.get("agents", {}).items():
        if info.get("role") == "admin" or info.get("name") == "吉量":
            host = info.get("host", "127.0.0.1")
            # 0.0.0.0 是服务端绑定地址，客户端连接需要 127.0.0.1
            if host == "0.0.0.0":
                host = "127.0.0.1"
            port = info.get("port", 18900)
            return f"ws://{host}:{port}"
    # 默认
    return "ws://127.0.0.1:18900"


def get_agent_name(config: dict, agent_id: str) -> str:
    """获取 Agent 的友好名称"""
    info = config.get("agents", {}).get(agent_id, {})
    name = info.get("name", agent_id)
    emoji = info.get("emoji", "")
    return f"{emoji}{name}" if emoji else name


def get_auth_payload(agent_id: str, channel: str = "script") -> dict:
    """使用 AIM 的 security 模块生成认证载荷（标准方式）"""
    try:
        sys.path.insert(0, str(AIM_DIR))
        from security import get_security_manager
        sec = get_security_manager()
        payload = sec.build_auth_payload(agent_id)
        payload["channel"] = channel
        payload["handler"] = False  # 脚本连接不做 AI 处理
        return payload
    except (ImportError, Exception) as e:
        # fallback: 简单 HMAC 签名（兼容小火鸡儿的 aim_send.py 方式）
        print(f"⚠️ security 模块加载失败 ({e})，使用内置 HMAC 认证", file=sys.stderr)
        timestamp = int(time.time())
        secret_file = AIM_DIR / "secrets" / f"{agent_id}.secret"
        if secret_file.exists():
            secret = secret_file.read_text().strip()
        else:
            # 尝试从 config 读取
            config = load_config()
            # 旧版 token 字段
            token = config.get("token", "")
            if token:
                return {"cmd": "auth", "agent_id": agent_id, "token": token, "channel": channel, "handler": False}
            print(f"ERROR: 找不到 {agent_id} 的密钥文件 {secret_file}")
            sys.exit(1)

        import hashlib
        import hmac
        message = f"{agent_id}:{timestamp}"
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return {"cmd": "auth", "agent_id": agent_id, "timestamp": timestamp, "signature": signature, "channel": channel, "handler": False}


async def send_message(from_agent: str, to_agent: str = None, content: str = "",
                       group_id: str = None, timeout: int = 15,
                       channel: str = "script") -> dict:
    """
    发送一条消息到 AIM 系统
    
    参数:
        from_agent: 发送者 ZS ID
        to_agent: 接收者 ZS ID（私信模式）
        content: 消息内容
        group_id: 群组 ID（群聊模式）
        timeout: 连接超时秒数
    
    返回:
        {"success": bool, "msg_id": str, "detail": str}
    """
    config = load_config()
    hub_url = get_hub_url(config)
    
    mode = "group" if group_id else "private"
    target = group_id or to_agent or ""
    target_name = get_agent_name(config, target) if not group_id else target
    
    msg_id = str(uuid.uuid4())[:12]
    
    try:
        async with ws_connect(hub_url, open_timeout=timeout) as ws:
            # Step 1: 认证
            auth_payload = get_auth_payload(from_agent, channel=channel)
            await ws.send(json.dumps(auth_payload))
            
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                auth_resp = json.loads(raw)
            except (asyncio.TimeoutError, json.JSONDecodeError) as e:
                return {"success": False, "msg_id": msg_id,
                        "detail": f"认证响应超时/异常: {e}"}
            
            if auth_resp.get("cmd") != "auth_ok":
                reason = auth_resp.get("reason", "未知原因")
                return {"success": False, "msg_id": msg_id,
                        "detail": f"认证失败: {reason}"}
            
            # Step 2: 发送消息
            msg = {
                "msg_id": msg_id,
                "type": "text",
                "from": from_agent,
                "content": content,
                "timestamp": time.time(),
                "datetime": datetime.now().isoformat(),
            }
            if group_id:
                msg["group"] = group_id
                msg["to"] = None
                send_cmd = {"cmd": "send", "to": group_id, "content": content,
                            "group": True, "msg_id": msg_id}
            else:
                msg["to"] = to_agent
                send_cmd = {"cmd": "send", "to": to_agent, "content": content,
                            "group": False, "msg_id": msg_id}
            
            await ws.send(json.dumps(send_cmd))
            
            # Step 3: 等待 ACK
            try:
                ack = await asyncio.wait_for(ws.recv(), timeout=5)
                ack_data = json.loads(ack)
                delivered = ack_data.get("delivered", True)
                if delivered:
                    return {"success": True, "msg_id": msg_id,
                            "detail": f"✅ 消息已送达 {target_name}({target})"}
                else:
                    return {"success": True, "msg_id": msg_id,
                            "detail": f"⚠️ 消息已发送，但对方可能不在线 {target_name}({target})"}
            except (asyncio.TimeoutError, json.JSONDecodeError):
                return {"success": True, "msg_id": msg_id,
                        "detail": f"消息已发送，未收到ACK确认 {target_name}({target})"}
    
    except (OSError, ConnectionRefusedError) as e:
        return {"success": False, "msg_id": msg_id,
                "detail": f"无法连接 AIM Hub ({hub_url}): Hub 未运行或端口不通"}
    except asyncio.TimeoutError:
        return {"success": False, "msg_id": msg_id,
                "detail": f"连接 AIM Hub 超时 ({hub_url})"}
    except Exception as e:
        return {"success": False, "msg_id": msg_id,
                "detail": f"发送异常: {type(e).__name__}: {e}"}


async def send_group_message(from_agent: str, group_id: str, content: str,
                             timeout: int = 15, channel: str = "script") -> dict:
    """群消息快捷方式"""
    return await send_message(from_agent, content=content, group_id=group_id,
                              timeout=timeout, channel=channel)


def main():
    """CLI 入口"""
    config = load_config()
    default_agent = config.get("node_id", "ZS0002")
    
    if len(sys.argv) < 3:
        print(f"AIM 统一发送工具 v2.0（V2 多 channel 版）")
        print(f"用法:")
        print(f"  python3 {sys.argv[0]} <to_id> <消息>           私信")
        print(f"  python3 {sys.argv[0]} grp_trio <消息> --group  群消息")
        print(f"  python3 {sys.argv[0]} <to_id> <消息> --from ZS0003  指定发送者")
        print(f"  python3 {sys.argv[0]} <to_id> <消息> --channel health  指定 channel")
        print(f"")
        print(f"示例:")
        print(f"  python3 {sys.argv[0]} ZS0001 '你好呱呱'")
        print(f"  python3 {sys.argv[0]} grp_trio '大家好' --group")
        print(f"  python3 {sys.argv[0]} ZS0002 '收到' --from ZS0003")
        sys.exit(1)
    
    to_id = sys.argv[1]
    content = sys.argv[2]
    from_id = default_agent
    is_group = False
    timeout = 15
    channel = "script"
    
    # 解析可选参数
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--from" and i + 1 < len(sys.argv):
            from_id = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--group":
            is_group = True
            i += 1
        elif sys.argv[i] == "--timeout" and i + 1 < len(sys.argv):
            timeout = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--channel" and i + 1 < len(sys.argv):
            channel = sys.argv[i + 1]
            i += 2
        else:
            i += 1
    
    if is_group:
        result = asyncio.run(send_group_message(from_id, to_id, content, timeout, channel))
    else:
        result = asyncio.run(send_message(from_id, to_id, content=content, timeout=timeout, channel=channel))
    
    if result["success"]:
        print(f"✅ {result['detail']}")
    else:
        print(f"❌ {result['detail']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
