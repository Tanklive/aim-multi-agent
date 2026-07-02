"""
ContextManager — AIM Client 上下文管理

职责：
- 读取 SOUL.md / context-card.md / context-live.md
- 缓存并 mtime 检查实现热刷新
- 文件缺失时优雅降级（不崩溃）
- 组装完整上下文用于注入 adapter 协议

协议版本：ADAPTER-PROTOCOL v1.0
"""

import os
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContextCache:
    """单个文件缓存"""
    path: Path
    content: str = ""
    mtime: float = 0.0
    last_read: float = 0.0


@dataclass
class ContextAssembly:
    """组装后的上下文"""
    personality: str     # SOUL.md / 人格
    project: str         # context-card.md / 项目上下文
    live: str            # context-live.md / 即时上下文
    history: str = ""    # 对话历史（由 SessionManager 注入）


class ContextManager:
    """
    AIM Client 上下文管理器

    用法:
        cm = ContextManager(agent_id="ZS0001")

        # 获取完整上下文
        ctx = cm.get_context(
            history="[前3条群聊记录...]",
            from_id="ZS0002",
        )

        # 热刷新（mtime 变化自动处理）
        cm.reload()

        # 组装为 adapter 协议字段
        context_str = cm.assemble(from_id="ZS0002", history="...")
    """

    def __init__(
        self,
        agent_id: str,
        personality_path: Optional[str] = None,
        project_path: Optional[str] = None,
        live_path: Optional[str] = None,
    ):
        """
        Args:
            agent_id: Agent ID，用于推断默认路径
            personality_path: SOUL.md 路径，默认从 workspace 读取
            project_path: context-card 路径，默认 ~/shared/aim/PROJECT/context-card.md
            live_path: context-live 路径，默认 ~/shared/aim/PROJECT/context-live.md
        """
        self.agent_id = agent_id
        self._created_at = time.time()

        # 默认路径
        workspace = Path.home() / ".openclaw" / "workspace"
        shared_aim = Path.home() / "shared" / "aim" / "PROJECT"

        self._caches: Dict[str, ContextCache] = {}

        # 注册文件
        self._register(
            "personality",
            personality_path or str(workspace / "SOUL.md"),
            required=False,
        )
        self._register(
            "project",
            project_path or str(shared_aim / "context-card.md"),
            required=False,
        )
        self._register(
            "live",
            live_path or str(shared_aim / "context-live.md"),
            required=False,
        )

    def _register(self, key: str, path_str: str, required: bool = False):
        """注册一个上下文文件"""
        path = Path(path_str).expanduser()
        self._caches[key] = ContextCache(path=path)
        if not path.exists() and required:
            logger.warning(f"ContextManager: required file missing: {path}")
        elif not path.exists():
            logger.debug(f"ContextManager: optional file missing: {path}")

    def _read_file(self, key: str) -> str:
        """读取文件，自动处理 mtime 缓存和降级"""
        cache = self._caches[key]
        path = cache.path

        if not path.exists():
            return ""

        try:
            current_mtime = os.path.getmtime(path)
        except OSError:
            return cache.content  # 返回缓存

        # mtime 未变 → 命中缓存
        if current_mtime <= cache.mtime and cache.last_read > 0:
            return cache.content

        # 读取新内容
        try:
            content = path.read_text(encoding="utf-8")
            cache.content = content
            cache.mtime = current_mtime
            cache.last_read = time.time()
            logger.debug(f"ContextManager: read {key} ({len(content)} chars)")
            return content
        except Exception as e:
            logger.error(f"ContextManager: failed to read {key} ({path}): {e}")
            return cache.content  # 降级返回旧缓存

    def get_context(self, history: str = "", from_id: str = "") -> ContextAssembly:
        """获取完整上下文对象"""
        return ContextAssembly(
            personality=self._read_file("personality"),
            project=self._read_file("project"),
            live=self._read_file("live"),
            history=history,
        )

    def assemble(self, history: str = "", from_id: str = "", max_chars: int = 4000) -> str:
        """
        组装上下文为单字符串，用于注入 adapter 协议

        顺序：人格 → 项目上下文 → 对话历史 → 即时上下文（按优先级）
        超过 max_chars 时按比例截断（优先保留人格+项目）

        Args:
            history: 对话历史文本
            from_id: 消息来源，用于上下文个性化
            max_chars: 最大字符数

        Returns:
            组装好的上下文字符串
        """
        ctx = self.get_context(history=history, from_id=from_id)

        # 组装优先级：personality > project > history > live
        parts = []
        if ctx.personality:
            parts.append(ctx.personality)
        if ctx.project:
            parts.append(f"\n\n[项目上下文]\n{ctx.project}")
        if history:
            parts.append(f"\n\n[对话历史]\n{history}")
        if ctx.live:
            parts.append(f"\n\n[即时上下文]\n{ctx.live}")

        full = "".join(parts)

        # 超过限制 → 按比例截断
        if len(full) > max_chars:
            logger.info(f"ContextManager: context {len(full)} → {max_chars} (truncated)")
            # personality 至少保留 60%
            personality_len = min(len(ctx.personality), int(max_chars * 0.6))
            remaining = max_chars - personality_len

            truncated = ctx.personality[:personality_len]
            for label, text in [
                ("\n\n[项目上下文]\n", ctx.project),
                ("\n\n[对话历史]\n", history),
                ("\n\n[即时上下文]\n", ctx.live),
            ]:
                if remaining <= 0:
                    break
                chunk = text[:remaining]
                truncated += label + chunk
                remaining -= len(label) + len(chunk)

            return truncated

        return full

    def reload(self) -> None:
        """热刷新：清除所有 mtime 缓存，下次读取强制 reload"""
        for key, cache in self._caches.items():
            cache.mtime = 0.0
            cache.last_read = 0.0
        logger.info("ContextManager: cache invalidated (will reload on next read)")

    def health(self) -> bool:
        """健康检查：关键文件是否可读"""
        for key in ["personality"]:  # 仅检查必需文件
            cache = self._caches[key]
            if cache.path.exists():
                return True
        return False

    def status(self) -> dict:
        """运行时指标"""
        stats = {}
        for key, cache in self._caches.items():
            stats[key] = {
                "path": str(cache.path),
                "exists": cache.path.exists(),
                "size_chars": len(cache.content) if cache.content else 0,
                "last_read_sec_ago": (
                    int(time.time() - cache.last_read) if cache.last_read else -1
                ),
            }
        return stats
