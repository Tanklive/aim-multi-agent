#!/usr/bin/env python3
"""AIM Agent NATS — 吉量 ZS0002 NATS 原生接入端 (Veritas 协议)

使用 aim_nats_sdk.py（标准 Veritas SDK）替代 WebSocket Hub。
集成 FrameworkCLI AI 调用 + Pin 去重 + Observer 事件 + 消息归档。

启动:
    python3 aim_agent_nats.py --agent-id ZS0002 --framework hermes

架构:
    AIMAgentNATS
    ├── AIMNATSClient (Veritas SDK — 连接/订阅/发送)
    ├── FrameworkCLI (AI 调用)
    ├── MessageDedup (内存 LRU 去重)
    ├── MessageArchive (JSONL 归档)
    └── Observer 事件 / 心跳

Author: 吉量 🐴
Protocol: AIM Veritas (§4, aim-veritas.md)
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Dict, Optional, Any

# ── 路径 ──────────────────────────────────

BASE_DIR = Path(__file__).parent
AIM_BASE = Path.home() / ".hermes" / "aim"
LOG_DIR = AIM_BASE / "logs"
DATA_DIR = AIM_BASE / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 引入标准 SDK
SDK_PATH = Path.home() / ".aim" / "bin" / "aim_nats_sdk.py"
if SDK_PATH.exists():
    sys.path.insert(0, str(SDK_PATH.parent))

try:
    from aim_nats_sdk import AIMNATSClient, Subjects, make_envelope, parse_message
except ImportError:
    print("ERROR: 未找到 aim_nats_sdk.py，请确认 ~/.aim/bin/ 下存在")
    print(f"  搜索路径: {SDK_PATH}")
    sys.exit(1)

# AIM Pin 持久化去重
PIN_PATH = Path.home() / "shared" / "aim" / "aim_pin.py"
if PIN_PATH.exists():
    sys.path.insert(0, str(PIN_PATH.parent))
try:
    from aim_pin import AIMPin
except ImportError:
    AIMPin = None  # fallback: 没有 PIN 就保持内存去重

# AIM Agent 共享模块（AIM_BASE 或当前目录）
sys.path.insert(0, str(AIM_BASE))
sys.path.insert(0, str(BASE_DIR))
from framework_cli import FrameworkCLI
from ai_types import AIRequest, AIResponse

# ── 日志 ──────────────────────────────────


def setup_logging(agent_id: str) -> logging.Logger:
    """配置日志（文件 + 控制台），防止重复添加 handler"""
    log_file = LOG_DIR / f"nats-agent-{agent_id}.log"
    log = logging.getLogger(f"aim-nats-{agent_id}")
    log.setLevel(logging.DEBUG)

    # 防止重复添加 handler（多次调用 setup_logging 时）
    if not log.handlers:
        fh = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        log.addHandler(fh)

        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        log.addHandler(ch)

    return log


# ── 消息归档 ─────────────────────────────


class MessageArchive:
    """消息归档到 JSONL"""

    def __init__(self, agent_id: str):
        self.file = DATA_DIR / f"nats_messages_{agent_id}.jsonl"

    def archive(self, msg: dict):
        try:
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            log.warning(f"归档失败: {e}")


# ── 组件 ──────────────────────────────────


class AIMAgentNATS:
    """AIM Agent NATS — 吉量 🐴

    使用 Veritas SDK（aim_nats_sdk.py）连接到 NATS Server，
    监听 aim.dm.* / aim.grp.* 消息，调用 AI 处理后回复。
    """

    MAX_CONCURRENT = 1
    AI_TIMEOUT_DEFAULT = 60     # 首次尝试 60秒
    AI_TIMEOUT_RETRY = 45       # 重试 45秒
    AI_TIMEOUT_SHORT = 30       # 简单消息/请求 30秒
    AI_TIMEOUT_LONG = 120       # 长任务 120秒
    AI_MAX_RETRIES = 1          # 只重试1次（超时了重试也很可能再超时）
    HEARTBEAT_INTERVAL = 240     # 心跳 240秒（4分钟），与 observer 过期时间错开，留余量
    PING_INTERVAL = 20

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        framework: str = "hermes",
        nats_url: str = "nats://127.0.0.1:4222",
        emoji: str = "🐴",
    ):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.framework = framework
        self.nats_url = nats_url
        self.emoji = emoji

        self.log = setup_logging(agent_id)
        self.log.info(f"🚀 {emoji} AIM Agent NATS v1.0 初始化")

        # SDK 客户端（从 config/aim.json 自动读取 token，命令行 URL 覆盖配置）
        self.client = AIMNATSClient.from_config(agent_id, server=nats_url)

        # 去重 + 归档
        if AIMPin is not None:
            self.dedup = AIMPin(agent_id, ttl=300, max_memory=2000)
        else:
            self.dedup = None
        self.archive = MessageArchive(agent_id)

        # 并发控制
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._active_tasks: Dict[str, asyncio.Task] = {}

        # AI 调用
        self._cli_paths = self._load_cli_paths()
        self._commands = self._load_commands_config()
        self._fw_cli = FrameworkCLI(self.framework, self._commands, self._cli_paths)

        # 运行状态
        self._running = False
        self._last_reply_time = 0
        self._reply_cooldown = 3

        # ── DM 三层冷却机制（防死循环 + 保留DM灵活性） ──
        # L1: 速率桶 {from_id: [(timestamp, text), ...]}  5分钟内最多3次AI回复
        # L2: ACK窗口 {from_id: [is_ack_bool, ...]}  最近5条中≥4条确认类→静默
        # L3: 回复去重 {from_id: (timestamp, reply_hash)}  30s内同回复→不发
        self._dm_rate_bucket: Dict[str, list] = {}      # L1 速率桶
        self._dm_ack_window: Dict[str, list] = {}        # L2 ACK窗口
        self._dm_last_reply: Dict[str, tuple] = {}       # L3 回复去重
        self._DM_RATE_LIMIT = 50         # 5分钟内最多50次AI回复（仅作极端熔断）
        self._DM_RATE_WINDOW = 300       # 速率窗口 5分钟
        self._DM_ACK_THRESHOLD = 4       # 最近5条中≥4条确认类→静默
        self._DM_ECHO_INTERVAL = 30      # 30秒内同回复→不发

        # 消息回调注册
        self._dm_handlers: list = []
        self._group_handlers: list = []

        # API 自适应退避：主会话和我共享同一 API key
        # 连续超时 count → 递增延迟，主会话空闲后自动恢复
        self._api_backoff_count = 0
        self._api_backoff_max = 5          # 最大连续超时计数
        self._api_backoff_delay = 10       # 基础延迟（秒）
        self._api_backoff_last_reset = time.time()

    # ── 配置加载 ─────────────────────────

    def _load_config(self) -> dict:
        config_file = AIM_BASE / "config.json"
        if config_file.exists():
            try:
                return json.loads(config_file.read_text())
            except Exception as e:
                self.log.warning(f"配置文件读取失败: {e}")
        return {}

    def _load_cli_paths(self) -> Dict[str, str]:
        config = self._load_config()
        cli_paths = {}
        agents = config.get("agents", {})
        for aid, info in agents.items():
            if isinstance(info, dict) and "cli" in info:
                cli_paths[aid] = os.path.expanduser(info["cli"])
        defaults = {
            "hermes": os.path.expanduser("~/.local/bin/hermes"),
            "openclaw": os.path.expanduser("~/.npm-global/bin/openclaw"),
            "letta": "letta",
        }
        for aid, cli in defaults.items():
            if aid not in cli_paths:
                cli_paths[aid] = cli
        return cli_paths

    def _load_commands_config(self) -> dict:
        config = self._load_config()
        return config.get("commands", {})

    # ── AI 调用 ─────────────────────────

    async def _call_ai(self, prompt: str, timeout: Optional[int] = None) -> str:
        """调用 AI 处理消息，带自适应退避（防主会话 API 竞争）"""
        if timeout is None:
            timeout = self.AI_TIMEOUT_DEFAULT

        # ── 自适应退避 ──
        # 连续超时/失败越多 → 延迟越久 → 主会话空闲后自动恢复
        now = time.time()
        # 每 5 分钟重置退避计数
        if now - self._api_backoff_last_reset > 300:
            if self._api_backoff_count > 0:
                self.log.info(f"🔄 API 退避重置: {self._api_backoff_count} → 0 (5min 超时窗口已过)")
            self._api_backoff_count = 0
            self._api_backoff_last_reset = now

        if self._api_backoff_count > 0:
            delay = self._api_backoff_delay * min(self._api_backoff_count, self._api_backoff_max)
            self.log.info(f"⏳ API 退避中 (count={self._api_backoff_count})，等待 {delay}s 后重试...")
            await asyncio.sleep(delay)

        last_error = ""
        for attempt in range(1, self.AI_MAX_RETRIES + 1):
            current_timeout = timeout if attempt == 1 else self.AI_TIMEOUT_RETRY
            try:
                result = await self._try_call_ai(prompt, current_timeout)
                if result:
                    if attempt > 1:
                        self.log.info(f"✅ AI 调用第{attempt}次尝试成功")
                    # 成功 → 逐步降低退避计数
                    if self._api_backoff_count > 0:
                        self._api_backoff_count = max(0, self._api_backoff_count - 1)
                        self._api_backoff_last_reset = now
                    return result
                last_error = f"AI 调用第{attempt}次返回空"
                self._api_backoff_count = min(self._api_backoff_count + 1, self._api_backoff_max)
            except Exception as e:
                last_error = str(e)
                self._api_backoff_count = min(self._api_backoff_count + 1, self._api_backoff_max)
                self.log.warning(f"⚠️ AI 调用第{attempt}次失败 (backoff={self._api_backoff_count}): {last_error}")

            if attempt < self.AI_MAX_RETRIES:
                self.log.info(f"🔄 2s 后重试...")
                await asyncio.sleep(2)

        self.log.error(f"❌ AI 调用最终失败（{self.AI_MAX_RETRIES}次尝试）: {last_error}")
        return ""

    async def _try_call_ai(self, prompt: str, timeout: int) -> str:
        """单次 AI 调用尝试，带存活探针 + watchdog 兜底超时"""
        # 存活探针：调 AI 前快速确认 Hermes CLI 可执行
        if not await self._check_cli_healthy():
            self.log.warning("⚠️ Hermes CLI 探针失败，跳过 AI 调用，发 fallback")
            return ""

        if self._fw_cli:
            try:
                request = AIRequest(
                    prompt=prompt,
                    agent_id=self.agent_id,
                    timeout=timeout,
                )
                # watchdog: 独立定时器强杀，不依赖 pipe 关闭
                response: AIResponse = await self._call_with_watchdog(
                    self._fw_cli.call(request), timeout
                )
                if response.success and response.text:
                    return response.text.strip()
                self.log.warning(f"AI 调用未返回内容: {response.error}")
            except Exception as e:
                self.log.error(f"FrameworkCLI 调用失败: {e}")

        return await self._fallback_call_ai(prompt, timeout)

    async def _check_cli_healthy(self) -> bool:
        """检查 Hermes CLI 是否可用（存活探针）"""
        cli_path = self._cli_paths.get(self.framework, self.framework)
        try:
            proc = await asyncio.create_subprocess_exec(
                cli_path, "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await asyncio.wait_for(proc.wait(), timeout=5)
            return rc == 0
        except Exception:
            return False

    async def _call_with_watchdog(self, coro, timeout: int):
        """带 watchdog 强杀机制的异步调用，不依赖 pipe 关闭"""
        task = asyncio.create_task(coro)
        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            # 延时一小段等待 cancellation 传播
            try:
                await asyncio.wait_for(task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            raise
        except Exception:
            task.cancel()
            raise

    async def _fallback_call_ai(self, prompt: str, timeout: int) -> str:
        """降级：直接执行 hermes chat -q"""
        cli_path = self._cli_paths.get(self.framework, self.framework)
        cmd = [cli_path, "chat", "-q", prompt, "-Q"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace").strip()
                lines = [line for line in output.split("\n")
                         if not line.startswith("⚠️")
                         and 'Normalized model' not in line
                         and not line.startswith('Query:')
                         and not line.startswith('session_id:')
                         and not line.startswith('─')]
                return "\n".join(lines).strip()
            else:
                self.log.error(f"CLI 调用失败 (rc={proc.returncode}): {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            self.log.error(f"AI 调用超时 ({timeout}s)")
        except FileNotFoundError:
            self.log.error(f"CLI 未找到: {cli_path}")
        except Exception as e:
            self.log.error(f"AI 调用异常: {e}")

        return ""

    # ── 消息处理 ─────────────────────────

    async def handle_message(self, msg_data: dict):
        """处理一条消息（私聊或群聊）"""
        # 从标准信封解析
        msg_id = msg_data.get("id", "")
        if not msg_id:
            # 可能已有 msg_id 字段
            msg_id = msg_data.get("msg_id", "")
        if not msg_id:
            return

        # Observer 回放过滤：跳过超过 10 分钟的旧消息
        ts_str = msg_data.get("ts", "")
        if ts_str:
            try:
                from datetime import datetime, timezone
                import re
                ts_clean = ts_str.replace('Z', '+00:00')
                msg_ts = datetime.fromisoformat(ts_clean)
                now = datetime.now(timezone.utc)
                age_seconds = (now - msg_ts).total_seconds()
                if age_seconds > 600:  # 超过 10 分钟
                    self.log.debug(f"🕰️ 跳过 observer 回放旧消息: {msg_id[:8]} (age={int(age_seconds)}s)")
                    return
            except (ValueError, TypeError):
                pass  # 时间戳解析失败，继续处理

        # 去重（AIMPin 持久化版）
        if self.dedup is not None:
            if await self.dedup.is_duplicate(msg_id):
                self.log.debug(f"🔄 跳过去重消息: {msg_id}")
                return
            await self.dedup.mark(msg_id)

        msg_type = msg_data.get("type", "dm")
        from_id = msg_data.get("from", "")
        payload = msg_data.get("payload", {})
        content = ""
        if isinstance(payload, dict):
            content = payload.get("text", "")
        else:
            content = str(payload)

        if not content and msg_data.get("content"):
            content = msg_data["content"]

        if not content:
            return

        # 不自言自语
        if from_id == self.agent_id:
            self.log.debug(f"🚫 跳过自己的消息: {msg_id}")
            return

        # ── DM 三层冷却机制 ──
        if msg_type == "dm" and content:
            import hashlib
            now = time.time()

            # ── 判定：是否为确认类消息 ──
            # 确认类 = 纯表情 / <20字且不含?？问题等 / 不含任务关键词
            text_clean = content.strip()
            has_question = any(c in text_clean for c in ("?", "？", "吗", "呢", "吧"))
            has_task = any(kw in text_clean for kw in (
                "帮我", "能不能", "帮我查", "帮我做", "看一下", "处理",
                "分析", "对比", "怎么", "如何", "修复", "优化",
                "升级", "部署", "测试", "上线", "发布",
            ))
            has_link_or_file = any(kw in text_clean for kw in ("http", "://", ".py", ".md", ".json", ".sh"))
            is_ack = (
                (len(text_clean) < 3 and text_clean in ("收到", "ok", "OK", "👌", "✅", "👍", "好的", "稍等", "pong", "嗯", "👂"))
                or (len(text_clean) < 20 and not has_question and not has_task and not has_link_or_file)
            )

            # ── Layer 2: ACK 窗口检测 ──
            ack_window = self._dm_ack_window.setdefault(from_id, [])
            ack_window.append(is_ack)
            if len(ack_window) > 5:
                ack_window.pop(0)
            ack_ratio = sum(1 for a in ack_window if a)

            # L2 静默：只在当前消息也是确认类时触发（防止误拦真实提问）
            if is_ack and ack_ratio >= self._DM_ACK_THRESHOLD:
                self.log.warning(
                    f"🚫 DM L2静默: {from_id} 最近5条中{ack_ratio}条确认类 → 中断循环"
                )
                self.archive.archive(msg_data)
                return

            # ── Layer 1: 速率桶 — 只计AI真实回复，确认类不计入 ──
            now = time.time()
            bucket = self._dm_rate_bucket.setdefault(from_id, [])
            # 清理过期记录
            bucket[:] = [b for b in bucket if now - b[0] < self._DM_RATE_WINDOW]

            if len(bucket) >= self._DM_RATE_LIMIT:
                self.log.warning(
                    f"🚫 DM L1限速: {from_id} 5分钟内已{len(bucket)}次AI回复 → 跳过"
                )
                self.archive.archive(msg_data)
                return

            # 纯确认消息 → 本地回 👌，不走AI（但计入速率桶）
            if is_ack:
                self.log.debug(f"ℹ️ DM 确认类: {from_id} - {content[:30]}")
                self.archive.archive(msg_data)
                await self.client.send_dm(from_id, "👌", reply_to=msg_id)
                # 记入速率桶（本地回复也占一个槽位，防止频繁确认占满回复额度）
                if len(bucket) >= self._DM_RATE_LIMIT:
                    bucket.pop(0)
                bucket.append((now, "<本地确认>"))
                return

        # 群聊节流：区分"与我无关的闲聊"vs"涉及协作的消息"
        # 明确提到我（ID/昵称）→ 必处理
        # 未提我但含协作/方案/任务关键词 → 处理（可能涉及分工讨论）
        # 大哥指令"你们一起/团队/评审"等 → 三方协作，必须处理
        # 纯确认/问候/闲聊 → 跳过
        if msg_type == "grp":
            mentioned_me = any(kw in content for kw in ("ZS0002", "吉量", "@ZS0002", "@吉量"))
            collab_keywords = any(kw in content for kw in (
                # 大哥三方协作指令
                "你们", "一起", "团队", "评审", "分工",
                # 通用协作词
                "方案", "讨论", "协作", "任务", "推进", "安排",
                "通知", "汇报", "回复", "反馈", "跟进", "确认", "完成",
                "问题", "修复", "优化", "改一下", "更新",
                "@ZS0001", "@ZS0003", "@all", "@所有人",
                "你觉得", "你怎么", "你看", "帮忙", "配合",
            ))
            if not mentioned_me and not collab_keywords:
                self.log.debug(f"🚫 群聊节流: 未提及协作内容，跳过 AI: {msg_id[:8]}")
                self.archive.archive(msg_data)
                return
            # 排除词：提到我了但实际是状态转述/不需要我回复的消息
            skip_phrases = ("收听了", "稍后看", "已收到", "知道了", "了解", "收到消息")
            if any(p in content for p in skip_phrases):
                self.log.debug(f"🚫 群聊节流: 状态转述/无需回复，跳过 AI: {msg_id[:8]}")
                self.archive.archive(msg_data)
                return

        # Observer: 去重通过，开始处理（合并 received+processing 减少事件）
        self.archive.archive(msg_data)

        # 队列饱和度检查：活跃任务超过容量 → 跳过不进AI处理
        active = len(self._active_tasks)
        queued = self.semaphore._value  # 剩余槽位，但MAX_CONCURRENT=1时这个不准确
        if active >= 2:  # 已有消息在处理 + 至少1条排队 = 响应已经慢了
            self.log.warning(f"⏳ AI 队列已满 ({active}活跃, sem={self.semaphore._value})，跳过: {msg_id[:8]}")
            return

        # 并发处理
        task = asyncio.create_task(
            self._process_message(msg_id, msg_type, from_id, content, msg_data)
        )
        self._active_tasks[msg_id] = task
        task.add_done_callback(lambda t: self._active_tasks.pop(msg_id, None))

    async def _process_message(
        self, msg_id: str, msg_type: str, from_id: str,
        content: str, raw_msg: dict
    ):
        """处理消息（AI 调用 + 回复）"""
        group_id = raw_msg.get("meta", {}).get("group", "") if isinstance(raw_msg.get("meta"), dict) else ""

        log.info(f"⚙️ [{msg_id[:8]}] {msg_type} from={from_id}: {content[:80]}")

        # Anti-loop: 跳过处理失败/错误消息，防止无限循环
        # 覆盖各种变体：处理失败/处理失败 * / ❌.../ error / Hermes CLI not found 等
        normalized = content.strip().lower()
        skip_keywords = [
            '处理失败',          # 标准中文
            '❌',                # 任何 ❌ 开头的错误
            'hermes cli not found',  # CLI 未找到
            'command not found',     # 命令未找到
            'error:',               # 通用错误
            'traceback',            # Python 回溯
        ]
        for keyword in skip_keywords:
            if normalized.startswith(keyword) or content.strip().startswith(keyword):
                log.warning(f"🚫 跳过处理失败/错误消息，避免循环: {msg_id[:8]} ({keyword})")
                return

        # Anti-loop: 跳过 CLI 噪声消息
        if content.startswith(('⚠️', 'Normalized model', 'Query:', 'Initializing', 'session_id:')):
            log.debug(f"🚫 跳过 CLI 噪声: {content[:50]}")
            return

        # Observer: 处理中 — 在 semaphore 外发出，避免被并发槽占满阻塞
        await self.client.emit_obs("processing", msg_id, f"处理 {from_id} 的消息")

        async with self.semaphore:
            try:
                # 构造 prompt — 明确给出角色上下文，防止 AI 把消息当"Observer 系统回调"短路
                # 你是吉量(ZS0002🐴)，这是一条来自 AIM 的消息，你需要代表吉量回复对方
                msg_source = "私聊" if msg_type == "dm" else "群聊"
                prompt = (
                    f"你正在通过 AIM（Agent Instant Messaging）接收消息。\n"
                    f"你是吉量（ZS0002🐴），你是 AIM 团队的一员。\n"
                    f"收到来自 {from_id} 的{msg_source}消息：\n"
                    f"---\n{content}\n"
                    f"---\n"
                    f"请作为吉量回复这条消息。直接输出回复内容，不要额外分析说明。"
                )

                # Observer: AI 调用开始
                await self.client.emit_obs("ai_start", msg_id, f"调用 AI 框架处理: {content[:50]}...")
                reply = await self._call_ai(prompt)

                if reply:
                    # Observer: AI 回复完成（带实际内容）
                    await self.client.emit_obs("ai_done", msg_id, f"💬 {reply[:200]}")

                    # ── Layer 3: 回复去重 ──
                    # 检测：如果回复与30秒内发给同对手的上一条回复相同 → 跳过
                    if msg_type == "dm":
                        import hashlib
                        reply_hash = hashlib.md5(reply.encode()).hexdigest()[:12]
                        last_reply = self._dm_last_reply.get(from_id)
                        if last_reply:
                            last_ts, last_hash = last_reply
                            if (now := time.time()) - last_ts < self._DM_ECHO_INTERVAL and last_hash == reply_hash:
                                log.warning(f"🚫 DM L3回声: {from_id} 30s内重复回复 → 跳过")
                                await self.client.emit_obs("ai_done", msg_id, f"🔇 回声拦截")
                                return
                        self._dm_last_reply[from_id] = (time.time(), reply_hash)

                        # ── Layer 1 记入速率桶 ──
                        now = time.time()
                        bucket = self._dm_rate_bucket.setdefault(from_id, [])
                        bucket[:] = [b for b in bucket if now - b[0] < self._DM_RATE_WINDOW]
                        bucket.append((now, reply[:80]))

                        await self.client.send_dm(from_id, reply, reply_to=msg_id)
                        log.info(f"✅ 回复 {from_id}: {reply[:60]}...")
                    elif msg_type == "grp" and group_id:
                        await self.client.send_grp(group_id, reply)
                        log.info(f"✅ 回复群聊 {group_id}: {reply[:60]}...")
                    elif msg_type == "grp" and raw_msg.get("group"):
                        await self.client.send_grp(raw_msg["group"], reply)
                        log.info(f"✅ 回复群聊 {raw_msg['group']}: {reply[:60]}...")

                    # 回复内容已在 ai_done 展示，completed 不再重复发
                    # await self.client.emit_obs("completed", msg_id, "")
                else:
                    # Observer: AI 无回复 — 发简短确认避免完全沉默
                    fallback = f"🐴 收到（AI 响应延迟，稍后处理）"
                    await self.client.emit_obs("ai_empty", msg_id, "AI 未生成回复，发简短确认")

                    if msg_type == "dm":
                        await self.client.send_dm(from_id, fallback, reply_to=msg_id)
                    elif msg_type == "grp" and (group_id or raw_msg.get("group")):
                        await self.client.send_grp(group_id or raw_msg["group"], fallback)
                    log.warning(f"⚠️ AI 未生成回复，已发简短确认: {msg_id}")

            except Exception as e:
                log.error(f"❌ 处理消息失败 [{msg_id[:8]}]: {e}")
                import traceback
                log.error(traceback.format_exc())
                await self.client.emit_obs("error", msg_id, str(e))

    # ── NATS 回调 ────────────────────────

    async def _on_dm_msg(self, envelope: dict, raw_msg):
        """私聊消息回调 (aim.dm.<agent_id>)"""
        try:
            log.debug(f"< 私聊: {envelope.get('from')} → {str(envelope.get('payload', {}).get('text', ''))[:80]}")
            await self.handle_message(envelope)
        except Exception as e:
            log.error(f"私聊处理失败: {e}")

    async def _on_grp_msg(self, envelope: dict, raw_msg):
        """群聊消息回调 (aim.grp.*)"""
        try:
            from_id = envelope.get("from", "")
            content = envelope.get("payload", {}).get("text", "")
            log.debug(f"< 群聊: {from_id} → {content[:80]}")
            await self.handle_message(envelope)
        except Exception as e:
            log.error(f"群聊处理失败: {e}")

    async def _on_request(self, raw_msg):
        """请求-回复回调 (aim.req.<agent_id>)"""
        try:
            data = parse_message(raw_msg.data)
            log.info(f"< 请求: {data.get('from')}: {str(data.get('payload', {}).get('text', ''))[:80]}")

            content = data.get("payload", {}).get("text", "")
            reply = await self._call_ai(content, timeout=self.AI_TIMEOUT_SHORT)

            # 修复：AI调用失败时返回"ok"而不是"处理失败"，避免触发对方的处理循环
            response = make_envelope(
                from_id=self.agent_id,
                msg_type="response",
                payload={"text": reply or "ok"},
            )
            await raw_msg.respond(json.dumps(response, ensure_ascii=False).encode())
        except Exception as e:
            log.error(f"请求处理失败: {e}")

    # ── 生命周期 ─────────────────────────

    async def on_connect(self):
        """连接成功后回调"""
        log.info(f"🟢 [{self.agent_id}] 已连接到 NATS: {self.nats_url}")
        await self.client.emit_obs("agent_online", "", f"{self.agent_id} ({self.agent_name}) 已上线")

    async def on_reconnect(self):
        """重连成功后回调（不发 agent_online，只记录日志）"""
        log.info(f"🔄 [{self.agent_id}] 重连成功: {self.nats_url}")

    async def on_disconnect(self):
        """断开连接后回调"""
        log.info(f"🔴 [{self.agent_id}] 已断开 NATS 连接")
        await self.client.emit_obs("agent_offline", "", f"{self.agent_id} ({self.agent_name}) 已下线")

    async def heartbeat_task(self):
        """定期心跳"""
        while self._running:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            if self.client.is_connected:
                await self.client.emit_obs("heartbeat", "", f"{self.agent_id} alive")
            else:
                log.warning("⏳ NATS 未连接，跳过心跳")

    # ── 注册 ─────────────────────────────

    async def register(self) -> bool:
        """通过 aim.reg.register request-reply 注册/确认身份"""
        log.info(f"📝 [{self.agent_id}] 注册中 (aim.reg.register)...")
        try:
            request = {
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "framework": self.framework,
                "emoji": self.emoji,
            }
            response = await self.client.nc.request(
                "aim.reg.register",
                json.dumps(request).encode(),
                timeout=5,
            )
            result = json.loads(response.data)
            log.info(f"✅ 注册成功: {result}")
            return True
        except asyncio.TimeoutError:
            log.warning("⚠️ 注册超时，Server 可能未运行注册服务，跳过")
            return True
        except Exception as e:
            log.warning(f"⚠️ 注册失败 ({e})，降级跳过")
            return True

    # ── 启动停止 ─────────────────────────

    async def start(self):
        """启动 Agent（含自动重连）"""
        self._running = True

        log.info(f"🚀 {self.emoji} {self.agent_id} ({self.agent_name}) NATS Agent 启动")
        log.info(f"   NATS: {self.nats_url}")
        log.info(f"   框架: {self.framework}")

        retry_delay = 2
        first_connect = True
        while self._running:
            try:
                # 连接 NATS
                await self.client.connect()
                await self.client.setup_streams()
                if first_connect:
                    await self.on_connect()
                    first_connect = False
                else:
                    await self.on_reconnect()

                # 注册
                await self.register()

                # 订阅私聊 (SDK subscribe_dm)
                await self.client.subscribe_dm(self._on_dm_msg)
                log.info("📬 已订阅私聊: aim.dm.ZS0002")

                # 订阅请求
                sub = await self.client.nc.subscribe(
                    f"aim.req.{self.agent_id}", cb=self._on_request
                )
                log.info("📬 已订阅请求: aim.req.ZS0002")

                # 订阅群聊
                for gid in ["grp_trio"]:
                    await self.client.subscribe_grp(gid, self._on_grp_msg)
                    log.info(f"📬 已订阅群聊: aim.grp.{gid}")

                # 心跳
                hb_task = asyncio.create_task(self.heartbeat_task())

                retry_delay = 2  # 重置重试延迟
                log.info(f"✅ {self.agent_id} 启动完成，等待消息...")

                # 保持运行
                while self._running:
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                log.info("收到取消信号，停止")
                break
            except KeyboardInterrupt:
                log.info("收到中断信号，停止")
                break
            except Exception as e:
                log.error(f"❌ 运行异常: {e}")
                import traceback
                log.error(traceback.format_exc())
                if not self._running:
                    break
                # 自动重连
                log.info(f"⏳ {retry_delay}s 后重连...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
            finally:
                await self.shutdown()

    async def shutdown(self):
        """关闭 Agent"""
        self._running = False
        log.info("🛑 关闭中...")

        for msg_id, task in list(self._active_tasks.items()):
            task.cancel()
        self._active_tasks.clear()

        await self.on_disconnect()
        await self.client.close()
        log.info("✅ 已停止")


# ── CLI ──────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AIM Agent NATS (Veritas)")
    parser.add_argument("--agent-id", default="ZS0002", help="Agent ID")
    parser.add_argument("--agent-name", default="吉量", help="Agent 名称")
    parser.add_argument("--framework", default="hermes", help="AI 框架")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222", help="NATS Server URL")
    parser.add_argument("--emoji", default="🐴", help="Agent emoji")
    args = parser.parse_args()

    global log
    log = setup_logging(args.agent_id)

    agent = AIMAgentNATS(
        agent_id=args.agent_id,
        agent_name=args.agent_name,
        framework=args.framework,
        nats_url=args.nats_url,
        emoji=args.emoji,
    )

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        log.info("收到 SIGINT")


if __name__ == "__main__":
    main()
