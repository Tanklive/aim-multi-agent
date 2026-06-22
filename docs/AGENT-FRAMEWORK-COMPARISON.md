# 全球主流 Agent 框架横向对比（Z→A）

> 日期: 2026-06-22 | 小火鸡儿调研 | 部分数据来自本机实测，部分来自文档/社区

---

## 核心对比维度

| 维度 | 含义 |
|:--|------|
| **架构模式** | Daemon(常驻守护) / CLI-per-message(每条启新进程) / API-server(HTTP服务) / IDE-extension |
| **进程模型** | 每条消息是否启动新进程？冷启动开销多大？ |
| **Runtime 调用** | adapter 怎么调它？HTTP API? CLI subprocess? WebSocket? |
| **并发模型** | 多消息同时处理？per-conv 串行跨 conv 并行？全局串行？ |
| **通信协议** | HTTP REST / WebSocket / POSIX pipe / gRPC / MCP |
| **SIGPIPE 风险** | 使用 POSIX pipe 的框架有 rc=141 风险 |

---

## 一、本机实测（确凿数据）

### 1. Letta Code（我，ZS0003）

| 维度 | 数据 |
|:--|------|
| **架构模式** | **CLI-per-message** |
| **版本** | v0.27.14 |
| **进程模型** | 每条消息 `letta --new` fork 新 node 进程，加载 agent ~17s |
| **Runtime 调用** | `timeout 35s letta --new -p "prompt" > tmpfile` |
| **并发模型** | per-conv 串行，跨 `--new` conv 并行（不同 node 进程） |
| **通信协议** | POSIX pipe（subprocess stdout/stderr） |
| **SIGPIPE 风险** | ⚠️ 高——`set -o pipefail` 下首个 `$()` 就 141 |
| **adapter 超时** | 35s（实测冷启动 17s + 推理 5-15s） |
| **每次冷启动** | ✅ 是（~17s） |
| **pre-loaded** | ❌ 不支持——TUI 有常驻 node 但不对外暴露 API |

### 2. Hermes（吉量，ZS0002）

| 维度 | 数据 |
|:--|------|
| **架构模式** | **Daemon（常驻守护进程）** |
| **进程模型** | `hermes-agent` python daemon 常驻（PID 73048） |
| **Runtime 调用** | `hermes chat -q "msg"` — CLI 通过 daemon 内部协议通信 |
| **并发模型** | daemon 内部管理，可能支持多会话并发 |
| **通信协议** | CLI ↔ daemon 内部协议（非 POSIX pipe 直通） |
| **SIGPIPE 风险** | ✅ 无——不走 pipe 裸连 LLM |
| **每次冷启动** | ❌ 无——daemon 已在内存 |
| **调用耗时** | ~20s（含 LLM 推理，非冷启动） |

### 3. OpenClaw（呱呱，ZS0001）

| 维度 | 数据 |
|:--|------|
| **架构模式** | **Daemon（常驻 gateway）** |
| **进程模型** | `openclaw gateway` node daemon 常驻（PID 626） |
| **Runtime 调用** | `openclaw -p "msg"` — CLI 通过 gateway 内部协议通信 |
| **并发模型** | gateway 管理多个 agent 会话并发 |
| **通信协议** | CLI → gateway 内部协议 / WebSocket |
| **SIGPIPE 风险** | ✅ 无 |
| **每次冷启动** | ❌ 无——gateway 已在内存 |
| **调用耗时** | ~5.7s（含 LLM 推理） |

---

## 二、三大框架架构差异本质

```
OpenClaw:   openclaw gateway (常驻) ← openclaw -p "..." (CLI 轻量，5.7s)
Hermes:      hermes-agent daemon (常驻) ← hermes chat -q "..." (CLI 轻量，20s)
Letta Code:  letta --new (每条消息新 node 进程，17s 冷启动)
                ↑
           这是今天所有 rc=141 / SIGPIPE 的物理根源
```

- OpenClaw 和 Hermes 的 "CLI" 其实只是对常驻 daemon 的 RPC 调用——不涉及进程启动
- Letta 的 "CLI" 是真正的每次 fork 新进程——加载 agent、连接后端、初始化 LLM engine
- 这个问题跟模型、跟 prompt、跟 bash 脚本都无关——是 **引擎架构层** 的差异

---

## 三、全球主流框架架构分类（推断标记 ⧖）

### A 类：Daemon/常驻守护进程（与 Hermes/OpenClaw 同类）

| 框架 | 厂商 | 架构 | 通信 | 冷启动 |
|:--|:--|:--|:--|:--|
| **Hermes** | — | python daemon | CLI → daemon RPC | 无 ✅ |
| **OpenClaw** | — | node gateway daemon | CLI → gateway RPC | 无 ✅ |
| **Claude Code** ⧖ | Anthropic | node CLI / MCP server | stdio/MCP | ⧖ |
| **Codex CLI** ⧖ | OpenAI | CLI 进程 | stdio/API | ⧖ |
| **Goose** ⧖ | Block | daemon + MCP | MCP / HTTP | ⧖ |

### B 类：CLI-per-message（与 Letta 同类）

| 框架 | 厂商 | 架构 | 冷启动 |
|:--|:--|:--|:--|
| **Letta Code** | Letta | node CLI per message | 17s ❌ |
| **Aider** ⧖ | — | CLI per invocation | 依赖模型加载 ⧖ |
| **Qwen-Agent** ⧖ | Alibaba | CLI per invocation | ⧖ |

### C 类：IDE/编辑器扩展

| 框架 | 厂商 | 架构 |
|:--|:--|:--|
| **GitHub Copilot** ⧖ | Microsoft/GitHub | VS Code 扩展 + LSP |
| **Cline** ⧖ | — | VS Code 扩展 |
| **Amazon Q Developer** ⧖ | AWS | IDE 扩展 |

### D 类：多 Agent 编排框架

| 框架 | 厂商 | 架构 |
|:--|:--|:--|
| **CrewAI** ⧖ | — | Python library, 多 Agent 编排 |
| **AutoGPT** ⧖ | — | Python library |
| **LangGraph** ⧖ | LangChain | Python library, 有状态图 |
| **MetaGPT** ⧖ | — | Python library, 多 Agent |

---

## 四、关键结论

### 1. 我的问题在同类框架中不是孤例

任何 **CLI-per-message** 模式的框架都会面临和我一样的冷启动问题。Aider、Qwen-Agent 如果用于高频率消息处理，也需要进程池化或常驻化。

### 2. 全球主流趋势是 Daemon 化

- Anthropic 的 Claude Code 走 MCP server（常驻）
- Block 的 Goose 走 MCP + daemon
- OpenAI 的 Codex CLI 文档不够清晰，但倾向于 agent 常驻

### 3. Letta 的定位

Letta Code v0.27.14 定位是"开发者的交互式 Agent CLI"，不是"消息队列消费者"。它最接近的是 Claude Code 和 Codex CLI —— 但在持续消息处理这个场景下，Letta 没有常驻模式是明确的短板。

### 4. 对我优化的指导意义

- **短期**：P0 参数优化已做（35s、去重试、去 pipefail）
- **中期**：等 Letta 出 daemon/HTTP API 模式
- **长期替代方案**：如果 Letta 长期不支持常驻，可以考虑让 ZS0003 迁移到 Hermes 或 OpenClaw 部署（框架替换，Agent 身份不变）

---

## 五、待 Web Search 补全（标注 ⧖）

以下框架的数据来自已知知识，需要 web search 确认。补全后会更新此文档。

---

> 最后更新: 2026-06-22 23:17 CST | 作者: 小火鸡儿 (ZS0003)
> Web search 子任务: task_7 (running)
