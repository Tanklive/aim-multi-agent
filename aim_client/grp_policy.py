"""
AIM 群聊冷却机制 — 三级热状态（Server端统一配置）

设计目标：
  - 被 @ → HOT：主动跟进，全面参与
  - 闲后 → WARM：降权，有实质才回
  - 再闲 → COLD：没 @ 就跳过

配置读取：~/.aim/config/grp_policy.json

使用：
  policy = GroupPolicyManager()
  state = policy.state_for(grp_id)     # "HOT" | "WARM" | "COLD"
  policy.mark_active(grp_id)            # 群有新消息时调用
  policy.mark_mentioned(grp_id)         # 被 @ 时调用 → 立即切 HOT
  should = policy.should_process(grp_id, is_mentioned, has_substance)  # 综合判断
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class _GroupState:
    grp_id: str
    state: str = "COLD"          # HOT | WARM | COLD
    last_active: float = 0.0     # 最后一条消息时间
    last_mentioned: float = 0.0  # 最后被 @ 时间
    message_count: int = 0       # 当前热态内消息计数

    def transition(self, now: float, hot_sec: int, warm_sec: int, cold_sec: int) -> str:
        """根据当前状态和时间计算新状态"""
        since_active = now - self.last_active
        since_mentioned = now - self.last_mentioned

        if self.state == "HOT":
            if since_mentioned > hot_sec and since_active > hot_sec:
                self.state = "WARM"
        elif self.state == "WARM":
            if since_mentioned > hot_sec + warm_sec and since_active > hot_sec:
                self.state = "COLD"
        elif self.state == "COLD":
            pass  # COLD 只能通过被 @ 升级

        return self.state


class GroupPolicyManager:
    """群聊三级热状态管理器 — 无状态（纯内存），重启后从 COLD 开始"""

    def __init__(self, config_path: Optional[Path] = None, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("grp-policy")

        # 读 server 端配置
        if config_path is None:
            config_path = Path.home() / ".aim" / "config" / "grp_policy.json"

        self._config = self._load_config(config_path)
        self.hot_sec = self._config.get("hot_after_mention_sec", 600)
        self.warm_sec = self._config.get("warm_after_idle_sec", 600)
        self.cold_sec = self._config.get("cold_after_idle_sec", 1800)
        self.dm_policy = self._config.get("dm_policy", "always")

        # 每个群的运行时状态 { grp_id: _GroupState }
        self._groups: Dict[str, _GroupState] = {}

    # ── 配置 ──

    @staticmethod
    def _load_config(path: Path) -> dict:
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        # 默认配置（硬编码兜底，发布时有文件覆盖）
        return {
            "hot_after_mention_sec": 600,
            "warm_after_idle_sec": 600,
            "cold_after_idle_sec": 1800,
            "dm_policy": "always",
        }

    # ── 公共 API ──

    def state_for(self, grp_id: str) -> str:
        """获取群当前热状态"""
        gs = self._groups.get(grp_id)
        if gs is None:
            return "COLD"
        now = time.time()
        gs.transition(now, self.hot_sec, self.warm_sec, self.cold_sec)
        return gs.state

    def mark_active(self, grp_id: str) -> None:
        """群有新消息时更新活跃时间"""
        gs = self._get_or_create(grp_id)
        now = time.time()
        gs.last_active = now
        gs.message_count += 1

    def mark_mentioned(self, grp_id: str) -> None:
        """被 @ 时立即切 HOT 并更新时间"""
        gs = self._get_or_create(grp_id)
        now = time.time()
        gs.state = "HOT"
        gs.last_mentioned = now
        gs.last_active = now

    def should_process(self, grp_id: str, is_mentioned: bool, has_substance: bool = True) -> bool:
        """综合判断：这条群消息该不该处理

        Args:
            grp_id: 群 ID
            is_mentioned: 是否被 @
            has_substance: 消息是否有实质内容（非纯 ACK/表情）

        Returns:
            True → 投递 adapter 处理；False → 跳过
        """
        # 被 @ 始终处理（最高优先级）
        if is_mentioned:
            self.mark_mentioned(grp_id)
            self.logger.info(f" [{grp_id}] 🟢 HOT (@mentioned)")
            return True

        # DM 不受群策略影响
        if not grp_id:
            return True

        gs = self._get_or_create(grp_id)
        now = time.time()

        # 计算冷却状态
        since_mentioned = now - gs.last_mentioned
        since_active = now - gs.last_active

        if since_mentioned < self.hot_sec:
            # HOT: 被 @ 后的热度窗口
            gs.last_active = now
            gs.message_count += 1
            return True

        if since_active < self.hot_sec:
            # WARM: 群活跃但未被 @
            gs.last_active = now
            gs.message_count += 1
            if has_substance:
                self.logger.debug(f" [{grp_id}] 🟡 WARM (active={since_active:.0f}s, has_substance)")
                return True
            else:
                self.logger.debug(f" [{grp_id}] 🟡 WARM skip (no substance)")
                return False

        # COLD: 全跳过
        self.logger.info(f" [{grp_id}] 🔵 COLD skip (mentioned={since_mentioned:.0f}s, active={since_active:.0f}s)")
        return False

    # ── 辅助 ──

    def _get_or_create(self, grp_id: str) -> _GroupState:
        if grp_id not in self._groups:
            self._groups[grp_id] = _GroupState(grp_id=grp_id)
        return self._groups[grp_id]
