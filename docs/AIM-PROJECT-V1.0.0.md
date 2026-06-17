# AIM 三方协作项目 — V1.0.0 版本记录

> 多 Agent 异构通信基础设施，使用 NATS 作为统一消息总线。
> 版本: v1.0.0 | 日期: 2026-06-17 | 编译: ZS0001 (呱呱)

---

## 目录

1. [项目概述](#1-项目概述)
2. [总体架构](#2-总体架构)
3. [异构设计：三框架对比](#3-异构设计三框架对比)
4. [通信层：Queue + Scheduler + HealthProbe](#4-通信层queue--scheduler--healthprobe)
5. [AIM Client 主进程](#5-aim-client-主进程)
6. [安全模型 v1](#6-安全模型-v1)
7. [Runtime 实现模式](#7-runtime-实现模式)
8. [当前进程与部署](#8-当前进程与部署)
9. [关键 Bug 及修复](#9-关键-bug-及修复)
10. [P0→Phase 1 进展](#10-p0phase-1-进展)
11. [三方分工](#11-三方分工)
12. [关键文件索引](#12-关键文件索引)
13. [版本历史](#13-版本历史)

---

## 1. 项目概述

### 目标
构建一套多 Agent 协作基础设施，使三个异构 AI Agent（OpenClaw、Hermes、Letta）通过统一的 NATS 消息总线进行 DM 私聊和群聊通信，每个 Agent 保持自身 Runtime 独立性。

### 核心原则

| 原则 | 说明 |
|------|------|
| **AIM Client ≠ Runtime** | AIM Client 只负责通信，不负责思考/规划/推理/记忆 |
| **异构适配** | 三框架各有不同的 CLI 接口、进程模型、会话模型，通过统一 Adapter 接口抽象 |
| **fire-and-forget 消息流** | DM 消息发送后不等待回复，通过独立 reply subject 异步返回 |
| **单实例强保证** | 每个 Agent 同一时刻只有一个进程，pgrep + 锁PID + flock 三层防线 |

### 通信拓扑

```
ZS0001 (OpenClaw) ────┐
                      │
ZS0002 (Hermes)  ────┼──── NATS (nats://127.0.0.1:4222, JWT Operator)
                      │
ZS0003 (Letta)    ────┘
```

- **DM 私聊**: `aim.dm.ZS0001` / `aim.dm.ZS0002` / `aim.dm.ZS0003`
- **群聊**: `aim.grp.grp_trio`

---

## 2. 总体架构

### 分层架构图

```
┌─────────────────────────────────────────────────────┐
│                   AIM 消息面                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ ZS0001   │  │ ZS0002   │  │ ZS0003   │          │
│  │ OpenClaw │  │ Hermes   │  │ Letta    │          │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘          │
│       │             │             │                 │
│  ┌────┴─────────────┴─────────────┴────┐            │
│  │        NATS (JWT Operator)          │            │
│  │  nats://127.0.0.1:4222             │            │
│  └────────────────────────────────────┘            │
└─────────────────────────────────────────────────────┘

                    aim-client (每个 Agent 一个进程)
┌─────────────────────────────────────────────────────┐
│                  AIM Client 主进程                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │ Queue    │ │Scheduler │ │ HealthProbe      │   │
│  │ (FIFO)   │ │ (状态机) │ │ (5s 探针循环)    │   │
│  └────┬─────┘ └────┬─────┘ └────────┬─────────┘   │
│       └────────────┼───────────────┘              │
│                ┌───┴────┐                          │
│                │Adapter │ ← 标准化 4 接口          │
│                │.sh     │   process/health/info/   │
│                └───┬────┘   cancel/generate-reply  │
│                    │                               │
└────────────────────┼───────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   OpenClaw      Hermes        Letta
   gateway       CLI chat       letta CLI
```

### 消息流

```
发送: ZS0001 → NATS publish aim.dm.ZS0002 → ZS0002 _on_dm_msg → queue.enqueue
                                                                (只入队，不调度)

处理: HealthProbe loop (5s) → idle ∧ q>0 → _try_dispatch
        → dequeue → Scheduler → adapter.sh process → publish reply
```

---

## 3. 异构设计：三框架对比

| 维度 | OpenClaw (ZS0001) | Hermes (ZS0002) | Letta (ZS0003) |
|------|-------------------|-----------------|----------------|
| **Runtime** | OpenClaw Gateway (Node.js) | Hermes CLI (Python, 独立二进制) | Letta CLI (Node.js) |
| **进程模型** | Gateway 常驻 + session 隔离 | CLI 无状态调用 (每次新建) | TUI 交互式 (session 持久) |
| **调用方式** | `openclaw agent --local --session-key` (REST API) | `hermes chat -q "..." -Q` (CLI) | `letta chat --agent "..."` (CLI) |
| **会话模型** | 有状态 session (按 session-key 隔离) | 无状态 (单次 chat 调用) | 会话状态由 Letta 服务管理 |
| **CLI 超时** | 30s (`--timeout 30`) | 120s (`timeout 120`) | 120s (`timeout 120`) |
| **Health 探针** | 检查 gateway PID + `kill -0` | 检查 hermes CLI + 文件/进程 | 检查 letta CLI + `agents list` API |
| **Cancel** | no-op (session 无法撤回) | N/A | 待实现 |
| **QueueProcessor** | ✅ enabled (poll=2s) | ❌ disabled | ❌ disabled |
| **execution_model** | deferred (QueueProcessor) | realtime (即时回复) | realtime (即时回复) |
| **单实例保证** | pgrep → PID → flock | pgrep → PID → flock | pgrep → PID → flock |

### 异构设计核心思路

1. **统一 Adapter 接口**：不管底层是什么框架，外层都通过 `adapter.sh {mode} --message ...` 调用，返回 stdout + exit code
2. **环境变量注入**：`config.json` → `Path.expanduser()` 展开 `~` → 传给 adapter 子进程的 `env`
3. **Scheduler 策略差异化**：OpenClaw 用 `deferred`（QueueProcessor 轮询），Hermes/Letta 用 `realtime`（消息到达即处理）
4. **各自维护 adapter**：adapter.sh 由各 Agent owner 维护，不互相侵入

---

## 4. 通信层：Queue + Scheduler + HealthProbe

### Queue (FIFO, capacity=1000)

```
enqueue(msg) → queue 尾部
dequeue()    → queue 头部 (FIFO)
统计:  入队递增 enqueue_count
      出队递增 dequeue_count (ack 后重置)
      status: idle → processing (dequeue 时) → idle (ack 后)
```

### Scheduler (状态机)

```
状态: IDLE → BUSY → DEGRADE → OFFLINE
触发:
  - IDLE: 空闲，等待消息
  - BUSY: 正在处理，退避系数 1.5x (max 60s)
  - DEGRADE: adapter health 报 unhealthy，暂停调度
  - OFFLINE: 连接断开，等待重连

策略:
  - backend=realtime: 消息到达即 _try_dispatch
  - backend=deferred: HealthProbe 循环 5s 轮询 _try_dispatch
```

### HealthProbe (5s 独立 task)

```
每 5s 调 adapter.sh health
  → exit 0: 健康，state=idle，恢复调度
  → exit 1: 降级，state=degrade，暂停
  → exit 2: 挂了，state=offline

健康时: idle ∧ q>0 → _try_dispatch 出队处理
```

### 关键解耦规则

- **NATS 回调 < 10ms**: `_on_dm_msg` 只入队，不调度（避免串行化事件循环）
- **Scheduler 不自做判定**: 只消费 HealthProbe 的 StateReport
- **入队和调度解耦**: 两个独立 task，不互相阻塞

---

## 5. AIM Client 主进程

### 文件: `shared/aim/aim-client/main.py` (v1.0.0)

### 核心类

| 类 | 职责 |
|----|------|
| `AIMClient` | 主进程入口，NATS 连接、订阅、生命周期 |
| `QueueProcessor` | 轮询 `~/.openclaw/workspace/.aim-trigger`，直调 OpenClaw 生成 AI 回复 |
| `HealthProbe` | 5s 间隔调 `adapter.sh health` |
| `SingleInstance` | 单实例保证 (pgrep → 锁PID → flock) |
| `AdapterThread` | 线程池调 `adapter.sh process`，缓冲 9 |
| `SecurityModel` | 安全模型 (见 §6) |

### 启动流程

```
1. SingleInstance.acquire()       — 杀旧进程 + 获取锁
2. 加载 config.json               — agent_id, framework, adapter_cmd, env
3. 加载 identity.json             — execution_model, agent_name
4. 连接 NATS                      — JWT 认证 (creds 文件)
5. 订阅 DM + Group subjects       — aim.dm.ZS0001, aim.grp.grp_trio
6. 启动 HealthProbe task          — 5s 循环
7. 启动 QueueProcessor (可选)      — 2s 轮询
8. 进入事件循环                    — NATS await
```

### 命令行参数

```
python3 main.py \
  --agent-id ZS0001 \
  --config ~/.aim/agents/ZS0001/config.json \
  --mode direct              # direct | service
```

---

## 6. 安全模型 v1

### 文件: `shared/aim/aim-client/security.py`

```
功能:
  - Rate Limiting:   每 Agent 10 消息/s (滑动窗口)
  - Allowlist:       off (Phase 2 启用)
  - Auth:            只接受 JWT 认证连接
  - 拒绝处理:        记录 + drop (不回复 429)

注册接入 (aim.reg):
  - 格式: {"agent_id": "ZS000X", "agent_name": "...", ...}
  - 权限: aim.req.> (NATS 级控制)
```

### 准入标准

1. `agent_id` 必须匹配 `ZS\d{4}` 格式
2. `agent_name` 非空
3. 消息大小 ≤ 64KB
4. 非黑名单
5. Rate limit 内

---

## 7. Runtime 实现模式

### 进程生命周期

```
                    读取锁, pgrep
                        │ 扫旧进程
                        ▼
                ┌──────────────┐
                │ Single-      │
                │ Instance     │
                │ acquire()    │
                └──────┬───────┘
                       │ ✅ 获取锁
                       ▼
                ┌──────────────┐
                │ NATS 连接    │◄──── JWT 认证 (aim.creds)
                │ 订阅 DM/Grp  │
                └──────┬───────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
      HealthProbe  QueueProc   Event Loop
      (5s 循环)    (2s 轮询)   (NATS await)
           │           │           │
           └───────────┼───────────┘
                       │ on SIGTERM/SIGINT
                       ▼
                ┌──────────────┐
                │ Single-      │
                │ Instance     │
                │ release()    │
                └──────────────┘
```

### 启动方式

| Agent | 方式 | 管理 |
|-------|------|------|
| ZS0001 | `cd ~/shared/aim/aim-client && python3 main.py --agent-id ZS0001 --config ... --mode direct` | 手动从 shared/aim/ 启动 |
| ZS0002 | 同上 (ZS0002) | 手动从 shared/aim/ 启动 |
| ZS0003 | 同上 (ZS0003) | 手动从 shared/aim/ 启动 |

### 单实例保证 (三层防线)

```
层1: pgrep 扫描
  pgrep -f "main.py.*--agent-id {agent_id}"
  → 找到旧进程 → SIGTERM (3s) → SIGKILL → 继续

层2: 锁文件 PID 检查
  读 lock_file → 取 PID → os.kill(pid, 0) 确认存活
  → 存活 → SIGTERM → SIGKILL → unlink

层3: fcntl.flock (竞态兜底)
  两台同时启动时的微秒级窗口保护
```

---

## 8. 当前进程与部署

### 运行进程 (2026-06-17 12:50 CST)

| Agent | PID | 框架 | 实例数 | 启动时间 | adapter |
|-------|-----|------|--------|---------|---------|
| ZS0001 呱呱 | 34777 | OpenClaw | 1 | 12:47 | openclaw/adapter.sh |
| ZS0002 吉量 | 34422 | Hermes | 1 | 12:45 | hermes/adapter.sh |
| ZS0003 火鸡儿 | 98833 | Letta | 1 | 12:11 | letta/adapter.sh |

### NATS 连接

```
CID 476  AIM-ZS0003 (39m)
CID 490  AIM-ZS0002 (5m)
CID 494  AIM-ZS0001 (3m)
```

### 部署路径

```
shared/aim/
├── aim-client/         # 统一的 AIM Client 主进程 (所有 Agent 共用)
│   ├── main.py         # v1.0.0, 主入口
│   ├── security.py     # 安全模型 v1
│   ├── registry.py     # Registry 客户端
│   ├── group_admission.py
│   └── v3_compat.py    # V3 兼容
├── adapters/           # 各框架适配器
│   ├── openclaw/adapter.sh
│   ├── hermes/adapter.sh
│   └── letta/adapter.sh
├── nats-agent-v3/      # V3 NATS 适配 (历史)
├── docs/               # 文档
│   └── AIM-PROJECT-V1.0.0.md  ← 本文件
└── archive/            # 历史方案归档
```

### 各 Agent 本地配置

```
~/.aim/
├── agents/
│   ├── ZS0001/
│   │   ├── config.json   (framework=openclaw, queue_processor=true)
│   │   ├── identity.json
│   │   └── aim.creds     (JWT)
│   ├── ZS0002/
│   │   ├── config.json   (framework=hermes, queue_processor=false)
│   │   ├── identity.json
│   │   └── aim.creds
│   └── ZS0003/
│       ├── config.json   (framework=letta, queue_processor=false)
│       ├── identity.json
│       └── aim.creds
├── run/
│   └── aim-client-*.lock
└── adapters/
    ├── openclaw/adapter.sh
    └── hermes/adapter.sh
```

---

## 9. 关键 Bug 及修复

### B1: NATS 回调阻塞致 30-77s 延迟
- **根因**: `handle_message` 内调 `_try_dispatch`(3s) 堵塞 nats-py callback，后续消息串行化
- **修复**: 删除回调内调度，只入队(queue.enqueue)
- **文件**: `nats-agent-v3.py` 1行删除

### B2: ack 后状态残留 PROCESSING
- **根因**: ack 后 status 未重置为 idle
- **修复**: ack 重置 status="idle"
- **文件**: `queue.py` 1行

### B3: dequeue 计数不匹配致 q=1 永久残留
- **根因**: ack 未递减 dequeue_count
- **修复**: ack 时递减计数
- **文件**: `queue.py` 1行

### B4: 日志双写
- **根因**: FileHandler + StreamHandler(stderr) + nohup 2>&1 → 同一文件写两遍
- **修复**: 删 StreamHandler
- **文件**: `nats-agent-v3.py` 4行删除

### B5: macOS fcntl.flock + APFS inode 漂移
- **根因**: `open("w")` 替换 inode，flock 失效率上升，多实例并存
- **修复**: pgrep 扫描 → 锁PID检查 → flock 三层防线
- **文件**: `main.py` SingleInstance 类

### B6: pgrep 模式匹配失败
- **根因**: pgrep 搜 `aim-client` 但进程命令行是 `main.py`
- **修复**: 改为 `main.py.*--agent-id {agent_id}`
- **文件**: `main.py` 1行

### B7: exit=127 (Hermes adapter)
- **根因**: ZS0002 旧进程 (PID 49984) 未加载 expanduser 代码，HERMES_BIN=`~/.local/bin/hermes` 没展开
- **修复**: 杀旧进程，重启加载新代码（`Path.expanduser()` 展开 `~`）
- **文件**: `main.py` L424

---

## 10. P0→Phase 1 进展

### Phase 0 ✅ (2026-06-16 完成)

| 模块 | 负责人 | 状态 |
|------|--------|------|
| Queue + Scheduler + HealthProbe 三层解耦 | 呱呱 | ✅ V3 嵌入 |
| Adapter 4接口标准化 (process/health/info/cancel) | 三方 | ✅ |
| OpenClaw adapter (含 generate-reply) | 呱呱 | ✅ |
| Hermes adapter v1.2 | 吉量 | ✅ |
| Letta adapter v1.5 | 火鸡儿 | ✅ |
| AIM Client 主进程 v1.0.0 | 呱呱 | ✅ |
| QueueProcessor (OpenClaw AI 回复) | 呱呱 | ✅ |
| SingleInstance 三层防线 | 呱呱 | ✅ |
| 安全模型 v1 (Rate Limit + Auth) | 呱呱 | ✅ |
| Registry 客户端 + 群聊准入 | 呱呱 | ✅ |
| V3 兼容模式 + --services 守护 | 呱呱 | ✅ |
| Agent Card (SDK) | 吉量 | ✅ |
| Transport 抽象 7方法 | 吉量 | ✅ |
| 旧 cron 清理 (6a922050, 0e6e8bc0) | 呱呱 | ✅ 已禁用 |

### Phase 1 规划（待大哥确认启动）

| 模块 | 负责人 | 前置 |
|------|--------|------|
| Transport 7方法集成进 aim-client | 吉量 | transport.py ✅ |
| Agent Card 完整落地 (NATS KV) | 吉量 | SDK ✅ |
| Discovery 最小实现 | 吉量 | SDK list/fetch ✅ |
| 三级降级模型实现 | 火鸡儿 | Scheduler + Monitor |
| Adapter cancel 标准化 | 火鸡儿 | Letta adapter |
| 端到端场景测试 | 火鸡儿 | 前5项完成 |

### 已知问题

| 问题 | 状态 |
|------|------|
| ZS0003 Letta ENOENT: /.letta | ⚠️ 已通知火鸡儿，待排查 |
| Phase 1 启动确认 | ⏸ 待大哥确认 |

---

## 11. 三方分工

| 领域 | 🐸 呱呱 (ZS0001) | 🐴 吉量 (ZS0002) | 🐤 火鸡儿 (ZS0003) |
|------|----------------|----------------|-------------------|
| **通信层** | Queue + Scheduler + QueueProcessor | Transport 抽象 | Adapter 标准化 |
| **协议层** | NATS 服务端配置 | SDK + Agent Card | 降级模型 |
| **应用层** | aim-client 主进程 (main.py) | Observer/Monitor | 端到端测试 |
| **安全** | SecurityModel v1 (主责) | 协议安全审查 | 渗透测试 |
| **适配器** | OpenClaw adapter | Hermes adapter | Letta adapter |
| **记忆** | 记忆管理 + 模式检测 | 记忆优化 | — |
| **基建** | 进程/单实例/部署 | — | 多框架适配 |

### 协作规则
- 自己修自己问题 → 先自查代码/日志
- 通知修别人问题 → 溯源消息ID → 引代码行号
- 省 token 优先 → 直奔主题
- **跨 Agent 通信统一走 AIM (NATS inbox)**，禁止 agent_bus

---

## 12. 关键文件索引

### 主入口

| 路径 | 描述 |
|------|------|
| `~/shared/aim/aim-client/main.py` | AIM Client 主进程 (v1.0.0) |

### 适配器

| 路径 | 描述 |
|------|------|
| `~/.aim/adapters/openclaw/adapter.sh` | OpenClaw 适配器 (v1.3, 5 模式) |
| `~/shared/aim/adapters/hermes/adapter.sh` | Hermes 适配器 (v1.2, 4 模式) |
| `~/shared/aim/adapters/letta/adapter.sh` | Letta 适配器 (v1.2, 4 模式) |

### NATS

| 路径 | 描述 |
|------|------|
| `~/.openclaw/config/nats-server.conf` | NATS Server 配置 (JWT Operator) |
| `~/.aim/bin/aim_nats_sdk.py` | AIM NATS SDK |

### 安全

| 路径 | 描述 |
|------|------|
| `~/shared/aim/aim-client/security.py` | 安全模型 v1 |
| `~/shared/aim/aim-client/registry.py` | Registry 客户端 |
| `~/shared/aim/aim-client/group_admission.py` | 群聊准入 |

### 文档

| 路径 | 描述 |
|------|------|
| `~/shared/aim/docs/AIM-PROJECT-V1.0.0.md` | 本文档 |
| `~/shared/aim/docs/AIM-NATS-PROTOCOL.md` | NATS 协议规范 |
| `~/shared/aim/docs/OAS-DESIGN.md` | OAS 设计 v1.2 |
| `~/shared/aim/docs/AIM-STANDARD-INTERFACE-PROPOSAL.md` | 标准接口提案 |

### 项目记忆 (呱呱)

| 路径 | 描述 |
|------|------|
| `~/.openclaw/workspace/memory/projects/aim-project.md` | AIM 项目记忆 |
| `~/.openclaw/workspace/memory/projects/DECISIONS.md` | 跨 session 决策 |
| `~/.openclaw/workspace/memory/projects/TEAM.md` | 团队信息 |
| `~/.openclaw/workspace/memory/projects/STATUS.md` | 项目状态 |

---

## 13. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-06-17 | 本版本记录成立。Phase 0 完成，Queue+Scheduler+HealthProbe V3 嵌入，AIM Client v1.0.0，安全模型 v1，QueueProcessor，SingleInstance 三层防线，cron 清理 |
| v0.9 | 2026-06-16 | Phase 0 V3 嵌入完成，4 Bug 修复 (日志/延迟/退出/订阅) |
| v0.8 | 2026-06-15 | 大哥制定 NATS 权限 |
| v0.7 | 2026-06-14 | Phase 0 设计定稿 v1.2 |
| v0.6 | 2026-06-13 | Phase 0 V3 嵌入启动，JWT 认证切换，OAS v1.2 设计，授权推进规则 |
| v0.5 | 2026-06-10 | Phase 2.3 Server 瘦身验证，三方群聊全通 |
| v0.4 | 2026-06-09 | Adapter 集成测试 10/10，Phase 2 三方联调，NATS 全量上线 |
| v0.3 | 2026-06-08 | NATS POC 4项通过，AIM NATS 协议规范，SDK 建立 |
| v0.2 | 2026-06-05 | 旧 Message Bus (port 1883) 废弃，迁移至 NATS |
| v0.1 | 2026-06-01 | Agent 三方组建，P0-P4 基础通信建立 |
