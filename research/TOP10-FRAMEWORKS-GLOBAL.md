# 全球 TOP10 智能体架构调研

> 日期: 2026-06-14
> 负责人: 小火鸡儿 🐤 (ZS0003)
> 协作: 吉量 🐴 (ZS0002) + 呱呱 🐸 (ZS0001)
> 状态: 完成

---

## 一、分类总览

全球智能体框架按通信/集成方式分三大类：

| 类别 | 特征 | 代表框架 |
|------|------|----------|
| **A. 协议原生类** | 自带 Agent 间通信协议 | MCP, A2A |
| **B. API 暴露类** | 通过 REST/WS 暴露 agent 能力 | LangGraph, Dify, Coze, n8n |
| **C. 进程内框架类** | Python 进程内调用，无原生网络通信 | CrewAI, AutoGen, MetaGPT, OpenAI SDK |

---

## 二、框架详细调研

### A1. MCP (Model Context Protocol) — Anthropic

- **定位**: AI 模型与外部工具的通信协议
- **通信方式**: JSON-RPC 2.0 over stdio / SSE
- **安全机制**: OAuth 2.0 (2025.3 起), API Key
- **消息格式**: JSON-RPC request/response/notification
- **最简接入**:
```json
{"jsonrpc":"2.0","method":"tools/list","id":1}
```
- **关键限制**: 单连接模型；长连接需 SSE；并发有限
- **AIM 适配**: MCP client → AIM envelope 转换器

### A2. A2A (Agent-to-Agent) — Google

- **定位**: 跨平台 Agent 通信协议
- **通信方式**: gRPC + HTTP/JSON
- **安全机制**: OAuth 2.0 + Service Account
- **消息格式**: Task (id, sessionId, messages[], metadata)
- **最简接入**:
```json
{"id":"task-1","sessionId":"sess-1","messages":[{"role":"user","parts":[{"text":"hello"}]}]}
```
- **关键限制**: gRPC 强依赖；需要 proto 编译；v0.2 仍在快速迭代
- **AIM 适配**: A2A Task ↔ AIM envelope 双向转换

### B1. LangGraph (LangChain)

- **定位**: 有状态 Agent/工作流框架
- **通信方式**: REST API + SSE streaming
- **安全机制**: API Key (LangSmith/LangServe)
- **消息格式**: State dict {messages: [...], config: {...}}
- **最简接入**:
```python
from langserve import add_routes
add_routes(app, graph, path="/agent")
```
- **关键限制**: Checkpointer 持久化；thread_id 必须；Platform API 2025 beta
- **AIM 适配**: HTTP client → langgraph endpoint → AIM

### B2. Dify

- **定位**: 开源 LLM 应用平台
- **通信方式**: REST API + SSE
- **安全机制**: API Key (Bearer), App Token
- **消息格式**: {query, conversation_id, user, inputs, response_mode}
- **最简接入**:
```bash
curl -X POST /v1/chat-messages -H "Authorization: Bearer $KEY" -d '{"query":"hi","user":"user-1","response_mode":"blocking"}'
```
- **关键限制**: conversation_id 必须连续；response_mode=streaming 需 SSE 解析
- **AIM 适配**: Dify chat-messages API ↔ AIM envelope

### B3. Coze (字节跳动)

- **定位**: AI Bot 开发平台
- **通信方式**: REST API + WebSocket
- **安全机制**: API Token (Bearer), OAuth 2.0
- **消息格式**: {bot_id, user_id, stream, additional_messages[], auto_save_history}
- **最简接入**:
```bash
curl -X POST /v3/chat -H "Authorization: Bearer $TOKEN" -d '{"bot_id":"xxx","user_id":"user-1","additional_messages":[{"role":"user","content":"hi"}]}'
```
- **关键限制**: conversation 由 Coze 管理；WS 仅用于实时对话
- **AIM 适配**: Coze chat API → AIM + conversation_id 映射

### B4. n8n

- **定位**: 自动化工作流平台
- **通信方式**: REST API + Webhook trigger
- **安全机制**: API Key, Basic Auth
- **消息格式**: 自定义 JSON body → workflow trigger
- **最简接入**:
```bash
curl -X POST /webhook/xxx -H "Content-Type: application/json" -d '{"message":"hi"}'
```
- **关键限制**: 非 AI-native，需手动建 workflow 连接 agent
- **AIM 适配**: Webhook → AIM；需 n8n 内建 AIM node

### C1. CrewAI

- **定位**: 多 Agent 协作框架
- **通信方式**: Python 进程内 (kickoff → Task → Agent)
- **安全机制**: 无原生网络层，依赖进程内权限
- **消息格式**: Python objects (Task, CrewOutput, TaskOutput)
- **最简接入**:
```python
from crewai import Agent, Task, Crew
crew = Crew(agents=[a1,a2], tasks=[t1,t2])
result = crew.kickoff()
```
- **关键限制**: 无原生跨进程通信；无网络暴露；需 wrapper 进程
- **AIM 适配**: 包装成 subprocess → stdin/stdout → AIM

### C2. AutoGen (Microsoft)

- **定位**: 多 Agent 对话框架
- **通信方式**: Python 进程内 (AgentEvent stream, subscribe)
- **安全机制**: 无原生网络层
- **消息格式**: AgentEvent {source, content, type}
- **最简接入**:
```python
from autogen_agentchat.messages import TextMessage
await runtime.subscribe(lambda event: print(event))
```
- **关键限制**: 事件流在进程内；无持久化消息总线
- **AIM 适配**: subscribe intercept → AIM envelope

### C3. MetaGPT

- **定位**: 多 Agent 软件公司模拟
- **通信方式**: Python 进程内 (Environment.publish_message)
- **安全机制**: 无原生网络层
- **消息格式**: Message {content, role, cause_by}
- **最简接入**:
```python
from metagpt.environment import Environment
env = Environment()
env.publish_message(Message(content="hi", role="user"))
```
- **关键限制**: SNS 消息模式；无跨进程能力
- **AIM 适配**: Environment.publish → bridge → AIM

### C4. OpenAI Agents SDK

- **定位**: OpenAI Agent 构建框架
- **通信方式**: Python 进程内 (handoff events)
- **安全机制**: API Key
- **消息格式**: HandoffEvent, RunResult, Agent.stream_events()
- **最简接入**:
```python
from agents import Agent, Runner
result = Runner.run_sync(agent, "hi")
```
- **关键限制**: 单 runner 进程；handoff 无网络层
- **AIM 适配**: Runner.stream_events() → intercept → AIM

---

## 三、关键发现

### 3.1 协议原生类（MCP, A2A）已定义通信格式
- MCP 的 JSON-RPC 可以直接映射到 AIM envelope
- A2A 的 Task 结构已有 message 列表，与 AIM DM 类似
- **AIM 应兼容这两种协议，作为一等公民**

### 3.2 API 暴露类（LangGraph, Dify, Coze, n8n）依赖 REST
- 共性：REST API + auth header + JSON body
- 差异：conversation 管理方式不同（session_id / conversation_id / thread_id）
- **AIM 适配器只需实现一个 HTTP client**

### 3.3 进程内框架（CrewAI, AutoGen, MetaGPT, OpenAI SDK）需要 wrapper
- 最大挑战：无原生网络通信能力
- 解决方案：AIM 客户端作为 subprocess wrapper 桥接
- **不需要改框架源码**

---

## 四、标准接口设计方向

```
AIM Envelope (不变)
    ↕
AIM Adapter (新增 — 每个框架一个)
    ↕
Target Framework (不改)
```

**4 个标准方法**:
1. `connect()` — 建立连接（HTTP/WS/subprocess）
2. `send(text)` — 发送消息
3. `receive()` — 接收消息（阻塞/stream/回调）
4. `capabilities()` — 查询框架能力

**适配层位置**: AIM 客户端侧，框架零侵入
