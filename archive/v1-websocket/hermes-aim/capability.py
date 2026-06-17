from typing import Optional
#!/usr/bin/env python3
"""
AIM 能力注册与匹配模块

用法:
    from capability import CapabilityRegistry
    
    reg = CapabilityRegistry()
    reg.register("ZS0001", [...])  # 注册能力
    cap = reg.match("代码审查")     # 匹配能力
    agent = reg.select_agent("修复BUG")  # 选择最佳Agent
"""

import json
import time
from pathlib import Path


class CapabilityRegistry:
    """能力注册与匹配引擎"""

    DATA_FILE = Path.home() / "shared" / "hub" / "capabilities.json"

    PRIORITY_WEIGHT = {
        "high": 3,
        "medium": 2,
        "low": 1,
    }

    def __init__(self):
        self.DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, dict] = {}  # agent_id -> agent_info
        self._load()

    def _load(self):
        """从文件加载能力数据"""
        if self.DATA_FILE.exists():
            try:
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    self._agents = json.load(f)
            except Exception:
                self._agents = {}

    def _save(self):
        """保存能力数据到文件"""
        with open(self.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self._agents, f, ensure_ascii=False, indent=2)

    def register(self, agent_id: str, capabilities: list[dict], name: str = "", emoji: str = "", framework: str = ""):
        """注册Agent能力"""
        self._agents[agent_id] = {
            "agent_id": agent_id,
            "name": name,
            "emoji": emoji,
            "framework": framework,
            "capabilities": capabilities,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }
        self._save()

    def unregister(self, agent_id: str):
        """注销Agent能力"""
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._save()

    def get_agent(self, agent_id: str) -> Optional[dict]:
        """获取Agent能力信息"""
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[dict]:
        """获取所有Agent能力信息"""
        return list(self._agents.values())

    def get_capabilities(self, agent_id: str) -> list[dict]:
        """获取Agent的能力列表"""
        agent = self._agents.get(agent_id)
        return agent.get("capabilities", []) if agent else []

    def match(self, message: str, agent_id: str = None) -> Optional[dict]:
        """根据消息内容匹配最佳能力
        
        Args:
            message: 消息内容
            agent_id: 指定Agent（可选，不指定则搜索所有Agent）
        """
        message_lower = message.lower()
        candidates = []

        agents = [self._agents[agent_id]] if agent_id and agent_id in self._agents else self._agents.values()

        for agent in agents:
            for cap in agent.get("capabilities", []):
                score = 0
                for keyword in cap.get("keywords", []):
                    if keyword.lower() in message_lower:
                        score += 1
                if score > 0:
                    weighted = score * self.PRIORITY_WEIGHT.get(cap.get("priority", "medium"), 1)
                    candidates.append({
                        "agent_id": agent["agent_id"],
                        "capability": cap,
                        "score": weighted,
                    })

        if not candidates:
            return None

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[0]

    def select_agent(self, message: str) -> Optional[str]:
        """根据任务选择最佳Agent"""
        result = self.match(message)
        if result:
            return result["agent_id"]
        # 默认返回第一个在线Agent
        agents = list(self._agents.keys())
        return agents[0] if agents else None

    def query(self, capability_id: str = None) -> list[dict]:
        """查询能力"""
        results = []
        for agent in self._agents.values():
            caps = agent.get("capabilities", [])
            if capability_id:
                caps = [c for c in caps if c.get("id") == capability_id]
            if caps:
                results.append({
                    "agent_id": agent["agent_id"],
                    "name": agent["name"],
                    "capabilities": caps,
                })
        return results


# 全局单例
_registry = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry
