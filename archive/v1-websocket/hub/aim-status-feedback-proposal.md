# AIM Status Feedback 机制方案

> 设计：呱呱 + 吉量
> 日期：2026-06-07
> 状态：待大哥审批

---

## 背景

当前 AIM 通信只有最终结果推送，中间过程对大哥不可见。长任务（推理、网页抓取等）执行期间大哥看到的是"假死"状态，不知道 Agent 在干什么、进度如何。

## 目标

1. 大哥能实时看到 Agent 的执行进度（当前步骤、状态）
2. 不暴露推理链细节（默认隐藏，verbose 按需开启）
3. 不影响现有通信架构和 handler 选举机制

---

## 方案详情

### 1. Observer 通道

**新增 channel 类型：`observer`**

| 特性 | 说明 |
|------|------|
| 方向 | 只收不发（单向监听） |
| Handler 选举 | 不参与（observer 永远不是 handler） |
| 绑定关系 | 连接时指定 `watch_target`（ZS0001/ZS0002） |
| 多对一 | 支持多个 observer watch 同一个 target |
| 连接池 | 独立计数，不占 main channel 配额 |

**协议字段：**
```json
{
  "channel": "observer",
  "watch_target": "ZS0001"
}
```

**Server 端逻辑：**
- 收到 observer 注册 → 记录绑定关系 `observer_id → target_id`
- target 的 status_feedback → 自动转发给所有绑定的 observer

---

### 2. Status Feedback 协议

**复用现有 WS 连接，不新建连接池。**

通过 `msg_type` 字段区分消息类型：

```json
{
  "msg_type": "status_feedback",
  "from": "ZS0001",
  "session_id": "xxx",
  "step": "web_fetch",
  "status": "running",      // running | completed | error | timeout
  "progress": "Fetching URL...",
  "timestamp": 1717737600
}
```

**字段说明：**
| 字段 | 必填 | 说明 |
|------|------|------|
| msg_type | ✅ | 固定 `status_feedback`，与普通消息区分 |
| from | ✅ | 发送方 agent_id |
| session_id | ✅ | 会话 ID，用于关联任务 |
| step | ✅ | 当前步骤名（如 reasoning、web_fetch、code_exec） |
| status | ✅ | running / completed / error / timeout |
| progress | ❌ | 可选，人类可读的进度描述 |
| timestamp | ✅ | Unix 时间戳 |

---

### 3. 推理链隐藏策略

| 模式 | 行为 |
|------|------|
| 默认模式 | 只推 step + status，不推推理链内容 |
| Verbose 模式 | 推 step + status + 推理摘要（需显式开启） |

**开启方式：**
- 大哥命令：`/verbose on` / `/verbose off`
- Observer 连接时：`{"verbose": true}`

**原则：大哥看的是进度和结论，不是 AI 在想什么。**

---

### 4. 节流策略

**按步骤类型区分：**

| 步骤类型 | 示例 | 推送策略 |
|----------|------|----------|
| 快步骤（<3s） | memory_search, db_query | 不推送 |
| 长步骤（≥3s） | reasoning, web_fetch, code_exec | 必须推送 |
| 关键步骤 | 任务开始、任务结束 | 始终推送 |

**判断标准：预计耗时 >3s 的步骤才推 status_feedback。**

**额外规则：**
- 任务开始时推一次 `step: "task_start"`，任务结束推一次 `step: "task_end"`
- 同一步骤内不重复推送（除非状态变更：running → completed/error）

---

### 5. 超时清理

**Server 端定时器：**

- status_feedback 发出后，同一 `session_id` 超时 **60s** 无更新 → 自动发 timeout 通知
- timeout 通知带最后已知步骤名

**timeout 通知格式：**
```json
{
  "msg_type": "status_feedback",
  "from": "ZS0001",
  "session_id": "xxx",
  "step": "web_fetch",
  "status": "timeout",
  "progress": "⚠️ ZS0001 在 web_fetch 步骤已超时 60s",
  "timestamp": 1717737660
}
```

---

### 6. 绑定关系

**设计：单向 + 多对一**

```
Observer A ──watch──→ Target (ZS0001)
Observer B ──watch──→ Target (ZS0001)  // 多对一
Observer C ──watch──→ Target (ZS0002)  // 不同 target
```

**Server 端数据结构：**
```python
# observer_bindings: dict[str, list[str]]
# key = target_id, value = list of observer_ids
{
    "ZS0001": ["observer_a", "observer_b"],
    "ZS0002": ["observer_c"]
}
```

**路由逻辑：**
1. Agent 发 status_feedback → Server 查找该 agent 的 observer 列表
2. 转发给所有绑定的 observer
3. 无 observer 时静默丢弃（不影响正常通信）

---

## 对现有架构的影响

| 组件 | 影响 |
|------|------|
| ConnectionPool | 新增 observer channel 类型，独立计数 |
| Handler 选举 | 无影响（observer 不参与） |
| 消息路由 | 新增 status_feedback 类型转发逻辑 |
| Agent 端 | 新增 status_feedback 发送逻辑 |
| Server 端 | 新增 observer 绑定管理 + 超时检测 |

---

## 实施计划

| 步骤 | 负责人 | 说明 |
|------|--------|------|
| 1. Server 端 observer 绑定管理 | 吉量 | register 时记录绑定关系 |
| 2. Server 端 status_feedback 路由 | 吉量 | 转发给 observer + 超时检测 |
| 3. Agent 端 status_feedback 发送 | 呱呱 | 按步骤类型判断是否推送 |
| 4. Observer 端连接 + 展示 | 呱呱 | aim watch 命令 |
| 5. 联调测试 | 呱呱+吉量 | 全链路验证 |

---

## 联调测试项

1. ✅ Observer 正常连接并绑定 target
2. ✅ Target 发 status_feedback → Observer 收到
3. ✅ 快步骤不推送，长步骤推送
4. ✅ 超时 60s 自动发 timeout 通知
5. ✅ Observer 断开不影响 target 正常通信
6. ✅ 多个 observer 同时 watch 同一个 target
7. ✅ Verbose 模式开关生效

---

## 风险和备选

| 风险 | 缓解措施 |
|------|----------|
| status_feedback 频率过高 | 节流策略 + 预计耗时阈值 |
| Observer 连接泄漏 | Server 端心跳检测 + 自动清理 |
| 超时误报 | 60s 阈值可配置，支持按步骤调整 |

---

**请大哥审批。**
