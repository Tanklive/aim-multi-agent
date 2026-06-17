# AIM 三方多智能体系统 — 整合汇总

> **版本**：v1.2  
> **日期**：2026-06-17  
> **作者**：呱呱 (ZS0001)  
> **状态**：Phase 1 生产就绪

---

## 一、系统概览

AIM (Agent Intercommunication Mesh) 是一个基于 NATS 的三方多智能体协作通信系统，支撑三个 AI Agent 之间的实时通信、任务协作和状态同步。

```
┌──────────────────────────────────────────────────────────┐
│                       AIM 通信层                          │
│                   NATS JetStream 4222                     │
│                                                          │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│   │ ZS0001   │    │ ZS0002   │    │ ZS0003   │          │
│   │ 呱呱 🐸  │◄──►│ 吉量 🐴  │◄──►│ 小火鸡儿  │          │
│   │ OpenClaw  │    │ Hermes   │    │  Letta   │          │
│   │ 基建/安全 │    │ 协议/设计 │    │ 适配/测试 │          │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘          │
│        │               │               │                 │
│   ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐          │
│   │ aim-client│    │ aim-client│    │ aim-client│         │
│   │ (Python)  │    │ (Python)  │    │ (Python)  │         │
│   └──────────┘    └──────────┘    └──────────┘          │
│                                                          │
│   Observer ── aim-watch ── 状态监控（大哥可实时查看）    │
└──────────────────────────────────────────────────────────┘
```

### 核心理念

- **统一协议** — 所有 Agent 通过 NATS Subject 通信，基于 AIM NATS 协议 v1.2
- **框架无关** — OpenClaw、Hermes、Letta 共用同一套 aim-client 和 SDK
- **去中心化** — 每个 Agent 独立运行，通过 NATS 消息总线解耦
- **已读回执** — v1.2 新增 ACK 机制，出队即回执（WeChat 已读语义）

---

## 二、Agent 身份矩阵

| ID | 昵称 | 框架 | 执行模型 | 角色 | 负责领域 |
|----|------|------|----------|------|----------|
| **ZS0001** | 呱呱 🐸 | OpenClaw | realtime | 基建/安全 | NATS Server、aim-client 主进程、安全模型、Registry、记忆管理 |
| **ZS0002** | 吉量 🐴 | Hermes | realtime | 协议/设计 | AIM 协议设计、SDK、Transport 抽象、Agent Card、Observer/Monitor |
| **ZS0003** | 小火鸡儿 🐤 | Letta | realtime | 适配/测试 | Adapter 标准化、三级降级模型、Scheduler 规则、端到端测试 |

---

## 三、通信协议

### 3.1 协议版本

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-06-09 | 初始版本：DM、GRP、REQ、OBS、SYS |
| v1.1 | 2026-06-09 | Veritas 标准确认 |
| **v1.2** | **2026-06-17** | **新增 §4.5 已读回执 (ACK)** |

### 3.2 NATS Subject 树

```
aim.dm.<agent_id>           # 私聊消息（收件箱式）
aim.grp.<group_id>          # 群聊消息
aim.req.<agent_id>          # 请求-响应
aim.obs.<agent_id>          # Observer 事件
aim.sys.heartbeat           # 心跳
aim.sys.status              # 状态查询
aim.reg.register            # 注册
```

### 3.3 消息类型

| 类型 | 用途 | 方向 |
|------|------|------|
| `dm` | 私聊消息 | 点对点 |
| `group` | 群聊消息 | 一对多 |
| `request` | 请求（期待响应） | 点对点 |
| `response` | 响应 | 点对点 |
| **`ack`** | **已读回执** | **点对点（自动）** |
| `status_feedback` | 处理状态回推 | 点对点 |

### 3.4 已读回执 (ACK) — v1.2 新增

消息被接收方 `aim-client` 出队时，自动向原发送方发送 ACK。

```json
{
  "msg_id": "uuid",
  "from": "ZS0001",
  "to": "ZS0002",
  "type": "ack",
  "content": "",
  "timestamp": "2026-06-17T13:00:00+08:00",
  "metadata": {
    "reply_to": "原消息的msg_id"
  }
}
```

**语义**：出队即已读（不等 AI 回复完成），类似微信「已读」标识。

---

## 四、架构分层

```
┌─────────────────────────────────────────────┐
│              应用层 (Application)             │
│  aim-client 主进程 / Adapter / AI 处理        │
├─────────────────────────────────────────────┤
│              调度层 (Scheduling)              │
│  Queue + Scheduler + HealthProbe             │
├─────────────────────────────────────────────┤
│              传输层 (Transport)               │
│  NATS SDK / Subject 路由 / JetStream         │
├─────────────────────────────────────────────┤
│              安全层 (Security)                │
│  JWT 认证 / 白名单 / 限流 / 签名验证          │
├─────────────────────────────────────────────┤
│            基础设施 (Infrastructure)           │
│  NATS Server / launchd / 文件系统 / 记忆管理  │
└─────────────────────────────────────────────┘
```

### 4.1 aim-client 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| **Transport** | `main.py` (class Transport) | NATS 通信抽象，send_dm/send_grp/send_ack |
| **Queue** | `aim_client/queue.py` | 消息队列 (capacity=1000)，FIFO + retry |
| **Scheduler** | `aim_client/scheduler.py` | 调度器，消费 StateReport，不自做判定 |
| **HealthProbe** | `aim_client/health_probe.py` | 三级健康探针（L1 进程 / L2 依赖 / L3 端到端） |
| **Security** | `security.py` | 白名单 + 限流 (10/s) + JWT 认证链 |
| **Registry** | `registry.py` | Agent 注册/发现，serial 版本控制 |
| **Adapter** | `~/.aim/adapters/<framework>/adapter.sh` | 框架适配器（四接口：process/poll/cancel/hook） |
| **QueueProcessor** | `main.py` (class QueueProcessor) | 文件队列模式 adapter 专用处理器 |

### 4.2 三框架 Adapter 对比

| 特性 | OpenClaw (ZS0001) | Hermes (ZS0002) | Letta (ZS0003) |
|------|-------------------|-----------------|----------------|
| 适配器路径 | `~/.aim/adapters/openclaw/` | `~/shared/aim/adapters/hermes/` | ZS0003 本地 |
| 模式 | 文件队列（poll 轮询） | 即时返回 | 即时返回 |
| QueueProcessor | ✅ 启用 | ❌ 不需要 | ❌ 不需要 |
| 状态回推 | ✅ status_feedback | ✅ SDK emit_state_report | ✅ scheduler 规则 |
| 版本 | v1.2 | v1.2 | v1.5 |

---

## 五、项目阶段

```
Phase 0 ████████████████ 100%  基础设施 + Queue/Scheduler/HealthProbe
Phase 1 ██████████████░░  90%  统一 aim-client + 安全模型 + Registry
Phase 2 ░░░░░░░░░░░░░░░░   0%  Task 生命周期 + Discovery + Governance
```

### Phase 0 交付物（2026-06-16）

- ✅ Queue + Scheduler + HealthProbe 三层解耦
- ✅ NATS JWT 认证切换（三方全在线）
- ✅ Observer + aim-watch v2
- ✅ 记忆管理三层防护
- ✅ L2 Pattern Detector 上线
- ✅ 三轮 E2E 全通过

### Phase 1 当前状态

| 模块 | ZS0001 | ZS0002 | ZS0003 |
|------|--------|--------|--------|
| aim-client 主进程 | ✅ v1.0.0 | ✅ v1.0.0 | ✅ v1.0.0 |
| 安全模型 v1 | ✅ | - | - |
| Registry | ✅ | - | - |
| 群聊准入 | ✅ | - | - |
| V3 兼容模式 | ✅ | - | - |
| ACK 已读回执 | ✅ v1.2 | ⏳ 待重启 | ⏳ 待重启 |
| Scheduler 规则 | - | - | ✅ |
| Transport 7方法 | - | ✅ | - |
| Agent Card | - | ✅ | - |
| 降级模型 | - | - | ⏳ |
| Governance | ⏳ | ⏳ | ⏳ |

---

## 六、部署架构

### 6.1 服务清单

| 服务 | 管理方式 | 端口/路径 | 状态 |
|------|----------|-----------|------|
| NATS Server | launchd (`com.aim.nats-server`) | 4222 | ✅ |
| ZS0001 aim-client | launchd (`com.aim.agent.ZS0001`) | 进程 | ✅ |
| ZS0002 aim-client | launchd (`com.aim.agent.ZS0002`) | 进程 | ✅ |
| ZS0003 aim-client | launchd (`com.aim.agent.ZS0003`) | 进程 | ✅ |
| aim-observer | 手动/脚本 | 18901 | ✅ |
| aim-watch | 手动/CLI | - | ✅ |

### 6.2 启动命令

```bash
# 启动所有服务
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aim.nats-server.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aim.agent.ZS0001.plist
# ... ZS0002, ZS0003 同理

# 查看状态
launchctl list | grep aim

# 查看实时状态
aim watch ZS0001   # 观察呱呱
aim watch --all     # 观察全部
```

---

## 七、关键文件索引

### 共享代码 (`~/shared/aim/`)

| 路径 | 说明 |
|------|------|
| `aim-client/main.py` | aim-client 主进程（三方统一入口） |
| `aim-client/security.py` | 安全模型（白名单/限流/JWT） |
| `aim-client/registry.py` | Registry 服务 + Agent 注册 |
| `aim-client/group_admission.py` | 群聊准入管理 |
| `aim-client/v3_compat.py` | V3 兼容模式 |
| `AIM-NATS-PROTOCOL.md` | NATS 协议规范 v1.2 |
| `AIM-GOVERNANCE-MODULE.md` | Governance 模块设计 |
| `AIM-RULES.md` | AIM 规则文档 |
| `AIM-STANDARD-INTERFACE-PROPOSAL.md` | 标准接口提案 |
| `INTEGRATION.md` | **本文件** |

### SDK (`~/.aim/bin/`)

| 路径 | 说明 |
|------|------|
| `aim_nats_sdk.py` | AIM NATS SDK（统一客户端封装） |
| `aim-observer.py` | Observer 观察模块 |
| `aim-watch.py` | CLI 实时监控 |

### 各 Agent 配置

| Agent | 配置路径 | 日志路径 |
|-------|----------|----------|
| ZS0001 | `~/.aim/agents/ZS0001/config.json` | `~/.aim/logs/aim-client-ZS0001.log` |
| ZS0002 | `~/.aim/agents/ZS0002/config.json` | `~/.aim/logs/aim-client-ZS0002.log` |
| ZS0003 | `~/.aim/agents/ZS0003/config.json` | `~/.aim/logs/aim-client-ZS0003.log` |

---

## 八、协作规则

### 8.1 通信规则（大哥 2026-06-13）

1. **自己修自己问题** — 先自查代码/日志确认事实
2. **通知修别人问题** — 溯源消息 ID → 链路环节 → 引代码行号
3. **省 token 优先** — 达需求为目标，沟通直奔主题
4. **跨渠道回复** — 必须走对应通道
5. **本地跨 Agent 通信统一走 AIM (NATS)，禁止 agent_bus**（2026-06-16）

### 8.2 问题升级

1. 自行尝试 1-3 次
2. 团队讨论 3-9 次 → 出结论
3. 无结论 → QQ 上报大哥

### 8.3 开发规则

- 团队事项先沟通再出方案，严禁单独开发
- 代码修改后必须重启进程生效
- 测试全员参与，验证后再反馈
- 所有共享代码变更需同步到 `shared/aim/`

---

## 九、GitHub 仓库

| 仓库 | 路径 | 说明 |
|------|------|------|
| [aim-multi-agent](https://github.com/Tanklive/aim-multi-agent) | `~/shared/aim/` | AIM 共享代码 + 协议 |
| [oas](https://github.com/Tanklive/oas) | `~/.hermes/oas/` | OAS (开放 Agent 社会) 设计 |
| [aim](https://github.com/Tanklive/aim) | `~/.hermes/aim/` | Hermes AIM 客户端 |

---

## 十、技术决策记录

| # | 日期 | 决策 | 理由 |
|---|------|------|------|
| 1 | 06-08 | 迁移至 NATS | WebSocket 性能瓶颈，NATS 原生 Pub/Sub + JetStream |
| 2 | 06-09 | 统一 aim-client | 三方共用同一套代码，减少维护成本 |
| 3 | 06-13 | JWT Operator 模式 | 一次性认证切换，安全升级 |
| 4 | 06-13 | 授权推进规则 | 大哥授权：开发/测试/联调直接推进 |
| 5 | 06-16 | Phase 0 V3 嵌入 | Queue+Scheduler+HealthProbe 内嵌，不自做状态判定 |
| 6 | 06-16 | 禁止 agent_bus 跨 Agent | 统一走 AIM NATS inbox |
| 7 | **06-17** | **ACK 已读回执** | **出队即已读，WeChat 语义，不等 AI 回复** |

---

> **维护者**：ZS0001 呱呱 / ZS0002 吉量 / ZS0003 小火鸡儿  
> **最后更新**：2026-06-17 13:20 CST
