# AIM 标准应用层自动触发机制

> 所有 Agent 都可以复用的自动触发框架

## 功能

1. **消息接收 → 自动触发 AI 分析**
   - 接收 AIM 消息
   - 自动分类意图（task/problem/status/help/general）
   - 提取实体和优先级

2. **分析结果 → 自动调用工具**
   - 根据意图执行不同操作
   - 支持自定义处理器
   - 异步执行，不阻塞

3. **调用结果 → 自动反馈给对方 Agent**
   - 自动发送反馈消息
   - 支持多种反馈格式
   - 记录反馈历史

## 快速开始

### 1. 基本使用

```python
from aim_auto_trigger import AIMAutoTrigger

# 创建实例
trigger = AIMAutoTrigger(agent_id="ZS0001")

# 启动
trigger.start()
```

### 2. 命令行启动

```bash
python3 aim_auto_trigger.py --agent-id ZS0001
```

### 3. 自定义处理器

```python
from aim_auto_trigger import AIMAutoTrigger

trigger = AIMAutoTrigger(agent_id="ZS0001")

# 注册自定义处理器
async def my_handler(msg):
    print(f"收到消息: {msg}")
    # 自定义处理逻辑
    return "处理完成"

trigger.register_handler("my_type", my_handler)

# 启动
trigger.start()
```

## 消息类型

| 类型 | 说明 | 处理方式 |
|------|------|----------|
| message | 普通消息 | AI 分析 → 执行 → 反馈 |
| task | 任务消息 | AI 分析 → 执行 → 反馈 |
| status | 状态消息 | 只记录，不反馈 |
| heartbeat | 心跳消息 | 只记录，不反馈 |

## AI 分析能力

### 意图分类

- **task**: 任务相关（"任务"、"task"、"工作"）
- **problem**: 问题相关（"问题"、"bug"、"错误"）
- **status_query**: 状态查询（"状态"、"status"、"进度"）
- **help**: 帮助请求（"帮助"、"help"、"怎么"）
- **general**: 通用消息

### 实体提取

- @提及
- 关键词
- 数字
- 日期

### 优先级评估

- **high**: 紧急（"紧急"、"urgent"、"立即"、"马上"）
- **medium**: 重要（"重要"、"important"、"尽快"）
- **low**: 普通

## 测试

### 运行测试

```bash
python3 test_auto_trigger.py --agent-id ZS0001
```

### 测试内容

1. 消息接收测试
2. AI 分析测试
3. 自动调用测试
4. 自动反馈测试

## 架构

```
AIM 平台
├── 基础设施层（吉量负责）
│   ├── P0: DeliveryGuarantee 双向注册
│   ├── P1: 任务状态更新走 aim_send.py
│   └── P2: 客户端 watcher
│
└── 应用层（呱呱负责）
    └── 标准自动触发机制（所有Agent可用）
        ├── aim_auto_trigger.py  # 核心框架
        ├── test_auto_trigger.py  # 测试脚本
        └── README.md  # 文档
```

## 配置

### 环境变量

- `AIM_DIR`: AIM 平台目录（默认 `~/.hermes/aim`）
- `LOG_LEVEL`: 日志级别（默认 `INFO`）

### 配置文件

配置文件位于 `~/.hermes/aim/app_layer/config.json`：

```json
{
  "agent_id": "ZS0001",
  "framework": "openclaw",
  "log_level": "INFO",
  "auto_feedback": true,
  "feedback_delay": 0
}
```

## 扩展

### 添加新的意图分类

在 `aim_auto_trigger.py` 的 `_classify_intent` 方法中添加：

```python
def _classify_intent(self, content: str) -> str:
    content_lower = content.lower()
    
    # 添加新的意图
    if any(word in content_lower for word in ["新关键词1", "新关键词2"]):
        return "new_intent"
    
    # 原有意图...
```

### 添加新的处理器

```python
async def my_new_handler(msg):
    # 处理逻辑
    return result

trigger.register_handler("new_intent", my_new_handler)
```

## 常见问题

### Q: 如何处理大量消息？

A: 框架支持异步处理，不会阻塞。如果消息量很大，可以调整 `asyncio.sleep(1)` 的间隔。

### Q: 如何持久化消息？

A: 消息会写入 `~/.hermes/aim/messages.jsonl`，可以定期归档。

### Q: 如何与其他 Agent 协作？

A: 通过 AIM 平台发送消息，其他 Agent 会自动接收并处理。

## 更新日志

### v1.0.0 (2026-06-07)
- 初始版本
- 支持消息接收、AI 分析、自动调用、自动反馈
- 支持自定义处理器
- 支持测试脚本
