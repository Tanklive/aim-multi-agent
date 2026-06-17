# AIM 标准接口调研 — 五框架对比

**作者**: ZS0002 (吉量)
**任务**: 大哥通过 ZS0003 (小火鸡儿) 分配 — AIM 标准接口调研 Round 2
**日期**: 2026-06-15
**版本**: v1.0

---

## 1. MCP (Model Context Protocol) — Anthropic

> 官网: https://modelcontextprotocol.io
> 协议版本: v1.0 (2025 年移交 AAIF/Linux Foundation)
> 许可证: Apache 2.0

### 1.1 通信方式

| 传输层 | 用途 | 连接方向 |
|--------|------|----------|
| **stdio** | 本地子进程通信（推荐） | 客户端↔服务端 (stdin/stdout) |
| **HTTP/HTTPS** | 网络 API 访问 | 客户端→服务端 |
| **WebSocket** | 实时双向通信 | 全双工 |

- 核心消息层: **JSON-RPC 2.0**
- 不支持 gRPC
- 标准端口: 无固定端口（自行配置）
- 连接模型: **请求-响应** + **通知**（单向，无响应）

### 1.2 消息格式

```
JSON-RPC 2.0 Request:
{
  "jsonrpc": "2.0",
  "id": "unique-id",
  "method": "tools/call",
  "params": { ... }
}

JSON-RPC 2.0 Response:
{
  "jsonrpc": "2.0",
  "id": "unique-id",
  "result": { ... }
}

Notification (无 id):
{
  "jsonrpc": "2.0",
  "method": "notifications/resources/updated",
  "params": { ... }
}
```

**核心 Namespace 方法:**
| 分组 | 方法 | 说明 |
|------|------|------|
| 初始化 | `initialize` | 握手机制，协商版本+能力 |
| 工具 | `tools/list`, `tools/call` | 工具查询与调用 |
| 资源 | `resources/list`, `resources/read` | 资源访问 |
| 提示 | `prompts/list`, `prompts/get` | 模板化提示词 |
| 补全 | `completion/complete` | 参数自动补全 |

### 1.3 安全机制

- **stdio 模式**: 无认证（进程隔离保证安全）
- **HTTP 模式**: 无内置认证标准（由宿主应用自行实现 API Key / OAuth）
- 早期参考实现使用 Bearer Token
- 无内置速率限制（宿主应用层实现）

### 1.4 最简接入示例

**Python MCP Server (stdio，~8 行核心):**
```python
from mcp.server import Server, stdio_server
from mcp.types import Tool, TextContent

server = Server("demo")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [Tool(name="greet", description="Say hello",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}}})]

@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    return [TextContent(type="text", text=f"Hello, {args['name']}!")]

server.run(stdio_server())
```

**Python MCP Client (HTTP):**
```python
import httpx

response = httpx.post("http://localhost:8000/mcp", json={
    "jsonrpc": "2.0", "id": "1",
    "method": "tools/call",
    "params": {"name": "greet", "arguments": {"name": "World"}}
})
print(response.json()["result"])
```

### 1.5 关键限制

- **超时**: 无标准超时定义，Client 自行实现超时
- **并发**: 单 TCP 连接顺序处理（HTTP）；stdio 模式受子进程限制
- **速率限制**: 无内置；依赖宿主应用
- **流式**: 默认不支持；可通过 SSE/WebSocket 扩展实现
- **容量**: 单条消息受实现限制（常用 ≤16MB）
- **初始化开销**: 每次连接需要 `initialize` 往返协商
- **生态**: 强在工具/资源暴露，弱在 Agent↔Agent 协作

---

## 2. A2A (Agent-to-Agent Protocol) — Google

> 官网: https://a2aprotocol.ai
> 协议版本: v0.2.5
> 许可证: Apache 2.0

### 2.1 通信方式

| 传输层 | 用途 | 说明 |
|--------|------|------|
| **HTTP/1.1** | 标准请求-响应 | Task 提交、查询、取消 |
| **HTTP/2** | 可选升级 | 提高并发性能 |
| **SSE** | 流式更新 | 长任务实时推送 state transition |

- 核心消息层: **JSON-RPC 2.0**（与 MCP 同层）
- 默认端口: **41241**（参考实现）
- 连接模型: **Client→Server Task 驱动**，支持 Push Notification 回调

### 2.2 消息格式

```json
TaskSendParams:
{
  "id": "task-uuid",
  "message": {
    "role": "user",
    "parts": [{ "text": "Hello, agent!" }]
  }
}

Task 生命周期状态:
  submitted → working → input-required → completed
                                          → canceled
                                          → failed
                                          → rejected
                          → auth-required
```

**A2A 核心 RPC 方法:**
| 方法 | 说明 |
|------|------|
| `tasks/send` | 提交 Task（非流式） |
| `tasks/sendSubscribe` | 提交 Task + SSE 实时订阅 |
| `tasks/get` | 查询 Task 状态 |
| `tasks/cancel` | 取消 Task |
| `tasks/pushNotification/set` | 设置 Push 回调 URL |
| `tasks/resubscribe` | 断连后重新订阅 |

**Part 类型:**
- `TextPart`: `{ "type": "text", "text": "..." }`
- `FilePart`: `{ "type": "file", "file": { "uri": "...", "mimeType": "..." } }`
- `DataPart`: `{ "type": "data", "data": { ... } }`

### 2.3 安全机制

| 方式 | 说明 |
|------|------|
| **API Key / Bearer Token** | 标准 HTTP Authorization header |
| **OAuth 2.0** | 支持 Authorization Code / Client Credentials |
| **Agent Card 声明** | 通过 `AgentCard.securitySchemes` 声明安全方案 |
| **无内置限流** | 宿主层实现 |

**AgentCard 暴露:**
```json
{
  "name": "MyAgent",
  "url": "https://my-agent.example.com/a2a",
  "skills": [ ... ],
  "capabilities": { "streaming": true },
  "securitySchemes": {
    "bearerAuth": { "type": "http", "scheme": "bearer" }
  }
}
```

### 2.4 最简接入示例

```python
import httpx, uuid

# Client: 提交 Task
task_id = str(uuid.uuid4())
response = httpx.post("http://localhost:41241/a2a", json={
    "jsonrpc": "2.0", "id": "1",
    "method": "tasks/send",
    "params": {
        "id": task_id,
        "message": {"role": "user", "parts": [{"type": "text", "text": "Hello"}]}
    }
})
print(response.json())
```

### 2.5 关键限制

- **超时**: 无内置超时；`sendSubscribe` 依赖 SSE 保活（心跳间隔未标准化）
- **并发**: HTTP/1.1 受连接池限制；HTTP/2 多路复用可改善
- **速率限制**: 无内置；宿主实现
- **任务持久化**: 协议不保证；需 Server 端自行实现 TaskStore
- **推送通知**: 需要 Agent 暴露可回调 URL（公网可达）
- **协议成熟度**: v0.2.5 — API 仍在演进，不稳定
- **生态**: 概念新，生产案例少（2025 年发布）
- **Agent 发现**: 依赖 AgentCard 的 Well-Known URL 机制

---

## 3. LangGraph — LangChain

> 官网: https://langchain-ai.github.io/langgraph
> 平台: LangGraph Server (langgraph-api) + SDK
> 许可证: MIT

### 3.1 通信方式

| 传输层 | 用途 | 说明 |
|--------|------|------|
| **REST (HTTP)** | 核心 API | Thread/Run/Assistant 管理 |
| **SSE** | 流式响应 | Token 级、Node 级流式推送 |
| **WebSocket (内部)** | LangGraph Studio 调试 | 开发工具专用 |

- 核心格式: **自定义 REST JSON**（非 JSON-RPC）
- 不是标准协议，是框架的 HTTP 封装
- 端口: **2024** (dev)，生产端口自配
- 部署方式: Docker 容器 (langgraph up) / LangGraph Cloud

### 3.2 消息格式

**REST API 端点 (~30+):**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/assistants` | GET | 列出可用 Graph |
| `/threads` | POST | 创建会话线程 |
| `/threads/{id}/runs` | POST | 执行一次 Run |
| `/threads/{id}/runs/{rid}/stream` | GET | SSE 流式输出 |
| `/threads/{id}/state` | GET | 获取当前状态 |

**Run 请求体:**
```json
{
  "messages": [
    {"role": "user", "content": "What's the weather in Tokyo?"}
  ]
}
```

**SSE 事件流 (stream_mode="values"):**
```
event: values
data: {"messages": [{"role": "user", ...}, {"role": "assistant", "content": "..."}]}

event: values
data: {"messages": [..., {"role": "tool", "content": "..."}]}
```

**三種 Stream Mode:**
| Mode | 粒度 | 适合场景 |
|------|------|----------|
| `values` | 全状态快照 | 后端编排/调试 |
| `updates` | 键级变更 diff | 轻量进度条 |
| `astream_events` | Token + Tool 事件 | 前端逐字渲染 |

### 3.3 安全机制

- **框架层无内置认证**（由外包 FastAPI middleware 实现）
- LangGraph Cloud: API Key + LangSmith 认证
- 自部署: 自行实现 API Key / JWT middleware
- Checkpoint: 支持 PostgreSQL 持久化（非内存）

### 3.4 最简接入示例

**Server 端 (agent.py，~10 行核心):**
```python
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_openai import ChatOpenAI

builder = StateGraph(MessagesState)
builder.add_node("chat", lambda s: {"messages": [ChatOpenAI().invoke(s["messages"])]})
builder.add_edge(START, "chat")
builder.add_edge("chat", END)
graph = builder.compile()
```

**Client 端 (Python SDK):**
```python
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2024")
thread = await client.threads.create()
async for event in client.runs.stream(
    thread["thread_id"], "agent",
    input={"messages": [{"role": "user", "content": "Hi"}]}
):
    if event.event == "values":
        print(event.data["messages"][-1]["content"])
```

### 3.5 关键限制

- **超时**: 默认 60s（可配置，`run_timeout` 参数）
- **并发**: 单线程默认 1；需 `max_concurrency` 配置 + 多 Worker
- **速率限制**: 无内置；自行实现
- **部署依赖**: 生产需 Docker + PostgreSQL
- **平台绑定**: 强耦合 LangChain 生态；非通用协议
- **状态管理**: Thread 有状态（需管理 thread_id）
- **开发模式**: 内存存储，重启丢失

---

## 4. Dify — 对话型应用 REST API

> 官网: https://docs.dify.ai
> API 版本: v1
> 许可证: Apache 2.0 (Self-hosted) + Cloud

### 4.1 通信方式

| 传输层 | 用途 | 说明 |
|--------|------|------|
| **REST (HTTP/HTTPS)** | 核心 API | 消息发送、会话管理 |
| **SSE** | 流式响应 | Token 级逐字推送 |

- 基础 URL: `https://api.dify.ai/v1` 或自部署 `{host}/v1`
- 核心端点: `POST /chat-messages`
- 非标准 JSON-RPC — 自定义 REST Schema
- 无 WebSocket / gRPC 支持

### 4.2 消息格式

**请求 (POST /chat-messages):**
```json
{
  "inputs": {},
  "query": "What is Dify?",
  "response_mode": "blocking",
  "conversation_id": "",
  "user": "user-123",
  "files": []
}
```

**响应 (blocking 模式):**
```json
{
  "event": "message",
  "message_id": "9da23599-...",
  "conversation_id": "45701982-...",
  "answer": "Dify is an open-source LLM app development platform...",
  "created_at": 1705407629,
  "metadata": {
    "usage": {
      "total_tokens": 219,
      "total_price": "0.0001395"
    }
  }
}
```

**SSE 事件类型 (streaming 模式):**
| 事件 | 说明 |
|------|------|
| `message` | 逐 token 返回 answer 内容 |
| `message_end` | 消息结束，含完整 usage 元数据 |
| `error` | 错误事件 |
| `agent_message` | Agent 模式下逐 token 输出 |
| `agent_thought` | Agent 思考过程（工具调用等） |

**其他端点:**
| 端点 | 方法 | 说明 |
|------|------|------|
| `/files/upload` | POST | 文件上传（RAG / 多模态） |
| `/conversations` | GET | 获取会话列表 |
| `/messages` | GET | 获取历史消息 |
| `/messages/{id}/feedbacks` | POST | 消息反馈（点赞/点踩） |

### 4.3 安全机制

- **API Key (Bearer Token)**: `Authorization: Bearer {app_api_key}`
- 每个 App 独立 API Key（可在 Studio 创建）
- 无 OAuth 支持（适用于服务端-服务端）
- 无内置速率限制（自部署可加 Nginx 限流）
- 警告: 不要在 Client 端暴露 API Key

### 4.4 最简接入示例

```python
import requests

response = requests.post(
    "https://api.dify.ai/v1/chat-messages",
    headers={"Authorization": "Bearer app-xxx", "Content-Type": "application/json"},
    json={"inputs": {}, "query": "Hello", "response_mode": "blocking",
          "conversation_id": "", "user": "user-123"}
)
print(response.json()["answer"])
```

### 4.5 关键限制

- **超时**: 阻塞模式默认 300s；流式模式无限等待（SSE 常开）
- **并发**: 自部署受 Server 资源配置限制；Cloud 未公开限流
- **速率限制**: Cloud 版有限流（未公开具体值）；自部署自控
- **对话上下文**: 通过 `conversation_id` 维持；需 Client 端管理
- **非 Agent-to-Agent**: 设计为 App→User 接口，非多 Agent 协作
- **平台绑定**: API 深度绑定 Dify 平台概念（App / Workflow 等）
- **无推送通知**: SSE 仅从 Server 到 Client，单向

---

## 5. Coze (扣子) — 字节跳动

> 官网: https://www.coze.cn (中国) / https://www.coze.com (国际)
> API 版本: v3
> 认证: PAT / OAuth / JWT / OAuth PKCE

### 5.1 通信方式

| 传输层 | 用途 | 说明 |
|--------|------|------|
| **REST (HTTP/HTTPS)** | 标准对话 | `POST /v3/chat` |
| **SSE** | 流式对话 | `stream: true` 参数 |
| **WebSocket** | 实时语音+文字 | `@coze/api` SDK `client.websockets` |

- 基础 URL (中国): `https://api.coze.cn`
- 基础 URL (国际): `https://api.coze.com` (COZE_COM_BASE_URL)
- 非标准 JSON-RPC — 自定义 REST Schema
- WebSocket 主要用于语音对话，非通用 Agent 通信

### 5.2 消息格式

**请求 (POST /v3/chat):**
```json
{
  "bot_id": "73428668xxxx",
  "user_id": "user-123",
  "stream": false,
  "auto_save_history": true,
  "additional_messages": [
    {"role": "user", "content": "Hello", "content_type": "text"}
  ],
  "conversation_id": "conv_xxx",
  "custom_variables": {}
}
```

**请求参数详解:**
| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `bot_id` | 是 | string | 智能体 ID |
| `user_id` | 是 | string | 用户唯一标识 |
| `stream` | 否 | bool | 是否流式 (默认 false) |
| `conversation_id` | 否 | string | 维持上下文 |
| `auto_save_history` | 否 | bool | 自动保存历史 (默认 true) |
| `additional_messages` | 否 | array | 历史消息（手动传上下文） |

**响应 (非流式):**
```json
{
  "code": 0,
  "msg": "Success",
  "data": {
    "conversation_id": "conv_123",
    "bot_id": "73428668xxxx",
    "messages": [
      {
        "role": "assistant",
        "content": "您好！",
        "content_type": "text",
        "created_at": 1717777777
      }
    ],
    "usage": {"input_tokens": 20, "output_tokens": 30}
  }
}
```

**SSE 事件类型 (streaming 模式):**
| 事件 | 说明 |
|------|------|
| `CONVERSATION_CHAT_CREATED` | 会话创建 |
| `CONVERSATION_MESSAGE_DELTA` | Token 增量（逐字推送） |
| `CONVERSATION_MESSAGE_COMPLETED` | 单条消息完成 |
| `CONVERSATION_CHAT_COMPLETED` | 整个对话完成（含 usage） |
| `DONE` | 对话流结束 |

### 5.3 安全机制

| 方式 | 说明 |
|------|------|
| **PAT** (Personal Access Token) | 最简单方式：`Authorization: Bearer pat_xxx` |
| **OAuth 2.0** | 标准授权码流程 |
| **OAuth PKCE** | 移动端安全授权 |
| **JWT** | 服务端-服务端互信 |

- PAT 在 Coze 控制台生成，需配置权限
- bot_id 需与 PAT 属于同一账号

### 5.4 最简接入示例

```python
import requests

response = requests.post(
    "https://api.coze.cn/v3/chat",
    headers={"Authorization": "Bearer pat_xxx", "Content-Type": "application/json"},
    json={"bot_id": "73428668xxxx", "user_id": "user-123", "additional_messages": [
        {"role": "user", "content": "Hello", "content_type": "text"}
    ]}
)
print(response.json()["data"]["messages"][0]["content"])
```

### 5.5 关键限制

- **超时**: REST 模式默认约 30-60s；SSE 流式可持续较长时间
- **并发**: Cloud 有限流（未公开）+ 按使用量计价
- **user_id 管理**: 需 Client 生成并管理；COZE 用此做上下文隔离
- **bot_id 绑定**: 每次请求固定 bot，无法动态路由
- **平台绑定**: 强绑定 Coze 生态（bot/space 概念）
- **WebSocket 适用场景窄**: 主要用于语音对话，非通用 Agent 通信
- **国内/国际分域**: 域名不同，数据隔离

---

## 6. 横向对比总结

| 维度 | MCP (Anthropic) | A2A (Google) | LangGraph | Dify | Coze |
|------|------------------|--------------|-----------|------|------|
| **传输层** | stdio/HTTP/WS | HTTP+SSE | REST+SSE | REST+SSE | REST+SSE+WS |
| **消息格式** | JSON-RPC 2.0 | JSON-RPC 2.0 | 自定义 REST | 自定义 REST | 自定义 REST |
| **通信方向** | Client↔Server (工具) | Agent↔Agent (Task) | App↔Server | App↔Server | App↔Bot |
| **核心抽象** | Tools/Resources | Tasks | Threads/Runs | Conversation | Conversation |
| **认证** | 无内置 / API Key | OAuth / API Key | 无内置 / Cloud Key | Bearer Token | PAT / OAuth / JWT |
| **流式** | 扩展实现 | SSE (sendSubscribe) | SSE (3 modes) | SSE | SSE + WS |
| **就绪度** | 生产级 (v1.0) | 早期 (v0.2.5) | 生产级 (平台) | 生产级 | 生产级 |
| **开放度** | ★★★★★ 开放标准 | ★★★★☆ 开放标准 | ★★★☆☆ 框架封闭 | ★★☆☆☆ 平台封闭 | ★★☆☆☆ 平台封闭 |
| **Agent↔Agent** | 弱 | 强（设计目标） | 中（需 SDK） | 无 | 无 |

### 6.1 对 AIM 设计的启示

1. **消息层选择**: JSON-RPC 2.0 是 MCP 和 A2A 的共同选择，证明其适合 Agent 通信。AIM 可参考此标准。

2. **Task 模型**: A2A 的 Task 生命周期（submitted→working→completed/failed）比简单消息更适合异步 Agent 协作。

3. **流式支持**: 5 个框架都支持 SSE（某种程度），证明流式响应是标配，AIM 应支持。

4. **发现机制**: MCP 的 `tools/list` 和 A2A 的 AgentCard 都实现了能力发现，AIM 暂无此机制——值得引入。

5. **安全**: 主流模式 = Bearer Token + 可选 OAuth。AIM 当前使用固定 Token，可考虑升级到 PAT / JWT。

6. **C/S vs Agent↔Agent**:
   - MCP/LangGraph/Dify/Coze 本质是 C/S 架构（App 调用 Server）
   - A2A 是唯一原生 Agent↔Agent 协议
   - AIM 目前介于两者之间——可作为切入参考

### 6.2 推荐深入方向

| 优先级 | 方向 | 理由 |
|--------|------|------|
| P0 | JSON-RPC 2.0 消息格式标准化 | MCP 和 A2A 均采用，社区验证 |
| P0 | SSE 流式支持 | 所有框架标配，用户体验刚需 |
| P1 | Task 生命周期状态机 | A2A 模型成熟，直接可参考 |
| P1 | Agent 能力声明（AgentCard 类似物） | 发现机制，降低耦合 |
| P2 | OAuth/PAT 安全升级 | PAT 模式为 Coze 验证 |
| P3 | WebSocket 实时双向 | Coze WS 参考，长期规划 |

---

*调研完成。下一轮需要呱呱和小火鸡儿 review 后讨论对齐方向。*
