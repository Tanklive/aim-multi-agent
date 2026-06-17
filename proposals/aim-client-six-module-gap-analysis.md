# AIM Client 六模块架构 — 现状差距分析

> 大哥定调：2026-06-16 13:49
> 分析：呱呱 🐸 (ZS0001)
> 目标：Agent ≠ Runtime → Agent = AIM Client + Runtime

---

## 一、大哥提出的六模块 AIM Client

```
AIM Client
├── 1. Transport    — NATS / A2A / HTTP / WebSocket
├── 2. Queue        — Inbox 缓存，支持 deferred delivery
├── 3. Scheduler    — busy / idle / offline 状态机
├── 4. State Monitor — Runtime 健康检查（PID/CPU/Session/对话状态）
├── 5. Adapter      — 标准适配器接口，对接各种 Runtime
└── 6. Identity     — Agent Card（能力声明、投递模式、并发限制）
```

---

## 二、当前 V3 架构映射

```
nats-agent-v3.py  ← 当前的"准 AIM Client"（183行单文件）
├── [混在一起] Transport=NATS硬编码
├── [不存在]   Queue=即时处理，无延迟投递
├── [不存在]   Scheduler=无 busy/idle 状态机
├── ⚠️ Observer=吉量写的 observer-daemon（独立进程，非 Client 内置）
├── ⚠️ call_adapter.py → adapter.sh（每框架一个 sh，无统一接口）
└── ⚠️ config.json（仅 agent_id + adapter_cmd，无 Agent Card 协议）
```

### 模块差距明细

| 模块 | 当前状态 | 目标状态 | 差距 | 优先级 |
|------|----------|----------|------|--------|
| **Transport** | SDK `AIMNATSClient` 封装 | 可插拔 Transport 层（NATS/A2A/HTTP/WS） | 硬编码 NATS，无抽象接口 | M2 |
| **Queue** | ❌ 不存在，消息即时处理 | Inbox Queue，支持 busy 时缓存 | 完全缺失 | **P0** |
| **Scheduler** | ❌ 不存在 | busy/idle/offline 三态 + 最大并发控制 | 完全缺失 → 小火鸡儿卡死的根因 | **P0** |
| **State Monitor** | Observer daemon（独立进程） | Client 内置 Runtime 健康探针 | 独立进程 → 内置，需加 Letta/CrewAI 探针 | **P0** |
| **Adapter** | `call_adapter.py` → `adapter.sh` | 标准 Adapter 接口，统一协议 | 每框架写 sh，无标准返回格式 | M1 |
| **Identity** | `config.json` 5 个字段 | Agent Card JSON Schema（OAS 兼容） | 需定义 Card 协议 | M1 |

---

## 三、小火鸡儿问题的根因：Scheduler + State Monitor 缺失

### 当前流程（有问题的）

```
NATS 消息 → V3 收到 → 直接 call_adapter() → adapter.sh → letta -p
                                                              ↓
                                                         Letta session 忙
                                                              ↓
                                                         探针 5s 超时
                                                              ↓
                                                         降级文件队列
```

**问题**：没有 Scheduler 判断"Letta 正在对话中，不要投递新消息"。
没有 State Monitor 检测"Letta Runtime 卡死/超时"。

### 目标流程

```
NATS 消息 → Transport → Queue（Inbox）
                           ↓
                    Scheduler（检查 Runtime 状态）
                      ├── busy  → 消息留 Inbox，不投递
                      ├── idle  → 从 Inbox 取一条 → Adapter → Runtime
                      └── offline → 降级或等待
                           ↑
                    State Monitor（持续探针 Runtime 健康）
```

---

## 四、现有资产可复用

| 现有资产 | 对应模块 | 复用方式 |
|----------|----------|----------|
| `aim_nats_sdk.py` | Transport | 现有 NATS 封装已成熟，作为 Transport 的第一个实现 |
| `aim-observer.py` (吉量) | State Monitor | 已有 Observer 协议和 Agent 状态追踪，可合并进 Client |
| `call_adapter.py` | Adapter | 已有退出码协议（0/1/2/3），标准化后作为 Adapter 接口 |
| `config.json` | Identity | 基础字段存在，升级为 Agent Card Schema |
| `aim_fast_consumer.py` | Queue (雏形) | 文件队列轮询机制，可作为 Inbox 的过渡方案 |
| `aim_send.py` | Transport (send) | 发送端已有，集成进 Client 统一入口 |

---

## 五、推进路线图（建议）

```
Phase 1 — P0 止血（小火鸡儿问题）
├── Scheduler 状态机（busy/idle/offline）
├── State Monitor（Runtime 探针：PID + session 状态）
├── Inbox Queue（内存队列，busy 时缓存）
└── 效果：Letta 忙时不丢消息，空闲时自动消费

Phase 2 — M1 标准化
├── 标准 Adapter 接口协议（统一 invoke/status/cancel）
├── Agent Card Schema v1（与 OAS ext.oas.capability 对齐）
├── 六模块骨架分离（单文件 → 包结构）
└── 效果：接入新 Runtime 只需实现 Adapter 接口

Phase 3 — M2 扩展
├── Transport 抽象层（除 NATS 外加 A2A/HTTP 支持）
├── 多 Runtime Provider 支持（AutoGen/CrewAI/LangGraph 等）
├── Agent Card 动态协商
└── 效果：OAS 公民模型完整实现
```

---

## 六、关键设计决策（待大哥确认）

1. **AIM Client 是独立进程还是嵌入 V3？**
   - 建议：独立进程 `aim-client`，V3 降级为 Transport 层的一种实现
   - 优势：Agent Runtime 挂了 Client 仍在线

2. **Scheduler 的并发模型**
   - 小火鸡儿：`max_concurrency=1`（Letta 单会话）
   - 吉量：`max_concurrency=?`（Hermes 可能并行）
   - 我：`max_concurrency=?`（OpenClaw 可控）

3. **State Monitor 的探针间隔**
   - Letta：需要 session 级别的探针（当前用 5s probe）
   - Hermes：可能只需要进程级探针
   - 是否需要统一的健康探针协议（类似 health check endpoint）？

4. **Inbox Queue 持久化**
   - 内存队列：重启丢失，简单
   - 文件队列：已有降级队列机制，可复用
   - NATS JetStream：天然持久化，但重启不消费有积压风险
