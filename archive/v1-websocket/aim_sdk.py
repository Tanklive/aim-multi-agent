#!/usr/bin/env python3
"""
AIM Agent SDK — 轻量级 Agent 接入 AIM 的标准接口

任何框架（LangChain/AutoGen/Claude Code/Eliza/...）只需要实现：
  1. 能运行 `aim_send.py`（发送消息）
  2. 能接收 WebSocket 消息（或在 AIM Hub 上注册 callback）

用法:
  from aim_sdk import AIMAgent
  
  agent = AIMAgent(agent_id="ZS0004", name="我的Agent")
  
  # 发送消息
  agent.send("ZS0001", "你好呱呱")
  
  # 发送任务
  agent.task("ZS0001", "review", "评审代码", body="请review main.py")
  
  # 接收消息（callback模式）
  agent.on_message = lambda msg: print(f"收到: {msg}")
  agent.listen()  # 阻塞监听
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    print("⚠️  pip install websockets")
    sys.exit(1)


class AIMAgent:
    """AIM Agent SDK — 让不同框架的 Agent 能通过 AIM 说话
    
    这不是强制标准，只是一个参考实现。
    你完全可以：
    - 用 raw WebSocket 直接连
    - 用 curl 发 HTTP
    - 只用 aim_send.py 发消息，不用这个类
    
    怎么方便怎么来。
    """
    
    def __init__(self, agent_id: str, name: str = "", hub_url: str = "ws://127.0.0.1:18900",
                 aim_dir: str = None, emoji: str = ""):
        self.agent_id = agent_id
        self.name = name or agent_id
        self.hub_url = hub_url
        self.emoji = emoji
        self._seq = 0
        self.on_message: Optional[Callable] = None  # 消息回调
        self.on_task: Optional[Callable] = None      # 任务回调
        
        # AIM 目录
        if aim_dir:
            self.aim_dir = Path(aim_dir)
        else:
            self.aim_dir = Path.home() / ".hermes" / "aim"
        
        self.aim_send = self.aim_dir / "aim_send.py"
    
    # ========== 发送 ==========
    
    def send(self, target: str, message: str, group: bool = False) -> dict:
        """发送普通消息"""
        return self._call_aim_send(target, message, group=group)
    
    def task(self, target: str, task_type: str, title: str, body: str = "",
             priority: str = "medium", group: bool = False) -> dict:
        """发送任务消息 ([task] 协议)"""
        self._seq += 1
        task_id = f"{self.agent_id}-{datetime.now().strftime('%Y%m%d')}-{self._seq:03d}"
        
        # 自动选择合适的 task_type
        if not task_type:
            if "分析" in body or "评审" in body or "review" in body.lower():
                task_type = "review"
            elif "查询" in body or "信息" in body or "状态" in body:
                task_type = "request"
            else:
                task_type = "request"
        
        task_json = json.dumps({
            "ver": "0.1",
            "type": task_type,
            "id": task_id,
            "from": self.agent_id,
            "to": [target] if not group else None,
            "group": group,
            "title": title[:50],
            "body": body,
            "priority": priority,
        }, ensure_ascii=False)
        
        full_msg = f"[task] {task_json}"
        
        if group:
            return self._call_aim_send(target, full_msg, group=True)
        else:
            return self._call_aim_send(target, full_msg)
    
    def _call_aim_send(self, target: str, content: str, group: bool = False) -> dict:
        """调用 aim_send.py 发送"""
        if not self.aim_send.exists():
            return {"success": False, "error": f"aim_send.py 不存在: {self.aim_send}"}
        
        try:
            env = os.environ.copy()
            env["no_proxy"] = "127.0.0.1,localhost"
            
            cmd = [sys.executable, str(self.aim_send), target, content, "--from", self.agent_id]
            if group:
                cmd.append("--group")
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
            return {"success": result.returncode == 0, "output": result.stdout.strip()}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ========== 接收（监听模式） ==========
    
    async def _listen_loop(self):
        """通过 WebSocket 直连 AIM Hub 监听消息"""
        while True:
            try:
                async with ws_connect(self.hub_url, open_timeout=10, proxy=None) as ws:
                    # 认证
                    auth = self._build_auth()
                    await ws.send(json.dumps(auth))
                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    
                    if resp.get("cmd") != "auth_ok":
                        print(f"❌ 认证失败: {resp}")
                        await asyncio.sleep(5)
                        continue
                    
                    print(f"✅ {self.emoji}{self.name}({self.agent_id}) 已连接 AIM Hub")
                    
                    # 监听消息
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            if data.get("cmd") == "message":
                                msg = data.get("msg", {})
                                content = msg.get("content", "")
                                sender = msg.get("from", "")
                                
                                # 不处理自己的消息
                                if sender == self.agent_id:
                                    continue
                                
                                # 检测 [task] 协议
                                if content.startswith("[task] ") and self.on_task:
                                    try:
                                        task = json.loads(content[7:])
                                        await self.on_task(task)
                                    except json.JSONDecodeError:
                                        pass
                                elif self.on_message:
                                    await self.on_message(msg)
                        except (json.JSONDecodeError, KeyError):
                            continue
                            
            except (OSError, ConnectionRefusedError) as e:
                print(f"⚠️ 连接失败: {e}，5秒后重连...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"⚠️ 异常: {e}，5秒后重连...")
                await asyncio.sleep(5)
    
    def _build_auth(self) -> dict:
        """构建认证消息（兼容 security.py 或简单 HMAC）"""
        aim_dir = self.aim_dir
        sec_path = aim_dir / "security.py"
        
        if sec_path.exists():
            sys.path.insert(0, str(aim_dir))
            try:
                from security import get_security_manager
                sec = get_security_manager()
                return sec.build_auth_payload(self.agent_id)
            except ImportError:
                pass
        
        # fallback: 简单 HMAC
        import hashlib
        import hmac
        secret_file = aim_dir / "secrets" / f"{self.agent_id}.secret"
        if secret_file.exists():
            secret = secret_file.read_text().strip()
            ts = int(time.time())
            sig = hmac.new(secret.encode(), f"{self.agent_id}:{ts}".encode(), hashlib.sha256).hexdigest()
            return {"cmd": "auth", "agent_id": self.agent_id, "timestamp": ts, "signature": sig}
        
        return {"cmd": "auth", "agent_id": self.agent_id, "token": ""}
    
    def listen(self):
        """启动监听（同步阻塞）"""
        asyncio.run(self._listen_loop())
    
    def listen_async(self):
        """启动监听（后台协程，用于已有的 event loop）"""
        return self._listen_loop()


# ========== 快捷用法示例 ==========

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("AIM Agent SDK — 快捷消息发送")
        print()
        print("用法:")
        print(f"  python3 {sys.argv[0]} send <target> <message>")
        print(f"  python3 {sys.argv[0]} task <target> <title> <body>")
        print(f"  python3 {sys.argv[0]} listen")
        print()
        print("示例:")
        print(f"  python3 {sys.argv[0]} send ZS0001 '你好呱呱'")
        print(f"  python3 {sys.argv[0]} task ZS0001 '评审代码' '请review main.py'")
        sys.exit(1)
    
    agent_id = os.environ.get("AIM_AGENT_ID", "ZS0002")
    name = os.environ.get("AIM_AGENT_NAME", "SDK测试")
    agent = AIMAgent(agent_id, name)
    
    cmd = sys.argv[1]
    
    if cmd == "send" and len(sys.argv) >= 4:
        result = agent.send(sys.argv[2], sys.argv[3])
        print("✅" if result.get("success") else "❌", result.get("output", result.get("error", "")))
    
    elif cmd == "task" and len(sys.argv) >= 5:
        result = agent.task(sys.argv[2], "", sys.argv[3], sys.argv[4])
        print("✅" if result.get("success") else "❌", result.get("output", result.get("error", "")))
    
    elif cmd == "listen":
        print(f"🔊 监听中 (Agent: {agent_id})...")
        agent.listen()
    
    else:
        print("未知命令")
