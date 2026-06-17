# AIM Watch 标准方案 v1.0 — 适配 TOP10 Agent 框架

> **版本**：v1.0 | **日期**：2026-06-10
> **作者**：吉量 🐴 (ZS0002)
> **状态**：方案草案，待三方评审
> **文档位置**：`~/shared/aim/PLAN-aim-watch-standard-v1.md`

---

## 一、问题定义

### 1.1 需求

大哥要求 aim-watch 是 **AIM 客户端标配功能**，装完 AIM 就能用。功能包括：

- Agent 通过 CLI/TUI 对话时，**AI 的自动处理过程**在 aim-watch 中实时显示（只读）
- 支持所有 Agent 框架（Hermes/OpenClaw/Letta...扩展到 TOP10）
- 任何框架都能用同一套协议投递事件
- aim-watch 本身不依赖任何特定框架

### 1.2 当前现状

| 现状 | 问题 |
|------|------|
| 已有 3 个 aim-watch.py 版本（WS版旧/NATS版/src版） | 版本分裂，功能不一致 |
| 已有 AIMObserverClient（SDK 只读客户端） | 已存在但 aim-watch.py 没直接用它 |
| 已有 emit_obs 标准事件链（7种） | 三方实现程度不同 |
| 已有 JetStream aim-observations stream | 已配置 |
| 已有 --history 回放功能 | 基础实现 |

### 1.3 冲突分析 — 为什么现有版本不统一

现有 aim-watch 实现之间有三个冲突点：

1. **订阅粒度**：呱呱 spec 主张 `aim.obs.>`（只看 Observer 事件），WS 版旧 aim-watch 监听所有消息 + status_feedback
2. **事件源**：Observer 事件 vs 原始消息 + 中间状态——哪个才是"AI 处理过程"的准确表达？
3. **架构**：自建 NATS 连接 vs 复用 AIMObserverClient（SDK 封装）

**方案定位结论**：
- Observer 事件是唯一可信源——它们由 nats-agent.py 在关键环节 emit，是 AI 处理过程的精确描述
- 原始消息（aim.dm/aim.grp）是补充——显示消息发收，但不显示内部处理细节
- SDK 的 AIMObserverClient 已封装好只读连接、重连、worker 池——直接复用

---

## 二、TOP10 Agent 框架适配方案

### 2.1 目标框架

| # | 框架 | 当前状态 | AIM 接入方式 | observer 事件 | 适配难度 |
|---|------|---------|-------------|--------------|---------|
| 1 | **Hermes**（吉量） | ✅ 已有 | aim_agent_nats.py + FrameworkCLI | ✅ partial (processing/completed/error/heartbeat) | — |
| 2 | **OpenClaw**（呱呱） | ✅ 已有 | nats-agent.py + FrameworkCLI | ✅ full (7种) | — |
| 3 | **Letta**（小火鸡儿） | ✅ 已有 | nats-agent.py + handler.sh | ✅ full (7种) | — |
| 4 | **CrewAI** | 🔄 规划中 | nats-agent.py + handler.sh | handler.sh 中 emit 关键事件 | 低 |
| 5 | **LangGraph** | ❌ 未接入 | handler.sh 回调 | handler.sh 中 emit | 低 |
| 6 | **AutoGen** | ❌ 未接入 | handler.sh 回调 | handler.sh 中 emit | 低 |
| 7 | **Semantic Kernel** | ❌ 未接入 | handler.sh 回调 | handler.sh 中 emit | 低 |
| 8 | **Dify** | ❌ 未接入 | handler.sh 回调 / webhook | handler.sh 中 emit | 低 |
| 9 | **Coze** | ❌ 未接入 | 外部 WebSocket | 不支持 observer | 高 |
| 10 | **Dify Workflow** | ❌ 未接入 | webhook → handler | handler 中 emit | 中 |

### 2.2 适配分层

```
AIM Watch  — 终端 TUI / JSON 输出
     │
     ├── AIMObserverClient (SDK 只读层)
     │    ├── 连接 NATS（只读 Token）
     │    ├── 订阅 aim.obs.>（所有 Observer 事件）
     │    ├── 订阅 aim.dm.> / aim.grp.>（消息收发，可选）
     │    └── 订阅 JetStream aim-observations（历史回放）
     │
     ├── emit_obs() API (SDK 发布层)
     │    ├── 由 SDK 实现（限流 + 双发：JS 持久化 + raw 实时）
     │    ├── 所有框架统一调用
     │    └── 1 条 emit = 1 个 Observer 事件
     │
     └── 框架适配层（事件来源）

Hermes:    framework_cli.py 的 _call_ai() 中 hook
OpenClaw:  framework_cli.py 或 nats-agent.py 中 hook
Letta:     nats-agent.py _process_message 中 hardcode emit
CrewAI:    nats-agent.py _process_message 中 hardcode emit
handler:   框架通用的 handler.sh 在关键步骤 pip emit 到 SDK
```

### 2.3 框架无关的适配原则

1. **所有框架共用同一个 SDK** `emit_obs()` 接口
2. **有 AI 框架调用能力的**：在框架调用前后 emit `ai_start` / `ai_done` / `ai_empty`
3. **只有 handler.sh 回调的**：handler.sh 不能直接 emit（Python SDK 依赖），由 nats-agent.py 在调用 handler 前后 emit
4. **不提供 AI 框架的**（纯转发 Agent）：不 emit AI 事件，只 emit 消息生命周期事件

**关键结论：所有适配在 nats-agent.py 层完成，框架自己不需要改代码。**

---

## 三、Observer 事件标准（最终版）

### 3.1 事件链（由呱呱实测通过 + 三方对齐）

```
标准事件链（11种，分3层）：

消息生命周期层（必选，所有框架必须实现）：
  📥 received      →  已收到消息，去重检查通过
  ⚙️ processing   →  进入处理流程（调用 AI 前）
  ✅ completed    →  回复已发送
  ❌ error        →  异常捕获（处理失败、回复发送失败等）

AI 处理层（可选，有 AI 能力的框架实现）：
  🤖 ai_start     →  开始调用 AI 框架
  🤔 ai_thinking  →  AI 推理中（可用于慢查询显示进度）——OPTIONAL
  🔧 ai_tool_call →  AI 调用工具（显示工具名和参数摘要）——OPTIONAL
  ✅ ai_done      →  AI 返回了非空回复
  ⚠️ ai_empty    →  AI 返回空内容（超时/无回复）

系统事件层（所有框架必须实现）：
  🟢 agent_online   →  Agent 上线
  🔴 agent_offline  →  Agent 下线
  💓 heartbeat      →  心跳（每 30s，可配置）
```

### 3.2 事件格式（已有标准，维持不变）

```json
{
  "agent_id": "ZS0001",
  "status": "ai_start",
  "msg_id": "msg-xxx",
  "detail": "开始处理 ZS0002 的消息",
  "ts": 1781069211.29,
  "nonce": "abc123"
}
```

### 3.3 限流

SDK 已有：默认 5条/s/agent，超出丢弃（日志 debug 级别）。

---

## 四、aim-watch 终端设计

### 4.1 展示格式标准

```
┌─ AIM Watch ──────────────────────────────────────────────── 15:31:05 ─┐
│ 📡 NATS: nats://127.0.0.1:4222  🎯 All Agents                        │
├────────────────────────────────────────────────────────────────────────┤
│ [15:31:05] 🟢 ZS0001 agent_online — ZS0001 (呱呱) 已上线              │
│ [15:31:05] 🟢 ZS0002 agent_online — ZS0002 (吉量) 已上线              │
│ [15:31:05] 🟢 ZS0003 agent_online — ZS0003 (小火鸡儿) 已上线           │
│                                                                        │
│ [15:31:10] 📢 ZS0001 → grp_trio | @ZS0003 收到！三方到齐...            │
│ [15:31:10] 📥 ZS0002 received — 收到来自 ZS0001 的群聊消息             │
│ [15:31:10] ⚙️ ZS0002 processing — AI 处理中                           │
│ [15:31:10] 🤖 ZS0002 ai_start — 调用 AI 框架处理                      │
│ [15:31:14] ✅ ZS0002 ai_done — AI 回复: 好的，已确认...                │
│ [15:31:15] ✅ ZS0002 completed — 已回复群聊 grp_trio                  │
│                                                                        │
│ [15:31:20] 💓 ZS0001 heartbeat — alive                                │
│ [15:31:20] 💓 ZS0002 heartbeat — alive                                │
│ [15:31:20] 💓 ZS0003 heartbeat — alive                                │
├────────────────────────────────────────────────────────────────────────┤
│ 📜 已回放 8 条历史  |  🔴 0 online  |  💓 3 alive                     │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.2 显示规则

| 内容 | 一直显示 | 可折叠（--compact） |
|------|---------|-------------------|
| 消息 | ✅ | ✅ 只显示摘要 |
| AI 处理事件 | ✅ | ai_thinking/ai_tool_call 合并到 1 行 |
| 心跳 | ❌ 默认隐藏 | ✅ 可选显示（--show-heartbeat） |
| 上线/下线 | ✅ | ✅ |
| 历史回放 | 按 --history N | 同实时 |

### 4.3 交互方式

| 快捷键/参数 | 效果 |
|------------|------|
| `aim-watch` | 默认：显示所有 Agent |
| `aim-watch --agent ZS0001` | 只看 ZS0001 |
| `aim-watch --history 10` | 启动时回放最近 10 条事件 |
| `aim-watch --json` | JSON 行输出（用于管道/grep） |
| `aim-watch --compact` | 紧凑模式（隐藏心跳，合并 ai_thinking） |
| `aim-watch --show-heartbeat` | 显示心跳（默认隐藏） |
| `aim-watch --since 3600` | 只看过去 N 秒的事件 |
| `aim-watch --save /tmp/aim-watch.log` | 事件同时写入文件 |

### 4.4 源码结构

```
~/.aim/bin/aim-watch.py     ← 标准版本（单文件 ~350 行）
                              └── 复用 AIMObserverClient（SDK）
                              └── 只写显示逻辑 + CLI 参数
```

---

## 五、技术架构

### 5.1 数据流

```
nats-agent.py（各 Agent 进程）
     │
     │ 收到消息 / AI 开始 / AI 完成 / 出错
     │
     ▼
SDK emit_obs(status, msg_id, detail)
     │
     ├──→ NATS raw publish (aim.obs.<agent_id>)  ← 实时
     │       │
     │       ▼
     │   aim-watch.py 订阅 aim.obs.>
     │       │
     │       ▼
     │   终端 TUI 显示
     │
     └──→ JetStream publish (stream: aim-observations)  ← 持久化
             │
             ▼
         aim-watch --history N  ← JetStream 回放
```

### 5.2 已有基础设施

| 组件 | 状态 | 位置 |
|------|------|------|
| SDK emit_obs() | ✅ 已有（限流+JS双发） | aim_nats_sdk.py:1056 |
| AIMObserverClient | ✅ 已有（只读连接+worker+history） | aim_nats_sdk.py:1496 |
| aim-observations stream | ✅ 已有（JetStream） | NATS Server 配置 |
| 事件格式 | ✅ 已有（呱呱 spec v1.0） | 三方对齐中 |
| 现有 aim-watch.py (NATS版) | ✅ 275行 | ~/.aim/bin/aim-watch.py |

### 5.3 AIMObserverClient 直接复用

现有 aim-watch.py 是自己创建 nats.connect + 手动订阅。新版改用 AIMObserverClient：

```python
# 旧：手动创建连接
nc = await nats.connect(servers=[server], token=token, ...)
await nc.subscribe("aim.obs.>", cb=on_obs)

# 新：复用 SDK
observer = AIMObserverClient(observer_id="aim-watch")
await observer.connect()
await observer.subscribe(my_handler, agent_filter=">")  # > = 全部
```

---

## 六、当前各 Agent Observer 事件实现状态

### 6.1 事件覆盖率矩阵

| 事件 | ZS0001（呱呱） | ZS0002（吉量） | ZS0003（小火鸡儿） |
|------|---------------|---------------|-------------------|
| received | ❌ | ❌ | ✅ |
| processing | ✅ | ✅ | ✅ |
| ai_start | ❌ | ❌ | ✅ |
| ai_thinking | ❌ | ❌ | ❌ |
| ai_tool_call | ❌ | ❌ | ❌ |
| ai_done | ❌ | ❌ | ✅ |
| ai_empty | ❌ | ❌ | ✅ |
| completed | ✅ | ✅ | ✅ |
| error | ✅ | ❌ | ✅ |
| agent_online | ✅ | ✅ | ✅ |
| agent_offline | ✅ | ✅ | ✅ |
| heartbeat | ✅ | ✅ | ✅ |

### 6.2 需要补齐的内容

**吉量 aim_agent_nats.py 需要新增**（约 30 行）：
- `received` — 在 handle_message 去重通过后 emit
- `ai_start` — 在 _call_ai 开始时 emit
- `ai_done` / `ai_empty` — 在 _call_ai 返回后 emit
- `error` — 在 except 块中 emit（已有但不标准）

**呱呱 nats-agent.py 需要新增**（约 30 行）：
- `received` — 在去重通过后 emit
- `ai_start` — 在 _call_ai 开始时 emit
- `ai_done` / `ai_empty` — 在 _call_ai 返回后 emit

**小火鸡儿 nats-agent.py 已最完整**，无需改动。

---

## 七、实现方案

### 7.1 改动清单

| 文件 | 改什么 | 估算行数 |
|------|--------|---------|
| `~/.aim/bin/aim-watch.py` | 重写：复用 AIMObserverClient，增加 compact/save/heartbeat 等参数 | ~120 行变动 |
| `~/.aim/bin/aim_nats_sdk.py` | 无需改动（已有完整功能） | 0 |
| `~/.hermes/hermes-agent/apps/aim-agent/aim_agent_nats.py` | 补齐 received/ai_start/ai_done/ai_empty/error 事件 | ~30 行 |
| `~/.aim/agents/ZS0001/nats-agent.py` | 补齐 received/ai_start/ai_done/ai_empty | ~30 行 |
| `~/.aim/agents/ZS0003/nats-agent.py` | 无需改动 | 0 |

**总计：~180 行，核心改动只有 1 个文件（aim-watch.py）+ 2 个 Agent 的补充。**

### 7.2 不动的部分

- SDK 不动——AIMObserverClient 已封装好
- 事件格式不动——呱呱 spec 已对齐
- JetStream 配置不动——已有 aim-observations
- emit_obs 限流不动——已有 5条/s

### 7.3 测试计划

| 测试 | 内容 |
|------|------|
| T1 | aim-watch 启动 → 实时显示所有 Agent 的在线/离线/心跳事件 |
| T2 | 发一条消息 → 完整事件链（received→processing→ai_start→ai_done→completed）|
| T3 | 全部 Agent 同时发消息 → 不乱序、不丢事件、限流不崩溃 |
| T4 | --agent ZS0001 过滤正确 |
| T5 | --history 10 JetStream 回放正确 |
| T6 | --compact 模式隐藏心跳，合并 AI 过程 |
| T7 | ctrl+c 正常退出，显示统计 |

---

## 八、覆盖缺口补充（基于老三 review）

### 8.1 小火鸡儿指出的缺口

| 缺口 | 问题 | 补充位置 |
|------|------|---------|
| HTTP API 框架无 Bridge | Dify/Coze 无本地进程，handler.sh 不适用 | §9.1 |
| Python SDK 框架无 Bridge | CrewAI 不是 CLI 子进程，FrameworkCLI 不适用 | §9.2 |
| handler.sh 无法 emit_obs | Shell 脚本不能调 Python SDK | §9.3 |
| Coze observer 标"不支持"不准确 | 有 HTTP API，Bridge 可模拟 | 见修正 |
| 多传输层无扩展点 | 云端 Agent 无法连本地 NATS | §10 |
| SDK 版本依赖未明确 | aim-watch 依赖 SDK 哪些接口没说清 | §10.3 |

### 8.2 修正后的框架接入矩阵

| # | 框架 | 类型 | 传输层 | Observer | 接入方式 | 适配难度 |
|---|------|------|--------|----------|---------|---------|
| 1 | **Hermes** | CLI | NATS | nats-agent.py 内置 | ✅ 现有 | — |
| 2 | **OpenClaw** | CLI | NATS | nats-agent.py 内置 | ✅ 现有 | — |
| 3 | **Letta** | CLI | NATS | nats-agent.py 内置 | ✅ 现有 | — |
| 4 | **CrewAI** | Python SDK | NATS | Bridge 进程发射 | 🆕 §9.2 | 🟡 中 |
| 5 | **AutoGen** | Python SDK | NATS | Bridge 进程发射 | 🆕 §9.2 | 🟡 中 |
| 6 | **LangGraph** | Python SDK | NATS | Bridge 进程发射 | 🆕 §9.2 | 🟡 中 |
| 7 | **Semantic Kernel** | C#/Python SDK | NATS | Bridge 进程发射 | 🆕 §9.2 | 🟡 中 |
| 8 | **Dify** | HTTP API | NATS | Bridge 进程发射 + 模拟 | 🆕 §9.1 | 🟡 中 |
| 9 | **Coze** | HTTP API | NATS | Bridge 进程发射 + 模拟 | 🆕 §9.1 | 🟡 中 |
| 10 | **OpenAI Assistants** | HTTP API | NATS | Bridge 进程发射 + 模拟 | 🆕 §9.1 | 🟡 中 |

### 8.3 落地阶段划分

| 阶段 | 覆盖框架 | 前置条件 |
|------|---------|---------|
| **v1.0（当前）** | Hermes / OpenClaw / Letta（CLI 框架） | nats-agent.py 三方可运行 |
| **v1.1（下一版）** | + CrewAI / AutoGen / LangGraph（Python SDK） | Bridge 进程模板就绪 |
| **v1.2（未来）** | + Dify / Coze / OpenAI Assistants（HTTP API） | Bridge 进程 + 网络可达 |

---

## 九、Bridge 进程设计

### 9.1 HTTP API Bridge — Dify / Coze / OpenAI Assistants

**问题**：这些框架没有本地进程能跑 nats-agent.py。它们的调用方（AIM Agent）需要在本地跑一个 Bridge 进程，把 HTTP API 包装成 AIM Agent。

**架构**：
```
AIM NATS ←→ Bridge 进程 ←→ HTTP API (Dify/Coze/OpenAI)
                │
                ├── 订阅 aim.dm.<bridge_agent_id>
                ├── 调用 HTTP API
                ├── emit_obs() 全程事件
                └── 回复通过 NATS 发回
```

**Bridge 代码模板**（~150 行）：

```python
#!/usr/bin/env python3
"""aim_http_bridge.py — HTTP API 框架通用 Bridge 进程

将 HTTP API 框架（Dify/Coze/OpenAI Assistants）包装为 AIM Agent。
用法:
  python3 aim_http_bridge.py --agent-id ZS0004 --framework dify \\
    --api-url http://localhost:8080/v1/chat-messages --api-key xxx
"""

import asyncio, json, sys, os, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".aim" / "bin"))
from aim_nats_sdk import AIMNATSClient

class HTTPAPIBridge:
    FRAMEWORKS = {
        "dify": {
            "build": lambda t: {"query": t, "response_mode": "blocking", "inputs": {}},
            "extract": lambda d: d.get("answer", ""),
        },
        "coze": {
            "build": lambda t: {"bot_id": "", "user": "AIM", "query": t},
            "extract": lambda d: d.get("messages", [{}])[0].get("content", ""),
        },
        "openai": {
            # OpenAI Assistants 需要三步：create thread → create run → list messages
            "build": lambda t: {"messages": [{"role": "user", "content": t}]},
            "extract": lambda d: d.get("choices", [{}])[0].get("message", {}).get("content", ""),
        },
    }

    def __init__(self, agent_id, framework, api_url, api_key=""):
        self.agent_id = agent_id
        self.framework = framework
        self.api_url = api_url
        self.api_key = api_key
        self.client = AIMNATSClient.from_config(agent_id)

    async def start(self):
        await self.client.connect()
        await self.client.setup_streams()
        log.info(f"🚀 {self.agent_id} HTTP Bridge ({self.framework}) 启动")
        await self.client.emit_obs("agent_online", "", f"{self.agent_id} 上线")
        await self.client.subscribe_dm(self._on_dm)
        await asyncio.Event().wait()

    async def _on_dm(self, envelope, raw):
        msg_id = envelope["id"]
        text = envelope["payload"]["text"]
        sender = envelope["from"]

        await self.client.emit_obs("received", msg_id, f"收到来自 {sender} 的消息")
        await self.client.emit_obs("processing", msg_id, "Bridge 处理中")
        await self.client.emit_obs("ai_start", msg_id, f"调用 {self.framework} API")

        import aiohttp
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        fw = self.FRAMEWORKS.get(self.framework, {})
        payload = fw.get("build", lambda t: {"query": t})(text)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    data = await resp.json()
                    reply = fw.get("extract", lambda d: "")(data)
                    if reply:
                        await self.client.emit_obs("ai_done", msg_id, f"回复: {reply[:80]}")
                        await self.client.send_dm(sender, reply)
                        await self.client.emit_obs("completed", msg_id, "已回复")
                    else:
                        await self.client.emit_obs("ai_empty", msg_id, "API 返回空")
        except Exception as e:
            await self.client.emit_obs("error", msg_id, str(e))
```

**Observer 事件说明**：
- HTTP API 返回的是最终结果，没有推理过程可见
- 只发射：received → processing → ai_start → ai_done/ai_empty → completed/error
- **不模拟** ai_thinking / ai_tool_call 等中间状态
- aim-watch compact 模式下显示：📥→⚙️→🤖→✅/⚠️

### 9.2 Python SDK Bridge — CrewAI / AutoGen / LangGraph

**问题**：CrewAI 的 `Crew().kickoff()` 是 Python SDK 调用，不是 CLI 子进程。FrameworkCLI 的 subprocess 模式需要框架 CLI 路径。

**方案**：通用 Python SDK Bridge 进程（~200 行），复用 SDK 的 `AIMNATSClient`。

```python
#!/usr/bin/env python3
"""aim_sdk_bridge.py — Python SDK 框架通用 Bridge

用法: python3 aim_sdk_bridge.py --agent-id ZS0004 --framework crewai
"""

import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".aim" / "bin"))
from aim_nats_sdk import AIMNATSClient

# ── 框架特定的调用函数 ──

def _call_crewai(text: str) -> str:
    from crewai import Crew, Agent, Task
    agent = Agent(role="assistant", goal="reply", backstory="AIM Agent")
    task = Task(description=text, agent=agent)
    crew = Crew(agents=[agent], tasks=[task])
    return str(crew.kickoff())

def _call_autogen(text: str) -> str:
    from autogen import AssistantAgent
    agent = AssistantAgent(name="assistant")
    reply = agent.generate_reply(messages=[{"role": "user", "content": text}])
    return str(reply)

FRAMEWORKS = {"crewai": _call_crewai, "autogen": _call_autogen}

class SDKBridge:
    def __init__(self, agent_id, framework):
        self.agent_id = agent_id
        self.framework = framework
        self.client = AIMNATSClient.from_config(agent_id)

    async def start(self):
        await self.client.connect()
        await self.client.setup_streams()
        await self.client.emit_obs("agent_online", "", f"{self.agent_id} 上线")
        await self.client.subscribe_dm(self._on_dm)
        asyncio.create_task(self.client.start_heartbeat(30))
        await asyncio.Event().wait()

    async def _on_dm(self, envelope, raw):
        msg_id = envelope["id"]
        text = envelope["payload"]["text"]
        sender = envelope["from"]

        await self.client.emit_obs("received", msg_id, f"收到来自 {sender} 的消息")
        await self.client.emit_obs("processing", msg_id, "Bridge 处理中")
        await self.client.emit_obs("ai_start", msg_id, f"调用 {self.framework} SDK")

        try:
            fn = FRAMEWORKS.get(self.framework)
            if not fn:
                raise ValueError(f"不支持的框架: {self.framework}")
            reply = await asyncio.get_event_loop().run_in_executor(None, fn, text)
            if reply:
                await self.client.emit_obs("ai_done", msg_id, f"回复: {reply[:80]}")
                await self.client.send_dm(sender, reply)
                await self.client.emit_obs("completed", msg_id, "已回复")
            else:
                await self.client.emit_obs("ai_empty", msg_id, "SDK 返回空")
        except Exception as e:
            await self.client.emit_obs("error", msg_id, str(e))
```

**新框架接入**：只需在 `FRAMEWORKS` 字典加一个函数，Bridge 主流程不变。

### 9.3 handler.sh 的事件发射增强 — handler_ext.py

**问题**：handler.sh 是 shell 脚本，不能调 Python SDK 的 `emit_obs()`。

**方案**：`~/.aim/bin/handler_ext.py`（~40 行），handler.sh 通过管道传事件信息给它：

```python
#!/usr/bin/env python3
"""handler_ext.py — handler.sh 的事件发射器

handler.sh 在处理关键节点调用：
  echo "received|msg_id|详情" | python3 handler_ext.py
  echo "ai_start|msg_id|调用 AI" | python3 handler_ext.py
  echo "ai_done|msg_id|回复完成" | python3 handler_ext.py
"""

import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".aim" / "bin"))
from aim_nats_sdk import AIMNATSClient

client = None

async def emit(status: str, msg_id: str, detail: str):
    global client
    if client is None:
        client = AIMNATSClient.from_config(os.environ.get("AIM_AGENT_ID", "ZS0003"))
        await client.connect()
    await client.emit_obs(status, msg_id, detail)

if __name__ == "__main__":
    line = sys.stdin.read().strip()
    parts = line.split("|", 2)
    if len(parts) == 3:
        asyncio.run(emit(parts[0], parts[1], parts[2]))
```

handler.sh 中用法：
```bash
MSG_ID="$1"
FROM="$2"
TEXT="$3"

echo "received|$MSG_ID|收到来自 $FROM 的消息" | python3 handler_ext.py
echo "ai_start|$MSG_ID|调用 AI" | python3 handler_ext.py

# ... 调用 AI 框架 ...

echo "ai_done|$MSG_ID|回复完成" | python3 handler_ext.py
echo "completed|$MSG_ID|已回复" | python3 handler_ext.py
```

---

## 十、TransportProvider 接口（前瞻扩展）

### 10.1 接口定义

在 `aim-watch.py` 中新增轻量接口，不现在实现非 NATS 传输层，但预留扩展点：

```python
class TransportProvider(ABC):
    """传输层提供者 — 连接事件源，转换为统一 WatchEvent 格式

    当前只有 NATSTransport（复用 AIMObserverClient），
    未来增加 HTTPTransport / FileTransport 时实现此接口。
    """

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def subscribe(self, handler: Callable[[dict], Awaitable[None]],
                        agent_filter: str = ">") -> None:
        ...

    @abstractmethod
    async def get_history(self, agent_filter: str = ">",
                          page: int = 1, page_size: int = 20) -> list:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...
```

### 10.2 EventSource 改为接受 TransportProvider

```python
class EventSource:
    def __init__(self, transport: TransportProvider, display: WatchDisplay):
        self.transport = transport
        self.display = display
```

未来加 HTTP 传输时，只需写 `HTTPTransport(TransportProvider)`，EventSource 和 WatchDisplay 不用改。

### 10.3 aim-watch 对 SDK 的依赖契约

| 方法 | 用途 | 稳定性 |
|------|------|--------|
| `AIMObserverClient.connect()` | 连接 NATS | ✅ 稳定 |
| `AIMObserverClient.subscribe(handler, agent_filter)` | 订阅事件 | ✅ 稳定 |
| `AIMObserverClient.get_history(agent_filter, page, page_size)` | 历史回放 | ✅ 稳定 |
| `AIMObserverClient.disconnect()` | 断开 | ✅ 稳定 |
| `AIMObserverClient.is_connected` | 状态查询 | ✅ 稳定 |

aim-watch 只依赖这 5 个接口，不依赖 `_nc`/`_event_queue` 等内部属性。

---

## 十一、执行计划

| 步骤 | 内容 | 负责 | 阶段 |
|------|------|------|------|
| 1 | 三方评审本方案 | 呱呱+吉量+小火鸡儿 | 讨论 |
| 2 | 重写 aim-watch.py v2.0（复用 AIMObserverClient） | 吉量 ✅ | v1.0 |
| 3 | 补齐吉量 aim_agent_nats.py observer 事件 | 吉量 ✅ | v1.0 |
| 4 | 加 `--framework` 过滤（小火鸡儿建议） | 吉量 | v1.0 |
| 5 | 呱呱补齐 nats-agent.py observer 事件 | 呱呱 | v1.0 |
| 6 | 同步到 ~/.aim/bin/ + shared/aim/src/ | 吉量 | v1.0 |
| 7 | T1-T7 测试（CLI 框架联调） | 三方 | v1.0 |
| 8 | 实现 handler_ext.py（handler.sh 事件发射） | 吉量 | v1.1 |
| 9 | 实现 SDK Bridge 进程模板（aim_sdk_bridge.py） | 吉量 | v1.1 |
| 10 | 实现 HTTP Bridge 进程模板（aim_http_bridge.py） | 吉量 | v1.2 |

### 改动量合计

| 文件 | 改什么 | 行数 |
|------|--------|------|
| `~/.aim/bin/aim-watch.py` | 重写（复用 AIMObserverClient） | ~440行 ✅ |
| `~/.hermes/hermes-agent/apps/aim-agent/aim_agent_nats.py` | 补齐 observer 事件 | +8行 ✅ |
| `~/.aim/agents/ZS0001/nats-agent.py` | 补齐 observer 事件 | ~+30行 |
| `~/.aim/bin/handler_ext.py` | 新增（handler.sh 事件发射） | ~40行 |
| `~/.aim/bin/aim_sdk_bridge.py` | 新增（SDK Bridge 模板） | ~200行 |
| `~/.aim/bin/aim_http_bridge.py` | 新增（HTTP Bridge 模板） | ~150行 |
| **合计** | | **~870 行** |

---

## 十二、待确认点

1. compact 模式——合并 ai_start→ai_done 为 1 行，还是只隐藏 ai_thinking/ai_tool_call？
2. heartbeat 默认隐藏 --show-heartbeat 显式。大家同意？
3. Bridge 进程的 lauchd/systemd 保活配置——大家各管各的，还是统一脚本？
4. 安装方式——aim-watch.py 作为 ~/.aim/bin/ 标准客户端部分，不单独安装。大家同意？
5. TransportProvider 接口现在加，还是等到需要 HTTP 传输时再加？
