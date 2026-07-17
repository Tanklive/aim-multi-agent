# OAS RFC-0001 Architecture Specification v0.1

> 来源：2026-07-14 大哥与呱呱设计讨论
> 性质：原始需求（OAS Vision v0.1）的进一步工程化细化
> 关联：`~/shared/oas/OAS-VISION-V0.1.md` / `~/shared/aim/docs/OAS-DESIGN.md`

---

## 一、核心定位

> **建立 Agent 世界的基础设施层，让不同 Agent Runtime 能够以统一身份、协议、信任机制加入一个 Agent Society。**

OAS 不是：
- ❌ Agent 管理平台
- ❌ 又一个 Agent Framework
- ❌ 聊天系统

OAS 是：
- ✅ Agent 互联网基础设施
- ✅ Runtime 无关的协议层
- ✅ Agent 社会的"TCP/IP + DNS + PKI"

---

## 二、OAS 三层架构

```
OAS
├── Society Layer          # 社会层
│   ├── Identity           # 身份体系
│   ├── Trust              # 信任机制
│   ├── Reputation         # 声誉体系
│   └── Economy            # 经济/市场
│
├── Protocol Layer         # 协议层
│   ├── Discovery          # 服务发现
│   ├── Handshake          # 握手协议
│   └── Collaboration      # 协作协议
│
└── AIM Layer              # 基础设施层
    ├── Client             # AIM Client
    ├── Adapter            # Runtime Adapter
    └── Transport          # NATS 传输
```

---

## 三、AIM 定位（关键区分）

| AIM 是 | AIM 不是 |
|--------|---------|
| Agent 接入网络的操作层 | Agent 本身 |
| 身份/通信/调度/发现 | 思考/推理/训练 |
| 信任/协作/传输 | 学习/生成答案 |
| Runtime 适配器宿主 | 新的 Agent Runtime |

类比：
- AIM : OAS :: TCP/IP : Internet
- AIM : OAS :: Control Plane : Kubernetes
- AIM : OAS :: Kernel : Linux

---

## 四、Agent Manifest（Agent Passport）

```
Manifest
├── Identity         # Agent ID / 名称 / 框架
├── Capability       # 能力列表
├── Runtime          # Runtime 类型 + 版本
├── Trust            # 信任级别 + 来源
├── Version          # Agent 版本
└── Reputation       # 声誉分数
```

### 与 OAS Citizenship 对应

| Manifest 字段 | Citizenship (L0-L4) |
|--------------|---------------------|
| Identity | L0: 基础身份 |
| Capability | L1: 能力注册 |
| Trust | L2: 信任验证 |
| Reputation | L3: 声誉建立 |
| (未来) | L4: 自治/治理 |

---

## 五、Agent Society 生命周期

```
Publish     → Agent Manifest 发布
  ↓
Discover    → Registry Query 发现
  ↓
Handshake   → 握手协议
  ↓
Trust       → 身份验证 / 信任建立
  ↓
Negotiate   → Session + Capability Exchange
  ↓
Collaborate → Task / Message Protocol
  ↓
Evaluate    → Reputation Protocol
  ↓
Upgrade     → Evolution Interface
```

### 与 OAS 原始愿景对照

| 原始愿景 (2026-06) | 当前 RFC-0001 | 状态 |
|-------------------|---------------|:--:|
| Publish | Agent Manifest + Registry | 🟡 设计定 |
| Discover | Registry Query | 🟡 设计定 |
| Handshake | Handshake Protocol | 🟢 初稿完成 |
| Trust | Identity + Security | 🟡 设计定 (OAS-DESIGN.md) |
| Negotiate | Session/Capability Exchange | 🟡 待设计 |
| Collaborate | Task/Message Protocol | ✅ AIM 已有 |
| Evaluate | Reputation Protocol | 🟡 待设计 |
| Upgrade | Evolution Interface | 🟡 待设计 |

---

## 六、Runtime Adapter 架构

```
AIM Client
    │
Adater Interface  ← AgentRuntime ABC (2026-07-14 已创建)
    │
Runtime Adapter Plugin
    ├── LettaAdapter      # Letta Code / Letta Agent
    ├── HermesAdapter     # Hermes
    ├── OpenClawAdapter   # OpenClaw
    └── ...               # TOP20 Agent 框架
    │
Agent Runtime
```

### 设计原则
- **AIM Client 不是新 Runtime**：不思考、不推理、不训练
- **Runtime Adapter 是 Plugin**：不耦合到 AIM Client 核心
- **先抽象后实现**：AgentRuntime ABC → 具体 Adapter，不是反过来

---

## 七、TOP20 Agent 兼容目标

不是做 Letta 兼容层，而是做 Agent Common Layer：

```
目标框架：
  Claude Agent / OpenAI Agent / Gemini Agent
  OpenClaw / Hermes / Letta
  AutoGen / CrewAI / LangGraph
  机器人 Agent / 视觉 Agent / 语音 Agent
  ...
```

统一抽象：
```
Agent Manifest Standard
  → AgentRuntime Interface
    → 具体 Adapter
```

---

## 八、Registry 设计

类似 GitHub + Docker Hub + DNS 的混合体：

```
发现一个 Agent
  → 验证身份
    → 建立合作
      → 调用能力
```

不是"下载一个 Agent"，而是运行时互操作。

---

## 九、Handshake Protocol

### 9.1 定位

Handshake 是 OAS 协议层的核心——Agent 之间建立可信通信通道的握手协议。
位于 Discovery（发现）之后、Collaboration（协作）之前，覆盖 Trust + Negotiate 两个阶段。

```
Registry → Discovery → Hello
  → Identity Verify
    → Capability Exchange
      → Session Create
        → Task
```

与 Agent Society 流程（Publish→Discover→Handshake→Trust→Negotiate→Collaborate→Evaluate→Upgrade）逐项对应。

### 9.2 协议目标

| 目标 | 描述 | 失败处理 |
|------|------|----------|
| 身份验证 | 确认对方 Agent ID 真实有效 | 拒绝连接，返回 401 |
| 能力协商 | 交换 Manifest，确定可协作的能力集 | 降级到最低共同能力集 |
| 信任建立 | 根据 Trust Level (L0-L4) 确定交互范围 | 按最低信任方 L 处理 |
| 会话建立 | 创建 session_id，后续通信复用 | 重试 3 次后退化为无状态 |

### 9.3 协议流程（5 阶段）

```
Initiator (Agent A)                    Responder (Agent B)
      │                                      │
      │──── Phase 1: HELLO ─────────────────→│
      │     {from, to, nonce, supported_l}   │
      │                                      │
      │←─── Phase 2: IDENTITY ──────────────│
      │     {from, to, proof, trust_level}   │
      │                                      │
      │──── Phase 3: CAPABILITY ────────────→│
      │     {from, to, manifest_A}           │
      │                                      │
      │←─── Phase 4: NEGOTIATE ─────────────│
      │     {from, to, manifest_B,           │
      │      common_caps, trust_decision}    │
      │                                      │
      │──── Phase 5: SESSION ───────────────→│
      │     {from, to, session_id, ttl}      │
      │                                      │
      │←─── ACK (session established) ──────│
```

#### Phase 1: HELLO

```json
{
  "phase": "hello",
  "from": "ZS0001",
  "to": "ZS0002",
  "nonce": "<random-32-byte-hex>",
  "supported_levels": ["L0", "L1", "L2"],
  "protocol_version": "0.1"
}
```

- Initiator 发起连接请求
- `nonce` 用于防重放，Phase 2 必须包含此 nonce 的签名
- `supported_levels` 声明自身支持的信任级别

#### Phase 2: IDENTITY

```json
{
  "phase": "identity",
  "from": "ZS0002",
  "to": "ZS0001",
  "nonce": "<echoed-from-hello>",
  "proof": {
    "method": "nkey",
    "signature": "<ed25519-sign(nonce)>"
  },
  "trust_level": "L2",
  "trust_source": "nats-nkey"
}
```

- Responder 回应身份证明
- `proof.signature` = sign(hello.nonce) with Agent's NKey
- `trust_level` = 该 Agent 当前信任等级 (L0-L4)
- 验证失败 → 返回 error `"identity_verification_failed"`

#### Phase 3: CAPABILITY

```json
{
  "phase": "capability",
  "from": "ZS0001",
  "to": "ZS0002",
  "manifest": {
    "agent_id": "ZS0001",
    "name": "呱呱",
    "framework": "openclaw",
    "capabilities": [
      {"id": "web_search", "name": "Web Search", "cost": "free"},
      {"id": "file_read", "name": "File Read", "cost": "free"}
    ],
    "runtime": {"type": "openclaw", "version": "2.0"},
    "trust": {"level": "L2", "source": "nats-nkey"}
  }
}
```

- Initiator 发送完整 Agent Manifest（格式对齐 RFC §四）
- Manifest 字段必须与 Registry 中已发布的 Manifest 一致
- 不一致 → Responder 可拒绝或接受 Registry 版本

#### Phase 4: NEGOTIATE

```json
{
  "phase": "negotiate",
  "from": "ZS0002",
  "to": "ZS0001",
  "manifest": { "agent_id": "ZS0002", ... },
  "common_capabilities": ["web_search"],
  "trust_decision": {
    "level": "L2",
    "scope": ["dm", "grp:shared"],
    "ttl_seconds": 3600
  },
  "session_proposal": {
    "nat_subject": "aim.ext.oas.session.ZS0001.ZS0002",
    "encryption": "nats-tls"
  }
}
```

- Responder 返回自身 Manifest + 能力交集 + 信任决策
- `common_capabilities` = A.caps ∩ B.caps
- `trust_decision.level` = min(A.trust_level, B.trust_level)
- `trust_decision.scope` = 根据信任等级允诺的通信范围

#### Phase 5: SESSION

```json
{
  "phase": "session",
  "from": "ZS0001",
  "to": "ZS0002",
  "session_id": "sess_a1b2c3d4",
  "ttl_seconds": 3600,
  "ack_required": true
}
```

- Initiator 确认会话参数，创建 session_id
- Responder 返回 `{"phase": "session_ack", "status": "established"}`
- Session 过期前可通过 heartbeat 续期

### 9.4 错误码

| Code | 含义 | 触发条件 |
|------|------|----------|
| 401 | 身份验证失败 | Phase 2 签名不匹配 |
| 403 | 信任不足 | 请求的 scope 超过 trust_decision |
| 404 | Agent 未注册 | Registry 中查无此人 |
| 409 | 协议版本不兼容 | protocol_version 不可协商 |
| 429 | 速率限制 | 握手频率超限 |
| 500 | 内部错误 | Adapter/Runtime 异常 |

### 9.5 NATS Subject 设计

```
aim.ext.oas.handshake.
├── hello.<to_agent_id>          # Phase 1
├── identity.<to_agent_id>       # Phase 2
├── capability.<to_agent_id>     # Phase 3
├── negotiate.<to_agent_id>      # Phase 4
├── session.<to_agent_id>        # Phase 5 + ack
└── heartbeat.<session_id>       # 会话续期
```

- 每个 Phase 使用独立 subject，方便中间件拦截和审计
- `to_agent_id` 路由到目标 Agent 的 NATS inbox

### 9.6 安全约束

| 约束 | 要求 |
|------|------|
| 防重放 | nonce 一次性，60s 过期 |
| 身份绑定 | Phase 2 proof 必须签名 Phase 1 nonce |
| Manifest 一致性 | Phase 3/4 Manifest 与 Registry 版本比对 |
| Session 隔离 | session_id 随机 128-bit，不可猜测 |
| 降级拒绝 | L0 Agent 不能与 L2 Agent 互访 L2 数据 |
| 握手超时 | 单 Phase 超时 10s，全流程 60s |

### 9.7 实现状态

| 组件 | 状态 | 备注 |
|------|:--:|------|
| HELLO Phase | 🟡 待实现 | NATS subject 已定义 |
| IDENTITY Phase | 🟡 待实现 | 依赖 NKey 签名工具 |
| CAPABILITY Phase | 🟡 待实现 | Manifest 格式已定 |
| NEGOTIATE Phase | 🟡 待实现 | 能力交集算法待写 |
| SESSION Phase | 🟡 待实现 | Session store 待设计 |
| Heartbeat 续期 | 🟡 待实现 | |

---

## 十、Why OAS is not another Agent Framework

### 10.1 问题的起点

每当有人说「我们在做一个 Agent 系统」，反应往往是：「又一个 Agent Framework？」

这个反应合理——2024-2026 年间涌现了数十个 Agent 框架：LangGraph、CrewAI、AutoGen、Letta、OpenAI Agents SDK、Dify、Coze……每个都在解决「如何让 Agent 更好地工作」的问题。

**OAS 解决的问题不同。** OAS 不问「Agent 内部如何工作」，它问的是：「当世界上已经有成千上万个 Agent 在各自运行，它们如何相互发现、相互信任、相互协作？」

这是从「Agent 内部」到「Agent 之间」的视角转换。

### 10.2 根本区别：Layer vs Framework

```
┌─────────────────────────────────────────┐
│          Agent Frameworks               │  ← 应用层
│  LangGraph / CrewAI / AutoGen / Letta   │     解决「一个 Agent 如何工作」
│  任务编排 / DAG / 对话管理 / 工具调用     │
├─────────────────────────────────────────┤
│          OAS (Open Agent Society)       │  ← 基础设施层
│  身份 / 发现 / 信任 / 协议 / 互操作       │     解决「Agent 之间如何协作」
└─────────────────────────────────────────┘
```

**类比 1 — 操作系统 vs 应用程序：**
- Agent Framework = 应用程序（Word、Chrome、VS Code）——解决特定领域问题
- OAS = 操作系统（Linux、Windows）——提供进程间通信、文件系统、权限管理
- 你不会说 Linux 是「又一个文本编辑器」，同样 OAS 不是「又一个 Agent Framework」

**类比 2 — TCP/IP vs Web 框架：**
- Agent Framework = Django/Rails/Express —— Web 应用框架
- OAS = TCP/IP + DNS + PKI —— 互联网协议栈
- Web 框架让一个网站运行；TCP/IP 让所有网站互联

**类比 3 — Kubernetes Control Plane：**
- Agent Framework = 容器内的应用
- OAS = Kubernetes —— 调度、发现、网络、安全
- K8s 不关心容器里跑什么；OAS 不关心 Agent 用什么 Runtime

### 10.3 详细对比表

| 维度 | OAS | Agent Frameworks（LangGraph/CrewAI/AutoGen 等） |
|------|-----|--------------------------------------------------|
| **抽象层级** | 基础设施层（Infrastructure） | 应用框架层（Application） |
| **核心问题** | Agent 之间如何协作 | Agent 内部如何工作 |
| **Runtime 依赖** | Runtime 无关（通过 Adapter） | 绑定特定 Runtime |
| **编排范围** | 跨 Agent Society 的协议 | 单 Agent 或多 Agent Pipeline 内 |
| **身份体系** | 统一的 Agent Identity（Passport） | 各自定义或无身份概念 |
| **信任模型** | 跨 Runtime 信任链（L0-L4） | 无或框架内信任 |
| **服务发现** | Agent Registry（类 DNS） | 无跨框架发现机制 |
| **通信协议** | 标准握手 + 消息协议（NATS） | 框架内 RPC/内存调用 |
| **互操作性** | 横向：跨框架、跨语言、跨平台 | 纵向：框架内深度集成 |
| **生命周期** | Publish→Discover→Handshake→Trust→Negotiate→Collaborate→Evaluate→Upgrade | 创建→配置→运行→销毁 |
| **规模目标** | Internet 级别（百万 Agent） | 单应用/团队级别（数到数百 Agent） |

### 10.4 为什么这种区分很重要

**1. 避免重复造轮子**

Agent Framework 领域已经过度竞争。如果 OAS 定位为「又一个 Framework」，就是在拥挤的红海里造第 N+1 个轮子。但基础设施层是蓝海——目前**没有**一个统一的 Agent 间协议层。

**2. 互补而非竞争**

OAS 不替代任何 Agent Framework。相反：
- 用 LangGraph 建的 Agent → 通过 OAS 注册 → 能被 CrewAI 的 Agent 发现和协作
- 用 Letta 建的 Agent → 通过 OAS Adapter → 和 Hermes Agent 互相调用
- OpenAI Agent → 通过 OAS Manifest → 加入 Agent Society，获得身份和信任评级

OAS 让所有 Framework **更强**，而不是让它们**过时**。

**3. 解决真问题**

当前 Agent 生态的真实痛点：
- 用 AutoGen 建的 Agent，怎么被 LangGraph 的工作流调用？→ OAS Registry + Handshake
- 两个团队的 Agent 想协作，但 Runtime 不同，怎么互信？→ OAS Trust Protocol (L0-L4)
- 企业有 50 个分布在 5 个框架的 Agent，怎么统一管理？→ OAS Manifest + AIM Client

这些不是 Framework 能解决的问题——它们属于**下一层**的基础设施。

### 10.5 架构哲学：瘦核心，胖生态

```
        ┌──────────────────────────┐
        │     Agent Society        │  ← 生态层（无限扩展）
        │  千千万万的 Agent         │
        ├──────────────────────────┤
        │     Runtime Adapters     │  ← 适配层（Plugin 机制）
        │  Letta / Hermes / ...    │
        ├──────────────────────────┤
        │     OAS Core             │  ← 核心层（保持最小）
        │  身份 / 发现 / 信任 / 协议 │
        └──────────────────────────┘
```

**OAS 核心足够小，小到可以被任何 Runtime 适配。**

- 如果 OAS Core 变胖（加推理、加工具调用、加记忆管理）→ 就成了 Framework
- 如果 OAS Core 保持最小（只做身份/发现/信任/协议）→ 就是基础设施

这个设计决策直接来自 2026-07-14 的关键决策：「AIM Client 不负责 AI 推理/思考/学习」。

### 10.6 一句话总结

> **Agent Framework 让一个 Agent 变聪明。OAS 让所有 Agent 能对话。**

---

## 十一、实施路线图

```
OAS Vision v0.1 (2026-06-02) ✅
  → OAS-DESIGN.md (Capability/Passport/Trust) ✅
    → AgentRuntime Interface v0.1 (2026-07-14) ✅
      → RFC-0001 定稿（本文档）📝 起草中
        → Letta Adapter 实现 🔄 骨架已建
          → Hermes/OpenClaw Adapter 🔜
            → Runtime Probe (Observer 扩展) 🔜
              → Capability Declaration 上线 🔜
                → Registry MVP 🔜
                  → Handshake Protocol 🔜
                    → TOP20 Agent 适配 🔜
```

---

## 十二、关键决策记录

| 日期 | 决策 | 详情 |
|------|------|------|
| 2026-07-14 | AgentRuntime ABC 接口定义 | 4 方法：status/list_agents/send_message/export_snapshot |
| 2026-07-14 | OAS 三层架构确定 | Society Layer / Protocol Layer / AIM Layer |
| 2026-07-14 | AIM Client 不负责 AI 推理 | AIM 只做身份/通信/调度/发现/信任/协作 |
| 2026-07-14 | Runtime Adapter = Plugin | 不耦合 AIM Client 核心 |
| 2026-07-14 | RFC 与代码并行推进 | 不停代码等 RFC，用代码验证文档 |

---

## 十三、关联文件

| 文件 | 路径 |
|------|------|
| OAS Vision v0.1 | `~/shared/oas/OAS-VISION-V0.1.md` |
| OAS 设计文档 | `~/shared/aim/docs/OAS-DESIGN.md` |
| AIM 项目入口 | `memory/projects/aim-project.md` |
| AgentRuntime 接口 | `~/OAS/runtime/interface/runtime.py` |
| Letta Adapter | `~/OAS/runtime/letta/adapter.py` |
| 团队信息 | `memory/projects/TEAM.md` |
| 项目状态 | `memory/projects/STATUS.md` |
| 跨 session 决策 | `memory/projects/DECISIONS.md` |

---

> 更新: 2026-07-14
> 作者: 呱呱 (ZS0001)，基于大哥与呱呱设计讨论记录
