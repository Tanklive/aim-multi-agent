# AI Agent 幻觉问题分析：根因、业界方案与解决思路

> 吉量 ZS0002 | 2026-06-20 | 参考呱呱 U-005 分析

---

## 一、问题现象

三层连锁故障：

| 层 | 现象 |
|----|------|
| 队列 | StallWatchdog 把已处理的消息重新投递 |
| dispatch | 收到重复消息，adapter 照样处理 |
| adapter | 基于过期上下文编造回复（"收到 👍"、编造对话） |

表象是"AI 在编造"，根因是消息系统没有幂等性。

---

## 二、业界方案

所有生产级消息系统都在传输层做幂等去重，不是应用层：

| 系统 | 机制 | 做法 |
|------|------|------|
| NATS JetStream | `Nats-Msg-Id` header | 自动在 `duplicate_window` 内去重 |
| Kafka | `enable.idempotence=true` | 生产者幂等 + 事务 exactly-once |
| AWS SQS FIFO | `MessageDeduplicationId` | 5 分钟去重窗口 |
| AWS Lambda | Event Source Mapping | 基于 message ID 的 at-least-once 去重 |
| Slack API | `message_ts` | 消息幂等键，重复请求不创建新消息 |
| OpenAI API | `X-Idempotency-Key` header | 幂等 chat completion（beta） |
| Discord API | `X-Audit-Log-Reason` + rate limit | 基于消息 ID 去重 |

**核心原则：消息去重是基础设施的事，不是 AI 的事。**

AWS Lambda 文档直言："Your function must be idempotent because the same event can be delivered more than once." 解决方案不是"让 Lambda 别出错"，而是**在入口去重**。

---

## 三、我们的根因

```
积压消息 → StallWatchdog 超时 → 重复投递 → adapter 照收 → 
基于旧上下文 + 重复消息 → 编造回复 → 形成共识
```

| 根因 | 说明 |
|------|------|
| 消息无幂等 | 同一个 msg_id 可以被 dispatch 多次 |
| adapter 无上下文隔离 | 历史消息混入当前会话 |
| dispatch 无去重 | 没有 PROCESSED_IDS 机制 |

**问题本质不是 AI 会编造，是消息系统允许同一条消息多次进入 adapter。**

---

## 四、解决思路（三层递进）

### 第一层：幂等去重（立即）~7 行

```python
PROCESSED_IDS: set = set()

def dispatch(msg):
    if msg.id in PROCESSED_IDS:
        logger.info(f"[DEDUP] {msg.id[:8]} 已处理")
        return
    PROCESSED_IDS.add(msg.id)
    # 定期清理
    if len(PROCESSED_IDS) > 1000:
        PROCESSED_IDS.clear()
    # ... 原有 dispatch
```

**效果：同一条消息绝不会被同一个 adapter 处理两次。**

### 第二层：输出护栏（跟随）

| 规则 | 说明 |
|------|------|
| 长度 < 3 字 | 不发送 |
| 纯 emoji | 不发送 |
| 与前一条回复完全相同 | 不发送（重复检测） |
| 系统发送者 | 不调 adapter（已实现 `_skip_adapter_for_operational`） |

### 第三层：熔断+死信（长期）

| 条件 | 动作 |
|------|------|
| 连续 N 次低质量回复 | 熔断，暂停 dispatch，告警 |
| adapter exit=3 FATAL | 入死信，人工介入 |
| 熔断后超时 | 自动恢复探针 |

---

## 五、效果对比

| 场景 | 旧方案 | 新方案（幂等去重） |
|------|--------|-------------------|
| 积压真消息（agent 上线） | ❌ 被年龄跳过丢弃 | ✅ 正常处理 |
| StallWatchdog 重复投递 | ❌ 重复进 adapter | ✅ 跳过（已处理） |
| adapter 自激循环 | ❌ 同消息反复编造 | ✅ 同消息不重复进 |

---

## 六、依据

1. **NATS JetStream 官方文档**：`Nats-Msg-Id` 去重是 stream 的内置功能 —— 我们在应用层缺失了它
2. **AWS Lambda 最佳实践**："Make your function idempotent" —— 同样是消息驱动系统
3. **Kafka Exactly-Once Semantics**：通过 `enable.idempotence` 实现生产者幂等
4. **OpenAI API 幂等键**：2024 年新增 `X-Idempotency-Key` header，chat completion 支持幂等

**结论：不是 AI 的问题，是消息系统缺少幂等性。修复在 dispatch 入口，~7 行。**
