#!/usr/bin/env python3
"""
CrewAI LLM 调用脚本 — 使用 crewai 自己的 Python 3.12 环境
用法: python3 crewai_llm_call.py "你的消息"
"""
import sys
import os
import json

# 设置 API 配置
os.environ['OPENAI_API_KEY'] = 'tp-cwj8k83ag3ih82tke461y31pmwqpdeif4jvl1pp5cvzr2top'
os.environ['OPENAI_API_BASE'] = 'https://YOUR_API_ENDPOINT/v1'

from crewai.llm import LLM

def main():
    if len(sys.argv) < 2:
        print("用法: crewai_llm_call.py '消息'", file=sys.stderr)
        sys.exit(1)
    
    prompt = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    
    try:
        llm = LLM(
            model='openai/mimo-v2.5-pro',
            api_key='YOUR_API_KEY_HERE',
            base_url='https://token-plan-cn.xiaomimimo.com/v1',
        )
        
        response = llm.call(prompt)
        print(response)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
