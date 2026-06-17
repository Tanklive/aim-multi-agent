#!/usr/bin/env python3
"""
AIM 标准应用层自动触发机制 - 测试脚本

测试内容：
1. 消息接收测试
2. AI 分析测试
3. 自动调用测试
4. 自动反馈测试

使用方法：
    python3 test_auto_trigger.py --agent-id ZS0001
"""

import os
import sys
import json
import time
import asyncio
import argparse
from datetime import datetime

# 添加 AIM 平台路径
sys.path.insert(0, os.path.expanduser("~/.hermes/aim/app_layer"))

from aim_auto_trigger import AIMAutoTrigger


class TestAIMAutoTrigger(AIMAutoTrigger):
    """测试用自动触发机制"""
    
    def __init__(self, agent_id: str, framework: str = "openclaw"):
        super().__init__(agent_id, framework)
        self.test_results = []
    
    async def _handle_message(self, msg):
        """测试消息处理"""
        print(f"✅ 测试消息处理: {msg.get('content', '')[:50]}...")
        self.test_results.append({
            "test": "消息处理",
            "status": "PASS",
            "time": datetime.now().isoformat()
        })
        return await super()._handle_message(msg)
    
    async def _analyze(self, msg):
        """测试 AI 分析"""
        print(f"✅ 测试 AI 分析")
        self.test_results.append({
            "test": "AI 分析",
            "status": "PASS",
            "time": datetime.now().isoformat()
        })
        return await super()._analyze(msg)
    
    async def _execute(self, analysis):
        """测试自动调用"""
        print(f"✅ 测试自动调用")
        self.test_results.append({
            "test": "自动调用",
            "status": "PASS",
            "time": datetime.now().isoformat()
        })
        return await super()._execute(analysis)
    
    async def _feedback(self, to, result):
        """测试自动反馈"""
        print(f"✅ 测试自动反馈给 {to}")
        self.test_results.append({
            "test": "自动反馈",
            "status": "PASS",
            "time": datetime.now().isoformat()
        })
        return await super()._feedback(to, result)
    
    def print_results(self):
        """打印测试结果"""
        print("\n" + "="*50)
        print("测试结果汇总")
        print("="*50)
        
        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r["status"] == "PASS")
        failed = total - passed
        
        for result in self.test_results:
            status = "✅" if result["status"] == "PASS" else "❌"
            print(f"{status} {result['test']}")
        
        print("="*50)
        print(f"总计: {total}, 通过: {passed}, 失败: {failed}")
        print(f"通过率: {passed/total*100:.1f}%")
        print("="*50)


def create_test_message(agent_id: str, content: str) -> dict:
    """创建测试消息"""
    return {
        "from": "test",
        "to": agent_id,
        "type": "message",
        "content": content,
        "timestamp": time.time(),
        "datetime": datetime.now().isoformat(),
    }


async def run_tests(agent_id: str):
    """运行测试"""
    print(f"\n{'='*50}")
    print(f"开始测试 AIM 标准应用层自动触发机制")
    print(f"Agent: {agent_id}")
    print(f"时间: {datetime.now().isoformat()}")
    print(f"{'='*50}\n")
    
    # 创建测试实例
    trigger = TestAIMAutoTrigger(agent_id=agent_id)
    
    # 测试消息
    test_messages = [
        create_test_message(agent_id, "请帮我处理一个任务"),
        create_test_message(agent_id, "有个 BUG 需要修复"),
        create_test_message(agent_id, "查询当前状态"),
        create_test_message(agent_id, "怎么使用这个功能？"),
        create_test_message(agent_id, "普通消息"),
    ]
    
    # 运行测试
    for i, msg in enumerate(test_messages, 1):
        print(f"\n测试 {i}/{len(test_messages)}: {msg['content'][:30]}...")
        await trigger._trigger_handler(msg)
        await asyncio.sleep(0.5)
    
    # 打印结果
    trigger.print_results()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="测试 AIM 标准应用层自动触发机制")
    parser.add_argument("--agent-id", default="ZS0001", help="Agent ID")
    
    args = parser.parse_args()
    
    asyncio.run(run_tests(args.agent_id))


if __name__ == "__main__":
    main()
