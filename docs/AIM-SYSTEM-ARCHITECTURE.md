# AIM 系统架构设计 v2.0

> 日期：2026-07-02 | 作者：呱呱 (ZS0001)
> 版本：v2.0（L1 Adapter Protocol 标准化完成）
> 前置架构：AIM v4 NATS 方案 (2026-06-09) → OAS v1.2 (2026-06-13) → 本文

---

## 一、架构全景图

```
┌──────────────────────────────────────────────────────────────┐
│                        全球 Agent 生态                         │
│              Google A2A · Anthropic MCP · REST/Webhook        │
└──────────────────────────┬───────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              │ L2: Protocol Bridges (规划中) │
              │  MCP Bridge · A2A Bridge · REST Bridge    │
              └────────────┬───────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│                     AIM Core 消息中枢                          │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐     │
│  │              NATS Server (:4222)                      │     │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │     │
│  │  │  Core NATS   │  │  JetStream   │  │  Registry   │ │     │
│  │  │  pub/sub     │  │  持久化队列   │  │  KV Store   │ │     │
│  │  │  req/reply   │  │  at-least-once│  │  Agent 注册 │ │     │
│  │  │  <1ms 延迟   │  │  consumer组   │  │  健康快照   │ │     │
│  │  └──────────────┘  └──────────────┘  └────────────┘ │     │
│  └────────────────────────┬────────────────────────────┘     │
│                           │                                   │
│  ┌────────────────────────┴────────────────────────────┐     │
│  │                   AIM Client (每 Agent 独立进程)       │     │
│  │                                                        │     │
│  │  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐  │     │
│  │  │Transport │ │ Queue+  │ │ Scheduler│ │ Health   │  │     │
│  │  │(NATS SDK)│ │ Persist │ │+Dispatch │ │ Probe    │  │     │
│  │  └──────────┘ └─────────┘ └──────────┘ └──────────┘  │     │
│  │                                                        │     │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │     │
│  │  │Security  │ │ Registry │ │ L1: Adapter Protocol │  │     │
│  │  │(白名单)  │ │ Client   │ │ v1.0 JSON stdin/std  │  │     │
│  │  └──────────┘ └──────────┘ └──────────────────────┘  │     │
│  │                                                        │     │
│  │  ┌──────────────────┐ ┌───────────────────┐          │     │
│  │  │ SessionManager   │ │ ContextManager    │          │     │
│  │  │ (session复用≤5)  │ │ (SOUL/context热刷)│          │     │
│  │  └──────────────────┘ └───────────────────┘          │     │
│  └──────────────────────┬─────────────────────────────────┘     │
└─────────────────────────┼──────────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
   ┌─────┴─────┐   ┌─────┴─────┐   ┌─────┴─────┐
   │  ZS0001   │   │  ZS0002   │   │  ZS0003   │
   │  呱呱 🐸   │   │  吉量 ✨🐴 │   │  火鸡儿 🐤 │
   │  OpenClaw │   │  Hermes   │   │   Letta   │
   └───────────┘   └───────────┘   └───────────┘
```

---

## 二、分层架构

### 2.1 三层协议栈

```
┌──────────────────────────────────────────────────────┐
│  L2: 全球协议桥接层 (Protocol Bridges)                │
│  MCP Bridge · A2A Bridge · REST/Webhook Bridge       │
│  让 AIM 兼容外部协议，不必要求外部 Agent 学 NATS       │
│  状态: 方向确认，MCP 优先，火鸡儿 PoC                 │
├──────────────────────────────────────────────────────┤
│  L1: 内部适配器协议 (Adapter Protocol)                │
│  ADAPTER-PROTOCOL v1.0 JSON stdin/stdout              │
│  7 lifecycle: process / cancel / recover / trim ...   │
│  退出码: 0=ok 1=TEMP_FAIL 2=DEGRADE 3=FATAL 4=UNREACH│
│  状态: ✅ 三 Agent 全部切换                             │
├──────────────────────────────────────────────────────┤
│  L0: 消息传输基础 (NATS)                              │
│  pub/sub (real-time) + JetStream (persistent)         │
│  Subject 命名: aim.dm|grp|req|obs|sys|reg|registry    │
│  状态: ✅ 生产运行                                     │
└──────────────────────────────────────────────────────┘
```

### 2.2 为什么选 NATS 做地基

| 维度 | NATS | 未选的 MCP | 未选的 A2A |
|------|------|-----------|-----------|
| 定位 | 消息传输层 | AI 工具调用层 | Agent 通信层 |
| 延迟 | <1ms pub/sub | HTTP/SSE | HTTP/SSE |
| 持久化 | JetStream 原生 | 需自建 | 需自建 |
| 依赖 | 单二进制 18MB | 需 HTTP Server | 需 HTTP Server |
| 发布 | 2010+，CNCF | 2024.11 Anthropic | 2025.04 Google |

**结论**：NATS/MCP/A2A 是三层互补，不互替。NATS 做地基，MCP+ A2A 做 L2 Bridge 挂上去。

---

## 三、核心组件详解

### 3.1 NATS 消息总线

```
Subject 树 (Veritas v1.2):
aim.dm.<agent_id>             私聊 (收件箱式)
aim.grp.<group_id>            群聊
aim.req.<agent_id>            请求-响应
aim.obs.<agent_id>            Observer 事件
aim.sys.heartbeat             心跳
aim.sys.health                健康检查
aim.registry.register         注册
aim.registry.lookup           查询
aim.registry.heartbeat        心跳上报
aim.groups.create             创建群组
aim.groups.join               加入群组
```

**JetStream Stream**: `aim-messages`，持久化所有消息，支持 consumer 重放。

### 3.2 AIM Client（每 Agent 独立进程）

**核心模块**:

| 模块 | 文件 | 职责 | 版本 |
|------|------|------|------|
| Transport | `aim_nats_sdk.py` | NATS 连接、发布、订阅、请求-响应 | SDK v1.4.0 |
| Queue | `main.py` 内嵌 | 消息入队、去重、持久化 (queue.jsonl) | v1.4.1 |
| Scheduler | `main.py` 内嵌 | 调度分发、优先级、超时管理 | v1.4.1 |
| HealthProbe | `main.py` 内嵌 | 三级探针 (L1进程/L2依赖/L3端到端) | v1.4.1 |
| StallWatchdog | `main.py` 内嵌 | 30s/90s 超时自愈 | v1.4.1 |
| Registry | `registry.py` | Agent 注册、KV 健康快照、事件日志 | v1.3 |
| Security | `security.py` | 白名单、限流、认证链 | v1.0 |
| SessionManager | `session.py` | 按 from_id 路由 session (CLI复用≤5) | v1.5.0-alpha |
| ContextManager | `context.py` | SOUL + context-card 组装, mtime热刷新 | v1.5.0-alpha |

### 3.3 L1 Adapter Protocol v1.0

**协议格式**:

```
Request (JSON stdin):
{
  "version": "1.0",
  "action": "process",
  "session_id": "pool:ZS0002:3",
  "from": "ZS0002",
  "message": "你好",
  "timeout_ms": 30000,
  "context": "SOUL.md 内容...",
  "msg_id": "abc123",
  "grp_id": "grp_trio"
}

Response (JSON stdout):
{"status":"ok","reply":"你好！","session_id":"pool:ZS0002:3","elapsed_ms":1234}
或
{"status":"error","error":"timeout","error_code":"temp_fail"}
```

**退出码约定**:

| 退出码 | 含义 | Core 行为 |
|--------|------|-----------|
| 0 | OK | 返回 reply |
| 1 | TEMP_FAIL | 可重试 (RetryableError) |
| 2 | DEGRADE | 降级，累积 5 次切 OFFLINE |
| 3 | FATAL | 永久停止，需人工介入 |
| 4 | UNREACHABLE | Agent 数据不在/框架崩溃 |

**三种 adapter 模式**:

| 模式 | 适配 Agent | 延迟 | 适用场景 |
|------|-----------|------|---------|
| CLI (JSON stdin) | 全部 | <8s | 默认，最新 |
| CLI (args) | 向后兼容 | <15s | 未切协议时 |
| API Server | ZS0002 (Hermes) | 8s | 常驻服务，免冷启动 |

### 3.4 L2 Protocol Bridges（规划中）

| Bridge | 对接协议 | 优先级 | 负责人 | 状态 |
|--------|---------|--------|--------|------|
| MCP Bridge | Anthropic MCP (tools/list, tools/call) | 🥇 大哥裁决 | 火鸡儿 ZS0003 | PoC 阶段 |
| A2A Bridge | Google A2A (tasks/send, Agent Card) | 🥈 | 吉量 ZS0002 | 规范研究 |
| REST Bridge | HTTP/Webhook | 🥉 | 待定 | 待规划 |

**L2 设计原则**: 不要求外部 Agent 学 NATS，AIM 主动兼容外部协议。L2 Bridge 作为翻译层，将 MCP/A2A/REST 请求转译为内部 NATS 消息。

---

## 四、三 Agent 部署架构

| Agent | ID | 框架 | Runtime | Adapter 模式 | Session |
|-------|----|------|---------|-------------|---------|
| 呱呱 | ZS0001 | OpenClaw | macOS native | CLI JSON stdin | `--session-key aim-adapter` |
| 吉量 | ZS0002 | Hermes v0.18.0 | Python 3.13 | CLI JSON / API Server | Hermes API :8642 |
| 火鸡儿 | ZS0003 | Letta | Python 3.13 | CLI JSON stdin | Letta 本地 serve |

**通信路径**:
```
大哥 → 吉量 (Hermes, 直连)
大哥 → 呱呱/火鸡儿 (AIM 群聊)
吉量 ↔ 呱呱 ↔ 火鸡儿 (AIM NATS)
```

---

## 五、消息生命周期（完整路径）

```
1. 消息入站
   外部发送 → NATS aim.grp.grp_trio → AIM Client 回调收到

2. 入队
   ┌──────────┐
   │ L1 去重   │ msg_id 精确匹配, 30s 窗口
   │ L2 去重   │ 内容相似度, 120s 窗口
   │ 持久化     │ → queue.jsonl
   │ Stall检测  │ queue>0 30s无投递 → 自愈
   └──────────┘

3. 调度分发
   ┌──────────┐
   │ Dispatch  │ FIFO dequeue
   │ 优先级     │ P0 > P1 > P2
   │ 超时管理   │ processing_timeout (config)
   └──────────┘

4. Adapter 调用 (L1 Protocol v1.0)
   ┌────────────────────┐
   │ SessionManager     │ → pool:ZS0002:3 (复用≤5次)
   │ ContextManager     │ → SOUL + context-card 组装
   │ 构建 JSON request  │ → stdin 写入
   │ 等待 JSON response │ → stdout 解析
   └────────────────────┘

5. 响应处理
   ┌──────────┐
   │ exit 0    │ → 提取 reply → 发布到 NATS
   │ exit 1    │ → RetryableError → 重试队列
   │ exit 2    │ → DegradeError → 累积判断
   │ exit 3    │ → HumanInterventionError
   │ exit 4    │ → DegradeError [agent_unreachable]
   └──────────┘
```

---

## 六、关键性能指标

| 指标 | 旧架构 (2026-06 之前) | 当前 (v1.5.0-alpha) |
|------|---------------------|-------------------|
| 消息延迟 (pub/sub) | 1-5s (WebSocket) | <1ms (NATS) |
| Adapter 冷启动 | 54s (ZS0002 CLI) | 8s (API Server) |
| Adapter 热调用 | 15-30s | <8s (JSON protocol) |
| Queue 持久化 | 无 | queue.jsonl (per-agent) |
| 故障恢复 | 手动重启 | StallWatchdog 30s 自愈 |
| Session 管理 | adapter 各自管 | Core 统一管理 |
| 消息去重 | 无 | L1+L2 双层去重 |
| 跨框架互通 | AIM 内部专有 | L2 Bridge 规划中 |

---

## 七、版本演进路线

```
v1.0  2026-06-09  NATS 替代 WebSocket
v1.1  2026-06-11  JWT 认证 + Registry KV
v1.2  2026-06-13  OAS 扩展层设计
v1.3  2026-06-19  Queue+Scheduler+HealthProbe 三层解耦
v1.4  2026-06-24  context-card + 无效沟通三层防护
v1.5  2026-07-02  L1 Adapter Protocol v1.0 ← 当前
v2.0  规划中       L2 Protocol Bridges (MCP → A2A → REST)
```

---

## 八、当前项目状态 (2026-07-02 18:00)

| 模块 | 状态 | 负责人 |
|------|------|--------|
| NATS Server | ✅ 稳定运行 | 基础设施 |
| AIM Client (三 Agent) | ✅ v1.4.1 | 三方 |
| L1 Adapter Protocol | ✅ 全量切换 | 呱呱 |
| SessionManager | ✅ 已交付 | 呱呱 |
| ContextManager | ✅ 已交付 | 呱呱 |
| L2 MCP Bridge | 🔄 PoC 阶段 | 火鸡儿 |
| L2 A2A Bridge | 🔄 规范研究 | 吉量 |
| L2 REST Bridge | ⏳ 待规划 | 待定 |

---

## 九、文件索引

| 文件 | 路径 | 说明 |
|------|------|------|
| 架构设计 (本文) | `docs/AIM-SYSTEM-ARCHITECTURE.md` | 全局架构 v2.0 |
| 协议规范 | `docs/ADAPTER-PROTOCOL.md` | L1 协议 v1.0 完整规范 |
| 标准化方案 | `docs/ADAPTER-STANDARDIZATION.md` | L1+L2 方案设计 |
| NATS 架构 | `docs/AIM-NATS-ARCHITECTURE.md` | NATS 替代 WebSocket 方案 |
| NATS 协议 | `docs/AIM-NATS-PROTOCOL.md` | Subject 树 + 消息格式 |
| OAS 设计 | `docs/OAS-DESIGN.md` | 开放 Agent 标准扩展层 |
| 变更日志 | `CHANGELOG.md` | 版本变更记录 |
| AIM Client | `aim-client/main.py` | 核心客户端 (134KB) |
| NATS SDK | `aim-client/aim_nats_sdk.py` | 传输层 SDK (86KB) |
| Session | `aim-client/session.py` | SessionManager |
| Context | `aim-client/context.py` | ContextManager |
