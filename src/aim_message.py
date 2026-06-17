"""
AIM Message / Task 分层定义（v1.2 方案 5.6）

分层方案：
  Transport Layer
       │
       ▼
  Message Layer (通用投递)
       │
       ├── Chat  ─── 即时对话（你好、收到了）
       │
       └── Task  ─── 工作指令（帮我分析这个仓库）

Phase 0：不做分层，所有消息当 Chat 处理。
Phase 1：引入 AIMTask 定义，Scheduler 识别任务并创建 task_id 追踪。
Phase 2+：Task Contract 完整落地——negotiation / result / cancellation 生命周期。
"""

from dataclasses import dataclass, field
from typing import Literal, Optional


# ── 消息类型常量 ──

MSG_TYPE_CHAT = "chat"
MSG_TYPE_TASK = "task"

# ── Task 状态常量 ──

TASK_PENDING = "pending"
TASK_PROCESSING = "processing"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

TaskStatus = Literal["pending", "processing", "done", "failed", "cancelled"]


# ── 数据类型定义 ──


@dataclass
class AIMChat:
    """即时对话——无状态

    适用场景：你好、收到了、明白、1
    处理方式：Transport 层 publish 即可，Scheduler 直接投递到 Adapter process
    """

    content: str
    """消息文本内容"""

    from_id: str
    """发送方 Agent ID"""

    reply_to: Optional[str] = None
    """回复链中关联的上一条消息 ID（可选）"""

    msg_id: str = ""
    """消息唯一 ID（由传输层生成或分配）"""

    def to_dict(self) -> dict:
        return {
            "msg_type": MSG_TYPE_CHAT,
            "content": self.content,
            "from_id": self.from_id,
            "reply_to": self.reply_to,
            "msg_id": self.msg_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AIMChat":
        return cls(
            content=d.get("content", ""),
            from_id=d.get("from_id", ""),
            reply_to=d.get("reply_to"),
            msg_id=d.get("msg_id", ""),
        )


@dataclass
class AIMTask:
    """工作指令——有状态

    适用场景：帮我分析这个仓库、写一个 test、调研 XX 方案
    处理方式：需要创建 task_id 追踪，Scheduler 根据 execution_model 选择投递策略
    """

    task_id: str
    """全局唯一任务 ID"""

    type: str
    """任务类型：log-analysis / code-review / research / code / test / ..."""

    input: dict
    """任务输入参数"""

    owner: str
    """发送任务的人（Agent ID）"""

    executor: str
    """执行任务的人（Agent ID，谁接的任务）"""

    status: TaskStatus = TASK_PENDING
    """任务状态"""

    deadline: Optional[str] = None
    """截止时间（ISO 格式，可选）"""

    expect: Optional[dict] = None
    """期望输出格式（可选）"""

    result: Optional[dict] = None
    """任务执行结果（完成后填充）"""

    msg_id: str = ""
    """关联消息 ID"""

    created_at: float = 0.0
    """任务创建时间戳"""

    def to_dict(self) -> dict:
        return {
            "msg_type": MSG_TYPE_TASK,
            "task_id": self.task_id,
            "type": self.type,
            "input": self.input,
            "owner": self.owner,
            "executor": self.executor,
            "status": self.status,
            "deadline": self.deadline,
            "expect": self.expect,
            "result": self.result,
            "msg_id": self.msg_id,
            "created_at": self.created_at or __import__("time").time(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AIMTask":
        return cls(
            task_id=d.get("task_id", ""),
            type=d.get("type", ""),
            input=d.get("input", {}),
            owner=d.get("owner", ""),
            executor=d.get("executor", ""),
            status=d.get("status", TASK_PENDING),
            deadline=d.get("deadline"),
            expect=d.get("expect"),
            result=d.get("result"),
            msg_id=d.get("msg_id", ""),
            created_at=d.get("created_at", 0.0),
        )


# ── 消息类型分发 ──


def is_task_message(content: str) -> bool:
    """判断一条消息是否是 Task 而不是 Chat

    启发式判断：包含"帮"、"分析"、"写"、"调研"、"检查"等任务性动词开头。

    Phase 1 先用简单规则，Phase 2+ 可以升级为 AI 分类或更精确的模式匹配。
    """
    task_triggers = [
        "帮我", "帮我分析", "帮我写", "帮我检查", "帮我看看",
        "分析", "调研", "研究", "设计", "实现", "开发",
        "评估", "对比", "整理", "总结", "写一个", "写个",
    ]
    for t in task_triggers:
        if t in content:
            return True
    return False
