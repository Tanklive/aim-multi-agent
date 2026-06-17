# AIM Status Feedback 最终方案

> 设计：呱呱 🐸 + 吉量 🐴
> 日期：2026-06-07
> 协议版本：aim-status-v1
> 状态：待大哥审批

---

## 核心目标

AIM 从「消息管道」升级为「Agent 实时会话可观测总线」。
Agent AI 处理消息的过程（分析、查询、推理、结论）实时可见，不是黑盒。

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        大哥的视角                                    │
│                                                                     │
│  终端窗口1                 终端窗口2                                 │
│  ┌──────────────────┐    ┌──────────────────┐                      │
│  │ 吉量 CLI 会话    │    │ aim watch ZS0001 │                      │
│  │ (任务沟通/安排)   │    │ (实时看呱呱处理) │                      │
│  │ 大哥←→吉量对话   │    │                  │                      │
│  └────────┬─────────┘    └────────┬─────────┘                      │
└───────────┼──────────────────────┼──────────────────────────────────┘
            │  handler 通道        │  observer 通道（只收不发）
            ▼                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        AIM Server (node.py)                         │
│                                                                     │
│   ┌──────────────────────────────────────────────────────────┐     │
│   │              observer_bindings 路由表                    │     │
│   │  ZS0001 → [observer_A(watch窗口), observer_B]           │     │
│   │  ZS0002 → [observer_C]                                  │     │
│   │  收到 status_feedback → 查 from → 定向推给绑定的observer │     │
│   └──────────────────────────────────────────────────────────┘     │
│                                                                     │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│   │消息路由   │  │observer │  │超时检测  │  │频率限制  │         │
│   │_deliver()│  │绑定管理  │  │60s无更新 │  │3条/s/agent│         │
│   └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│                                                                     │
│   Agent 崩溃清理：重连时 session_cleanup → 清理旧 in-flight session │
│   Observer 断连恢复：last_seq + status_log.jsonl 回放              │
│                                                                     │
└────────────────────┬────────────────────────────────────────────────┘
                     │  AIM 消息（现有协议不变）
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │呱呱Agent  │ │吉量Agent │ │小火鸡儿  │
  │ZS0001    │ │ZS0002    │ │ZS0003    │
  │aim-agent │ │aim-agent │ │aim-agent │
  └──────────┘ └──────────┘ └──────────┘
```

---

## 三条路径清晰分开

| 路径 | 内容 | 流向 | 改动 |
|------|------|------|------|
| **大哥↔吉量CLI** | 任务沟通、安排（主会话） | 大哥终端 ←→ 吉量 AI | 不改 |
| **aim watch** | 实时看Agent AI处理过程 | Server → observer 通道 | 新增 |
| **AIM 消息** | Agent间通信 | Agent ↔ Server ↔ Agent | 不变 |

---

## 推送方式

**WS 实时推送（主） + status_log.jsonl 持久化（兜底）。**

```
Agent 产生 status_feedback
  ├── 1. WS 推送给 Server（实时）
  │      └── Server 转发给 observer（实时）
  └── 2. 同时追加写入 status_log.jsonl（持久化）
         └── observer 重连时，从文件回放断连期间的状态
```

**Observer 重连恢复：**
- Server 维护每个 observer 的 `last_seen_seq`（类似 offset）
- observer 重连时带上 `last_seq`
- Server 从 status_log.jsonl 找到断连期间条目补推

---

## Observer 通道

| 特性 | 说明 |
|------|------|
| 方向 | 只收不发（单向监听） |
| Handler 选举 | 不参与（observer 永远不是 handler） |
| 绑定关系 | 连接时指定 `watch_target`（ZS0001/ZS0002/ZS0003） |
| 多对一 | 支持多个 observer watch 同一个 target |
| 连接池 | 独立计数，不占 main channel 配额 |

**协议字段：**
```json
{
  "cmd": "auth",
  "agent_id": "ZS0002",
  "channel": "observer",
  "watch_target": "ZS0001",
  "verbose": false
}
```

---

## Status Feedback 协议

**复用现有 WS 连接，不新建连接池。** 通过 `msg_type` 字段区分消息类型。

```json
{
  "msg_type": "status_feedback",
  "protocol_version": "aim-status-v1",
  "from": "ZS0001",
  "session_id": "msg_xxx",
  "step": "web_fetch",
  "status": "running",
  "progress": "正在抓取 URL: https://...",
  "duration_ms": 1200,
  "timestamp": 1717737600
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| msg_type | ✅ | 固定 `status_feedback`，与普通消息区分 |
| protocol_version | ✅ | `aim-status-v1`，方便后续迭代 |
| from | ✅ | 发送方 agent_id |
| session_id | ✅ | 关联原始 msg_id，用于并发任务隔离 |
| step | ✅ | 当前步骤名（task_start / memory_search / web_fetch / reasoning / code_exec / task_end / still_working） |
| status | ✅ | running / completed / error / timeout |
| progress | ❌ | 可选，人类可读的进度描述 |
| duration_ms | ❌ | 可选，当前步骤已执行耗时 |
| timestamp | ✅ | Unix 时间戳 |

---

## 推理链策略

| 模式 | 行为 |
|------|------|
| 默认模式 | 推但不展示推理链内容，observer 端只显示 step + status |
| Verbose 模式 | observer 展示推理摘要（大哥 `/verbose on`，或 observer 连接时带 `verbose: true`） |
| 作用域 | 全局开关 + observer 连接时单独覆盖 |

**原则：大哥看的是进度和结论，不是 AI 在想什么。**

---

## 节流策略

按步骤类型区分 + 最大静默期兜底：

| 步骤类型 | 示例 | 推送策略 |
|----------|------|----------|
| 快步骤（<3s） | memory_search, db_query | 不推送 |
| 长步骤（≥3s） | reasoning, web_fetch, code_exec | 必须推送 |
| 关键步骤 | task_start, task_end | 始终推送 |
| 静默兜底 | 距离上次推送 >5秒 | 强制推一次（即使是快步骤） |
| 长任务保活 | 持续 >30s 的任务 | 每 30s 发一次 still_working heartbeat |

**效果：大哥永远不会看到超过 5 秒的空白期。**

---

## 并发隔离

Agent 可能同时处理多条消息。用 **session_id = 原始 msg_id** 区分：

```json
// Agent 同时处理 msg_abc 和 msg_def
{"from":"ZS0001","session_id":"msg_abc","step":"reasoning","status":"running"}
{"from":"ZS0001","session_id":"msg_def","step":"web_fetch","status":"running"}
```

Observer 端按 `[session_id]` 前缀分组展示：

```
[13:22:05] ZS0001 ▸ [msg_abc] memory_search ✅ (0.8s)
[13:22:06] ZS0001 ▸ [msg_def] web_fetch    🟡 running...
[13:22:08] ZS0001 ▸ [msg_abc] reasoning    🟡 running...
```

---

## 超时清理

| 机制 | 说明 |
|------|------|
| 检测条件 | 同一 session_id **无任何 status_feedback 更新**超过 60s |
| 操作 | Server 自动推送 timeout 给绑定 observer |
| 长任务保活 | Agent 每 30s 发 `still_working` heartbeat，不会触发超时 |
| 崩溃清理 | Agent 重连时发 `session_cleanup` 命令，Server 清理该 Agent 的旧 in-flight session，标记为 interrupted |

---

## 频率限制

| 限制 | 行为 |
|------|------|
| 同一 agent_id 每秒 ≥3 条 status_feedback | 丢弃超出的条目，记 warning 日志 |
| 丢弃条目标记 | `dropped: true`，observer 端显示"部分事件已节流" |

---

## 文件管理

| 文件 | 策略 |
|------|------|
| status_log.jsonl | 按天轮转：`status_log.2026-06-07.jsonl` |
| 保留周期 | 最近 7 天，过期自动清理 |
| 单文件上限 | 10MB 强制轮转 |

---

## 实施计划

| 步骤 | 负责人 | 改动量 | 内容 |
|------|--------|--------|------|
| 1. Server observer 绑定 | 吉量 | ~30 行 | observer_bindings 路由表 |
| 2. Server status_feedback 路由 | 吉量 | ~30 行 | msg_type 分支 + 转发逻辑 |
| 3. Server 超时检测 | 吉量 | ~40 行 | 60s watchdog + timeout 推送 |
| 4. Server 重连回放 | 吉量 | ~40 行 | last_seq + jsonl 回放 |
| 5. Server 频率限制 | 吉量 | ~20 行 | 3条/s/agent 丢弃+dropped标记 |
| 6. Server session_cleanup | 吉量 | ~20 行 | Agent 重连时清理旧 session |
| 7. Agent 状态回推逻辑 | 呱呱 | ~50 行 | 快慢判断 + tool call 前后插入上报 |
| 8. Agent status_log.jsonl 写入 | 呱呱 | ~20 行 | 追加写入 + 按天轮转 |
| 9. Observer 连接 + aim watch 展示 | 呱呱 | ~80 行 | observer 通道 + 终端展示 |
| 10. 联调测试 | 呱呱+吉量 | — | 全链路 7 项验证 |

**总计：Server ~180 行，Agent ~150 行，Observer/CLI ~80 行。**

---

## 联调测试项

1. ✅ Observer 正常连接并绑定 target
2. ✅ Target 发 status_feedback → Observer 实时收到
3. ✅ 快步骤不推，长步骤推，>5s 静默强制推
4. ✅ Observer 断连 → last_seq 重连回放
5. ✅ Agent 崩溃 → 重连后 session_cleanup → 旧 session 标记 interrupted
6. ✅ 长任务（2min+）→ still_working heartbeat → 不触发超时
7. ✅ 频率限制 → 超3条/s 丢弃 + dropped 标记
8. ✅ 多条并发 → session_id 隔离，分组展示

---

## aim watch 输出效果

```
─────────────────────────────────────────────
aim watch ZS0001
─────────────────────────────────────────────
[13:22:05] ZS0001 收到 ZS0002 消息: "查一下..."
[13:22:06] ZS0001 ▸ [msg_abc] memory_search    ✅ done    (0.8s)
[13:22:08] ZS0001 ▸ [msg_abc] web_fetch        🟡 running (已过 2s)
[13:22:12] ZS0001 ▸ [msg_abc] reasoning        🟡 running (已过 4s)
[13:22:15] ZS0001 ▸ [msg_abc] task_end          ✅ done    (总耗时 10s)
[13:22:15] ZS0001 ▸ [msg_abc] 结论: "查到了，结果是..."
─────────────────────────────────────────────
```

---

## 同类方案参考

| 方案 | 相似点 | 差异点 |
|------|--------|--------|
| **SSE (Server-Sent Events)** | 单向实时推送，断连自动重连 | Web 原生，AIM 已有 WS |
| **GitHub Actions 实时日志** | 步骤级执行状态展示 | 构建流水线，不是 Agent |
| **Docker logs -f** | 实时日志流 | 日志，不是步骤追踪 |
| **Temporal.io** | 步骤级状态追踪 | 太重，企业级引擎 |

**最接近 SSE 思路**——单向推送 + 断连重连 + 事件流。AIM 已有 WS 连接，直接复用，不另起协议。

---

## 风险 & 缓解

| 风险 | 缓解 |
|------|------|
| status_feedback 频率过高 | 节流 + 频率限制 3条/s/agent |
| Observer 断连丢状态 | last_seq + jsonl 回放 |
| Agent 崩溃后状态卡住 | 60s 超时 + 重连 session_cleanup |
| 长步骤假死 | still_working heartbeat 每 30s |
| 多条并发混淆 | session_id = msg_id 隔离 |

---

**请大哥审批。**
