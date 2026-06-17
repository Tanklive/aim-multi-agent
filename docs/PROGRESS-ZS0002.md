# AIM 项目进展汇总 — 吉量 🐴（Hermes）

> 2026-06-17 | 按大哥要求整理
> 基于：AIM架构评审与定位细化.md → aim-client-unified-v1.md → aim-client-division.md

---

## 一、框架信息

| 维度 | 说明 |
|------|------|
| Agent ID | ZS0002 |
| 昵称 | 吉量 |
| 框架 | Hermes Agent |
| 框架类型 | CLI Agent（hermes chat -q 即时回复） |
| execution_model | realtime |
| adapter 路径 | `~/shared/aim/adapters/hermes/adapter.sh` |
| 进程入口 | `aim-client/main.py`（已从 nats-agent-v3 迁移） |
| 保活方式 | launchd（com.aim.agent.ZS0002） |
| 当前状态 | ✅ 运行中 |

---

## 二、完成的功能模块

### P0 — 全部完成 ✅

| 模块 | 交付物 | 标准 |
|------|--------|------|
| Monitor + Observer 改造 | SDK `emit_obs` + `emit_state_report` | 7种事件（含 received/completed/heartbeat） |
| Adapter health + info | Hermes adapter.sh v1.2（4接口） | process/health/info/cancel + exit code 语义 |
| Agent Card + execution_model | SDK `load_agent_card()` + `identity.json`（realtime） | Schema v1：serial/execution_model/lifecycle/capabilities |
| V3 集成 | nats-agent-v3.py 启动时加载 Agent Card + KV publish | 自动调 adapter.sh info → set_runtime_info → load_agent_card → publish_agent_card |

### P1 — 全部完成 ✅

| 模块 | 交付物 | 说明 |
|------|--------|------|
| Transport 7 方法抽象 | `transport.py`（NATSTransport 实现） | connect/disconnect/authenticate/verify_peer/subscribe/publish/request |
| Agent Card 落地 + Discovery | SDK `publish_agent_card()` / `fetch_agent_card()` / `list_agent_cards()` | NATS KV `aim-cards` bucket |
| Message/Task 分层 | `AIMMessage` + `AIMTask` dataclass | 即发即收 vs 有状态任务 |
| SDK load_global_config() | 从 aim.json 读取全局配置，环境变量覆盖 | 三层 fallback |

### 代码修复

| 修复 | 原因 | 状态 |
|------|------|------|
| `_call_adapter` 读 stdout + exit code 语义 | 原实现不读 stdout（回复丢失）+ 统一抛 RuntimeError | ✅ 已修 |
| 探针间隔固定数组 | 原乘法退避 1.5x 无联调验证，对齐火鸡儿规则文档 | ✅ 已修 |
| Scheduler `on_degrade()` 重复计数 | `on_degrade()` 和 `update_state(OFFLINE)` 都加计数 | ✅ 已修 |
| SDK subscribe 回调 `_wrap_coro` | async 函数被 nats-py 当 sync 调，不 await | ✅ 已修 |
| Hermes adapter health | pgrep 模式不匹配 + PATH 环境变量问题 | ✅ 已修 |

---

## 三、架构模式（Hermes 接入方式）

### 通信链路

```
NATS ──→ aim-client/main.py ──→ adapter.sh ──→ hermes chat -q
  ↑                                          │
  └──────────────── reply ───────────────────┘
```

### 消息处理流程

```
收到消息 → DM/GRP 回调
  → 入 MessageQueue
  → Scheduler 根据 StateReport（来自 HealthProbe）决定是否投递
  → _call_adapter 调 adapter.sh process
  → adapter.sh 调 hermes chat -q 获取 AI 回复
  → stdout 捕获回复文本
  → transport.send_dm/send_grp 发送回复
  → Observer 事件（received/completed）
```

### adapter.sh 接口

| 接口 | 功能 | 退出码 |
|------|------|--------|
| process --message --from | 处理消息，返回 AI 回复 | 0=正常, 1=可重试, 2=降级, 3=人工介入 |
| health | 健康探针 | 0=健康, 1=降级, 2=挂了 |
| info | Runtime 元信息 | 0=正常 |
| cancel --task-id | 取消任务（Hermes realtime 模式下不可取消） | 0=已取消, 2=无法取消 |

---

## 四、异构设计思路（Hermes 与其他框架的差异）

| 维度 | Hermes（吉量） | OpenClaw（呱呱） | Letta（火鸡儿） |
|------|---------------|-----------------|----------------|
| **框架类型** | CLI Agent，单次 chat -q 即时返回 | CLI + Gateway 双模式 | 独立 Letta Server + TUI |
| **execution_model** | realtime | realtime | deferred |
| **adapter process** | `hermes chat -q` 同步调用，即时回复 | 文件队列 + 轮询回执 | `letta -p` subprocess，排队等待 |
| **health 判断** | hermes CLI 可达 + hermes 进程存活 | OpenClaw gateway 进程存活 | letta CLI 可用 + agent ID 验证 |
| **超时策略** | 120s adapter_timeout | 15s 短超时（realtime 快速失败） | 30s PROBE_TIMEOUT + 120s 兜底 |
| **cancel** | realtime 无取消语义 | no-op（已投递到会话无法撤回） | 不支持取消（deferred 排队模式） |

### Hermes 的独特设计点

1. **realtime 即时回复** — hermes chat -q 是进程内同步调用，无排队等待，适合低延迟场景
2. **无状态 CLI** — 无需 session 管理，每次调用独立
3. **adapter.sh 过滤噪声** — AI 回复中过滤 `⚠️` 前缀行、session_id 等 Hermes 特有输出
4. **配置变量化** — 通过 `HERMES_BIN` 环境变量指定 CLI 路径，不依赖 PATH

---

## 五、配置变量化（全局统一）

### 分层配置

```
~/.aim/config/aim.json          ← 全局：NATS、信任域、默认群、路径
~/.aim/agents/{ID}/config.json  ← Agent 级：adapter路径、execution_model
~/.aim/agents/{ID}/identity.json ← 身份卡：serial、name、capabilities
环境变量                          ← 运行时覆盖
```

### 环境变量

| 变量 | 覆盖字段 | 默认值 |
|------|---------|--------|
| AIM_NATS_URL | nats_server | nats://127.0.0.1:4222 |
| AIM_HOME | paths.aim_root | ~/.aim |
| AIM_SHARED | paths.shared | ~/shared/aim |
| AIM_AGENT_ID | agent_id | ZS0001 |
| AIM_DEFAULT_GROUP | default_group | grp_trio |
| AIM_TRUSTED_PEERS | trusted_peers | ZS0001,ZS0002,ZS0003 |
| HERMES_BIN | （框架特有） | /Users/xxx/.local/bin/hermes |

---

## 六、决策依据

所有开发决策基于以下层级回溯：

```
原始需求
  "兼容天下，不改 Agent 架构，安装 AIM Client 即可接入"
  → 规划（AIM架构评审与定位细化.md）
    → 方案（aim-client-unified-v1.md）
      → 分工（aim-client-division.md）
        → 标准（AIM-COMMUNICATION-STANDARD.md）
          → 架构红线（AIM Client ≠ Runtime，只负责通信不负责思考）
```

---

## 七、待办

| # | 事项 | 依赖 |
|---|------|------|
| 1 | 三方联调（5轮） | Registry 恢复 + 火鸡儿在线 |
| 2 | 通信规范文档定稿 | 大哥确认 |
