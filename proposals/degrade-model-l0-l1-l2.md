# 三级降级模型 (L0/L1/L2)

> 小火鸡儿 🐤 | 2026-06-17 | Phase 1 — AIM Client v1.2

---

## 一、设计目标

让 AIM Client 在 Runtime（Letta/OpenClaw/Hermes）不可用或降级时，有清晰的分层应对策略，而不是"要么完全工作、要么完全挂"。

三层对应三个递进的故障级别：
- **L0**：正常态，消息即时处理
- **L1**：轻降级，Runtime 暂时忙 → 排队重试
- **L2**：重降级，Runtime 不可用 → 持久化等待恢复

---

## 二、三级定义

```
L0  NORMAL    Runtime 空闲，消息即时处理
     │
     │ health: exit 0, session 空闲
     │
     ▼
L1  DEGRADED  Runtime 忙（TUI 占 session / 处理中）
     │         消息排队等待，不丢
     │
     │ health: exit 0 但 process: exit 1 (timeout)
     │ 或 health: exit 1
     │
     ▼
L2  STALLED   Runtime 不可用（lettacron 挂了 / Node.js 崩了）
              消息持久化到 dead 队列，定期探针恢复
     │
     │ health: exit 2 (不可用)
     │
     ▼
   (auto recover to L0 when health returns)
```

| 级别 | 名称 | 触发条件 | 消息行为 | 探针频率 | 恢复 |
|------|------|---------|---------|---------|------|
| **L0** | NORMAL | health=0 + session 空闲 | 即时 dispatch → 秒回 | 5s | — |
| **L1** | DEGRADED | health=0 但 process exit=1(超时) 连续 N 次 | 消息 pending 队列排队 | 5s → 10s → 15s | session 空闲后自动恢复 L0 |
| **L2** | STALLED | health exit=2 或 health 连续 3 次 != 0 | 消息持久化到 dead 队列 | 15s → 30s → 60s | health 恢复后自动升级到 L0，消费 dead 队列 |

---

## 三、L0 — NORMAL（正常态）

### 触发

```
health exit=0 且上一次 dispatch 成功（exit 0）
```

### 行为

```
消息到达 → NATS 回调入队 → Scheduler 检查 state == NORMAL
  → dispatch → adapter process → exit 0 → 回复 NATS → 保持 NORMAL
```

### Scheduler 参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `probe_interval` | 5s | 空闲时探针频率 |
| `dispatch_mode` | immediate | 有消息立即投递 |
| `max_retries` | 3 | 单条消息重试上限 |
| `state_persistence` | false | NORMAL 态不持久化 |

### Observer 事件

```json
{"event": "state_change", "from": "L1", "to": "L0", "detail": "Runtime 恢复"}
{"event": "dispatch", "msg_id": "...", "latency_ms": 3200}
```

---

## 四、L1 — DEGRADED（轻降级）

### 触发

```
process exit=1 (超时) 连续 N 次（建议 N=3）
或 health exit=1 (degraded) 持续时间 > 30s
```

### 设计原理

L1 是 **Letta 的核心降级场景**：TUI 会话占着 Letta 单线程，subprocess `letta -p` 不能并行运行。这不是 bug，是架构约束。

关键认知（来自 v1.2-v1.7 迭代）：
- v1.2/v1.3 犯过错误：session 忙时 adapter 直接 exit 2 → 206 条消息全部降级死队列
- 正确做法：exit 1 → RETRY → pending 队列 → session 空闲后自动恢复

### 行为

```
消息到达 → NATS 回调入队 → Scheduler 检查 state == DEGRADED
  → 不立即 dispatch（避免浪费 30s 等待）
  → 消息留在 pending 队列
  → 探针检测到 health=0 + 空闲 → 升级到 L0 → 消费 pending 队列
```

### Scheduler 参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `probe_interval` | 5s → 10s → 15s (递增) | 没必要每秒探针 |
| `dispatch_mode` | deferred | 先探针确认空闲再 dispatch |
| `max_retries` | 3 | RETRY 上限，超过 → dead 队列 |
| `state_persistence` | true | L1 态持久化到 JetStream KV |
| `degrade_ttl` | 30min | L1 态最长持续时间，超过 → 通知大哥 |

### Letta 特化处理

```
L1 触发条件（Letta 特定）:
  adapter.sh process exit=1 (30s timeout)
  说明: TUI 会话占据 session，subprocess 阻塞 30s 被 timeout

L1 恢复策略（Letta 特定）:
  health 探针: letta agents list (秒回，不受 TUI 阻塞)
  health=0 → 说明 letta CLI 本身正常
  → 尝试 dispatch 一条轻量消息（ping，timeout 10s）
    - ping 秒回 → 真正空闲 → 升级 L0 → 消费 pending
    - ping 超时 → 仍忙 → 保持 L1 → 等下一轮探针
```

### Observer 事件

```json
{"event": "state_change", "from": "L0", "to": "L1", "detail": "连续 3 次 dispatch 超时"}
{"event": "degrade_alert", "level": "L1", "msg": "Runtime 暂时忙，消息排队中 (pending=12)", "ts": "..."}
{"event": "state_change", "from": "L1", "to": "L0", "detail": "ping 恢复，pending=12 开始消费"}
```

---

## 五、L2 — STALLED（重降级）

### 触发

```
health exit=2 或 health 连续 3 次 != 0
或 adapter.sh 自身不可用（exec error / segfault）
或 NATS 连接断开
```

### 设计原理

L2 是 true failure：Runtime 本身不可用（而非暂时忙）。此时排队没意义——消息必须持久化到外部存储，等 Runtime 恢复后再消费。

### 行为

```
消息到达 → NATS 回调入队 → Scheduler 检查 state == STALLED
  → 消息不 dispatch
  → 直接写入 dead 队列（JetStream KV 或文件）
  → 探针持续轮询 health
  → health 恢复 → 升级到 L0 → 消费 dead 队列（FIFO，保留 TTL）
```

### 持久化策略

```
dead 队列:
  - 存储引擎: JetStream KV (优先) / degrade 文件目录 (fallback)
  - TTL: 24h（超过 TTL 的消息丢弃，记录日志）
  - 最大容量: 10000 条
  - 格式: 同 pending 消息，加 ts 和 degrade_level 字段
```

### Scheduler 参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `probe_interval` | 15s → 30s → 60s → 120s (递增到上限) | STALLED 期间没必要频繁探针 |
| `dispatch_mode` | none | 不 dispatch，只入 dead 队列 |
| `state_persistence` | true | 持久化到 JetStream KV |
| `dead_queue_ttl` | 24h | dead 队列消息存活时间 |
| `alert_threshold` | 5min | 持续 L2 > 5min → 通知大哥 |

### 恢复流程

```
STALLED → L0 恢复:
  1. health 返回 exit=0 (连续 2 次确认)
  2. 升级到 L0
  3. 先消费 pending 队列（更新鲜）
  4. 再消费 dead 队列（按 FIFO，跳过超过 TTL 的）
  5. 消费过程中如果 health 又失败 → 回退 L2
```

### Observer 事件

```json
{"event": "state_change", "from": "L1", "to": "L2", "detail": "health 连续 3 次 exit=2"}
{"event": "degrade_alert", "level": "L2", "msg": "Runtime 不可用 >5min，需人工介入", "ts": "..."}
{"event": "state_change", "from": "L2", "to": "L0", "detail": "health 恢复，dead_queue=47 条待消费"}
```

---

## 六、Scheduler 状态判定增强

在现有三态（OFFLINE/AVAILABLE/BUSY）之上叠加降级级别：

```
AgentState 枚举扩展:
  IDLE      → L0
  BUSY      → L0 (正常处理中)
  DEGRADED  → L1
  OFFLINE   → L2
```

降级级别不影响 AgentState 本身——它们是正交维度：
- AgentState 描述 Runtime 当前行为（空闲/忙/离线）
- DegradeLevel 描述系统当前健康程度（正常/降级/停摆）

```
当前有效组合:
  IDLE + L0     = 正常运行
  BUSY + L0     = 正在处理，正常
  IDLE + L1     = Runtime 空闲但最近 dispatch 连续失败，等待恢复确认
  BUSY + L1     = (不可能——BUSY 时 dispatch 成功才标记 BUSY)
  OFFLINE + L2  = Runtime 不可用
  IDLE + L2     = (不可能——health 恢复后自动升级 L0)
```

---

## 七、Letta 侧接口定义

adapter.sh 当前已经实现的退出码已经覆盖降级信号，不需要改 adapter。

需要新增的是 **降级状态查询接口**：

### 7.1 degrade-status 模式（新增）

```bash
# 查询当前降级状态
adapter.sh degrade-status

# 输出 JSON
{
  "level": "L0",          # L0 | L1 | L2
  "last_success_ts": 1781631054,
  "consecutive_failures": 0,
  "pending_count": 0,
  "detail": "normal"
}
```

这个接口由 Scheduler 维护，adapter 只负责返回当前状态。

### 7.2 现有 adapter 接口不变

| 接口 | L0 行为 | L1 行为 | L2 行为 |
|------|--------|--------|--------|
| health | exit 0 | exit 0（lettacron 还活着）或 exit 1 | exit 2 |
| process | exit 0（秒回） | exit 1（30s 超时） | N/A（不 dispatch） |
| info | exit 0 | exit 0 | exit 2 |
| cancel | exit 2 | exit 2 | exit 2 |

---

## 八、实现优先级

### P0（本阶段可做，无外部依赖）
- [ ] 降级模型设计文档 ✅（本文档）
- [ ] `types.py` 新增 `DegradeLevel` 枚举
- [ ] `StateReport` 新增 `degrade_level` 字段
- [ ] L0/L1/L2 触发条件判定函数（纯逻辑，不依赖呱呱）
- [ ] Observer 降级事件格式定义

### P1（依赖呱呱 Scheduler 接口稳定后）
- [ ] Scheduler 中集成 DegradeLevel 状态机
- [ ] acquire_degrade_level() 函数（降级级别变更 + Observer 通知）
- [ ] pending 队列持久化到 JetStream KV（L1/L2 下）
- [ ] dead 队列 TTL 消费逻辑
- [ ] L2 持续 >5min 通知大哥
- [ ] degrade-status 模式落地

### P2（Phase 2 多 Runtime 后）
- [ ] L1/L2 在 OpenClaw/Hermes 框架下的适配
- [ ] 降级恢复策略的框架特定优化
- [ ] 降级事件 dashboard

---

## 九、与方案文档的关系

```
aim-client-unified-v1.md (吉量 16:39)
  └→ AIM架构评审与定位细化.md (GPT 细化)
      └→ aim-client-division.md (分工表)
          └→ degrade-model-l0-l1-l2.md (本文档 🐤)  ← 三级降级模型 Phase 1
          └→ scheduler-state-rules.md (小火鸡儿 🐤) ← Phase 0 状态判定
```

---

## 十、配置 (config.json)

```json
{
  "degrade": {
    "l1_trigger_consecutive_timeouts": 3,
    "l1_probe_intervals": [5, 10, 15],
    "l1_max_duration_seconds": 1800,
    "l2_trigger_consecutive_health_fails": 3,
    "l2_probe_intervals": [15, 30, 60, 120],
    "l2_alert_threshold_seconds": 300,
    "dead_queue_ttl_hours": 24,
    "dead_queue_max_size": 10000,
    "ping_verify_timeout": 10
  }
}
```

---

*小火鸡儿 🐤 | 2026-06-17 | 等待呱呱 Scheduler 接口稳定后 P1 代码落地*
