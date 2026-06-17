# AIM 标准接口调研报告 — TOP10 框架整合

> 任务来源：大哥指令 — 适配全球 TOP10 智能体架构的 AIM 统一标准平台
>
> 调研团队：呱呱 (ZS0001)、吉量 (ZS0002)、小火鸡儿 (ZS0003)
>
> 原则：不碰框架内部，在 AIM 客户端侧做适配。目标是让 AIM 客户端能转换任何框架的消息到 AIM 统一信封。

---

## 结论先行

### 三大分类

小火鸡儿将 TOP10 智能体架构按通信模式分为 3 类：

| 分类 | 框架数 | 代表框架 | 适配模式 |
|------|--------|---------|---------|
| **协议原生** | 2 | MCP, A2A | 协议层直接复用 — 框架本身就是标准协议 |
| **API 暴露** | 4 | LangGraph, Dify, Coze, n8n | REST/WS API → AIM 适配器 |
| **进程内** | 4 | OpenAI SDK, CrewAI, AutoGen, MetaGPT | SDK 包装/钩子 — 在 Python 调用处做映射 |

### AIM 标准接口（适配器模式）

```
┌─────────────────────────────────────────────────┐
│                 AIM Client                       │
│  ┌──────────────────────────────────────┐       │
│  │        Unified API (4 方法)          │       │
│  │  connect()  — 建立连接与认证          │       │
│  │  send()     — 发送消息               │       │
│  │  receive()  — 接收响应               │       │
│  │  capabilities() — 发现框架能力        │       │
│  └──────────────────────────────────────┘       │
│          │          │          │                 │
│     ┌────┴────┐ ┌──┴───┐ ┌───┴─────┐           │
│     │MCP Adpt │ │REST │ │SDK Adpt│  ...        │
│     │(直接复用)│ │Adpt│ │(包装器)│             │
│     └─────────┘ └──────┘ └─────────┘           │
│                     adapters/                    │
└─────────────────────────────────────────────────┘
```

### TOP10 框架总表

| # | 框架 | 分类 | 通信机制 | 消息格式 | 适配难度 | 适配方式 |
|---|------|------|---------|---------|---------|---------|
| 1 | **MCP** | 协议原生 | JSON-RPC 2.0 (stdio/HTTP) | JSON-RPC 2.0 信封 | ★☆☆ 最低 — 天生信封化 | 协议层直接映射 |
| 2 | **A2A** | 协议原生 | HTTP/JSON Task 模型 | Task 对象结构 | ★☆☆ 最低 — 标准 HTTP | 协议层直接映射 |
| 3 | **Dify** | API 暴露 | REST API / WebSocket / MCP | OpenAI 兼容 Chat API + App Schema | ★★☆ 低 — OpenAI 兼容 | REST 适配器 |
| 4 | **n8n** | API 暴露 | REST API / Webhook | Chat Post Message Schema | ★★☆ 低 — 标准 REST | REST 适配器 |
| 5 | **Coze** | API 暴露 | REST + WebSocket | v3/chat message 格式 | ★★☆ 低 — 标准 REST | REST + WS 适配器 |
| 6 | **LangGraph** | API 暴露 | REST + SSE | LangServe Schema | ★★☆ 低 — 标准 API | REST 适配器 |
| 7 | **OpenAI Agents SDK** | 进程内 | Python SDK 直接调用 | OpenAI Chat Messages | ★★☆ 低 — 标准格式 | SDK 包装器 |
| 8 | **CrewAI** | 进程内 | Python SDK + 工具调用 | Agent/Runner 输出 | ★★★ 中 — 需进程管理 | SDK 包装器 |
| 9 | **AutoGen** | 进程内 | Python SDK (消息循环) | AgentMessage 对象 | ★★★ 中 — 异步消息 | SDK 包装器 |
| 10 | **MetaGPT** | 进程内 | Python SDK (角色-动作) | Message/Role 对象 | ★★★ 中 — 多层抽象 | SDK 包装器 |

### 核心结论

1. **MCP 是事实上的工具集成标准** — Dify/ADK/A2A 均已兼容或将兼容
2. **OpenAI Chat 格式是事实上的消息格式标准** — SDK/Dify/Coze 均兼容
3. **AIM 统一信封建议**：采用 JSON-RPC 2.0 作为协议层 + OpenAI Chat Messages 作为消息体
4. **适配器模式是最佳路径** — 不碰框架内部，在 AIM 客户端侧加 `adapters/` 目录做映射
5. **统一消息信封扩展** `meta.origin` 保留原生消息，确保可追溯

### 适配优先级建议

```
Phase 1 — 协议原生 (MCP, A2A)     → 直接复用，低投入高回报
Phase 2 — API 暴露 (Dify, LangGraph, Coze, n8n) → REST 适配器，标准路径
Phase 3 — 进程内 (OpenAI SDK, CrewAI, AutoGen, MetaGPT) → SDK 包装器，需更多工作
```

---

## 吉量 — 4 框架详细调研（OpenAI Agents SDK / MCP / Google ADK / Dify）

---

## 1. OpenAI Agents SDK

### 概述

Python 代码优先框架（2025年3月发布），提供 5 个基础原语：Agent、Runner、Handoff、Guardrail、Session。2026年4月更新加入沙箱执行。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | Python SDK 直接调用（同步/异步） |
| **核心 API** | `Runner.run(agent, input)` → 返回 `RunResult` |
| **流式** | `Runner.run_streamed()` → 事件流 |
| **跨进程** | 无内置 — 通过 Responses API 暴露为 HTTP |
| **子Agent** | Handoff 机制 — LLM 自动发现调度的 tool call 式委派 |
| **调用约定** | 纯 Python 函数调用（无独立协议层） |

### 消息格式

```
输入: str / list[dict] — 标准 OpenAI Chat Messages 格式
  [
    {"role": "system", "content": "指令"},
    {"role": "user", "content": "用户消息"},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "...", "tool_call_id": "..."}
  ]

输出: RunResult 对象
  - final_output: str — 最终结果文本
  - last_agent: Agent — 最后执行 agent
  - new_items: list[RunItem] — 每轮对话记录
  - input_guardrail_results / output_guardrail_results
```

### 认证方案

- **API Key** (Bearer Token in Authorization header) — OpenAI Responses API 调用
- **沙箱认证** — Docker socket 权限（本地）；托管服务端认证

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|-------------|
| `Runner.run(agent, input)` | 输入消息 | 拦截 input → 转换为 AIM 信封格式 |
| `final_output` | 输出结果 | 从 RunResult 提取 final_output → AIM 响应信封 |
| `new_items` | 工具调用轨迹 | 解析 tool_call / tool_result → AIM action/result 字段 |
| `handoff` | Agent 委派 | 映射到 AIM route 目标字段 |

### 适配要点

- OpenAI Agents SDK 不定义独立传输协议，**一切通过 Python SDK 对象传递**
- AIM 适配方式：**SDK Hook/包装器**，在 input/output 处做格式转换
- 输入输出都是标准 OpenAI Chat 格式，映射最直接

---

## 2. MCP (Model Context Protocol)

### 概述

Anthropic 发起的**通用上下文协议**，定义 LLM 与外部工具/数据源之间的标准化通信协议。2025-2026 年成为工具集成的行业标准。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | stdio (本地) / Streamable HTTP (远程) |
| **协议** | **JSON-RPC 2.0** — 严格的双向 RPC |
| **生命周期** | initialize → capabilities 协商 → notifications/initialized → 方法调用 |
| **状态** | 有状态（可降级为无状态 via Streamable HTTP） |
| **服务模式** | 1 个 Server : N 个 Client (HTTP) 或 1:1 (stdio) |

### 消息格式

```
// 请求 — 标准 JSON-RPC 2.0
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "weather_current",
    "arguments": {"location": "San Francisco"}
  }
}

// 响应
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {"type": "text", "text": "Current weather: 68°F..."},
      {"type": "image", "data": "base64..."},
      {"type": "resource", "resource": {...}}
    ]
  }
}

// 工具列表响应
{
  "tools": [
    {
      "name": "weather_current",
      "title": "Weather Information",
      "description": "Get current weather",
      "inputSchema": {
        "type": "object",
        "properties": {
          "location": {"type": "string"},
          "units": {"type": "string", "enum": ["metric", "imperial"]}
        },
        "required": ["location"]
      }
    }
  ]
}
```

### Server 核心原语

| 原语 | 用途 | 方法 |
|------|------|------|
| **Tools** | 可执行函数（文件操作/API/DB） | `tools/list`, `tools/call` |
| **Resources** | 上下文数据源 | `resources/list`, `resources/read` |
| **Prompts** | 可复用交互模板 | `prompts/list`, `prompts/get` |

### Client 能力

| 能力 | 用途 |
|------|------|
| **Sampling** | Server 请求 LLM 补全 |
| **Elicitation** | Server 请求用户输入 |
| **Logging** | Server 发送调试/监控消息 |

### 认证方案

| 传输 | 认证方式 |
|------|---------|
| **stdio** | 无（进程级信任） |
| **HTTP** | OAuth 2.0 / Bearer Token / API Key |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|-------------|
| `tools/list` | 工具发现 | AIM 注册时自动发现并注册 Agent 能力 |
| `tools/call` | 工具执行 | 映射到 AIM action → tool_execution → result |
| `resources/read` | 数据源读取 | 映射到 AIM query → data_retrieval |
| `initialize` / capabilities | 能力协商 | 映射到 AIM 注册握手阶段 |
| `notifications/...` | 事件通知 | 映射到 AIM event/notification 消息类型 |
| Content types | 多格式输出 | text/image/resource → AIM content 字段 |

### 适配要点

- MCP **本身就是信封协议** — JSON-RPC 2.0 是天然的统一消息包装
- AIM 可以直接在 MCP 信封外层再加一层（AIM 协议头）
- 或反过来 — AIM 作为 MCP Client 连接各框架
- **这是最容易适配的框架** — 协议层匹配度最高

---

## 3. Google ADK (Agent Development Kit)

### 概述

Google 的开源 Agent 框架（2025年10月发布，2026年4月 1.0 GA），覆盖 Python/Go/Java/TypeScript 四语言。深度集成 Gemini 模型、Vertex AI、A2A (Agent-to-Agent) 协议。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | Python SDK 事件流 / A2A HTTP 协议 |
| **核心 API** | `Runner.run(user_id, session_id, new_message)` → 事件流 |
| **事件模式** | **ask-yield** — 执行逻辑 yield 事件回 Runner |
| **架构** | 事件驱动运行时 + 三层服务 (Session/Artifact/Memory) |
| **子Agent** | AgentTool（嵌套调用）/ SequentialAgent / ParallelAgent / LoopAgent |
| **A2A** | Agent-to-Agent 协议 — Linux Foundation 托管，150+ 企业使用 |

### 消息格式

```
// 输入 — Content 对象包裹
new_message = Content(role="user", parts=[Part(text="What is the weather?")])

// 输出 — 事件流 (yield 模式)
for event in runner.run(user_id=..., session_id=..., new_message=...):
    if event.is_final_response():
        final_text = event.content.parts[0].text
    # 中间事件：tool_call, tool_result, model_thinking 等

// 工具调用格式
tool_call = ToolCall(
    function_name="get_weather",
    args={"city": "London"}
)

tool_result = ToolResult(
    function_name="get_weather",
    result={"status": "success", "report": "..."}
)
```

### 服务层抽象

| 服务 | 用途 | 生产实现 |
|------|------|---------|
| **Session Service** | 对话状态持久化 | InMemory / Firestore / PostgreSQL |
| **Artifact Service** | 文件存储 | InMemory / GCS / 本地 |
| **Memory Service** | 跨会话长期记忆 | InMemory / Vertex AI Memory Bank |

### 认证方案

| 场景 | 认证方式 |
|------|---------|
| **本地 SDK** | API Key / 无（本地信任） |
| **Vertex AI 部署** | Google Cloud IAM (Service Account) |
| **A2A 协议** | OAuth 2.0 / JWT / API Key |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|-------------|
| `Runner.run()` 的 `new_message` | 输入消息 (Content) | 拦截 → 转为 AIM 请求信封 |
| 事件流 (`for event in ...`) | 输出/中间步骤 | 缓冲事件流 → 重组为 AIM 完整响应 |
| `is_final_response()` | 最终结果 | 标记 AIM response.complete = true |
| ToolCall / ToolResult | 工具调用轨迹 | 映射到 AIM action/result |
| AgentTool (嵌套) | 子Agent 委派 | 映射到 AIM route 字段 |
| A2A Protocol | 跨 Agent 通信 | 直接复用 — AIM 可作为 A2A 协议的抽象层 |

### 适配要点

- **事件流是最大挑战** — Runner.run() 不返回单个结果，而是 yield 事件序列
- AIM 适配需做**事件缓冲/重组**：收集所有事件 → 构建完整 AIM 响应
- ADK 的 A2A 协议与 AIM 目标高度吻合（跨 Agent 通信标准）
- ADK 本身支持多语言，AIM 只需适配 Python SDK 事件层即可

---

## 4. Dify

### 概述

开源 AI 应用开发平台（131K GitHub stars），可视化工作流编排 + RAG 管道 + Agent 运行时 + API 发布。支持 100+ LLM 模型。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | REST API / WebSocket / MCP Client/Server |
| **核心 API** | Chat Messages API (OpenAI 兼容) / Workflow Run API / App Schema API |
| **流式** | SSE (Server-Sent Events) / WebSocket |
| **内部** | 可视化画布编排 → 节点图执行引擎 |
| **子Agent** | Supervisor Agent 模式（1 协调 → N 子Agent → 聚合） |

### 消息格式

```
// REST API — Chat 模式 (OpenAI 兼容)
POST /v1/chat-messages
{
  "inputs": {},
  "query": "What's the weather in London?",
  "response_mode": "streaming",  // 或 "blocking"
  "conversation_id": "",
  "user": "user-abc"
}

// 流式响应 (SSE)
data: {"event": "message", "answer": "Let me check...", "conversation_id": "abc", "created_at": 1234567890}
data: {"event": "agent_message", "answer": "The current weather...", "conversation_id": "abc"}
data: {"event": "agent_thought", "id": "...", "position": 1, "thought": "User wants weather", "observation": "...", "tool": "get_weather", "tool_input": "...", "tool_output": "..."}
data: {"event": "message_file", "type": "image", "url": "..."}
data: {"event": "message_end", "id": "...", "metadata": {"usage": {...}}}

// Workflow 执行 API
POST /v1/workflows/run
{
  "inputs": {"city": "London"},
  "response_mode": "streaming",
  "user": "user-abc"
}

// Agent 策略
- Function Calling — 标准 LLM tool call
- ReAct — thought/action/observation 循环
- MCP Client — 连接外部 MCP Server
```

### 认证方案

| 场景 | 认证方式 |
|------|---------|
| **API 调用** | API Key (Header: `Authorization: Bearer <app-key>`) |
| **Dify Cloud** | 用户 Token + App Secret |
| **MCP Server** | 随 MCP 协议 OAuth/Bearer |
| **自托管** | 管理员凭据 / SSO |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|-------------|
| `POST /v1/chat-messages` | 输入接口 | 将 AIM 消息转为 Dify 请求格式 |
| SSE 事件流 | 流式输出 | 解析 `message` / `agent_message` / `agent_thought` 事件 → AIM 响应 |
| `POST /v1/workflows/run` | 工作流接口 | AIM 任务分发 → Dify 工作流执行 |
| `MCP Server` 暴露 | Dify 应用作为 MCP Server | 通过 MCP 协议桥接到 AIM |
| `MCP Client` 能力 | Dify 连接外部 MCP | AIM 可作为 MCP Server 被 Dify 发现 |

### 适配要点

- **API 兼容层最成熟** — Dify Chat API 已经兼容 OpenAI 格式
- SSE 事件流需解析多种事件类型（message / agent_message / agent_thought / message_end）
- Dify 的 MCP 双角色（Client + Server）与 AIM 天然互补
- Agent 运行时支持 Function Calling 和 ReAct 两种策略，AIM 信封需兼容两种模式的轨迹

---

## 跨框架对比总结

### 1. 协议/传输层

```
json-rpc 2.0       REST / SSE          Python SDK        Event Stream
     │                  │                  │                  │
    MCP               Dify          OpenAI SDK         Google ADK
   (stdio/HTTP)    (HTTP/WS)         (in-process)      (in-process)
```

### 2. 消息格式统一性

| ✅ **高 (天生信封化)** | MCP (JSON-RPC 2.0) |
|:---|:---|
| 🔶 **中 (标准兼容)** | OpenAI SDK (Chat Messages) / Dify (OpenAI 兼容 REST) |
| 🔴 **低 (需适配)** | Google ADK (事件流需缓冲重组) |

### 3. AIM 适配优先级建议

1. **MCP** — 天然匹配，协议层复用。AIM 可设计为 MCP 的上层包装
2. **OpenAI Agents SDK** — 标准 OpenAI 格式，直接转换
3. **Dify** — OpenAI 兼容 API + MCP 双角色，适配路径清晰
4. **Google ADK** — 事件流模式需额外处理，但 A2A 协议可复用

### 4. 核心发现

- **MCP 是事实上的工具集成标准** — 四个框架中有三个（MCP/Dify/Google ADK）已经或正在支持 MCP
- **OpenAI Chat 格式是事实上的消息格式标准** — SDK 和 Dify 都兼容
- **AIM 统一信封设计建议**：采用 MCP-like JSON-RPC 2.0 作为协议层，OpenAI Chat Messages 作为消息体格式
- Google ADK 的 **A2A 协议** 与 AIM 的跨 Agent 通信意图高度吻合，值得关注
- Dify 是唯一同时扮演 **MCP Client 和 MCP Server** 的平台，适配最有弹性

---

## 呱呱 — 技术评估与实施建议

> 领域：安全审计 / 基础设施 / 代码实现
>
> 原则：结论优先，可操作优先。不重复已有调研结果。

### 一、适配器模式安全评估

| 适配器类型 | 主要风险 | 缓解措施 | 严重度 |
|-----------|---------|---------|--------|
| **JSON-RPC (MCP)** | 方法名注入 / 参数过大导致 DoS | 白名单方法注册 + 输入长度限制 + JSON schema 校验 | 🟡 中 |
| **HTTP REST (A2A/Dify/LangGraph/Coze/n8n)** | API Key 泄露 / MITM / SSRF | TLS 强制 + Key 文件化(非环境变量) + URL 白名单 | 🟡 中 |
| **WebSocket (Dify/Coze)** | 长连接劫持 / 重放攻击 | WSS 强制 + 连接级 nonce + 心跳超时断开 | 🟠 中高 |
| **SSE (LangGraph/Dify)** | 事件注入 / 连接耗尽 | 严格事件类型校验 + 连接池上限 | 🟡 中 |
| **SDK 包装器 (OpenAI/CrewAI/AutoGen/MetaGPT)** | 子进程逃逸 / 资源泄漏 | subprocess 沙箱 + 资源限制 (cgroup/memory) + 超时 kill | 🟠 中高 |
| **stdio (MCP)** | 进程间通信劫持 | Unix socket 权限 + 非 root 运行 | 🟢 低 |

### 二、统一认证层设计

```python
# adapters/auth.py — 统一认证接口
class AdapterAuth:
    """所有适配器共享的认证层"""
    
    def __init__(self, auth_type: str, credentials: dict):
        self.auth_type = auth_type  # bearer / oauth2 / api_key / none
        self.credentials = self._validate(credentials)
    
    def apply(self, transport) -> dict:
        """为传输层注入认证信息，返回 headers/params"""
        if self.auth_type == "bearer":
            return {"headers": {"Authorization": f"Bearer {self.credentials['token']}"}}
        if self.auth_type == "api_key":
            return {"headers": {self.credentials.get("header", "X-API-Key"): self.credentials["key"]}}
        if self.auth_type == "oauth2":
            return self._oauth2_flow()
        return {}
    
    def rotate(self):
        """密钥轮换 — 支持热更新不中断连接"""
        pass
```

**安全铁律**：
- API Key **只存文件**（600 权限），不用环境变量
- Token 定期轮换（cron 触发，默认 24h）
- 所有 HTTP 适配器**强制 TLS**，无降级允许

### 三、错误处理统一模式

```python
# adapters/base.py — 所有适配器的基类
class AdapterError(Exception):
    code: str       # TIMEOUT / AUTH_FAILED / RATE_LIMITED / FRAMEWORK_ERROR
    retryable: bool
    retry_after: int  # seconds

class BaseAdapter:
    async def send(self, message: AIMEnvelope) -> AIMEnvelope:
        try:
            return await self._do_send(message)
        except AdapterError as e:
            if e.retryable and self.retries < self.max_retries:
                await asyncio.sleep(e.retry_after)
                return await self.send(message)
            raise  # 不可重试 → 上层处理（通知/告警/降级）
```

### 四、实施路线图

| 阶段 | 内容 | 工作量 | 产出 |
|------|------|--------|------|
| **P0 (2-3天)** | `adapters/base.py` + `adapters/auth.py` + MCP 适配器 | 1人日 | MCP 适配器可用 |
| **P1 (3-5天)** | A2A + Dify REST 适配器 + 统一错误处理 | 2人日 | 3 框架适配完成 |
| **P2 (1周)** | LangGraph + Coze + n8n REST 适配器 | 2人日 | 6 框架适配完成 |
| **P3 (1-2周)** | OpenAI SDK + CrewAI + AutoGen + MetaGPT 包装器 | 3人日 | 全部 10 框架适配 |
| **P4 (持续)** | 集成测试 + 性能基准 + 文档 | 按需 | 生产可用 |

**预估总工作量**：约 8-10 人日（1 人全职 2 周，2 人协作 1 周）

### 五、基础设施考虑

```
AIM Client (Python asyncio)
├── adapters/              # 框架适配器（每个框架一个文件）
│   ├── __init__.py
│   ├── base.py            # BaseAdapter + AdapterError
│   ├── auth.py            # 统一认证
│   ├── mcp.py             # MCP (JSON-RPC)
│   ├── a2a.py             # A2A (HTTP/JSON)
│   ├── dify.py            # Dify (REST + SSE)
│   ├── langgraph.py       # LangGraph (REST + SSE)
│   ├── coze.py            # Coze (REST + WS)
│   ├── n8n.py             # n8n (REST + Webhook)
│   ├── openai_sdk.py      # OpenAI Agents SDK
│   ├── crewai.py          # CrewAI (subprocess)
│   ├── autogen.py         # AutoGen (subprocess)
│   └── metagpt.py         # MetaGPT (subprocess)
├── envelope.py            # AIM 统一信封（已有）
├── registry.py            # Adapter 注册表 + 能力发现
└── monitor.py             # 健康检查 + 指标收集
```

**部署要求**：
- Python 3.11+ (asyncio + subprocess 管理)
- 依赖按需安装（每个适配器的 extras，不用全装）
- 配置文件：`adapters.yaml`（集中管理每个框架的连接信息 + 凭证路径）

### 六、已识别风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 框架版本 breaking changes | 适配器失效 | 版本锁定 + CI 自动检测 API 变更 |
| 进程内框架资源泄漏 | 宿主机 OOM | subprocess 内存/CPU 限制 + 超时 kill |
| WebSocket 断连 | 消息丢失 | 重连 + 消息队列缓冲（最多 1000 条） |
| API rate limit | 请求失败 | 令牌桶限速器 + 指数退避 |
| 凭证泄露 | 安全事故 | 文件权限 600 + audit log + 只读挂载 |

### 七、对当前 AIM 架构的补充建议

1. **envelope.py 扩展**：增加 `meta.adapter_trace` 字段（记录经过的适配器链，用于调试）
2. **registry.py 新增**：`capabilities()` 返回标准化能力清单，支持自动发现
3. **monitor.py 新增**：每个适配器暴露 `/health` 端点 + Prometheus metrics
4. **配置热加载**：`adapters.yaml` 变更自动重载，无需重启进程

---

## 呱呱 — 4 框架详细调研（LangGraph / CrewAI / AutoGen / MetaGPT）

> 补充吉量未覆盖的 4 个框架（API暴露类 + 进程内类的剩余部分），与吉量的 4 框架调研格式对齐。

---

## 6. LangGraph (LangChain)

### 概述

LangChain 出品的有状态多 Actor 编排框架，使用图（Graph）定义 Agent 工作流。支持单 Agent、多 Agent、层级 Agent 等架构。通过 **LangServe** 对外暴露为 REST API。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | Python SDK 直接调用 + LangServe REST API (HTTP/SSE) |
| **核心 API** | `graph.compile()` → `app.invoke(input)` / `app.stream(input)` / `app.astream(input)` |
| **流式** | 5 种 stream_mode：`values`(完整状态快照)、`updates`(增量)、`messages`(LLM token级)、`custom`(自定义)、`debug`(调试追踪) |
| **编排方式** | StateGraph — 节点(Node) + 边(Edge) + 条件边(ConditionalEdge) |
| **跨 Agent** | SubGraph 机制 — Agent 可嵌套为子图；多 Agent 协作通过共享 State 或 Handoff |
| **对外暴露** | LangServe → 自动生成 REST endpoint (POST + SSE streaming) + OpenAPI 文档 |

### 消息格式

```
// 核心 State 使用 LangChain Messages 格式
{
  "messages": [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "用户输入"},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "...", "tool_call_id": "..."}
  ]
}

// LangServe REST 请求 (POST /invoke)
{
  "input": {"messages": [...]},
  "config": {"configurable": {"thread_id": "session-1"}}
}

// SSE 流式事件
event: metadata
data: {"run_id": "..."}

event: updates
data: {"chatbot": {"messages": [...]}}
```

### 核心抽象

| 抽象 | 用途 |
|------|------|
| **StateGraph** | 定义图拓扑（节点+边+条件分支） |
| **State** | TypedDict / Pydantic Model — 图节点间传递的共享状态 |
| **Checkpointer** | 持久化状态（MemorySaver/SqliteSaver/PostgresSaver）— 支持断点恢复 |
| **Interrupt** | Human-in-the-Loop — 暂停执行等待人工审批 |
| **Command** | 手动推进图执行（配合 interrupt 使用） |
| **SubGraph** | 嵌套子图实现模块化 Agent 架构 |

### 认证方案

| 方式 | 说明 |
|------|------|
| **LangServe** | 通过 FastAPI middleware 添加认证（API Key / OAuth2 / JWT） |
| **LangSmith** | API Key 认证 — 用于 tracing 和 observability |
| **LLM 层** | 在各节点内自行处理 LLM Provider 的 API Key |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|-------------|
| `app.invoke(state)` | 同步调用，返回完整 State | 包装为 AIM send → response 模式 |
| `app.stream(state, stream_mode="updates")` | 流式增量 State | 映射到 AIM 流式事件（SSE → AIM SSE） |
| `app.astream_events(state)` | 细粒度事件流 | 事件类型映射：on_chat_model_stream → AIM token 事件 |
| `config.configurable.thread_id` | 会话 ID | 映射到 AIM session_id |
| Interrupt / Command | 人工审批点 | 映射到 AIM human_in_loop 消息类型 |
| LangServe REST | HTTP POST + SSE | 通用 REST 适配器（类似 Dify/Coze） |

### 适配要点

- LangGraph 提供 **两种接入方式**：LangServe REST（推荐，AIM 作为 HTTP 客户端）和 SDK 包装器（subprocess）
- LangServe 自动生成 OpenAPI 文档，AIM 可直接解析并生成适配器配置
- State 中的 `messages` 列表即为标准 OpenAI Chat 格式，字段映射最直接
- 5 种 streaming mode 对应 AIM 不同场景：`messages`(实时chat) / `updates`(进度条) / `values`(状态快照)
- **注意**：Checkpointer 依赖数据库（SQLite/Postgres），AIM 需要管理持久化生命周期

---

## 7. CrewAI

### 概述

多 Agent 协作框架（GitHub 52.4k stars, 截至2026.06）。核心理念：定义 Agent (角色+目标+工具)，组装成 Crew (团队)，通过 Process (顺序/层级) 执行任务。支持原生 A2A 协议。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | Python SDK — `Crew.kickoff()` 同步 / `Crew.kickoff_async()` 异步 |
| **编排流程** | Sequential (顺序) / Hierarchical (层级，Manager Agent 分配) / Hybrid |
| **Agent 间通信** | 任务委派 — Manager Agent 将 Task 分配给合适的 Agent，结果返回 |
| **外部暴露** | 无内置 REST API — 需通过 FastAPI 包装，或使用 CrewAI Enterprise 部署 |
| **A2A 协议** | 原生支持 — Agent-to-Agent task delegation + result aggregation |
| **Flows** | 事件驱动工作流编排：`@start` / `@listen` / `@router` / `@or` / `@and` |

### 消息格式

```
// Agent 定义
agent = Agent(
    role="研究员",
    goal="收集最新信息",
    backstory="经验丰富的研究分析师",
    tools=[search_tool, web_scraper_tool],
    verbose=True,
    memory=True,          # 启用短期/长期/实体记忆
    knowledge_sources=[...]  # 知识库接入
)

// Task 定义（Pydantic）
task = Task(
    description="研究 AI Agent 框架对比",
    expected_output="Markdown 格式报告",
    agent=researcher,     # 或省略让 Manager 分配
    output_pydantic=ReportSchema,  # 结构化输出
    human_input=True      # Human-in-the-loop
)

// Crew 执行
crew = Crew(agents=[researcher, writer], tasks=[task1, task2], process=Process.sequential)
result = crew.kickoff()  # → CrewOutput { raw, pydantic, json_dict, token_usage }
```

### 核心抽象

| 抽象 | 用途 |
|------|------|
| **Agent** | 角色定义（role/goal/backstory/tools/llm/memory） |
| **Task** | 任务定义（description/expected_output/agent/output_pydantic） |
| **Crew** | 团队编排（agents/tasks/process/manager_llm） |
| **Tool** | `@tool` 装饰器 — 可被 Agent 调用的函数，支持 MCP 工具 |
| **Memory** | 短期(上下文)/长期(向量DB)/实体(结构化)三层记忆 |
| **Flow** | 事件驱动编排（@start/@listen/@router），状态可持久化 |
| **Process** | sequential / hierarchical / hybrid |

### 认证方案

| 方式 | 说明 |
|------|------|
| **LLM 层** | `OPENAI_API_KEY` 等环境变量，或 LLM(model="...", api_key="...", base_url="...") |
| **CrewAI Enterprise** | API Key + 部署环境管理 |
| **工具认证** | 每个 Tool 自行处理（API Key / OAuth 等） |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|------|
| `crew.kickoff(inputs={})` | 同步执行，返回 CrewOutput | 包装为 AIM send → response |
| `crew.kickoff_async(inputs={})` | 异步执行 | 适合 AIM 异步消息模式 |
| `Task(output_pydantic=Schema)` | 结构化输出 | 映射到 AIM 结构化 response 字段 |
| `task.human_input=True` | 人类审批 | 映射到 AIM human_in_loop 消息类型 |
| `agent.memory=True` | Agent 记忆 | 可与 AIM session 记忆层对接 |
| Flow `@listen` | 事件驱动步骤 | 映射到 AIM event/notification |
| A2A 协议 | Agent 间委派 | 直接复用 A2A 适配器 |
| Tool calls | 工具调用轨迹 | 解析 tool 调用 → AIM action/result |

### 适配要点

- CrewAI **无内置 REST API**，AIM 适配方式：**subprocess 包装器** — 在子进程启动 Python 脚本
- `kickoff()` 是阻塞调用，适合请求-响应模式；`kickoff_async()` 适合流式场景
- CrewOutput 包含 `raw`(文本)、`pydantic`(结构化对象)、`token_usage`(用量) → 可完整映射到 AIM 信封
- **A2A 原生支持**意味着 CrewAI → A2A 适配器可复用（先转 A2A，再转 AIM）
- 风险：subprocess 管理复杂（资源限制、超时 kill、僵尸进程清理）

---

## 8. AutoGen (Microsoft, v0.4+)

### 概述

Microsoft 的多 Agent 对话框架。2025年1月 v0.4 全面重构为异步事件驱动+Actor 模型架构。2026年4月合并 Semantic Kernel → **Microsoft Agent Framework 1.0 GA**。原生支持 .NET + Python，内置 OpenTelemetry 观测。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | Actor Model — Agent 通过异步消息总线通信（Publish/Subscribe 模式） |
| **核心 API** | `agent.on_message(msg)` — Agent 接收消息并返回响应 |
| **消息传递** | Agent → Topic → Agent（解耦的 pub/sub，支持广播、组播、点对点） |
| **编排方式** | SelectorGroupChat / RoundRobinGroupChat — 多种编排策略 |
| **流式** | 内置 streaming — `on_message_stream()` 生成器 |
| **跨语言** | Python ↔ .NET Agent 互操作（通过标准化消息格式） |
| **分布式** | 支持跨进程/跨机器 Agent（通过消息总线扩展） |
| **Human-in-Loop** | 内置 — Agent 可请求人类输入，支持审批/编辑/拒绝 |

### 消息格式

```
// AutoGen AgentMessage (内部消息总线格式)
{
  "type": "TextMessage",          // 消息类型
  "content": "研究结果...",
  "source": "researcher_agent",   // 发送者
  "models_usage": {               // LLM 用量追踪
    "prompt_tokens": 150,
    "completion_tokens": 80
  },
  "metadata": {}                  // 扩展元数据
}

// 工具调用消息
{
  "type": "ToolCallRequestEvent",
  "content": [
    {"name": "search_web", "arguments": {"query": "..."}}
  ]
}

// 工具结果
{
  "type": "ToolCallExecutionEvent",
  "content": [
    {"name": "search_web", "content": "搜索结果...", "call_id": "call_1"}
  ]
}

// Handoff 消息
{
  "type": "HandoffMessage",
  "content": "需要架构师审查",
  "target": "architect_agent",
  "context": [...]
}
```

### 核心抽象

| 抽象 | 用途 |
|------|------|
| **Agent** | 基础 Agent（AssistantAgent 等内置类型 + 自定义） |
| **Topic** | 消息主题 — Agent 发布/订阅的消息通道 |
| **Subscription** | `TypeSubscription(topic, agent, message_filter)` — Agent 订阅特定消息 |
| **GroupChat** | 编排策略容器 — SelectorGroupChat(LLM选择下一个) / RoundRobinGroupChat(轮询) |
| **ToolAgent** | 工具执行专用 Agent |
| **CodeExecutorAgent** | 代码执行 Agent（沙箱执行 Python） |
| **Magentic-One** | 预构建多 Agent 系统（Orchestrator+WebSurfer+FileSurfer+Coder+ComputerTerminal） |

### 认证方案

| 方式 | 说明 |
|------|------|
| **LLM 层** | 标准 API Key — OpenAI / Azure OpenAI / 本地模型 |
| **Agent 间通信** | 无认证（进程内/信任网络内） |
| **跨网络** | gRPC + TLS (Microsoft Agent Framework 1.0) |
| **Azure AI Foundry** | 托管认证（Managed Identity / API Key） |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|------|
| `agent.on_message(msg)` | 消息接收 | AIM → AutoGen 消息转换（AIM 信封 → AgentMessage） |
| `agent.on_message_stream(msg)` | 流式响应 | 映射到 AIM 流式事件 |
| `topic.publish(msg)` | 发布消息到 Topic | AIM 消息投递 → 对应 Topic |
| GroupChat 编排 | 多 Agent 协作 | 可作为 AIM 内部路由机制复用 |
| HandoffMessage | Agent 委派 | 映射到 AIM route/target 字段 |
| ToolCallRequestEvent/ExecutionEvent | 工具调用 | 映射到 AIM action/result |
| `models_usage` 字段 | LLM 用量追踪 | 映射到 AIM usage 统计 |

### 适配要点

- AutoGen v0.4+ 的 **Actor Model** 与 AIM 的消息信封模型高度兼容
- 消息格式已有标准化字段（type/content/source/metadata）— 映射到 AIM 信封工作量小
- 分布式支持意味着 Agent 可独立部署，AIM 作为消息路由中间件
- **注意**：AutoGen 已合并为 **Microsoft Agent Framework**（2026.04.03），建议适配目标改为新框架
- Python subprocess 包装器或 gRPC 桥接两种适配方式可选

---

## 9. MetaGPT (DeepWisdom)

### 概述

多 Agent 框架，模拟软件公司的角色分工（产品经理/架构师/项目经理/工程师/QA），通过标准操作流程（SOP）驱动 Agent 协作完成复杂任务。GitHub 66k+ stars，MIT 开源。

### 通信机制

| 维度 | 详情 |
|------|------|
| **传输** | Python SDK — 所有 Agent 在同一进程内运行 |
| **核心 API** | `Team(roles=[...], env=env, idea="任务描述").run(n_round=5)` |
| **Agent 通信** | **共享 Message Pool** — 所有 Agent 写入 Environment 的共享消息池，各自读取所需消息 |
| **调度方式** | SOP 驱动 — 每轮按角色顺序（PM → Architect → PM → Engineer → QA）依次执行 |
| **多轮迭代** | 通过 `n_round` 控制迭代次数，每轮所有角色执行一次 |
| **人类参与** | `add_human=True` — 插入人类角色参与协作 |

### 消息格式

```
// MetaGPT Message 类
class Message(BaseModel):
    content: str              # 消息文本内容
    instruct_content: Optional[BaseModel]  # 结构化指令（如 PRD、Design、Code）
    role: str                 # 发送者角色：product_manager / architect / engineer ...
    cause_by: str             # 触发 Action 名称
    sent_from: str            # 发送 Agent 名称
    send_to: set[str]         # 接收者（公开给所有 / 定向给特定角色）
    restricted_to: set[str]   # 权限限制

// Environment 共享消息池
{
  "messages": [
    Message(role="product_manager", content="需求文档 PRD..."),
    Message(role="architect", content="系统设计 Design..."),
    Message(role="engineer", content="代码实现 code...")
  ]
}

// Action 输出 (结构化)
ProductManager.WritePRD → "PRD 文档内容"
Architect.WriteDesign → "系统架构设计"
Engineer.WriteCode → "def main():..."
```

### 核心抽象

| 抽象 | 用途 |
|------|------|
| **Role** | 基类 — 定义 Agent 的角色身份、行为和记忆 |
| **Action** | Agent 可执行的动作（WritePRD / WriteDesign / WriteCode / WriteTest / FixBug / SearchAndSummarize） |
| **Environment** | 共享工作空间 — 消息池 + 项目文件 + SOP 规则 |
| **Message** | 通信单元 — content + instruct_content(结构化) + role + send_to |
| **Memory** | Agent 记忆 — 短期(消息历史) + 长期(文件) |
| **Team** | 团队编排容器 — roles + env + n_round + idea |
| **SOP (Standard Operating Procedure)** | 每次 run() 迭代中角色执行顺序 |

### 预定义角色

| 角色 | 职责 | 核心 Action |
|------|------|-------------|
| **ProductManager** | 需求分析、编写 PRD | `PrepareDocuments` → `WritePRD` |
| **Architect** | 系统架构设计 | `WriteDesign` |
| **ProjectManager** | 任务分解 | `WriteTasks` |
| **Engineer** | 代码实现 | `WriteCode` / `WriteTest` / `FixBug` |
| **QaEngineer** | 质量保证 | `WriteTest` / `RunCode` / `DebugError` |
| **Searcher** | 信息检索 | `SearchAndSummarize` |

### 认证方案

| 方式 | 说明 |
|------|------|
| **LLM 层** | 标准 API Key（OpenAI / Anthropic / Google / 本地模型），通过 `config.yaml` 配置 |
| **Agent 间** | 无（纯进程内，共享 Message Pool） |
| **外部暴露** | 无内置 API — 需自行包装 |

### 集成点 / AIM 适配入口

| 入口 | 说明 | AIM 适配方式 |
|------|------|------|
| `team.run(idea="...")` | 任务启动入口 | AIM send → team.run() → 收集结果 → AIM response |
| `env.messages` | 共享消息池 | 每轮迭代后拉取全部 Message → AIM 事件流 |
| `message.role` / `message.cause_by` | 消息溯源 | 映射到 AIM meta.source_role / meta.action |
| `message.instruct_content` | 结构化输出 | PRD/Design/Code 等 → AIM structured_content 字段 |
| `add_human=True` | 人类审批 | 映射到 AIM human_in_loop |
| `n_round` 进度 | 迭代进度 | 可暴露为 AIM progress/status 事件 |

### 适配要点

- MetaGPT 是**纯进程内框架**，AIM 适配方式：**subprocess 包装器**
- `team.run()` 是同步阻塞长时调用（完整 SOP 多轮），需超时控制
- Message Pool 提供完整的 Agent 交互历史 → 可映射到 AIM 事件/action/result 三类消息
- `instruct_content` 的结构化输出（PRD/Design/Code）是 MetaGPT 的独特优势 → AIM 需支持 rich content 字段
- 风险：长时间运行（n_round ≥ 5 可能耗时 5-10 分钟），需要流式进度推送机制
- SOP 流程固定，不像 AutoGen/CrewAI 灵活 — 更适合结构化开发任务

---

### 四大框架横向对比（补充到 TOP10 决策矩阵）

| 框架 | 分类 | 通信 | 暴露方式 | 流式支持 | 适配难度 | AIM 适配方式 |
|------|------|------|---------|---------|---------|-------------|
| LangGraph | API 暴露 | Python SDK / LangServe REST+SSE | ✅ 内置 REST API | ✅ 5种模式 | ★★☆ | REST 适配器 或 subprocess |
| CrewAI | 进程内 | Python SDK + A2A | ❌ 需自行包装 | ⚠️ async kickoff | ★★★ | subprocess 包装器 |
| AutoGen v0.4+ | 进程内/分布式 | Actor Model 消息总线 | ❌ 需 gRPC 桥接 | ✅ 内置 streaming | ★★★ | 消息桥接 或 subprocess |
| MetaGPT | 进程内 | 共享 Message Pool | ❌ 需自行包装 | ❌ 无内置 | ★★★ | subprocess 包装器 |

### 更新后的适配优先级

```
Phase 1 — 协议原生 (MCP, A2A)                  → 直接复用
Phase 2 — API 暴露 (Dify, LangGraph, Coze, n8n) → REST 适配器（LangGraph 有 LangServe，工作量最小）
Phase 3a — 进程内-易 (OpenAI SDK)               → SDK 包装器
Phase 3b — 进程内-中 (CrewAI, AutoGen, MetaGPT)  → subprocess 包装器，需进程管理
```

> **调研时间**：2026-06-14 ｜ **调研人**：呱呱 (ZS0001)
