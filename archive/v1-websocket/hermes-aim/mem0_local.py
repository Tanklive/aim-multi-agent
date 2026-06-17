#!/usr/bin/env python3
"""
Mem0 本地适配层
使用 Ollama bge-m3 做语义搜索，不依赖 Qdrant
"""

import json
import os
import time
import hashlib
import subprocess
from typing import List, Dict, Optional
from pathlib import Path

class Mem0Local:
    """本地 Mem0 适配层，使用 Ollama bge-m3 做语义搜索"""
    
    def __init__(self, storage_dir: str = None):
        self.storage_dir = storage_dir or os.path.expanduser("~/.openclaw/workspace/memory/mem0")
        os.makedirs(self.storage_dir, exist_ok=True)
        
        self.memories_file = os.path.join(self.storage_dir, "memories.jsonl")
        self.embeddings_file = os.path.join(self.storage_dir, "embeddings.json")
        
        # 加载现有记忆
        self.memories = self._load_memories()
        self.embeddings = self._load_embeddings()
    
    def _load_memories(self) -> List[Dict]:
        """加载记忆文件"""
        memories = []
        if os.path.exists(self.memories_file):
            with open(self.memories_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        memories.append(json.loads(line))
        return memories
    
    def _load_embeddings(self) -> Dict:
        """加载 embedding 缓存"""
        if os.path.exists(self.embeddings_file):
            with open(self.embeddings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def _save_memory(self, memory: Dict):
        """保存记忆"""
        with open(self.memories_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(memory, ensure_ascii=False) + '\n')
        self.memories.append(memory)
    
    def _save_embeddings(self):
        """保存 embedding 缓存"""
        with open(self.embeddings_file, 'w', encoding='utf-8') as f:
            json.dump(self.embeddings, f)
    
    def _get_embedding(self, text: str) -> List[float]:
        """获取文本的 embedding"""
        # 检查缓存
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in self.embeddings:
            return self.embeddings[text_hash]
        
        # 调用 Ollama API
        try:
            import requests
            response = requests.post(
                "http://127.0.0.1:11434/api/embeddings",
                json={"model": "qllama/bge-m3:latest", "prompt": text},
                timeout=10
            )
            if response.status_code == 200:
                embedding = response.json().get("embedding", [])
                # 缓存
                self.embeddings[text_hash] = embedding
                self._save_embeddings()
                return embedding
        except Exception as e:
            print(f"获取 embedding 失败: {e}")
        
        return []
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def add(self, content: str, user_id: str = "default", agent_id: str = "default", 
            metadata: Dict = None) -> Dict:
        """添加记忆"""
        memory_id = hashlib.md5(f"{content}{time.time()}".encode()).hexdigest()[:12]
        
        memory = {
            "id": memory_id,
            "content": content,
            "user_id": user_id,
            "agent_id": agent_id,
            "metadata": metadata or {},
            "created_at": time.time(),
            "updated_at": time.time()
        }
        
        # 获取 embedding
        embedding = self._get_embedding(content)
        if embedding:
            memory["embedding_hash"] = hashlib.md5(content.encode()).hexdigest()
        
        self._save_memory(memory)
        return memory
    
    def search(self, query: str, user_id: str = None, agent_id: str = None, 
               limit: int = 5) -> List[Dict]:
        """语义搜索记忆"""
        if not self.memories:
            return []
        
        # 获取查询的 embedding
        query_embedding = self._get_embedding(query)
        if not query_embedding:
            # 降级到关键词搜索
            return self._keyword_search(query, user_id, agent_id, limit)
        
        # 计算相似度
        results = []
        for memory in self.memories:
            # 过滤条件
            if user_id and memory.get("user_id") != user_id:
                continue
            if agent_id and memory.get("agent_id") != agent_id:
                continue
            
            # 获取记忆的 embedding
            content = memory.get("content", "")
            content_embedding = self._get_embedding(content)
            if not content_embedding:
                continue
            
            # 计算相似度
            similarity = self._cosine_similarity(query_embedding, content_embedding)
            if similarity > 0.3:  # 阈值
                results.append({**memory, "score": similarity})
        
        # 按相似度排序
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:limit]
    
    def _keyword_search(self, query: str, user_id: str = None, agent_id: str = None, 
                       limit: int = 5) -> List[Dict]:
        """关键词搜索（降级方案）"""
        results = []
        query_lower = query.lower()
        
        for memory in self.memories:
            # 过滤条件
            if user_id and memory.get("user_id") != user_id:
                continue
            if agent_id and memory.get("agent_id") != agent_id:
                continue
            
            content = memory.get("content", "").lower()
            if query_lower in content:
                results.append(memory)
        
        return results[:limit]
    
    def get_all(self, user_id: str = None, agent_id: str = None) -> List[Dict]:
        """获取所有记忆"""
        results = []
        for memory in self.memories:
            if user_id and memory.get("user_id") != user_id:
                continue
            if agent_id and memory.get("agent_id") != agent_id:
                continue
            results.append(memory)
        return results
    
    def update(self, memory_id: str, content: str) -> Optional[Dict]:
        """更新记忆"""
        for i, memory in enumerate(self.memories):
            if memory.get("id") == memory_id:
                self.memories[i]["content"] = content
                self.memories[i]["updated_at"] = time.time()
                # 重新保存所有记忆
                self._save_all_memories()
                return self.memories[i]
        return None
    
    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        for i, memory in enumerate(self.memories):
            if memory.get("id") == memory_id:
                self.memories.pop(i)
                self._save_all_memories()
                return True
        return False
    
    def _save_all_memories(self):
        """保存所有记忆"""
        with open(self.memories_file, 'w', encoding='utf-8') as f:
            for memory in self.memories:
                f.write(json.dumps(memory, ensure_ascii=False) + '\n')


# 测试
if __name__ == "__main__":
    mem0 = Mem0Local()
    
    # 添加记忆
    mem0.add("我是大哥，管理呱呱、吉量、小火鸡儿", user_id="OP0001", agent_id="ZS0001")
    mem0.add("AIM Server 端口是 18900", user_id="OP0001", agent_id="shared")
    mem0.add("呱呱是 ZS0001，吉量是 ZS0002", user_id="OP0001", agent_id="shared")
    
    # 搜索
    results = mem0.search("谁是大哥", user_id="OP0001")
    print("搜索结果:")
    for r in results:
        print(f"  - {r['content']} (score: {r.get('score', 0):.2f})")
    
    # 获取所有
    all_memories = mem0.get_all(user_id="OP0001")
    print(f"\n所有记忆: {len(all_memories)} 条")
