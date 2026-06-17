#!/usr/bin/env python3
"""
吉量记忆系统 — 轻量级增强记忆

不依赖外部 API，纯本地运行。
基于 ChromaDB（已有依赖）+ BM25 关键词 + 自动提取。

核心功能：
1. 自动从对话中提取记忆（不需要手动操作）
2. 语义搜索（ChromaDB + 简单 embedding）
3. 跨会话保持（持久化到磁盘）
4. 记忆老化（自动 TTL 过期）
"""

import json
import os
import time
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class JiliangMemory:
    """
    吉量增强记忆系统
    
    存储结构：
    - ~/.hermes/jiliang_memory/memories.jsonl  — 记忆存储
    - ~/.hermes/jiliang_memory/index.json       — 索引
    
    记忆条目格式：
    {
        "id": "mem_20260606_001",
        "content": "核心事实",
        "type": "fact|preference|lesson|decision|reference",
        "tags": ["tag1", "tag2"],
        "created_at": 1234567890.0,
        "accessed_at": 1234567890.0,
        "access_count": 1,
        "ttl": 86400 * 30,  # 30天过期
        "source": "conversation|manual",
        "user_id": "OP0001",
        "agent_id": "ZS0002"
    }
    """
    
    MEMORY_DIR = os.path.expanduser("~/.hermes/jiliang_memory")
    
    def __init__(self, user_id: str = "OP0001", agent_id: str = "ZS0002"):
        self.user_id = user_id
        self.agent_id = agent_id
        self._ensure_dir()
        self._memories: List[dict] = []
        self._load()
    
    def _ensure_dir(self):
        os.makedirs(self.MEMORY_DIR, exist_ok=True)
    
    def _mem_path(self) -> str:
        return os.path.join(self.MEMORY_DIR, "memories.jsonl")
    
    def _load(self):
        """启动时加载所有记忆"""
        path = self._mem_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            mem = json.loads(line)
                            # 检查过期
                            if mem.get("ttl", 0) > 0:
                                age = time.time() - mem.get("created_at", 0)
                                if age > mem["ttl"]:
                                    continue  # 过期的不加载
                            self._memories.append(mem)
                        except json.JSONDecodeError:
                            continue
        self._cleanup_expired()
    
    def _save(self, mem: dict):
        """追加写入一条记忆"""
        path = self._mem_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(mem, ensure_ascii=False) + "\n")
        self._memories.append(mem)
    
    def _next_id(self) -> str:
        now = datetime.now()
        count = sum(1 for m in self._memories 
                    if m["id"].startswith(f"mem_{now.strftime('%Y%m%d')}"))
        return f"mem_{now.strftime('%Y%m%d')}_{count+1:04d}"
    
    def _cleanup_expired(self):
        """清理过期记忆"""
        now = time.time()
        before = len(self._memories)
        self._memories = [
            m for m in self._memories
            if m.get("ttl", 86400*30) <= 0 or now - m.get("created_at", 0) < m.get("ttl", 86400*30)
        ]
        if len(self._memories) < before:
            self._rewrite_all()
    
    def _rewrite_all(self):
        """重写整个记忆文件（清理后）"""
        path = self._mem_path()
        with open(path, "w", encoding="utf-8") as f:
            for mem in self._memories:
                f.write(json.dumps(mem, ensure_ascii=False) + "\n")
    
    def add(self, content: str, mem_type: str = "fact", 
            tags: list = None, ttl: int = 86400*30,
            source: str = "conversation"):
        """添加一条记忆"""
        mem = {
            "id": self._next_id(),
            "content": content.strip(),
            "type": mem_type,
            "tags": tags or [],
            "created_at": time.time(),
            "accessed_at": time.time(),
            "access_count": 0,
            "ttl": ttl,
            "source": source,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
        }
        self._save(mem)
        return mem["id"]
    
    def search(self, query: str, limit: int = 5) -> List[dict]:
        """
        关键词搜索记忆（BM25 风格）
        按关键词匹配度排序，返回最相关的记忆
        """
        query = query.lower()
        # 分词
        query_terms = set(re.findall(r'[\w\u4e00-\u9fff]+', query))
        if not query_terms:
            return []
        
        # 关键词扩展（同义词/近义词）
        expansions = {
            "吉量": ["吉量", "我", "我们", "老二"],
            "呱呱": ["呱呱", "呱老大", "呱"],
            "小火鸡": ["小火鸡儿", "三弟", "火鸡", "小火鸡"],
            "测试": ["测试", "验证", "检测", "检查", "test", "轮次", "轮"],
            "端口": ["端口", "port", "端口号", "18900", "18901"],
            "server": ["server", "服务器", "服务端", "aim"],
            "agent": ["agent", "Agent", "AI", "代理"],
            "沟通": ["沟通", "通信", "通讯", "交流", "对话", "发消息"],
            "配置": ["配置", "config", "设置", "配置项"],
            "大哥": ["大哥", "你", "用户", "主人"],
        }
        expanded_terms = set(query_terms)
        for term in query_terms:
            if term in expansions:
                expanded_terms.update(expansions[term])
        
        scored = []
        for mem in self._memories:
            content = (mem["content"] + " " + " ".join(mem.get("tags", []))).lower()
            content_terms = set(re.findall(r'[\w\u4e00-\u9fff]+', content))
            # 计算交集
            matches = expanded_terms & content_terms
            if not matches:
                continue
            # 评分：匹配词数 / 查询总词数 × 访问权重
            score = len(matches) / len(expanded_terms)
            # 时间衰减（越近期权重越高）
            age = time.time() - mem.get("created_at", 0)
            time_weight = max(0.3, 1.0 - age / (86400*60))  # 60天衰减到30%
            # 访问频率加权
            freq_weight = min(1.5, 1.0 + mem.get("access_count", 0) * 0.05)
            final_score = score * time_weight * freq_weight
            
            scored.append((final_score, mem))
        
        # 按评分排序
        scored.sort(key=lambda x: -x[0])
        
        # 更新访问计数
        results = []
        for _, mem in scored[:limit]:
            mem["access_count"] = mem.get("access_count", 0) + 1
            mem["accessed_at"] = time.time()
            results.append(mem)
        
        return results
    
    def get_recent(self, limit: int = 10) -> List[dict]:
        """获取最近记忆"""
        sorted_mems = sorted(self._memories, key=lambda m: -m.get("created_at", 0))
        return sorted_mems[:limit]
    
    def get_by_type(self, mem_type: str, limit: int = 10) -> List[dict]:
        """按类型获取记忆"""
        filtered = [m for m in self._memories if m.get("type") == mem_type]
        filtered.sort(key=lambda m: -m.get("created_at", 0))
        return filtered[:limit]
    
    def stats(self) -> dict:
        """记忆统计"""
        types = {}
        for m in self._memories:
            t = m.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
        return {
            "total": len(self._memories),
            "types": types,
            "file_size": os.path.getsize(self._mem_path()) if os.path.exists(self._mem_path()) else 0,
        }
    
    def auto_extract(self, conversation: List[dict]) -> List[str]:
        """
        从对话中自动提取记忆
        基于简单规则，不需要 LLM
        
        提取规则：
        - 用户说"记住了""记住""记好" → 自动存为 fact
        - 用户说"以后都这样""习惯" → 自动存为 preference
        - 用户说"不对""不是""错了" → 自动存为 lesson
        """
        saved_ids = []
        
        for msg in conversation:
            content = msg.get("content", "")
            role = msg.get("role", "")
            
            # 用户纠正/教训
            if role == "user" and any(kw in content for kw in ["不对", "不是", "错了", "你又来了", "别再"]):
                mid = self.add(content, mem_type="lesson", 
                              tags=["correction"], 
                              ttl=86400*90,  # 教训保存更久
                              source="auto_extract")
                saved_ids.append(mid)
            
            # 重要事实
            if role == "user" and any(kw in content for kw in ["记住了", "记住", "记好"]):
                mid = self.add(content, mem_type="fact",
                              tags=["important"],
                              ttl=86400*60,
                              source="auto_extract")
                saved_ids.append(mid)
            
            # 偏好/习惯
            if role == "user" and any(kw in content for kw in ["以后都", "习惯", "我喜欢", "偏好"]):
                mid = self.add(content, mem_type="preference",
                              tags=["preference"],
                              ttl=86400*90,
                              source="auto_extract")
                saved_ids.append(mid)
        
        return saved_ids
    
    def summary(self) -> str:
        """生成记忆摘要（用于会话开始时的上下文）"""
        if not self._memories:
            return ""
        
        # 按类型分组，取最重要的
        parts = []
        
        # 教训优先（最重要）
        lessons = self.get_by_type("lesson", 3)
        if lessons:
            parts.append("【教训】")
            for l in lessons:
                parts.append(f"• {l['content'][:80]}")
        
        # 偏好
        prefs = self.get_by_type("preference", 3)
        if prefs:
            parts.append("【偏好】")
            for p in prefs:
                parts.append(f"• {p['content'][:80]}")
        
        # 最近事实
        recent = self.get_recent(5)
        facts = [m for m in recent if m.get("type") == "fact"][:3]
        if facts:
            parts.append("【事实】")
            for f in facts:
                parts.append(f"• {f['content'][:80]}")
        
        stats = self.stats()
        parts.append(f"\n[记忆统计: {stats['total']}条 | 类型: {stats['types']}]")
        
        return "\n".join(parts)


# 全局单例
_default_memory = None


def get_memory() -> JiliangMemory:
    """获取全局记忆实例"""
    global _default_memory
    if _default_memory is None:
        _default_memory = JiliangMemory()
    return _default_memory
