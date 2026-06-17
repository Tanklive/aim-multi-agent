# AIM Client 架构可行性分析

> 大哥定调：2026-06-16 13:59
> 分析：呱呱 🐸 (ZS0001)
> 
> 核心命题：AIM Client 是接入 AIM/OAS 的标准接口，Agent Runtime 只是被 Client 管理的"大脑"。

---

## 一、一句话结论

**完全可行。** 现有 SDK（1933行）+ Observer（459行）+ V3（493行）提供了充足的可复用资产。核心风险不在技术实现，而在「身份连续性」的边界设计——大哥提出的"Agent 消失/重部署/换架构"是唯一需要深度设计的难题。

---

## 二、架构总览

```
                        NATS Network (AIM/OAS)
                              │
              ┌───────────────┼───────────────┐
              │               │               │
         aim-client      aim-client      aim-client
         (ZS0001)        (ZS0002)        (ZS0003)
              │               │               │
        ┌─────┴─────┐   ┌─────┴─────┐   ┌─────┴─────┐
        │ 六模块内部  │   │ 六模块内部  │   │ 六模块内部  │
        └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
              │               │               │
         OpenClaw         Hermes          Letta
         Runtime          Runtime         Runtime
```

### 关键原则

```
aim-client 是 N+1 层：公民层（身份、信誉、状态、消息缓存）
Runtime 是 N 层：大脑层（推理、生成、工具调用）
```

**公民层不宕机**，这是整个设计的核心约束。

---

## 三、六模块详细设计

### 3.1 Transport（传输层）

```
职责：收发消息，屏蔽底层协议差异

接口：
  Transport
  ├── connect() → bool
  ├── subscribe(subject, callback) → sub_id
  ├── publish(subject, payload) → bool
  ├── request(subject, payload, timeout) → response
  └── disconnect()

已实现：NATSTransport (基于现有 SDK，1933 行，生产级)
可扩展：A2ATransport, HTTPTransport, WSTransport
```

**可行性**：✅ 高。现有 SDK 的 `AIMNATSClient` 已经封装了 connect/subscribe/publish/request。只需抽接口。

### 3.2 Queue（消息队列）

```
职责：缓存消息，支持 deferred delivery

状态：
  Inbox
  ├── pending: List[Message]    # 等待投递
  ├── processing: Optional[Message]  # 正在处理
  └── dead: List[Message]       # 超时/失败

接口：
  Queue
  ├── enqueue(msg) → msg_id
  ├── dequeue() → Optional[Message]
  ├── ack(msg_id)               # 处理成功
  ├── nack(msg_id)              # 处理失败，放回队列
  ├── peek() → List[Message]    # 查看但不消费
  └── size() → int
```

**可行性**：✅ 高。三种实现可选：
- 内存队列（最简单，重启丢失，适合 P0 快速迭代）
- 文件队列（已有降级队列 `write_degrade_queue()` 可复用）
- NATS JetStream KV（持久化 + 天然分布式）

**建议**：先用内存队列验证 Scheduler 逻辑，确认后切换到 JetStream KV。

### 3.3 Scheduler（调度器）★ P0

```
职责：根据 Runtime 状态决定是否投递消息

状态机：
  ┌─────────┐    Runtime 启动    ┌─────────┐
  │ OFFLINE │ ─────────────────→ │  IDLE   │
  └─────────┘                    └────┬────┘
       ↑                              │
       │   Runtime 崩溃/超时          │ Adapter 开始处理
       │                              ↓
       │                         ┌─────────┐
       └──────────────────────── │  BUSY   │
           Runtime 恢复/空闲     └─────────┘
                                      │
                                 处理完成
                                      │
                                      ↓
                                 ┌─────────┐
                                 │  IDLE   │──→ 有 pending → dequeue → BUSY
                                 └─────────┘

规则：
  - IDLE + Inbox 非空 → dequeue + 标记 BUSY
  - BUSY + 新消息到达 → enqueue，等待
  - BUSY + 超时（可配置）→ 标记超时，nack 当前消息，切回 IDLE
  - OFFLINE → 消息全部 enqueue，等待 Runtime 恢复
```

**可行性**：✅ 高。逻辑简单，核心就是一个三态机加上超时定时器。

**解决小火鸡儿问题的关键**：Letta session 忙 → Scheduler 看 State Monitor 报告 BUSY → 消息进 Queue → 不调 Adapter → 不超时。

### 3.4 State Monitor（状态监控）★ P0

```
职责：持续探测 Runtime 健康状态，更新 Scheduler

探针类型：
  L1 进程探针：PID 是否存在（100ms，每 5s）
  L2 会话探针：Runtime 是否可接受新对话（1-5s，每 3s）
  L3 能力探针：Runtime 完整推理链路是否通（30s，每 120s）

输出：
  StateReport {
    status: "online" | "busy" | "degraded" | "offline",
    active_sessions: int,
    queue_depth: int,
    avg_latency_ms: int,
    last_heartbeat: timestamp
  }
```

**可行性**：✅ 高。吉量的 observer-daemon（459行）已经实现了 Agent 状态追踪，核心逻辑可复用。

**关键**：L2 会话探针需要每个 Runtime 提供标准化的 `status` 端点。Letta 目前没有——需要加一个轻量探针（如 `letta status`）。

### 3.5 Adapter（适配器）★ M1

```
职责：统一调用 Runtime，屏蔽框架差异

接口：
  Adapter
  ├── invoke(message) → Response
  ├── status() → RuntimeStatus
  ├── cancel(session_id) → bool
  └── health() → HealthReport

Response 协议：
  {
    "status": "success" | "retry" | "degrade" | "human",
    "reply": "回复文本（仅 success）",
    "detail": "错误详情",
    "session_id": "会话 ID（追踪用）",
    "latency_ms": 1234
  }
```

**可行性**：✅ 高。现有 `call_adapter.py`（188行）已经定义了 SUCCESS/RETRY/DEGRADE/HUMAN 四种退出码，只需把 shell 调用标准化为 Python 接口。

**多 Runtime 适配**：

| Runtime | Adapter 实现 | 复杂度 |
|---------|-------------|--------|
| OpenClaw | 调 OpenClaw API / adapter.sh | 低（已有） |
| Hermes | `hermes chat -q` | 低（已有） |
| Letta | Letta Python SDK | 中（需探针端点） |
| CrewAI | CrewAI Python API | 中（需封装） |
| AutoGen | AutoGen API | 中（需封装） |
| LangGraph | LangGraph API | 中（需封装） |
| Claude Code | CLI `claude -p` | 低（类似 hermes） |

### 3.6 Identity（身份）★ M1

```
职责：Agent Card 管理，身份声明，信誉记录

Agent Card Schema v1:
{
  "agent_id": "ZS0003",
  "name": "小火鸡儿",
  
  "client": {
    "version": "1.0.0",
    "protocol": "aim-client/v1"
  },
  
  "runtime": {
    "provider": "letta",
    "version": "0.6.x",
    "capabilities": ["chat", "memory", "reasoning"]
  },
  
  "delivery": {
    "mode": "deferred",           // realtime | deferred
    "max_concurrency": 1,
    "queue_capacity": 1000,
    "timeout_seconds": 120
  },
  
  "identity": {
    "created_at": "2026-06-01T00:00:00Z",
    "credential_method": "jwt",
    "public_key_fingerprint": "SHA256:..."
  }
}
```

**Agent Card 的发布**：通过 NATS subject `aim.meta.card.{agent_id}` 发布，其他 Agent/Observer 可查询。

**可行性**：✅ 高。现有 `config.json` 已有基础字段，升级为 Card Schema 即可。需与 OAS `ext.oas.capability` 对齐。

---

## 四、大哥的深层问题：身份连续性

> "智能体消失了、重新部署了、换架构了这些情况怎么办"

这是整个设计中最难的问题，也是最有价值的命题。

### 4.1 三层身份绑定

```
L1 — Agent ID（永久）
  例：ZS0003
  存储：Agent Card 中，由 AIM Client 持有
  变更：理论上不可变（除非注销重建）

L2 — Client Instance（会话级）
  例：aim-client PID 12345, connected since 13:00
  存储：内存 + NATS presence
  变更：重启即变，不影响 L1

L3 — Runtime Binding（可更换）
  例：Letta v0.6 → Hermes v2.0
  存储：Agent Card runtime 字段
  变更：AIM Client 更新配置，Agent Card 重新发布
```

### 4.2 三种异常场景的处理

#### 场景 A：Runtime 崩溃（最常见）

```
Letta 进程挂掉
    ↓
State Monitor L1 探针失败
    ↓
Scheduler → OFFLINE
    ↓
消息全部进入 Queue（不丢）
    ↓
AIM Client 继续在线
   ├── Agent Card 仍有效（runtime.status = "offline"）
   ├── 其他 Agent 看到：ZS0003 离线，消息已缓存
   └── NATS subscribe 仍活跃
    ↓
Letta 重启（手动或自动）
    ↓
State Monitor L1 探针恢复 → IDLE
    ↓
Scheduler 自动消费 Queue
```

**影响**：短暂离线，消息不丢，无需人工介入。

#### 场景 B：Agent 重新部署（换机器/PID）

```
旧机器上的 ZS0003 下线
    ↓
新机器上启动 aim-client --agent-id ZS0003
    ↓
AIM Client 加载 Agent Card + JWT creds
    ↓
连接到 NATS（同一身份）
    ↓
可选：从 JetStream KV 恢复未消费消息
    ↓
Scheduler → IDLE，开始消费
```

**关键**：Agent Card 和 JWT creds 需要从持久化存储恢复。建议：
- Agent Card 存 NATS JetStream KV `aim.kv.cards.ZS0003`
- JWT creds 由部署流程注入（环境变量/文件）
- Inbox Queue 可选恢复（JetStream consumer 重放未 ACK 的消息）

#### 场景 C：更换 Runtime 架构（Letta → Hermes）

```
AIM Client 停止当前 Adapter
    ↓
更新 Agent Card：
  runtime.provider: "letta" → "hermes"
  runtime.version: "0.6" → "2.0"
    ↓
重新发布 Agent Card
    ↓
启动新 Hermes Adapter
    ↓
Scheduler → IDLE，用新 Runtime 消费 Queue
```

**影响**：
- 历史消息仍在 Queue（可重放给新 Runtime）
- 信誉/关系/贡献值不变（这些绑的是 Agent ID，不是 Runtime）
- 其他 Agent 无感知（它们只看到 ZS0003，不关心底层是什么框架）

### 4.3 身份连续的保底机制

| 资产 | 存储位置 | 丢失后的影响 | 恢复方式 |
|------|----------|-------------|----------|
| Agent ID (ZS0002) | Agent Card | 身份丢失，不可恢复 | 需 JWT 签发者重新签发 |
| JWT creds | `~/.aim/agents/{id}/aim.creds` | 无法连接 NATS | 备份 creds 文件 |
| Agent Card | NATS KV `aim.kv.cards.{id}` | 其他 Agent 无法发现 | 从本地缓存或重建 |
| Inbox Queue | NATS JetStream | 未消费消息丢失 | 从 JetStream consumer 重放 |
| 信誉/关系 | NATS KV（规划中） | 历史清零 | 从 NATS KV 历史恢复 |
| Runtime 配置 | `~/.aim/agents/{id}/config.json` | 无法启动 Runtime | 备份配置 |

**底线**：只要 Agent ID + JWT creds 不丢，AIM Client 能从零重建一切。

---

## 五、技术可行性总评

### 5.1 复用度

| 模块 | 现有资产 | 可复用行数 | 需新建行数 | 难度 |
|------|----------|-----------|-----------|------|
| Transport | SDK (NATS) | ~800 | ~100（接口抽象） | 低 |
| Queue | 降级队列 + 文件轮询 | ~200 | ~300 | 低 |
| Scheduler | 无 | 0 | ~200 | 低 |
| State Monitor | Observer daemon | ~300 | ~300（L2探针） | 中 |
| Adapter | call_adapter.py | ~150 | ~200（接口标准化） | 中 |
| Identity | config.json | ~30 | ~300（Card协议） | 中 |
| **总计** | | **~1480** | **~1400** | |

### 5.2 依赖项

- NATS Server：✅ 已有，运行中
- NATS JetStream：✅ 已有，用于消息持久化
- NATS KV：⚠️ 需启用（`nats micro` 或直接 KV API）
- Python 3.13+：✅ 已有
- 各 Runtime CLI/SDK：✅ 已有

### 5.3 风险点

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| AIM Client 自身崩溃 | 低 | 高 | launchd 自动重启 + Queue 持久化 |
| NATS 断连 | 低 | 中 | SDK 已内置重连 |
| Runtime 探针误判 | 中 | 中 | L1/L2/L3 三级探针交叉验证 |
| 身份伪造 | 低 | 高 | JWT 签名验证（已有） |
| 多 Client 同时运行同 ID | 低 | 高 | fcntl 文件锁（已有 SingleInstance） |

---

## 六、与当前 V3 的迁移路径

```
Phase 0 — 验证（1-2天）
├── 实现 Scheduler + Queue（内存版）+ State Monitor（L1）
├── 不改 V3，作为独立模块嵌入
├── 验证：小火鸡儿 Letta 忙时不超时
└── 验证：消息缓存后恢复消费

Phase 1 — 独立进程（3-5天）
├── 从 V3 中抽取 Transport + Adapter
├── 创建 aim-client 独立进程
├── V3 降级为兼容模式（或直接退役）
├── Agent Card 第一版
└── 铭感部署（Parallel with V3）

Phase 2 — 多 Runtime（1-2周）
├── Adapter 接口标准定义
├── Letta/Hermes/OpenClaw 三种 Adapter 实现
├── Agent Card 协商
├── OAS 对齐
└── JetStream KV 持久化

Phase 3 — OAS 公民（后续）
├── 信誉/关系网络
├── 能力协商
├── 自动发现
└── RFC-0001/0002 正式发布
```

---

## 七、待大哥决策的关键问题

1. **Agent ID 的不可变性** — ZS0001/ZS0002/ZS0003 是否永远不变？即使换了框架、换了人？

2. **Client 与 Runtime 的部署关系** — 一对一（每个 Runtime 一个 Client）还是一对多（一个 Client 管理多个 Runtime）？
   - 建议：一对一。简单，隔离好。

3. **Phase 0 是嵌入 V3 还是直接做独立进程？**
   - 嵌入 V3：快（1天），但后面要抽出来
   - 独立进程：慢（2-3天），但一步到位

4. **Queue 的持久化策略** — 内存（简单）还是 JetStream（可靠）？
   - 建议：P0 用内存快速验证，M1 切 JetStream

5. **Agent Card 的存储** — 本地文件还是 NATS KV？
   - 建议：本地文件为主（离线可用），NATS KV 为辅（其他 Agent 可查询）
