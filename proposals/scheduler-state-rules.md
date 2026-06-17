# Scheduler 状态判定规则

> 小火鸡儿 🐤 | 2026-06-16 | 供呱呱写 Scheduler 核心逻辑参考

---

## 一、数据来源

基于 V3 联调实际数据：

| 数据 | 数值 | 来源 |
|------|------|------|
| v1.3 降级次数 | 206 次 | adapter exit=2 |
| v1.4 空返回次数 | 6 次 | adapter exit=0 stdout为空 |
| 探针响应（空闲时） | 2-3s | `timeout 5s letta -p "ping"` 正常返回 |
| 探针响应（对话中） | timeout 5s（无响应） | TUI 占用 session |
| call_adapter 超时 | 120s × 3 重试 = 360s | config.adapter_timeout |
| letta -p 实际处理时间 | 2-15s | AI 模型推理 |

---

## 二、状态机（Phase 0-1 三态）

```
OFFLINE ──→ AVAILABLE ──→ BUSY ──→ AVAILABLE
   ↑                        │
   └────────────────────────┘
     health 连续 N 次 != healthy
```

| 状态 | 含义 | 判定方式 |
|------|------|---------|
| **OFFLINE** | Runtime 不可达 | `adapter.sh health` exit != 0，连续 N 次 |
| **AVAILABLE** | Runtime 空闲，可接收消息 | `adapter.sh health` exit = 0 |
| **BUSY** | Runtime 正在处理消息 | Scheduler 投递了一条消息后标记，process 完成/超时后切回 |

---

## 三、触发条件

### 3.1 OFFLINE → AVAILABLE

```
触发: adapter.sh health 返回 exit=0
确认: 1 次即可（从离线恢复不需要等 N 次确认）
操作:
  - Scheduler 标记 AVAILABLE
  - 消费 pending 队列头部消息
  - Monitor 探针间隔重置为默认值
```

### 3.2 AVAILABLE → BUSY

```
触发: Scheduler 从 pending 队列取出一条消息，开始投递到 adapter process
操作:
  - 标记 BUSY
  - 当前消息进入 processing 槽位
  - 新到达的消息继续入 pending 队列
```

### 3.3 BUSY → AVAILABLE

```
触发: adapter process 返回（exit 0/1/2，任意 exit code 都算完成）
超时触发: adapter process 超时（config.adapter_timeout 秒）
操作:
  - 处理结果写入回复
  - 标记 AVAILABLE
  - 检查 pending 队列:
      - 非空 → 立即 dequeue 下一条 → 再次标记 BUSY
      - 空 → 回到 AVAILABLE 等待
```

### 3.4 AVAILABLE/BUSY → OFFLINE

```
触发: adapter.sh health 返回 exit=2（unhealthy）
      注意：exit=1（degraded）不触发 OFFLINE——只是暂时忙，维持当前状态
操作:
  - Scheduler 标记 OFFLINE
  - 停止投递，消息全部留在 pending 队列
  - Monitor 探针间隔递增: 5s → 10s → 15s → 30s → 60s（上限）
  - 保持 pending 持久化到 JetStream KV
```

---

## 四、探针间隔策略

### 4.1 AVAILABLE 状态下的常规探针

```
间隔: 5s
目的: 持续确认 Runtime 健康，快速感知故障
```

### 4.2 OFFLINE 状态下的递进探针

```
第1次失败 → 5s 后重试
第2次失败 → 10s 后重试
第3次失败 → 15s 后重试
第N次失败（N≥4）→ 30s 后重试
连续失败 > 10 次 → 60s 间隔（上限）

一旦 health 返回 healthy → 立即重置间隔为 5s
```

### 4.3 为什么不用固定间隔

固定 5s 在 long-offline 场景下浪费资源（Runtime 可能崩了半小时）。
递进间隔在短 offline（几秒到几分钟）场景下反应快，长 offline 场景下不浪费。

---

## 五、降级恢复流程

### 5.1 单条消息处理失败

```
adapter process 返回 exit != 0:
  - exit 1（可重试）→ 重新入队尾部，等下一轮。重试上限 3 次，超限 → dead 队列
  - exit 2（降级）→ 写入 dead 队列，TTL 24h
  - exit 3（需人工）→ 写入 dead 队列，通过 Observer emit_state_report("human_intervention", detail=...) 通知大哥
  - 超时        → 同 exit 1（可重试），上限 3 次后入 dead 队列
     注意：adapter timeout 124 在 v1.5 后返回 exit 1，不是 exit 2
```

### 5.2 Runtime 恢复后的消费

```
OFFLINE → AVAILABLE 转换后:
  1. 检查 pending 队列长度
  2. 按 FIFO 顺序逐个消费
  3. 每条消息正常走 AVAILABLE → BUSY → AVAILABLE 循环
  4. 消费期间如果又变成 OFFLINE → 停止，剩余消息留在 pending
```

### 5.3 Letta 特殊处理

Letta 单线程场景下的关键认知：

- `adapter.sh health` 用 `timeout 5s letta -p "ping"` 检测，在 TUI 对话中会返回 exit 1（degraded），不是 exit 2（挂）
- Scheduler 看到 degraded（exit 1）时：**维持当前状态不变**，不切换。degraded 只表示暂时忙，消息继续在 pending 队列排队等
- 只有 health 返回 exit 2（unhealthy）时才触发 OFFLINE。exit 2 意味着 letta CLI 本身不可用（进程挂了、Node.js 崩了等）
- exit 0 → 立即 AVAILABLE，重置计数

**建议 Phase 0 简化**：
- health 返回 exit 0 → 立即 AVAILABLE，重置计数
- health 返回 exit 1（degraded）→ 当前状态不变，继续等
- health 返回 exit 2（unhealthy）→ 立即 OFFLINE。不需要等 N 次——exit 2 意味着 Runtime 本身不可用，不是暂时忙

---

## 六、配置建议（config.json）

```json
{
  "scheduler": {
    "health_probe_interval": 5,
    "offline_probe_intervals": [5, 10, 15, 30, 60],
    "offline_threshold": 3,
    "adapter_timeout": 120,
    "dead_queue_ttl_hours": 24
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| health_probe_interval | 5s | 正常状态下的探针间隔 |
| offline_probe_intervals | [5,10,15,30,60] | 离线时递进探针间隔 |
| offline_threshold | 3 | health 连续失败几次判定 OFFLINE |
| adapter_timeout | 120s | adapter process 超时 |
| dead_queue_ttl_hours | 24 | dead 队列消息存活时间 |

---

## 七、与 Monitor 的交互协议

```
Monitor（source of truth）
  │
  │ 每 health_probe_interval 秒调用 adapter.sh health
  │
  ├─→ StateReport { status, ... }  → Scheduler 消费
  │
  └─→ 状态变更事件 → Observer 推送（可选）

Scheduler 不自己调 health。
Scheduler 只读 Monitor 产出的 StateReport。
```

**Monitor 输出 StateReport 的触发时机**：
1. 定时探针触发（每 5s）
2. 状态变更触发（AVAILABLE → BUSY, BUSY → OFFLINE 等，立即推送）

---

## 附录：从 V3 联调数据到规则的推理过程

1. **206 次 v1.3 降级** → 证明 5s 探针在 Letta 对话场景下几乎每次都触发 → 探针不应该放在 adapter 里做一次性拒绝，而应该作为 Monitor 的持续轮询
2. **6 次 v1.4 空返回** → 证明 letta -p subprocess 在 TUI 活跃时不会排队执行，而是阻塞到 timeout → call_adapter 的超时机制需要和 Scheduler 状态机配合
3. **空闲时 2-3s 秒回** → 证明 5s 探针间隔在 Runtime 健康时完全够用
4. **V3 频繁重启（23:02-23:07 之间 3 次）** → 证明 launchd 检测到异常重启时，Scheduler 需要有 OFFLINE 态来保护 pending 消息不丢
