#!/usr/bin/env python3
"""
CrewAI Wrapper — 非交互式使用 crewai
用法: python3 crewai_wrapper.py "你的消息"
"""
import sys
import os

# 添加 crewai 的 Python 路径
sys.path.insert(0, os.path.expanduser("~/.local/share/uv/tools/crewai/lib/python3.12/site-packages"))

from crewai import Crew, Agent, Task
from crewai.llm import LLM

def main():
    if len(sys.argv) < 2:
        print("用法: python3 crewai_wrapper.py '你的消息'")
        sys.exit(1)
    
    prompt = sys.argv[1]
    
    # 创建一个简单的 agent
    agent = Agent(
        role="助手",
        goal="帮助用户解决问题",
        backstory="你是一个 helpful AI assistant",
        verbose=False,
        allow_delegation=False,
    )
    
    # 创建任务
    task = Task(
        description=prompt,
        expected_output="一个 helpful 的回复",
        agent=agent,
    )
    
    # 创建 crew 并执行
    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=False,
    )
    
    result = crew.kickoff()
    print(result)

if __name__ == "__main__":
    main()
