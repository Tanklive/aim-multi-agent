#!/usr/bin/env python3
"""
Mem0 配置文件
使用 Groq Llama 3.3 70B + Ollama bge-m3 + Qdrant
"""

from mem0 import Memory

# Groq API Key
GROQ_API_KEY = "gsk_YOUR_GROQ_API_KEY_HERE"

# Mem0 配置
config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "mem0_memories",
            "host": "localhost",
            "port": 6333,
            "embedding_model_dims": 1024,  # bge-m3 维度
        },
    },
    "llm": {
        "provider": "groq",
        "config": {
            "model": "llama-3.3-70b-versatile",
            "api_key": GROQ_API_KEY,
        },
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "qllama/bge-m3:latest",
            "ollama_base_url": "http://localhost:11434",
        },
    },
}

def get_memory():
    """获取 Memory 实例"""
    return Memory.from_config(config)

# 测试
if __name__ == "__main__":
    m = get_memory()
    
    # 添加记忆
    result = m.add([
        {"role": "user", "content": "我是大哥，管理呱呱、吉量、小火鸡儿"},
        {"role": "assistant", "content": "明白了大哥"}
    ], user_id="OP0001")
    print(f"添加记忆: {result}")
    
    # 搜索
    results = m.search("谁是大哥", filters={"user_id": "OP0001"})
    print(f"搜索结果: {results}")
