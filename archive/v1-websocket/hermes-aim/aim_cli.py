#!/usr/bin/env python3
"""
AIM CLI — 标准 AIM 客户端工具

兼容所有 Agent 框架（Hermes/OpenClaw/CrewAI/AutoGen/LangGraph...）
只要：能发 JSON 到 WS，能收 JSON 从 WS，能响应心跳

用法：
  aim send --to ZS0001 --msg "你好"
  aim watch                     # 实时监听所有消息
  aim watch --from ZS0001       # 只看呱呱发的
  aim watch --to ZS0002         # 只看发给我的
  aim watch --grep "关键词"     # 内容过滤
  aim history                   # 最近消息
  aim status                    # 连接状态
  aim help                      # 帮助
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# ====== 配置 ======

def get_config():
    """获取配置：环境变量 > 配置文件 > 默认值"""
    config = {}
    
    # 1. 配置文件（按优先级）
    config_paths = [
        Path("~/.hermes/aim/config.json").expanduser(),
        Path("config.json"),
        Path("~/.aim/config.json").expanduser(),
    ]
    for p in config_paths:
        if p.exists():
            try:
                with open(p) as f:
                    cfg = json.load(f)
                    domain = cfg.get("domain", "")
                    if domain and not domain.startswith(("ws://", "wss://")):
                        config["server"] = f"ws://{domain}:18900"
                    else:
                        config["server"] = domain or ""
                    config["agent_id"] = cfg.get("node_id", "")
                    # 从 tokens.json 读取 token
                    tokens_candidates = [
                        Path("~/.hermes/aim/tokens.json").expanduser(),
                        Path("tokens.json"),
                        Path("~/.aim/tokens.json").expanduser(),
                    ]
                    for tc in tokens_candidates:
                        if tc.exists():
                            try:
                                with open(tc) as tf:
                                    tokens = json.load(tf)
                                    if config.get("agent_id") in tokens:
                                        config["token"] = tokens[config["agent_id"]]
                                        break
                            except:
                                pass
                    # 读取 secret 文件
                    secret_files = [
                        Path(f"~/.aim/secrets/{config.get('agent_id', '')}.secret").expanduser(),
                        Path(f"secrets/{config.get('agent_id', '')}.secret"),
                    ]
                    for sf in secret_files:
                        if sf.exists():
                            config["secret"] = sf.read_text().strip()
                            break
            except:
                pass
            break
    
    # 2. 环境变量（覆盖配置文件）
    server_env = os.environ.get("AIM_SERVER_URL", "")
    config["server"] = server_env or "ws://127.0.0.1:18900"
    config["token"] = os.environ.get("AIM_TOKEN", config.get("token", ""))
    config["agent_id"] = os.environ.get("AIM_AGENT_ID", config.get("agent_id", "")) or "ZS0002"
    config["secret"] = os.environ.get("AIM_SECRET", config.get("secret", ""))
    
    return config


def get_auth_payload(config):
    """构建认证 payload"""
    payload = {
        "cmd": "auth",
        "agent_id": config["agent_id"],
        "channel": "script",
        "handler": False,
    }
    
    # 优先 HMAC 签名
    if config.get("secret"):
        ts = int(time.time())
        import hmac, hashlib
        message = f'{config["agent_id"]}:{ts}'
        sig = hmac.new(config["secret"].encode(), message.encode(), hashlib.sha256).hexdigest()
        payload["signature"] = sig
        payload["timestamp"] = ts
    elif config.get("token"):
        payload["token"] = config["token"]
    
    return payload


# ====== SEND 命令 ======

async def cmd_send(args):
    """发送消息"""
    config = get_config()
    if args.to:
        config["to"] = args.to
    if args.msg:
        config["msg"] = args.msg
    if args.group:
        config["group"] = args.group
    
    import websockets
    
    try:
        async with websockets.connect(config["server"], open_timeout=5, ping_interval=None) as ws:
            # 认证
            auth = get_auth_payload(config)
            await ws.send(json.dumps(auth))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            
            if resp.get("cmd") != "auth_ok":
                print(f"❌ 认证失败: {resp.get('reason', '未知')}")
                return 1
            
            # 发送
            send_cmd = {
                "cmd": "send",
                "to": args.to,
                "content": args.msg,
                "group": args.group or False,
                "channel": "script",
            }
            await ws.send(json.dumps(send_cmd))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            
            if ack.get("cmd") == "ack":
                print(f"✅ 消息已送达 {args.to}")
                return 0
            else:
                print(f"⚠️ 发送完成: {ack.get('cmd', 'unknown')}")
                return 0
    except asyncio.TimeoutError:
        print("❌ 连接超时")
        return 1
    except ConnectionRefusedError:
        print("❌ 连接被拒绝，AIM Server 是否在运行？")
        return 1
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return 1


# ====== WATCH 命令 ======

async def cmd_watch(args):
    """实时监听 AIM 消息"""
    config = get_config()
    no_from = args.from_filter
    no_to = args.to_filter
    grep = args.grep
    show_json = args.json
    
    import websockets
    
    last_reconnect = 0
    retry_count = 0
    
    while True:
        try:
            async with websockets.connect(config["server"], open_timeout=5, ping_interval=20) as ws:
                # 认证
                auth = get_auth_payload(config)
                await ws.send(json.dumps(auth))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                
                if resp.get("cmd") != "auth_ok":
                    print(f"\r{'[DISCONNECTED]':>20} 认证失败: {resp.get('reason', '')}", end="", flush=True)
                    await asyncio.sleep(5)
                    continue
                
                retry_count = 0
                print(f"\r{'[CONNECTED]':>20}   监听从 {config['server']}", flush=True)
                
                # 监听消息
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        cmd = msg.get("cmd", "")
                        
                        # 只处理消息类型
                        if cmd not in ("message", "chat_message", "send"):
                            continue
                        
                        # 过滤
                        f = msg.get("from_id", msg.get("from", ""))
                        t = msg.get("to_id", msg.get("to", ""))
                        content = msg.get("content", "")
                        
                        if no_from and f != no_from:
                            continue
                        if no_to and t != no_to:
                            continue
                        if grep and grep not in content:
                            continue
                        
                        # 输出
                        ts = datetime.now().strftime("%H:%M:%S")
                        if show_json:
                            print(json.dumps({"ts": ts, "from": f, "to": t, "content": content}, ensure_ascii=False))
                        else:
                            # 截断长消息
                            display = content[:120].replace("\n", " ")
                            print(f"[{ts}] {f} → {t}: {display}")
                            if len(content) > 120:
                                print(f"{'':>5}  ... (共 {len(content)} 字符)")
                        
                        sys.stdout.flush()
                        
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        print(f"\r{'[WARN]':>20} 解析异常: {e}", flush=True)
                        
        except (ConnectionRefusedError, OSError) as e:
            now = time.time()
            if now - last_reconnect > 10:
                retry_count += 1
                delay = min(2 ** retry_count, 30)
                print(f"\r{'[RECONNECTING]':>20} 连接失败，{delay}s 后重试 (第{retry_count}次)", end="", flush=True)
                last_reconnect = now
            await asyncio.sleep(1)
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"\r{'[ERROR]':>20} {e}", flush=True)
            await asyncio.sleep(5)
            continue


# ====== WATCH-LOG 命令 ======

def cmd_watch_log(args):
    """查看 Observer 历史日志（JSONL 持久化文件）"""
    target = args.target
    limit = args.limit or 20
    grep = args.grep
    show_json = args.json
    
    log_dir = Path.home() / "shared" / "aim" / "logs" / "observer"
    
    if target:
        log_file = log_dir / f"observer-{target}.jsonl"
        if not log_file.exists():
            print(f"无日志文件: {log_file}")
            print(f"可用目标:")
            for f in sorted(log_dir.glob("observer-*.jsonl")):
                name = f.stem.replace("observer-", "")
                count = sum(1 for _ in open(f))
                print(f"  {name} ({count} 条)")
            return 1
        files = [log_file]
    else:
        files = sorted(log_dir.glob("observer-*.jsonl"))
        if not files:
            print(f"无日志文件: {log_dir}")
            return 1
        print(f"Observer 日志目录: {log_dir}")
        print(f"共 {len(files)} 个目标:\n")
        for f in files:
            name = f.stem.replace("observer-", "")
            count = sum(1 for _ in open(f))
            print(f"  {name}: {count} 条")
        print(f"\n用 --target <ID> 查看具体日志")
        return 0
    
    # 读取日志
    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                msg = entry.get("msg", {})
                content = msg.get("content", "")
                
                # grep 过滤
                if grep and grep not in content and grep not in json.dumps(msg, ensure_ascii=False):
                    continue
                
                entries.append(entry)
            except json.JSONDecodeError:
                continue
    
    if not entries:
        print(f"日志为空或无匹配: {log_file}")
        return 1
    
    # 取最后 N 条
    for entry in entries[-limit:]:
        msg = entry.get("msg", {})
        dt = entry.get("dt", "")
        cmd = msg.get("cmd", msg.get("msg_type", ""))
        from_id = msg.get("from_id", msg.get("from", ""))
        to_id = msg.get("to_id", msg.get("to", ""))
        content = msg.get("content", "")
        
        if show_json:
            print(json.dumps(entry, ensure_ascii=False))
        else:
            # 截断长消息
            display = content[:120].replace("\n", " ") if content else ""
            tag = f"[{cmd}]" if cmd else ""
            print(f"[{dt}] {tag} {from_id} → {to_id}: {display}")
            if len(content) > 120:
                print(f"{'':>5}  ... (共 {len(content)} 字符)")
    
    print(f"\n共 {len(entries)} 条记录 (显示最后 {min(limit, len(entries))} 条)")
    return 0


# ====== HISTORY 命令 ======

def cmd_history(args):
    """查看历史消息"""
    config = get_config()
    limit = args.limit or 10
    data_dir = Path.home() / ".hermes" / "aim" / "data"
    msg_file = data_dir / "messages.jsonl"
    
    if not msg_file.exists():
        print("无消息历史（文件不存在）")
        return 1
    
    with open(msg_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    if not lines:
        print("无消息历史")
        return 1
    
    # 取最后 N 条
    for line in lines[-limit:]:
        try:
            msg = json.loads(line.strip())
            ts = msg.get("datetime", msg.get("timestamp", ""))
            f = msg.get("from_id", msg.get("from", ""))
            t = msg.get("to_id", msg.get("to", ""))
            content = msg.get("content", "")[:100].replace("\n", " ")
            print(f"[{ts}] {f} → {t}: {content}")
        except:
            pass
    
    return 0


# ====== STATUS 命令 ======

async def cmd_status(args):
    """检查 AIM 连接状态"""
    config = get_config()
    
    import websockets
    
    print(f"AIM Server: {config['server']}")
    print(f"Agent ID: {config['agent_id']}")
    print(f"认证方式: {'HMAC' if config.get('secret') else 'Token' if config.get('token') else '无'}")
    print()
    
    try:
        async with websockets.connect(config["server"], open_timeout=3, ping_interval=None) as ws:
            auth = get_auth_payload(config)
            await ws.send(json.dumps(auth))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            
            if resp.get("cmd") == "auth_ok":
                print(f"\r{'✅':>4} 连接正常")
                print(f"    Agent: {resp.get('agent', {}).get('name', config['agent_id'])}")
                if "groups" in resp:
                    print(f"    群组: {', '.join(str(g) for g in resp.get('groups', []))}")
                if "agents" in resp:
                    online = [a for a in resp['agents'] if a.get('online')]
                    print(f"    在线 Agent: {len(online)}")
                    for a in online:
                        print(f"      {a.get('emoji','')} {a.get('name','')}({a.get('agent_id','')})")
                return 0
            else:
                print(f"\r{'❌':>4} 认证失败: {resp.get('reason', '')}")
                return 1
    except ConnectionRefusedError:
        print(f"\r{'❌':>4} 连接被拒绝")
        return 1
    except asyncio.TimeoutError:
        print(f"\r{'❌':>4} 连接超时")
        return 1


# ====== 主入口 ======

def main():
    parser = argparse.ArgumentParser(
        prog="aim",
        description="AIM CLI — 标准 Agent 通讯客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  aim send --to ZS0001 --msg "你好"
  aim watch                     # 实时监听
  aim watch --from ZS0001       # 只看呱呱
  aim watch --grep "测试"       # 内容过滤
  aim history                   # 最近10条
  aim watch-log --target ZS0001 # Observer 历史日志
  aim watch-log                 # 列出所有 Observer 目标
  aim status                    # 连接状态
  
环境变量:
  AIM_SERVER_URL   Server 地址 (默认: ws://127.0.0.1:18900)
  AIM_TOKEN        认证 Token
  AIM_AGENT_ID     Agent ID (默认: ZS0002)
  AIM_SECRET       认证密钥 (HMAC)
        """
    )
    
    sub = parser.add_subparsers(dest="command", help="子命令")
    
    # send
    p_send = sub.add_parser("send", help="发送消息")
    p_send.add_argument("--to", required=True, help="接收方 Agent ID")
    p_send.add_argument("--msg", required=True, help="消息内容")
    p_send.add_argument("--group", help="群组名")
    
    # watch
    p_watch = sub.add_parser("watch", help="实时监听消息")
    p_watch.add_argument("--from", dest="from_filter", help="只显示指定发送方")
    p_watch.add_argument("--to", dest="to_filter", help="只显示指定接收方")
    p_watch.add_argument("--grep", help="内容过滤关键词")
    p_watch.add_argument("--json", action="store_true", help="JSON 格式输出")
    
    # history
    p_hist = sub.add_parser("history", help="查看历史消息")
    p_hist.add_argument("--limit", type=int, default=10, help="条数 (默认: 10)")
    
    # status
    sub.add_parser("status", help="连接状态")
    
    # watch-log
    p_watch_log = sub.add_parser("watch-log", help="查看 Observer 历史日志")
    p_watch_log.add_argument("--target", help="目标 Agent ID (如 ZS0001)")
    p_watch_log.add_argument("--limit", type=int, default=20, help="条数 (默认: 20)")
    p_watch_log.add_argument("--grep", help="内容过滤关键词")
    p_watch_log.add_argument("--json", action="store_true", help="JSON 格式输出")
    
    # observer
    p_observer = sub.add_parser("observer", help="Observer 模式（实时观察客户端状态）")
    p_observer.add_argument("target", help="要 watch 的目标 agent_id (如 ZS0001)")
    p_observer.add_argument("--verbose", "-v", action="store_true", help="显示推理摘要")
    p_observer.add_argument("--last-seq", type=int, default=0, help="上次断连的 seq")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == "send":
        return asyncio.run(cmd_send(args))
    elif args.command == "watch":
        try:
            return asyncio.run(cmd_watch(args))
        except KeyboardInterrupt:
            print()
            return 0
    elif args.command == "watch-log":
        return cmd_watch_log(args)
    elif args.command == "history":
        return cmd_history(args)
    elif args.command == "status":
        return asyncio.run(cmd_status(args))
    elif args.command == "observer":
        from aim_observer import run_observer
        try:
            config = get_config()
            return asyncio.run(run_observer(
                config["server"],
                config["agent_id"],
                args.target,
                args.verbose,
                args.last_seq,
            ))
        except KeyboardInterrupt:
            print()
            return 0
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
