#!/usr/bin/env python3
"""
AIM Agent Daemon — 消息接收 + 归档 + AI 处理 + 自动回复

功能：
  1. 连接 AIM Hub，监听消息
  2. 消息自动归档到本地聊天记录
  3. 触发 AI 框架处理消息
  4. AI 回复自动通过 AIM 发出
  5. 支持静默模式（不自动回复的通知类消息）
  6. 心跳保活（防止空闲被踢）
  7. 并发处理（方案B）

用法：
  python3 aim-agent.py --agent-id ZS0001 --framework openclaw
  python3 aim-agent.py --agent-id ZS0002 --framework hermes
  python3 aim-agent.py --agent-id ZS0003 --framework crewai
"""

import argparse
import asyncio
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from msg_dedup import MessageDedup
from framework_cli import FrameworkCLI
from ai_types import AIRequest, AIResponse

try:
    import websockets
    from websockets.asyncio.client import connect
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)


def safe_json_dumps(obj, **kwargs):
    """安全的JSON序列化，防止恶意对象的__str__方法被执行"""
    # 确保输入是纯Python基本类型
    def sanitize(o):
        if isinstance(o, dict):
            return {str(k): sanitize(v) for k, v in o.items()}
        elif isinstance(o, (list, tuple)):
            return [sanitize(v) for v in o]
        elif isinstance(o, (str, int, float, bool, type(None))):
            return o
        else:
            # 其他类型转为字符串，不调用可能恶意的方法
            return str(o)
    
    return json.dumps(sanitize(obj), **kwargs)


def safe_json_loads(raw):
    """安全的JSON解析，确保输入是字符串类型"""
    if not isinstance(raw, str):
        raise ValueError(f"JSON解析需要字符串，得到 {type(raw)}")
    # 限制输入大小，防止DoS
    if len(raw) > 1024 * 1024:  # 1MB限制
        raise ValueError("JSON输入过大")
    return json.loads(raw)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

# 日志轮转配置
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"agent-{sys.argv[2] if len(sys.argv) > 2 else 'unknown'}.log"

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stderr),  # 控制台输出
        RotatingFileHandler(
            LOG_FILE,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3,
            encoding='utf-8'
        )
    ]
)
log = logging.getLogger("aim-agent")

# 强制刷新日志输出
for handler in logging.root.handlers:
    handler.flush = sys.stderr.flush

# 任务进度追踪
def log_task_progress(msg_id: str, stage: str, status: str, detail: str = ""):
    """统一的任务进度日志"""
    icons = {"start": "🚀", "processing": "⚙️", "success": "✅", "error": "❌", "timeout": "⏰", "skip": "📋"}
    icon = icons.get(status, "📋")
    msg = f"{icon} [{stage}] {msg_id[:8]}: {status}"
    if detail:
        msg += f" | {detail}"
    print(msg, flush=True)

# 不需要 AI 回复的消息前缀
NO_REPLY_PREFIXES = [
    "【系统】", "[SYSTEM]", "---", "AIM Watcher",
    "在线:", "已发送", "已连接", "断开",
]


class AIMAgentDaemon:
    """AIM Agent 守护进程 — 支持并发处理（方案B）+ delegate模式"""

    MAX_CONCURRENT = 3  # 并发上限
    AI_TIMEOUT_DEFAULT = 120  # 默认AI调用超时（秒）— 增加到120秒以处理长消息
    AI_TIMEOUT_SHORT = 180  # 紧急/高优先级消息超时（秒）— 给QwenPaw等加载型框架留足时间
    AI_TIMEOUT_LONG = 300  # 长任务超时（秒）— 增加到300秒
    AI_TIMEOUT = AI_TIMEOUT_DEFAULT  # 兼容旧代码的默认超时
    PING_INTERVAL = 20  # 心跳间隔（秒）

    # Agent ID到CLI框架的映射（从config.json加载）
    AGENT_FRAMEWORK = {}  # 将在__init__中从config.json加载

    # 连接池 Client 端配置
    HEALTH_CHANNEL_INTERVAL = 30  # health 通道心跳间隔（秒）
    GRACE_PERIOD = 30             # 优雅窗口期（秒）

    def __init__(self, agent_id: str, token: str, server: str,
                 framework: str, name: str = "", emoji: str = ""):
        self.agent_id = agent_id
        self.token = token
        self.server = server
        self.framework = framework  # hermes / openclaw / qwenpaw / crewai
        self.name = name
        self.emoji = emoji
        self._running = False
        self._ws = None  # 当前活跃的WebSocket连接
        self._last_reply_time = 0
        self._reply_cooldown = 3  # 最短回复间隔（秒）

        # 从config.json加载Agent框架映射
        self._load_agent_framework()

        # 方案B：并发控制
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._dedup = MessageDedup(max_size=100, ttl=300)  # LRU去重缓存
        self._active_tasks: dict[str, asyncio.Task] = {}  # msg_id -> task
        self._current_session_id: str = None  # 当前会话ID（用于上下文保持）

        # 发送端去重：记录已发送消息的hash，防止重发
        self._sent_hashes: dict[str, float] = {}  # content_hash -> timestamp
        self._sent_lock = asyncio.Lock()
        self.SENT_DEDUP_TTL = 300  # 5分钟内不重复发送

        # Presence去重：记录最近的presence消息，防止重复通知
        self._presence_cache: dict[str, float] = {}  # "agent_id:status" -> timestamp
        self.PRESENCE_DEDUP_TTL = 5  # 5秒内不重复处理相同presence

        # 降级机制：记录最近收到的消息类型，连续2条"收到"后强制升级
        self._recent_msg_types: list[str] = []  # 最近10条消息类型
        self.MAX_RECENT_TYPES = 10

        # CLI路径配置 + 命令模板
        self._cli_paths = self._load_cli_paths()
        self._commands = self._load_commands_config()
        self._fw_cli = FrameworkCLI(self.framework, self._commands, self._cli_paths)

        # 通知配置
        self._notification_config = self._load_notification_config()

        # 消息队列（轻量级，用完即删）
        sys.path.insert(0, str(BASE_DIR))
        import queue as msg_q
        self._queue = msg_q

        # 归档模块
        sys.path.insert(0, str(BASE_DIR))
        from archive import archive_message, get_recent_context
        self._archive = archive_message
        self._get_context = get_recent_context

        # ── 连接池 Client 端 ──
        self._health_ws = None       # health 通道 WS 连接
        self._health_connected = False
        self._channels: dict = {}    # {channel: ws}

    def _load_cli_paths(self) -> dict:
        """加载CLI路径配置"""
        config_file = BASE_DIR / "config.json"
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
            return config.get("cli_paths", {})
        return {}

    def _load_commands_config(self) -> dict:
        """加载命令模板配置"""
        config_file = BASE_DIR / "config.json"
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
            return config.get("commands", {})
        return {}

    def _load_notification_config(self) -> dict:
        """加载通知配置"""
        config_file = BASE_DIR / "config.json"
        default_config = {
            "enabled": True,
            "delay_seconds": {"high": 0, "medium": 15, "low": -1},
            "heartbeat_interval": 15
        }
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
            return config.get("notification", default_config)
        return default_config

    def _load_agent_framework(self):
        """从config.json加载Agent框架映射"""
        config_file = BASE_DIR / "config.json"
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
            agents = config.get("agents", {})
            for agent_id, agent_info in agents.items():
                framework = agent_info.get("framework")
                if framework:
                    self.AGENT_FRAMEWORK[agent_id] = framework

    async def _run_main(self):
        retry_delay = 2
        while self._running:
            # 启动时版本检查和自动恢复
            if retry_delay > 10:
                # 连续重连失败，尝试版本恢复
                try:
                    import subprocess
                    result = subprocess.run(
                        [sys.executable, os.path.expanduser("~/.hermes/scripts/aim_version.py"), "auto-recover"],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.stdout:
                        log.info(f"版本恢复: {result.stdout.strip()[:100]}")
                except Exception:
                    pass
            
            try:
                log.info(f"[main] 连接 {self.server} ...")
                async with connect(
                    self.server, open_timeout=10,
                    ping_interval=self.PING_INTERVAL,
                    ping_timeout=10
                ) as ws:
                    # 使用HMAC签名认证
                    from security import get_security_manager
                    sec = get_security_manager()
                    auth_payload = sec.build_auth_payload(self.agent_id)
                    auth_payload["channel"] = "main"
                    auth_payload["handler"] = True
                    auth_payload["term"] = 1
                    await ws.send(safe_json_dumps(auth_payload))

                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    resp = safe_json_loads(raw)
                    if resp.get("cmd") != "auth_ok":
                        log.error(f"认证失败: {resp.get('reason')}")
                        # 认证失败时增加重连延迟，避免频率限制
                        retry_delay = min(retry_delay * 2, 30)
                        await asyncio.sleep(retry_delay)
                        continue

                    log.info(f"✅ [main] 已连接: {self.emoji}{self.name}({self.agent_id}) | 框架: {self.framework}")
                    self._ws = ws  # 保存当前活跃连接
                    self._channels["main"] = ws
                    retry_delay = 2
                    
                    # 版本检查（启动时检测，小版本自动升级）
                    try:
                        import subprocess
                        ver_check = subprocess.run(
                            [sys.executable, os.path.expanduser("~/.hermes/scripts/aim_version.py"), "check-and-prompt"],
                            capture_output=True, text=True, timeout=15
                        )
                        if ver_check.stdout:
                            for line in ver_check.stdout.strip().split("\n"):
                                if line.strip():
                                    log.info(f"版本: {line.strip()}")
                    except Exception:
                        pass

                    # 处理离线消息
                    for msg in resp.get("unread", []):
                        await self._process_incoming(msg)

                    # 监听循环
                    print(f"📡 监听中...", flush=True)
                    async for raw in ws:
                        print(f"📥 收到原始消息: {raw[:100]}", flush=True)
                        try:
                            data = safe_json_loads(raw)
                            cmd = data.get("cmd", "")
                            print(f"📋 命令: {cmd}", flush=True)
                            if cmd == "message":
                                msg = data.get("msg", {})
                                # 兼容from_id字段
                                if "from" not in msg and "from_id" in msg:
                                    msg["from"] = msg["from_id"]
                                await self._process_incoming(msg)
                            elif cmd == "relay":
                                msg = data.get("msg", {})
                                if "from" not in msg and "from_id" in msg:
                                    msg["from"] = msg["from_id"]
                                await self._process_incoming(msg)
                            elif cmd == "presence":
                                aid = data.get("agent_id")
                                status = data.get("status")
                                # Presence去重：5秒内相同agent_id+status不重复处理
                                presence_key = f"{aid}:{status}"
                                now = time.time()
                                if presence_key in self._presence_cache:
                                    if now - self._presence_cache[presence_key] < self.PRESENCE_DEDUP_TTL:
                                        continue  # 跳过重复presence
                                self._presence_cache[presence_key] = now
                                # 清理过期缓存
                                expired = [k for k, t in self._presence_cache.items() if now - t > 60]
                                for k in expired:
                                    del self._presence_cache[k]
                                print(f"👤 {data.get('emoji','')}{data.get('name','')} {'上线' if status=='online' else '下线'}", flush=True)
                            elif cmd == "shutdown":
                                reason = data.get("reason", "unknown")
                                print(f"🔌 服务端关闭: {reason} — 即将自动重连", flush=True)
                                # 主动断开，触发快速重连
                                await ws.close(1001, f"server_{reason}")
                                break
                        except json.JSONDecodeError:
                            print(f"⚠️ JSON解析失败: {raw[:50]}", flush=True)
                            continue

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
                log.warning(f"[main] 断开: {e}")
                self._ws = None  # 清空连接引用
                self._channels.pop("main", None)
                # 自适应重连策略
                if isinstance(e, (ConnectionRefusedError, ConnectionError)):
                    retry_delay = 1  # 服务端未启动，极速重试
                elif isinstance(e, websockets.ConnectionClosed):
                    retry_delay = min(retry_delay * 1.5, 8)  # 服务端重启，快速恢复
                elif isinstance(e, asyncio.TimeoutError):
                    retry_delay = min(retry_delay * 1.5, 10)  # 超时，中等速度重试
                else:
                    retry_delay = min(retry_delay * 2, 15)
                jitter = random.uniform(0, retry_delay * 0.5)
                log.info(f"⏳ {retry_delay + jitter:.1f}s 后重连...")
                await asyncio.sleep(retry_delay + jitter)
            except Exception as e:
                log.error(f"[main] 重连异常: {type(e).__name__}: {e}")
                self._ws = None  # 清空连接引用
                self._channels.pop("main", None)
                # 所有异常都走自适应重连，不只是睡5秒
                retry_delay = min(retry_delay * 2, 15)
                jitter = random.uniform(0, retry_delay * 0.5)
                log.info(f"⏳ {retry_delay + jitter:.1f}s 后重连...")
                await asyncio.sleep(retry_delay + jitter)

    async def _run_health(self):
        """health channel — 健康检查 + 心跳（独立连接，不影响 main）"""
        retry_delay = 5
        while self._running:
            try:
                log.info(f"[health] 连接 {self.server} ...")
                async with connect(
                    self.server, open_timeout=10,
                    ping_interval=25, ping_timeout=10
                ) as ws:
                    # 使用 HMAC 签名认证，走 health channel
                    from security import get_security_manager
                    sec = get_security_manager()
                    auth_payload = sec.build_auth_payload(self.agent_id)
                    auth_payload["channel"] = "health"
                    auth_payload["handler"] = False  # health 不做 AI 处理
                    auth_payload["term"] = 1
                    await ws.send(safe_json_dumps(auth_payload))

                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    resp = safe_json_loads(raw)
                    if resp.get("cmd") != "auth_ok":
                        log.error(f"[health] 认证失败: {resp.get('reason')}")
                        await asyncio.sleep(retry_delay)
                        continue

                    log.info(f"✅ [health] 已连接: {self.emoji}{self.name}({self.agent_id})")
                    self._health_ws = ws
                    self._health_connected = True
                    self._channels["health"] = ws
                    retry_delay = 5

                    # 监听循环 — health 只关心 promote 和 ping
                    async for raw in ws:
                        try:
                            data = safe_json_loads(raw)
                            cmd = data.get("cmd", "")
                            if cmd == "promote_to_handler":
                                # 被提升为 handler — 记录日志
                                channel = data.get("channel", "")
                                log.info(f"⚡ [health] 被提升为 handler (channel={channel})")
                                # 通知主连接也更新状态（如果有主连接）
                                if "main" in self._channels:
                                    log.info(f"⚡ [health] main 通道在，维持现有 handler 不变")
                                else:
                                    log.info(f"⚡ [health] main 通道已断，health 接管")
                            elif cmd == "ping":
                                continue  # 底层 ws 库自动处理 pong
                            elif cmd == "pong":
                                continue
                            elif cmd == "heartbeat_ack":
                                continue
                        except json.JSONDecodeError:
                            continue

            except (ConnectionRefusedError, ConnectionError, asyncio.TimeoutError, websockets.ConnectionClosed) as e:
                log.warning(f"[health] 断开: {e}")
                self._health_ws = None
                self._health_connected = False
                self._channels.pop("health", None)
                retry_delay = min(retry_delay * 1.5, 30)
                jitter = random.uniform(0, retry_delay * 0.3)
                log.info(f"[health] ⏳ {retry_delay + jitter:.1f}s 后重连...")
                await asyncio.sleep(retry_delay + jitter)
            except Exception as e:
                log.error(f"[health] 异常: {type(e).__name__}: {e}")
                self._health_ws = None
                self._health_connected = False
                self._channels.pop("health", None)
                retry_delay = min(retry_delay * 2, 30)
                await asyncio.sleep(retry_delay)

    async def _process_incoming(self, msg: dict):
        """处理收到的消息：入队 → 按优先级出队 → delegate处理"""
        if not msg or not msg.get("content"):
            return

        sender = msg.get("from", "?")
        content = msg.get("content", "")
        is_group = msg.get("group", False)
        msg_id = msg.get("msg_id", "")

        # 1. 去重检查（LRU Cache + TTL）
        if self._dedup.is_duplicate(msg_id):
            log_task_progress(msg_id, "接收", "skip", "已处理（去重）")
            return

        # 2. 归档
        self._archive(msg)

        # 2.5 检测 AIM 任务协议消息
        if content.startswith("[task] "):
            asyncio.create_task(self._handle_task(content[7:], sender, msg_id))
            return
        
        # 2.6 检测 AIM 升级指令
        if content.startswith("[AIM-UPDATE] "):
            # 解析升级指令
            # 格式: [AIM-UPDATE] {"version":"20260604.2009M","type":"MINOR"}
            try:
                update_info = json.loads(content[len("[AIM-UPDATE] "):])
                log.info(f"收到升级指令: {update_info}")
                asyncio.create_task(self._handle_update(update_info))
            except json.JSONDecodeError:
                log.warning(f"升级指令JSON解析失败: {content[:80]}")
            return
        
        # 2.7 检测 AIM 任务状态更新
        if content.startswith("[task-status] "):
            # 任务状态更新由发送方自己追踪，不需要 AI 处理
            log_task_progress(msg_id, "任务状态", "recv", content[14:60])
            return
        
        # 2.7 发送 delivered 状态
        asyncio.create_task(self._send_status_update(
            msg_id, "delivered", 
            is_group=is_group, 
            group_id=msg.get("to", "") if is_group else "",
            original_from=sender
        ))

        # 2.6 推送到桥接服务（异步，不阻塞）
        asyncio.create_task(self.push_to_bridge(msg))

        # 3. 日志
        dt = datetime.now().strftime("%H:%M:%S")
        prefix = "[群]" if is_group else ""
        priority = self._get_priority(msg)
        msg_type = self._classify_message(content)
        log_task_progress(msg_id, "接收", "start", f"{prefix}[{sender}] {content[:50]}")

        # 4. 判断是否需要处理
        if not self._should_reply(msg):
            log_task_progress(msg_id, "过滤", "skip", "不需要回复")
            self._dedup.mark_processed(msg_id)
            return

        # 5. 入队
        self._queue.enqueue(msg, priority)
        log_task_progress(msg_id, "入队", "success", f"优先级: {priority} | 队列: {self._queue.size()}")

        # 6. 从队列取出处理（按优先级，支持抢占）
        while self._queue.size() > 0:
            item = self._queue.dequeue()
            if item:
                item_id = item.get("msg_id", "")
                item_priority = item.get("priority", "medium")
                
                # 检查是否需要处理（LRU去重）
                if self._dedup.is_duplicate(item_id):
                    log_task_progress(item_id, "出队", "skip", "已处理（去重）")
                    continue
                
                # 优先级抢占：urgent消息可以抢占低优先级任务
                if item_priority == "urgent" and self._active_tasks:
                    log_task_progress(item_id, "抢占", "start", f"urgent消息抢占 {len(self._active_tasks)} 个活跃任务")
                    # 取消所有非urgent的活跃任务
                    for task_id, task in list(self._active_tasks.items()):
                        if not task.done():
                            task.cancel()
                            log_task_progress(task_id, "抢占", "cancelled", "被urgent消息抢占")
                    self._active_tasks.clear()
                
                self._dedup.mark_processed(item_id)
                task = asyncio.create_task(
                    self._process_with_semaphore(item, item.get("from", "?"),
                                                 item.get("content", ""), item.get("group", False),
                                                 item_id, item_priority)
                )
                self._active_tasks[item_id] = task
                log_task_progress(item_id, "出队", "start", f"优先级: {item_priority} | 活跃任务: {len(self._active_tasks)}")

    def _should_reply(self, msg: dict) -> bool:
        """判断是否需要 AI 回复（消息路由策略）"""
        content = msg.get("content", "")
        sender = msg.get("from", "")

        # 不回复自己的消息
        if sender == self.agent_id:
            return False

        # 不回复系统消息
        for prefix in NO_REPLY_PREFIXES:
            if content.startswith(prefix):
                return False

        # 群聊逻辑
        if msg.get("group"):
            # 检查@了哪些人
            at_targets = self._detect_at_targets(content)
            
            # 场景0：@了多人 → 只有被@的人回复
            if len(at_targets) > 1:
                return self.agent_id in at_targets
            
            # 场景1：@了单人且不是自己 → 不回复，让目标处理
            if len(at_targets) == 1 and at_targets[0] != self.agent_id:
                return False
            
            # 场景2：@自己 → 高优先级回复
            if f"@{self.agent_id}" in content:
                return True

            # 场景3：包含名字 → 中优先级回复
            if self.name.lower() in content.lower():
                return True

            # 场景3.5：通知类消息（如"收到"、"OK"）→ 不回复，打断死循环
            msg_type = self._classify_message(content)
            if msg_type == "通知":
                # 降级机制：连续2条通知类消息后，第3条强制升级为讨论类
                self._recent_msg_types.append(msg_type)
                if len(self._recent_msg_types) > self.MAX_RECENT_TYPES:
                    self._recent_msg_types.pop(0)
                
                # 检查是否连续2条通知类
                if len(self._recent_msg_types) >= 2:
                    if self._recent_msg_types[-1] == "通知" and self._recent_msg_types[-2] == "通知":
                        # 连续2条通知，强制升级为讨论类，需要回复
                        return True
                return False

            # 场景4：讨论类消息 → 三方都参与
            discussion_keywords = ["讨论", "商量", "一起", "大家", "各自", "你们", "涉及", "分析", "确认", "回复"]
            for keyword in discussion_keywords:
                if keyword in content:
                    return True

            # 场景5：吉量负责路由（吸收/解读/分发）
            if self.agent_id == "ZS0002":
                return True
            else:
                return False

        # 私信：回复
        return True

    def _get_priority(self, msg: dict) -> str:
        """获取消息优先级（支持优先级抢占）"""
        content = msg.get("content", "").lower()
        sender = msg.get("from", "")
        is_group = msg.get("group", False)
        
        # urgent优先级：大哥消息（ZS0002的私信）
        if sender == "ZS0002" and not is_group:
            return "urgent"
        
        # high优先级：@自己 或 包含名字 或 私信
        if not is_group:
            return "high"
        if f"@{self.agent_id}" in content or self.name.lower() in content:
            return "high"
        
        # medium优先级：包含关键词
        keywords = ["架构", "安全", "测试", "方案", "讨论", "确认", "回复", "问题"]
        for keyword in keywords:
            if keyword in content:
                return "medium"
        
        # low优先级：其他
        return "low"

    def _classify_message(self, content: str) -> str:
        """消息分类（小火鸡儿优化建议 + 吉量扩展）"""
        content_lower = content.lower()
        
        # 指令类：立即执行（最高优先级）
        cmd_keywords = ["请", "帮我", "立即", "马上", "执行", "运行", "测试"]
        for keyword in cmd_keywords:
            if keyword in content_lower:
                return "指令"
        
        # 讨论类：AI分析（高优先级）- 扩展关键词
        discuss_keywords = [
            "架构", "安全", "方案", "讨论", "分析", "问题", "建议",
            "任务", "分工", "进度", "修复", "评估", "计划", "目标", "bug", "改进",
            "确认", "回复", "评估", "设计", "优化", "重构", "测试", "部署"
        ]
        for keyword in discuss_keywords:
            if keyword in content_lower:
                return "讨论"
        
        # 通知类：不回复（低优先级）
        notify_keywords = ["收到", "确认", "ok", "好的", "了解", "明白"]
        for keyword in notify_keywords:
            if content_lower == keyword or content_lower.startswith(keyword):
                return "通知"
        
        # 默认：普通消息
        return "普通"

    async def _process_with_semaphore(self, msg: dict, sender: str,
                                       content: str, is_group: bool, msg_id: str, priority: str = "medium"):
        """带信号量的并发处理"""
        async with self._semaphore:
            try:
                log_task_progress(msg_id, "AI", "processing", f"优先级: {priority}")
                # 发送 processing 状态
                asyncio.create_task(self._send_status_update(
                    msg_id, "processing",
                    is_group=is_group,
                    group_id=msg.get("to", "") if is_group else "",
                    original_from=sender
                ))

                # 检测@了哪些人
                at_targets = self._detect_at_targets(content)

                # 判断谁应该处理这条消息
                should_handle = False
                should_forward = None  # 需要转发给的目标agent_id

                if len(at_targets) == 0:
                    # 没@任何人 → 由当前agent自己处理（自己的消息自己判断）
                    should_handle = True
                elif len(at_targets) > 1:
                    # 多人@ → 只在@列表中才处理
                    should_handle = self.agent_id in at_targets
                elif at_targets[0] == self.agent_id:
                    # @自己 → 处理
                    should_handle = True
                else:
                    # @了别人但不是自己 → 如果是群聊，通过AIM转发给目标
                    if is_group:
                        should_forward = at_targets[0]
                    else:
                        # 私信里@别人，对方不在本会话中，自己处理
                        should_handle = True

                if should_forward:
                    # 通过 AIM 转发给目标 Agent，不本地调 CLI
                    log_task_progress(msg_id, "路由", "start", f"转发→{should_forward}")
                    self.log.info(f"转发消息到 {should_forward}: {content[:50]}")
                    await self._send_via_aim(should_forward, f"[转发] {sender}: {content}", group=False)
                    log_task_progress(msg_id, "路由", "done", f"已转发→{should_forward}")
                    return

                if not should_handle:
                    log_task_progress(msg_id, "路由", "skip", f"不在@列表中，跳过")
                    return

                log_task_progress(msg_id, "路由", "skip", f"本地处理")
                context = self._build_context(sender, is_group)
                ai_response = await self._call_ai(content, context, sender, priority)

                if not ai_response:
                    log_task_progress(msg_id, "AI", "error", "无回复")
                    return

                # AI 回复 SKIP 表示不需要回复（打断死循环）
                # 但Agent间通信不跳过（避免回复被吞）
                is_from_agent = sender.startswith("ZS") and sender != self.agent_id
                if ai_response.strip().upper() == "SKIP" and not is_from_agent:
                    log_task_progress(msg_id, "AI", "skip", "SKIP - 跳过回复")
                    return

                # 回复质量底线：纯确认类回复（≤5字）拦截，要求补充判断
                # 但Agent间通信不拦截
                if not is_from_agent:
                    pure_confirm_words = ["收到", "了解", "明白", "好的", "ok", "确认", "知道了"]
                    if len(ai_response.strip()) <= 5 and ai_response.strip().lower() in pure_confirm_words:
                        log_task_progress(msg_id, "AI", "skip", "回复质量底线 - 纯确认类，跳过")
                        return

                log_task_progress(msg_id, "AI", "success", f"生成回复: {ai_response[:30]}...")

                # 通过 AIM 发送回复
                if is_group:
                    group_id = msg.get("to", "") or "grp_trio"
                    await self._send_via_aim(group_id, ai_response, group=True)
                else:
                    await self._send_via_aim(sender, ai_response, group=False)

                log_task_progress(msg_id, "发送", "success", f"已回复 {sender}")
                # 发送 done 状态
                asyncio.create_task(self._send_status_update(
                    msg_id, "done",
                    is_group=is_group,
                    group_id=msg.get("to", "") if is_group else "",
                    original_from=sender
                ))

                # 通知主会话（文件通知方案v2.0）
                await self._notify_main_session(msg, ai_response, priority)

            except asyncio.TimeoutError:
                log_task_progress(msg_id, "AI", "timeout", "处理超时")
                # 发送 timeout 状态
                asyncio.create_task(self._send_status_update(
                    msg_id, "timeout", "处理超时",
                    is_group=is_group,
                    group_id=msg.get("to", "") if is_group else "",
                    original_from=sender
                ))
            except Exception as e:
                log_task_progress(msg_id, "AI", "error", str(e)[:50])
                # 发送 error 状态
                asyncio.create_task(self._send_status_update(
                    msg_id, "error", str(e)[:100],
                    is_group=is_group,
                    group_id=msg.get("to", "") if is_group else "",
                    original_from=sender
                ))

    def _detect_at_targets(self, content: str) -> list[str]:
        """检测消息中@了谁，返回所有目标Agent ID列表"""
        targets = []
        # 检查@agent_id格式
        for agent_id in self.AGENT_FRAMEWORK:
            if f"@{agent_id}" in content and agent_id not in targets:
                targets.append(agent_id)
        # 检查@名字格式
        name_to_id = {"呱呱": "ZS0001", "吉量": "ZS0002", "小火鸡儿": "ZS0003"}
        for name, agent_id in name_to_id.items():
            if f"@{name}" in content and agent_id not in targets:
                targets.append(agent_id)
        return targets

    def _detect_at_target(self, content: str) -> str:
        """兼容旧接口：返回第一个@目标"""
        targets = self._detect_at_targets(content)
        return targets[0] if targets else None

    async def _delegate_to_agent(self, target_agent: str, content: str, sender: str, msg_id: str) -> str:
        """delegate模式：调用目标Agent的CLI处理消息（通过 FrameworkCLI 模板驱动）"""
        framework = self.AGENT_FRAMEWORK.get(target_agent)
        if not framework:
            return None

        prompt = f"收到来自 {sender} 的消息：\n{content}\n\n请分析并回复。"

        # 为 delegate 创建独立的 FrameworkCLI 实例（目标框架）
        delegate_cli = FrameworkCLI(framework, self._commands, self._cli_paths)

        # openclaw delegate 用持久化 session_key 保持上下文
        session_key = None
        if framework == "openclaw":
            session_key = f"aim-{sender.replace(' ', '_')}-{target_agent}"

        request = AIRequest(
            prompt=prompt,
            timeout=self.AI_TIMEOUT,
            agent_id=target_agent,
            session_key=session_key,
        )

        try:
            response = await delegate_cli.call(request)
            return response.text if response.success else None
        except Exception as e:
            log.error(f"delegate失败: {e}")
            return None

    async def _delegate_cleanup(self, msg_id: str):
        """清理delegate任务"""
        if msg_id and msg_id in self._active_tasks:
            del self._active_tasks[msg_id]
            log_task_progress(msg_id, "完成", "success", f"活跃任务: {len(self._active_tasks)}")

    async def _notify_main_session(self, msg: dict, response: str, priority: str):
        """通知主会话（文件通知方案v2.0 + AIM自动唤醒）"""
        try:
            notify_dir = os.path.expanduser("~/.hermes/aim/processed/pending")
            os.makedirs(notify_dir, exist_ok=True)

            # 从配置文件读取延迟设置
            delay_seconds = self._notification_config.get("delay_seconds", {}).get(priority, 15)
            
            notification = {
                "msg_id": msg.get("msg_id"),
                "from_id": msg.get("from"),
                "from_name": msg.get("from_name", msg.get("from", "")),
                "to_id": self.agent_id,
                "content": msg.get("content"),
                "timestamp": datetime.now().isoformat(),
                "priority": priority,
                "delay_seconds": delay_seconds,
                "msg_type": self._classify_message(msg.get("content", "")),
                "ai_response": response[:200] if response else "",
                "notify_method": "realtime" if priority == "high" else "file",
                "status": "pending",
                "is_group": msg.get("group", False),
            }
            
            # 写入pending目录
            filepath = os.path.join(notify_dir, f"{msg['msg_id']}.json")
            with open(filepath, "w") as f:
                json.dump(notification, f, ensure_ascii=False, indent=2)
            
            # 高优先级消息 → 通过 AIM 唤醒自己
            if priority in ("urgent", "high"):
                sender_name = msg.get("from_name", msg.get("from", "未知"))
                content_preview = msg.get("content", "")[:80]
                wake_msg = f"⚡ {sender_name} 有新消息:\n{content_preview}"
                try:
                    aim_send = os.path.expanduser("~/.hermes/aim/aim_send.py")
                    subprocess.run(
                        [sys.executable, aim_send, self.agent_id, wake_msg, "--from", self.agent_id],
                        capture_output=True, text=True, timeout=10,
                        env={**os.environ, "no_proxy": "127.0.0.1,localhost"}
                    )
                except Exception:
                    pass
            
            log_task_progress(msg.get("msg_id", ""), "通知", "success", f"优先级: {priority}")
            
        except Exception as e:
            log.error(f"通知主会话失败: {e}")

    async def _inject_to_main_session(self, notification: dict):
        """实时注入主会话（通过 FrameworkCLI 模板驱动）"""
        try:
            prompt = f"AIM消息通知：{notification.get('from_name', '未知')}说：{notification.get('content', '')}"
            request = AIRequest(prompt=prompt, timeout=60)
            response = await self._fw_cli.call(request)
            if response.success:
                log_task_progress(notification.get("msg_id", ""), "注入", "success", f"实时注入{self.framework}")
            else:
                log_task_progress(notification.get("msg_id", ""), "注入", "error", response.error or "")
        except asyncio.TimeoutError:
            log_task_progress(notification.get("msg_id", ""), "注入", "timeout", "注入超时")
        except Exception as e:
            log.error(f"实时注入失败: {e}")

    def _build_context(self, sender: str, is_group: bool) -> str:
        """构建 AI 上下文"""
        recent = self._get_context(self.agent_id, limit=5)
        context_lines = []
        for m in recent:
            who = m.get("from", "?")
            what = m.get("content", "")
            context_lines.append(f"[{who}]: {what[:100]}")

        return "\n".join(context_lines) if context_lines else ""

    async def _call_cli(self, prompt: str, timeout: int = None) -> dict:
        """统一CLI调用接口（通过 FrameworkCLI 模板驱动）
        
        返回格式：
        {
            "success": True/False,
            "text": "回复内容",
            "session_id": "会话ID（可选）",
            "error": "错误信息（可选）"
        }
        """
        if timeout is None:
            timeout = self.AI_TIMEOUT_DEFAULT

        # openclaw 每次生成独立 session_key，避免旧 session 阻塞
        session_key = None
        if self.framework == "openclaw":
            session_key = f"aim-agent-{self.agent_id}-{int(time.time()*1000)}"

        request = AIRequest(
            prompt=prompt,
            timeout=timeout,
            session_id=self._current_session_id,
            agent_id=self.agent_id,
            session_key=session_key,
        )

        response: AIResponse = await self._fw_cli.call(request)
        # 更新 session_id（FrameworkCLI 从输出中提取）
        if response.session_id:
            self._current_session_id = response.session_id
        return response

    async def _call_ai(self, message: str, context: str, sender: str, priority: str = "medium") -> str:
        """调用 AI 框架处理消息（支持上下文保持）"""
        # 构建 prompt
        prompt = f"收到来自 {sender} 的消息：\n{message}"
        if context:
            prompt = f"最近对话记录：\n{context}\n\n{prompt}"
        # 判断是否来自其他Agent的消息（非用户）
        is_from_agent = sender.startswith("ZS") and sender != self.agent_id

        if is_from_agent:
            # Agent间通信：不触发SKIP机制，避免回复被吞
            prompt += '\n\n请直接回复内容（这是来自其他Agent的消息，不要回复SKIP）。'
        else:
            prompt += '\n\n请简洁回复。如果是通知类消息（如"收到"、"OK"、"明白了"等确认类消息），请直接回复"SKIP"（不要回复"收到"，避免死循环）。'

        # 超时分级（2026-06-05 增加以处理长消息）
        if priority == "high":
            timeout = self.AI_TIMEOUT_SHORT  # 180秒
        elif priority == "low":
            timeout = self.AI_TIMEOUT_LONG  # 300秒
        else:
            timeout = self.AI_TIMEOUT_DEFAULT  # 120秒

        # 动态超时：根据消息长度增加超时时间
        # 长消息（>500字符）需要更多处理时间
        msg_len = len(message)
        if msg_len > 1000:
            timeout = max(timeout, 300)  # 超长消息至少300秒
        elif msg_len > 500:
            timeout = max(timeout, 180)  # 长消息至少180秒

        # 使用统一CLI接口
        response: AIResponse = await self._call_cli(prompt, timeout)
        
        if response.success:
            # 更新session_id（如果有）
            if response.session_id:
                self._current_session_id = response.session_id
            return response.text
        else:
            error = response.error or "未知错误"
            print(f"❌ AI 调用失败: {error}", flush=True)
            return None

    async def _send_via_aim(self, to_id: str, content: str, group: bool = False):
        """通过 AIM 发送消息（带发送端去重）"""
        if not self._ws:
            log.error("WebSocket未连接，无法发送")
            return

        # 参数校验：防止--to等参数泄露
        if content.startswith("--"):
            log.error(f"⚠️ 内容以'--'开头，疑似参数泄露，拒绝发送: {content[:50]}")
            return

        # 生成内容hash用于去重
        content_hash = hashlib.md5(f"{to_id}:{content}:{group}".encode()).hexdigest()[:16]

        # 检查是否重复发送
        async with self._sent_lock:
            now = time.time()
            # 清理过期记录
            expired = [h for h, ts in self._sent_hashes.items() if now - ts > self.SENT_DEDUP_TTL]
            for h in expired:
                del self._sent_hashes[h]

            if content_hash in self._sent_hashes:
                log.info(f"⚠️ 发送去重: 跳过重复消息 -> {to_id}")
                return

            # 记录本次发送
            self._sent_hashes[content_hash] = now

        try:
            msg_id = str(uuid.uuid4())[:8]
            
            # 生成 HMAC 签名
            from security import get_security_manager
            sec = get_security_manager()
            timestamp, signature = sec.generate_message_signature(self.agent_id, msg_id, content)
            
            await self._ws.send(safe_json_dumps({
                "cmd": "send",
                "msg_id": msg_id,
                "from_id": self.agent_id,
                "to": to_id,
                "content": content,
                "group": group,
                "timestamp": timestamp,
                "signature": signature,
            }, ensure_ascii=False))
            # 也归档自己发出的消息
            self._archive({
                "from": self.agent_id,
                "to": to_id,
                "content": content,
                "group": group,
                "msg_id": msg_id,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ts": time.time(),
                "direction": "sent",
            })
            log_task_progress(msg_id, "发送", "success", f"-> {to_id}: {content[:30]}...")
        except Exception as e:
            log_task_progress(msg_id if 'msg_id' in locals() else "unknown", "发送", "error", str(e)[:50])

    async def _send_status_update(self, msg_id: str, state: str, detail: str = "", is_group: bool = False, group_id: str = "", original_from: str = ""):
        """发送状态更新到 Hub"""
        if not self._ws:
            return
        try:
            # 生成 HMAC 签名
            from security import get_security_manager
            sec = get_security_manager()
            content = f"{state}:{detail}"
            timestamp, signature = sec.generate_message_signature(self.agent_id, msg_id, content)
            
            status_msg = {
                "cmd": "status_update",
                "msg_id": msg_id,
                "state": state,
                "ts": timestamp,
                "by": self.agent_id,
                "detail": detail,
                "group": is_group,
                "group_id": group_id,
                "original_from": original_from,
                "signature": signature,
            }
            if self._ws and hasattr(self._ws, "state") and self._ws.state == 1:  # 1 = OPEN
                await self._ws.send(safe_json_dumps(status_msg))
                log.debug(f"📊 状态更新: {msg_id} → {state}")
        except Exception as e:
            import traceback
            log.debug(f"⚠️ 状态更新失败: {e}")
            log.debug(traceback.format_exc())

    def cleanup_old_processes(self):
        """清理同agent_id的旧进程"""
        import subprocess
        current_pid = os.getpid()
        
        # 查找同agent_id的旧进程
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"aim-agent.*{self.agent_id}"],
                capture_output=True, text=True
            )
            for pid in result.stdout.strip().split("\n"):
                if pid and int(pid) != current_pid:
                    log.info(f"🧹 清理旧进程: PID {pid}")
                    try:
                        subprocess.run(["kill", pid], check=False)
                    except Exception as e:
                        log.warning(f"⚠️ 清理进程失败: {e}")
        except Exception as e:
            log.warning(f"⚠️ 查找旧进程失败: {e}")

    def _cleanup_old_processes(self):
        """确保同一agent_id只有一个进程"""
        import subprocess
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"aim-agent.*{self.agent_id}"],
                capture_output=True, text=True
            )
            current_pid = str(os.getpid())
            for pid in result.stdout.strip().split("\n"):
                if pid and pid != current_pid:
                    log.info(f"🧹 清理旧进程: PID {pid}")
                    subprocess.run(["kill", pid], check=False)
        except Exception as e:
            log.warning(f"清理进程失败: {e}")

    def run(self):
        """阻塞运行（多 channel）"""
        self._cleanup_old_processes()  # 启动前清理旧进程
        self._running = True
        log.info(f"🚀 AIM Agent Daemon 启动")
        log.info(f"   身份: {self.emoji}{self.name}({self.agent_id})")
        log.info(f"   框架: {self.framework}")
        log.info(f"   服务器: {self.server}")

        try:
            asyncio.run(self._run_multi_channel())
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            log.info("Agent Daemon 已关闭")

    async def _run_multi_channel(self):
        """多 channel 运行 — main + health 独立连接"""
        main_task = asyncio.create_task(self._run_main())
        health_task = asyncio.create_task(self._run_health())
        # 任一 channel 退出，等另一个完成
        done, pending = await asyncio.wait(
            [main_task, health_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        # 取消未完成的任务
        for task in pending:
            task.cancel()
        # 传播异常
        for task in done:
            if task.exception():
                raise task.exception()

    async def push_to_bridge(self, msg: dict):
        """将消息推送到桥接服务"""
        try:
            import websockets
            bridge_url = "ws://127.0.0.1:18901"
            
            # 快速检查桥接服务是否可用
            try:
                async with websockets.connect(bridge_url, open_timeout=1) as ws:
                    # 注册
                    await ws.send(safe_json_dumps({
                        "cmd": "register",
                        "agent_id": self.agent_id
                    }))
                    await asyncio.wait_for(ws.recv(), timeout=1)
                    
                    # 推送消息
                    await ws.send(safe_json_dumps({
                        "cmd": "message",
                        "msg": msg,
                        "msg_id": msg.get("msg_id", str(uuid.uuid4())[:8])
                    }))
                    
                    # 等待 ack
                    resp = safe_json_loads(await asyncio.wait_for(ws.recv(), timeout=1))
                    if resp.get("cmd") == "ack":
                        log.debug(f"📤 消息已推送到桥接: {msg.get('msg_id', 'unknown')[:8]}")
            except (ConnectionRefusedError, asyncio.TimeoutError):
                # 桥接服务未运行，静默跳过
                pass
                    
        except Exception as e:
            log.debug(f"⚠️ 推送到桥接失败: {e}")

    # ── AIM 任务协议 handler ──────────────────────────

    async def _handle_task(self, task_json: str, msg_from: str, msg_id: str):
        """处理 AIM 任务协议消息 ([task] 前缀)"""
        try:
            task = json.loads(task_json)
        except json.JSONDecodeError:
            log.warning(f"任务JSON解析失败: {task_json[:100]}")
            return

        task_id = task.get("id", msg_id)
        task_type = task.get("type", "unknown")
        task_to = task.get("to", [])
        task_from = task.get("from", "")

        # 安全校验：from 字段必须与消息来源一致
        if task_from != msg_from:
            log.warning(f"任务来源不匹配: 消息来自 {msg_from}, 任务声明 {task_from}")
            return

        # 目标校验
        if self.agent_id not in task_to:
            log.info(f"📋 任务跳过: [{task_id}] 目标不是自己 ({task_to})")
            return

        log.info(f"📋 任务收到: [{task_id}] {task_type}: {task.get('title','')}")

        # 去重：已处理过的任务 ID 跳过
        if not hasattr(self, "_processed_tasks"):
            self._processed_tasks = set()
        if task_id in self._processed_tasks:
            log_task_progress(task_id, "任务", "skip", f"已处理过 (去重)")
            return
        self._processed_tasks.add(task_id)

        task_from = task.get("from", "")
        # 发 received 状态
        await self._send_task_status(task_id, "received", task_from=task_from)
        await self._send_task_status(task_id, "processing", task_from=task_from)

        # 按类型分发
        try:
            if task_type == "review":
                result = await self._handle_task_review(task)
            elif task_type == "execute":
                result = await self._handle_task_execute(task)
            elif task_type == "request":
                result = await self._handle_task_request(task)
            elif task_type == "confirm":
                result = await self._handle_task_confirm(task)
            elif task_type == "notify":
                result = {"summary": "已记录"}
            else:
                log.warning(f"📋 任务未知类型: [{task_id}] {task_type}")
                result = {"error": f"未知任务类型: {task_type}"}
                await self._send_task_status(task_id, "failed", result=result, task_from=task_from)
                return

            log.info(f"📋 任务完成: [{task_id}] {task_type} → OK")
            await self._send_task_status(task_id, "done", result=result, task_from=task_from)

        except Exception as e:
            log.error(f"📋 任务失败: [{task_id}] {e}")
            await self._send_task_status(task_id, "failed", result={"error": str(e)}, task_from=task_from)

    async def _send_task_status(self, task_id: str, status: str, result: dict = None, task_from: str = ""):
        """通过 AIM Hub 直接发送任务状态更新（不走 aim_send.py，避免认证频率限制）"""
        payload = {"id": task_id, "status": status}
        if result:
            payload["result"] = result
        content = "[task-status] " + json.dumps(payload, ensure_ascii=False)
        try:
            # 通过 AIM 的 send 命令发回给任务发送方
            # 不走 aim_send.py 子进程（避免认证频率限制 + 进程开销）
            target = task_from if task_from else self.agent_id
            if self._ws:
                send_cmd = {
                    "cmd": "send",
                    "to": target,
                    "content": content,
                    "group": False,
                    "msg_id": str(uuid.uuid4())[:12]
                }
                await self._ws.send(safe_json_dumps(send_cmd))
        except Exception as e:
            log.error(f"发送任务状态失败: {e}")

    async def _handle_task_review(self, task: dict) -> dict:
        """review 类型：调用 AI 分析后返回结构化反馈（带超时保护）"""
        body = task.get("body", "")
        title = task.get("title", "")
        prompt = f"【评审任务】{title}\n\n{body}\n\n请给出评审意见，包含: 整体评价、问题列表、改进建议。"
        context = self._build_context(task.get("from", ""), False)
        try:
            ai_response = await asyncio.wait_for(
                self._call_ai(prompt, context, task.get("from", ""), "medium"),
                timeout=120
            )
            return {"summary": ai_response[:500] if ai_response else "无回复"}
        except asyncio.TimeoutError:
            return {"error": "AI评审超时 (120s)"}
        except Exception as e:
            return {"error": f"AI评审异常: {e}"}

    async def _handle_task_execute(self, task: dict) -> dict:
        """execute 类型：执行脚本/命令（带安全限制 + 超时保护）"""
        body = task.get("body", "")
        allowed_prefixes = ["ls", "cat", "head", "tail", "wc", "python3 -c", "date", "echo", "pwd", "whoami"]
        safe = any(body.strip().startswith(p) for p in allowed_prefixes)
        if not safe:
            return {"error": f"命令不在白名单中，已拒绝执行", "allowed": allowed_prefixes}
        try:
            proc = await asyncio.create_subprocess_shell(
                body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode()[:1000] if stdout else "",
                "stderr": stderr.decode()[:500] if stderr else ""
            }
        except asyncio.TimeoutError:
            return {"error": "执行超时 (30s)"}
        except Exception as e:
            return {"error": str(e)}

    async def _handle_task_request(self, task: dict) -> dict:
        """request 类型：查询本地信息（直接执行，不走AI）"""
        body = task.get("body", "")
        # 解析查询意图，直接执行对应命令
        body_lower = body.lower()
        result = {}
        
        if "时间" in body_lower or "time" in body_lower:
            import datetime
            now = datetime.datetime.now()
            result["time"] = now.strftime("%Y-%m-%d %H:%M:%S %z")
            result["timezone"] = str(now.astimezone().tzinfo)
        if "日期" in body_lower or "date" in body_lower or "今天" in body_lower:
            import datetime
            result["date"] = datetime.datetime.now().strftime("%Y-%m-%d %A")
        if "进程" in body_lower or "ps" in body_lower or "运行" in body_lower:
            # 安全检查：只允许 ps aux 类查询
            import subprocess
            try:
                p = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
                lines = p.stdout.split("\n")
                # 只返回关键进程的统计
                result["process_count"] = len([l for l in lines if l.strip()]) - 1
                result["agent_processes"] = len([l for l in lines if "aim-agent" in l])
            except:
                pass
        if "磁盘" in body_lower or "disk" in body_lower or "空间" in body_lower:
            import shutil
            usage = shutil.disk_usage("/")
            result["disk"] = {
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "used_pct": round(usage.used / usage.total * 100, 1)
            }
        
        if not result:
            # fallback: 返回基本信息
            import datetime, os, socket
            result["hostname"] = socket.gethostname()
            result["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %z")
            result["agent_id"] = self.agent_id
        
        return {"data": result}

    async def _handle_task_confirm(self, task: dict) -> dict:
        """confirm 类型：确认验收（快速返回，不走AI）"""
        body = task.get("body", "")
        prompt = f"请确认以下内容是否通过：{body}\n请只回复 pass 或 fail，不要其他内容。"
        context = self._build_context(task.get("from", ""), False)
        try:
            ai_response = await asyncio.wait_for(
                self._call_ai(prompt, context, task.get("from", ""), "high"),
                timeout=30
            )
            passed = "pass" in (ai_response or "").lower()[:10]
            return {"verdict": "pass" if passed else "fail", "summary": (ai_response or "")[:500]}
        except asyncio.TimeoutError:
            return {"verdict": "fail", "error": "确认超时"}
        except Exception as e:
            return {"verdict": "fail", "error": str(e)}

    # ── AIM 升级指令 handler ──────────────────────────

    async def _handle_update(self, update_info: dict):
        """处理 AIM 升级指令 — 只提示，不自动执行"""
        version = update_info.get("version", "")
        vtype = update_info.get("type", "MINOR")
        desc = update_info.get("description", "")
        
        local_ver = "unknown"
        ver_file = os.path.expanduser("~/.hermes/aim/VERSION")
        if os.path.exists(ver_file):
            with open(ver_file) as f:
                local_ver = f.read().strip()
        
        type_names = {"BREAKING": "🔴 大版本", "MINOR": "🟡 小版本", "PATCH": "🟢 修复"}
        type_name = type_names.get(vtype, "更新")
        
        log.info(f"📢 {type_name} 可用: v{version} (当前: v{local_ver})")
        log.info(f"   说明: {desc[:80]}")
        
        if vtype == "BREAKING":
            log.info(f"   涉及通信协议变更，请手动执行: python3 ~/.hermes/scripts/aim_version.py upgrade")
        else:
            log.info(f"   建议升级: python3 ~/.hermes/scripts/aim_version.py upgrade")


def main():
    parser = argparse.ArgumentParser(description="AIM Agent Daemon")
    parser.add_argument("--agent-id", required=True, help="Agent ID (ZS0001/ZS0002/ZS0003)")
    parser.add_argument("--framework", required=True, choices=["hermes", "openclaw", "qwenpaw", "crewai"],
                        help="AI framework")
    parser.add_argument("--server", default="ws://127.0.0.1:18900", help="AIM server URL")
    parser.add_argument("--token", help="Auth token (or from tokens.json)")
    args = parser.parse_args()

    # 加载 token
    token = args.token
    if not token:
        tokens_file = BASE_DIR / "tokens.json"
        if tokens_file.exists():
            with open(tokens_file) as f:
                tokens = json.load(f)
            token = tokens.get(args.agent_id, "")

    if not token:
        print(f"ERROR: 未提供 token")
        sys.exit(1)

    # 加载 Agent 信息
    name, emoji = "", ""
    config_file = BASE_DIR / "config.json"
    if config_file.exists():
        with open(config_file) as f:
            config = json.load(f)
        agent_cfg = config.get("agents", {}).get(args.agent_id, {})
        name = agent_cfg.get("name", "")
        emoji = agent_cfg.get("emoji", "")

    daemon = AIMAgentDaemon(
        agent_id=args.agent_id,
        token=token,
        server=args.server,
        framework=args.framework,
        name=name,
        emoji=emoji,
    )
    daemon.run()


if __name__ == "__main__":
    main()


# ========== 消息桥接 ==========
