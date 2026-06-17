#!/usr/bin/env python3
"""
AIM 标准应用层自动触发机制
所有 Agent 都可以复用的自动触发框架

功能：
1. 消息接收 → 自动触发 AI 分析
2. 分析结果 → 自动调用工具
3. 调用结果 → 自动反馈给对方 Agent

使用方法：
    from aim_auto_trigger import AIMAutoTrigger
    
    trigger = AIMAutoTrigger(agent_id="ZS0001")
    trigger.start()
"""

import os
import sys
import json
import time
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime

# 添加 AIM 平台路径
sys.path.insert(0, os.path.expanduser("~/.hermes/aim"))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("AIMAutoTrigger")


class AIMAutoTrigger:
    """AIM 标准应用层自动触发机制"""
    
    def __init__(self, agent_id: str, framework: str = "openclaw"):
        """
        初始化自动触发机制
        
        Args:
            agent_id: Agent ID（如 ZS0001, ZS0002）
            framework: 框架类型（openclaw, hermes 等）
        """
        self.agent_id = agent_id
        self.framework = framework
        self.running = False
        self.handlers: Dict[str, Callable] = {}
        
        # AIM 平台路径
        self.aim_dir = os.path.expanduser("~/.hermes/aim")
        self.messages_file = os.path.join(self.aim_dir, "messages.jsonl")
        
        # 注册默认处理器
        self._register_default_handlers()
        
        logger.info(f"🚀 AIMAutoTrigger 初始化完成 (Agent: {agent_id})")
    
    def _register_default_handlers(self):
        """注册默认消息处理器"""
        self.handlers = {
            "message": self._handle_message,
            "task": self._handle_task,
            "status": self._handle_status,
            "heartbeat": self._handle_heartbeat,
        }
    
    def register_handler(self, msg_type: str, handler: Callable):
        """
        注册自定义消息处理器
        
        Args:
            msg_type: 消息类型
            handler: 处理函数
        """
        self.handlers[msg_type] = handler
        logger.info(f"📝 注册处理器: {msg_type}")
    
    def start(self):
        """启动自动触发机制"""
        logger.info(f"🟢 启动自动触发机制 (Agent: {self.agent_id})")
        self.running = True
        
        # 启动消息监听循环
        asyncio.run(self._message_loop())
    
    def stop(self):
        """停止自动触发机制"""
        logger.info(f"🔴 停止自动触发机制 (Agent: {self.agent_id})")
        self.running = False
    
    async def _message_loop(self):
        """消息监听循环"""
        logger.info("📡 启动消息监听循环")
        
        last_position = 0
        while self.running:
            try:
                # 检查新消息
                new_messages = self._read_new_messages(last_position)
                
                for msg in new_messages:
                    # 更新位置
                    last_position = msg.get("_position", last_position)
                    
                    # 过滤：只处理发给自己的消息
                    if msg.get("to") != self.agent_id:
                        continue
                    
                    # 触发处理
                    await self._trigger_handler(msg)
                
                # 等待一段时间再检查
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"❌ 消息循环错误: {e}")
                await asyncio.sleep(5)
    
    def _read_new_messages(self, last_position: int) -> list:
        """读取新消息"""
        messages = []
        
        try:
            if not os.path.exists(self.messages_file):
                return messages
            
            with open(self.messages_file, "r") as f:
                # 跳到上次位置
                f.seek(last_position)
                
                # 读取新行
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        msg = json.loads(line)
                        msg["_position"] = f.tell()
                        messages.append(msg)
                    except json.JSONDecodeError:
                        continue
                
        except Exception as e:
            logger.error(f"❌ 读取消息失败: {e}")
        
        return messages
    
    async def _trigger_handler(self, msg: Dict[str, Any]):
        """触发消息处理器"""
        msg_type = msg.get("type", "message")
        
        logger.info(f"📨 收到消息: {msg_type} from {msg.get('from', 'unknown')}")
        
        # 查找处理器
        handler = self.handlers.get(msg_type)
        if handler:
            try:
                await handler(msg)
            except Exception as e:
                logger.error(f"❌ 处理器错误: {e}")
        else:
            logger.warning(f"⚠️ 未找到处理器: {msg_type}")
    
    async def _handle_message(self, msg: Dict[str, Any]):
        """处理普通消息"""
        logger.info(f"💬 处理消息: {msg.get('content', '')[:50]}...")
        
        # AI 分析
        analysis = await self._analyze(msg)
        
        # 执行操作
        result = await self._execute(analysis)
        
        # 反馈结果
        await self._feedback(msg.get("from"), result)
    
    async def _handle_task(self, msg: Dict[str, Any]):
        """处理任务消息"""
        logger.info(f"📋 处理任务: {msg.get('task_name', 'unknown')}")
        
        # AI 分析
        analysis = await self._analyze(msg)
        
        # 执行操作
        result = await self._execute(analysis)
        
        # 反馈结果
        await self._feedback(msg.get("from"), result)
    
    async def _handle_status(self, msg: Dict[str, Any]):
        """处理状态消息"""
        logger.info(f"📊 处理状态: {msg.get('status', 'unknown')}")
        # 状态消息通常不需要反馈
    
    async def _handle_heartbeat(self, msg: Dict[str, Any]):
        """处理心跳消息"""
        logger.debug(f"💓 处理心跳: {msg.get('from', 'unknown')}")
        # 心跳消息通常不需要反馈
    
    async def _analyze(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        AI 分析消息
        
        Args:
            msg: 消息内容
        
        Returns:
            分析结果
        """
        logger.info("🧠 AI 分析中...")
        
        # 这里应该调用 AI 进行分析
        # 示例：简单分类
        content = msg.get("content", "")
        
        analysis = {
            "original_msg": msg,
            "intent": self._classify_intent(content),
            "entities": self._extract_entities(content),
            "priority": self._assess_priority(content),
        }
        
        logger.info(f"🧠 分析结果: intent={analysis['intent']}, priority={analysis['priority']}")
        return analysis
    
    def _classify_intent(self, content: str) -> str:
        """分类意图"""
        # 简单的关键词匹配
        content_lower = content.lower()
        
        if any(word in content_lower for word in ["任务", "task", "工作"]):
            return "task"
        elif any(word in content_lower for word in ["问题", "bug", "错误"]):
            return "problem"
        elif any(word in content_lower for word in ["状态", "status", "进度"]):
            return "status_query"
        elif any(word in content_lower for word in ["帮助", "help", "怎么"]):
            return "help"
        else:
            return "general"
    
    def _extract_entities(self, content: str) -> list:
        """提取实体"""
        # 简单的实体提取
        entities = []
        
        # 提取 @提及
        import re
        mentions = re.findall(r'@(\w+)', content)
        entities.extend([{"type": "mention", "value": m} for m in mentions])
        
        return entities
    
    def _assess_priority(self, content: str) -> str:
        """评估优先级"""
        content_lower = content.lower()
        
        if any(word in content_lower for word in ["紧急", "urgent", "立即", "马上"]):
            return "high"
        elif any(word in content_lower for word in ["重要", "important", "尽快"]):
            return "medium"
        else:
            return "low"
    
    async def _execute(self, analysis: Dict[str, Any]) -> Any:
        """
        执行操作
        
        Args:
            analysis: 分析结果
        
        Returns:
            执行结果
        """
        logger.info("⚡ 执行操作...")
        
        intent = analysis.get("intent", "general")
        
        # 根据意图执行不同操作
        if intent == "task":
            return await self._execute_task(analysis)
        elif intent == "problem":
            return await self._execute_problem(analysis)
        elif intent == "status_query":
            return await self._execute_status_query(analysis)
        elif intent == "help":
            return await self._execute_help(analysis)
        else:
            return await self._execute_general(analysis)
    
    async def _execute_task(self, analysis: Dict[str, Any]) -> str:
        """执行任务"""
        # 这里应该调用具体的任务执行逻辑
        return "任务已接收，正在处理..."
    
    async def _execute_problem(self, analysis: Dict[str, Any]) -> str:
        """执行问题处理"""
        # 这里应该调用具体的问题处理逻辑
        return "问题已记录，正在分析..."
    
    async def _execute_status_query(self, analysis: Dict[str, Any]) -> str:
        """执行状态查询"""
        # 这里应该查询实际状态
        return "状态正常"
    
    async def _execute_help(self, analysis: Dict[str, Any]) -> str:
        """执行帮助"""
        # 这里应该提供帮助信息
        return "我可以帮助你处理任务、查询状态、解决问题。"
    
    async def _execute_general(self, analysis: Dict[str, Any]) -> str:
        """执行通用处理"""
        # 这里应该调用通用处理逻辑
        return "收到消息"
    
    async def _feedback(self, to: str, result: Any):
        """
        反馈结果
        
        Args:
            to: 目标 Agent ID
            result: 反馈内容
        """
        logger.info(f"📤 反馈结果给 {to}: {str(result)[:50]}...")
        
        # 这里应该通过 AIM 发送反馈
        # 示例：写入消息文件
        feedback_msg = {
            "from": self.agent_id,
            "to": to,
            "type": "message",
            "content": str(result),
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
        }
        
        # 写入消息文件
        try:
            with open(self.messages_file, "a") as f:
                f.write(json.dumps(feedback_msg) + "\n")
            logger.info(f"✅ 反馈已发送")
        except Exception as e:
            logger.error(f"❌ 反馈发送失败: {e}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="AIM 标准应用层自动触发机制")
    parser.add_argument("--agent-id", required=True, help="Agent ID")
    parser.add_argument("--framework", default="openclaw", help="框架类型")
    
    args = parser.parse_args()
    
    # 创建并启动自动触发机制
    trigger = AIMAutoTrigger(
        agent_id=args.agent_id,
        framework=args.framework
    )
    
    try:
        trigger.start()
    except KeyboardInterrupt:
        trigger.stop()


if __name__ == "__main__":
    main()
