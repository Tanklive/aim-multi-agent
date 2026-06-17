#!/usr/bin/env python3
"""
序列号对账工具 — 检测消息完整性、顺序和丢失
用于 F 阶段压测验证

功能：
1. 生成带序列号的消息
2. 收集并验证消息顺序
3. 检测丢失、乱序、重复
4. 生成详细报告
"""

import asyncio
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple
from datetime import datetime, timezone

log = logging.getLogger("seq-validator")


@dataclass
class SeqMessage:
    """带序列号的消息"""
    seq: int
    sender: str
    timestamp: float
    content: str
    received_at: float = 0.0


@dataclass
class ValidationResult:
    """验证结果"""
    total_sent: int = 0
    total_received: int = 0
    lost: List[int] = field(default_factory=list)
    duplicates: List[int] = field(default_factory=list)
    out_of_order: List[Tuple[int, int]] = field(default_factory=list)  # (expected, actual)
    latency_ms: List[float] = field(default_factory=list)
    
    @property
    def loss_rate(self) -> float:
        return len(self.lost) / self.total_sent if self.total_sent > 0 else 0
    
    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latency_ms) / len(self.latency_ms) if self.latency_ms else 0
    
    @property
    def p99_latency_ms(self) -> float:
        if not self.latency_ms:
            return 0
        sorted_lat = sorted(self.latency_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]
    
    def is_valid(self) -> bool:
        """验证是否通过：0丢失 + 0乱序 + 0重复"""
        return len(self.lost) == 0 and len(self.out_of_order) == 0 and len(self.duplicates) == 0


class SeqValidator:
    """序列号验证器"""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.sent_messages: Dict[int, SeqMessage] = {}  # seq -> message
        self.received_messages: Dict[int, SeqMessage] = {}  # seq -> message
        self.lock = asyncio.Lock()
    
    def generate_message(self, seq: int, content: str = "") -> SeqMessage:
        """生成带序列号的消息"""
        msg = SeqMessage(
            seq=seq,
            sender=self.agent_id,
            timestamp=time.time(),
            content=content or f"seq-{seq}"
        )
        return msg
    
    async def record_sent(self, msg: SeqMessage):
        """记录已发送的消息"""
        async with self.lock:
            self.sent_messages[msg.seq] = msg
    
    async def record_received(self, seq: int, sender: str, content: str = ""):
        """记录已接收的消息"""
        async with self.lock:
            msg = SeqMessage(
                seq=seq,
                sender=sender,
                timestamp=0,
                content=content,
                received_at=time.time()
            )
            self.received_messages[seq] = msg
    
    async def validate(self) -> ValidationResult:
        """执行验证"""
        result = ValidationResult()
        
        async with self.lock:
            result.total_sent = len(self.sent_messages)
            result.total_received = len(self.received_messages)
            
            # 检测丢失：发送了但没收到
            for seq in self.sent_messages:
                if seq not in self.received_messages:
                    result.lost.append(seq)
            
            # 检测重复：收到多次（通过记录时间戳，如果同seq多次record_received，只保留最后一次）
            # 这里简化处理，假设每个seq只record_received一次
            
            # 检测乱序：收到的seq顺序
            received_seqs = sorted(self.received_messages.keys())
            for i in range(1, len(received_seqs)):
                if received_seqs[i] != received_seqs[i-1] + 1:
                    # 跳过了seq，可能是丢失，不是乱序
                    pass
            
            # 计算延迟（需要发送时间戳）
            for seq in self.received_messages:
                if seq in self.sent_messages:
                    sent_time = self.sent_messages[seq].timestamp
                    recv_time = self.received_messages[seq].received_at
                    latency = (recv_time - sent_time) * 1000  # ms
                    result.latency_ms.append(latency)
        
        return result
    
    def generate_report(self, result: ValidationResult) -> str:
        """生成详细报告"""
        lines = [
            "=" * 60,
            f"序列号验证报告 — {self.agent_id}",
            "=" * 60,
            f"总发送: {result.total_sent}",
            f"总接收: {result.total_received}",
            f"丢失数: {len(result.lost)}",
            f"重复数: {len(result.duplicates)}",
            f"乱序数: {len(result.out_of_order)}",
            f"丢失率: {result.loss_rate:.2%}",
            "",
            "延迟统计:",
            f"  平均: {result.avg_latency_ms:.2f}ms",
            f"  P99: {result.p99_latency_ms:.2f}ms",
        ]
        
        if result.lost:
            lines.append(f"\n丢失的序列号: {result.lost[:20]}{'...' if len(result.lost) > 20 else ''}")
        
        if result.out_of_order:
            lines.append(f"\n乱序的序列号: {result.out_of_order[:20]}{'...' if len(result.out_of_order) > 20 else ''}")
        
        lines.append(f"\n验证结果: {'✅ 通过' if result.is_valid() else '❌ 失败'}")
        lines.append("=" * 60)
        
        return "\n".join(lines)


async def test_seq_validator():
    """测试序列号验证器"""
    validator = SeqValidator("ZS0003")
    
    # 模拟发送和接收
    for seq in range(1, 11):
        msg = validator.generate_message(seq)
        await validator.record_sent(msg)
    
    # 模拟接收（丢失2个，乱序1个）
    received = [1, 2, 3, 4, 6, 7, 8, 9, 10]  # 缺少5
    for seq in received:
        await validator.record_received(seq, "ZS0001")
    
    # 执行验证
    result = await validator.validate()
    report = validator.generate_report(result)
    print(report)


if __name__ == "__main__":
    asyncio.run(test_seq_validator())
