# AIM 标准接口需求 — 三方框架技术调研报告 v1.0

> 调研时间：2026-06-14
> 调研范围：Google A2A v1.0 / Anthropic MCP v2025-11-25 / IBM ACP
> 调研方式：以官方文档为准，提取最小接入代码和 envelope 结构

---

## 一、调研概要

| 框架 | 主导方 | 定位 | 当前状态 | 是否独立存活 |
|------|--------|------|----------|-------------|
| **A2A** | Google → Linux Foundation | Agent ↔ Agent 通信 | v1.0 稳定（2025年4月发布） | ✅ 是 |
| **MCP** | Anthropic → Linux Foundation (AAIF) | Agent ↔ Tool / 数据源 | v2025-11-25 稳定（2024年11月发布） | ✅ 是 |
| **ACP** | IBM → Linux Foundation (LF AI & Data) | Agent ↔ Agent（REST风格） | **2025年8月合并入 A2A** | ❌ 已停止独立演进 |

---

## 二、A2A (Agent-to-Agent Protocol) — Google

### 2.1 定位

Agent-to-Agent 通信标准。A2A 不关心 Agent 内部用什么框架、工具链，只定义 Agent 之间如何互相发现、委托任务、交换结果。

### 2.2 Wire Protocol

- **传输层**：HTTP/1.1、HTTP/2、gRPC
- **消息格式**：JSON-RPC 2.0
- **流式**：SSE (Server-Sent Events)
- **推送**：HTTP POST webhook

### 2.3 核心数据结构（Envelope）

```
Send Message Request:
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tasks/send",
  "params": {
    "id": "task-xxx",
    "message": {
      "role": "user",
      "parts": [
        {"type": "text", "text": "调研一下竞品"}
      ]
    }
  }
}
```

```
Agent Card（元数据发现）:
{
  "name": "税务分析Agent",
  "description": "处理企业税务合规分析",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [
    {"id": "tax-analysis", "name": "税务分析", "description": "分析企业税务数据"}
  ]
}
```

### 2.4 核心概念

| 概念 | AIM 对应关系 |
|------|-------------|
| **AgentCard** | ↔ AIM identity.json（身份+能力描述） |
| **Task**（任务单元） | ↔ AIM handler.sh 的一次调用 + 状态追踪 |
| **Message**（消息交换单元） | ↔ AIM 的 发送方+消息体，但 AIM 是纯文本，A2A 是结构化 Parts |
| **Parts**（多模态内容块） | ↔ AIM 无此概念，目前只有纯文本 |

### 2.5 状态机

```
SUBMITTED → WORKING → INPUT-REQUIRED → COMPLETED
                    ↘ FAILED / CANCELLED（终端状态）
```

- `INPUT-REQUIRED` 支持 Human-in-the-loop：Agent 暂停，请求额外输入后恢复

### 2.6 最小接入代码（Python SDK）

**Server端（~15行）：**
```python
from a2a import A2AServer, A2ARequest, A2AResponse
from a2a.types import Task, Message, TextPart

app = A2AServer()

@app.agent
async def my_agent(req: A2ARequest) -> A2AResponse:
    message = req.params.message.parts[0].text
    return A2AResponse(
        task=Task(
            id=req.params.id,
            messages=[Message(role="agent", parts=[TextPart(text=f"收到: {message}")])],
            status="completed"
        )
    )

# 启动：uvicorn app:app --port 9999
```

**Client端（~8行）：**
```python
from a2a import A2AClient

async with A2AClient("http://localhost:9999") as client:
    card = await client.agent_card()
    result = await client.send_message("你好，Agent")
    print(result.task.messages[-1].parts[0].text)
```

### 2.7 版本兼容

- 当前稳定版：v1.0（2026-03-05）
- v0.3 → v1.0 有迁移指南（Issue #742）
- Python SDK 最新 v1.1.0（2026-05-29）
- SDK 支持多种传输：JSON-RPC、HTTP+REST、gRPC

### 2.8 治理

- 起源 Google，已捐赠 **Linux Foundation**
- 技术指导委员会：AWS、Cisco、Google、IBM、Microsoft、Salesforce、SAP、ServiceNow
- 开源许可：Apache 2.0
- 6种语言 SDK：Python/JS/Java/C#/Go/Rust

---

## 三、MCP (Model Context Protocol) — Anthropic

### 3.1 定位

Agent-to-Tool / Agent-to-Data 通信标准。MCP 的核心是让 LLM 应用可以调用外部工具、读取外部数据源。**它不是 Agent-Agent 通信协议。**

### 3.2 Wire Protocol

- **传输层**：STDIO（本地进程间通信）、HTTP+SSE（远程）
- **消息格式**：JSON-RPC 2.0
- 流式：SSE

### 3.3 核心数据结构（Envelope）

```
Tool Call Request:
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "calculate_tax",
    "arguments": {"company": "北京顿开科技", "year": 2026}
  }
}
```

```
会话初始化（能力协商）:
Client → Server:
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {}, "resources": {}},
    "clientInfo": {"name": "claude-desktop", "version": "1.0.0"}
  }
}

Server → Client:
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {}, "prompts": {}},
    "serverInfo": {"name": "my-server", "version": "1.0.0"}
  }
}
```

### 3.4 核心概念

| 概念 | 说明 |
|------|------|
| **Host** | LLM 应用（Claude Desktop、ChatGPT） |
| **Client** | Host 内部的连接器，管理 N 个 Server |
| **Server** | 暴露工具/资源/提示的服务端 |
| **Tools** | 可调用的函数（对应 AIM 的工具层） |
| **Resources** | 可读取的数据（文件、API 输出） |
| **Prompts** | 模板消息和工作流 |
| **Sampling** | Server 反向向 LLM 请求生成文本 |

### 3.5 最小接入代码（Python SDK, 使用 FastMCP）

**MCP Server（~5行）：**
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def hello(name: str) -> str:
    """给指定的 name 打招呼"""
    return f"你好, {name}!"

if __name__ == "__main__":
    mcp.run()  # 默认 STDIO 模式，HTTP 模式用 mcp.run("sse")
```

### 3.6 版本兼容

- 起源：2024年11月 Anthropic 发布
- 当前稳定版：2025-11-25
- 2025年12月捐赠给 **AAIF (Agentic AI Foundation)**，Linux Foundation 管辖
- 生态最广：Claude Desktop / ChatGPT / VS Code / Cursor / Cline 均原生支持
- 社区仓库：86k+ stars（official servers）
- 安全性：用户必须显式批准每个工具调用

---

## 四、ACP (Agent Communication Protocol) — IBM

### 4.1 现状

**2025年8月合并入 A2A**，不再独立存在。

### 4.2 历史贡献

- RESTful 设计（HTTP CRUD on Agents/Threads/Runs）
- OpenAPI-first 规范定义
- 多模态 Part 设计（Parts + MIME Type + content_url）
- 内置 OpenTelemetry 追踪
- "awaiting" 中间状态（Human-in-the-loop）

### 4.3 核心消息格式（作为参考）

```
ACP Message Envelope:
{
  "role": "agent/research-analyst",
  "parts": [
    {"content_type": "text/plain", "content": "分析完成。"},
    {"content_type": "application/json", "content": "{\"score\": 0.73}"},
    {"content_type": "application/pdf", "content_url": "https://.../report.pdf"}
  ]
}
```

---

## 五、三方框架关键差异矩阵

| 维度 | A2A (Google) | MCP (Anthropic) | ACP (IBM) |
|------|-------------|-----------------|-----------|
| 定位 | Agent ↔ Agent | Agent ↔ Tool | Agent ↔ Agent（已合并） |
| 传输 | HTTP/SSE/gRPC | STDIO/HTTP+SSE | HTTP/REST |
| 消息格式 | JSON-RPC 2.0 | JSON-RPC 2.0 | REST CRUD |
| 核心单位 | **Task**（有状态生命周期） | **方法调用**（无状态） | **Thread+Run** |
| 多模态 | ✅ Parts[]（text/file/data） | ✅ ContentBlock[]（text/image/audio） | ✅ Parts（MIME Type） |
| 发现机制 | AgentCard（JSON元数据） | 能力协商（初始化握手） | 无专用发现 |
| 状态追踪 | Task FSM（6个状态） | Session 生命周期 | Run FSM |
| 流式 | SSE + Webhook | SSE | SSE |
| 最小接入 | ~15 行 Python | ~5 行 Python（FastMCP） | 同 A2A |
| SDK 语言数 | 6（Py/JS/Java/C#/Go/Rust） | 4（Py/TS/C#/Java） | 同 A2A |
| 生态 | 较新，社区增长中 | 最成熟（86k+ stars） | 已停止 |
| 治理 | Linux Foundation | Linux Foundation (AAIF) | 无 |
| 认证 | OAuth 2.0 + 可扩展 | OAuth 2.1 + 可插拔 | 同 A2A |

---

## 六、与 AIM v4.2 Envelope 的适配对比

### AIM v4.2 当前结构

```
发送: aim_send.py <target> <plain_text>
处理: handler.sh — SENDER="$1" MESSAGE="$2"
消息体: 纯文本字符串（无结构化字段）
认证: HMAC + 连接时注册
目录: identity.json 自描述
传输: WebSocket（持久连接）
```

### 对接难度评估

| 维度 | A2A | MCP |
|------|-----|-----|
| 消息体升级 | handler 加 JSON parse | 不匹配（Tool 调用模式） |
| 发现机制 | ✅ AgentCard ↔ identity.json 1:1 | ❌ 能力协商不同 |
| 状态追踪 | ✅ Task FSM 可映射到 handler 调用 | ❌ 无状态 |
| 多模态 | ✅ Parts 可逐步引入 | ✅ ContentBlock 也可 |
| 传输层 | WebSocket ↔ HTTP 需 bridge | STDIO 不匹配 WS |
| 认证 | 都支持可扩展 | 都支持可扩展 |
| **整体适配** | **★★★★☆** | **★★★☆☆** |

---

## 七、推荐结论

### 最适配 AIM envelope 的第一方案：✅ **A2A**

理由：
1. **A2A 和 AIM 的定位完全一致**——都是 Agent-to-Agent 通信协议。MCP 是 Agent-to-Tool，不在同一赛道。
2. **A2A 的 AgentCard + AIM identity.json 1:1 对应**——AIM 注册制天然提供 Agent 身份和能力元数据。
3. **A2A 的 Task 生命周期** ——AIM handler.sh 的一次回调可以映射为一个 Task，天然支持状态追踪、超时重试、人工介入（AIM 退出码 3）。
4. **A2A 的 handler 模式**——A2A 的 agent_executor.py 和 AIM 的 handler.sh 结构一致：收到消息 → 处理 → 返回结果。
5. **行业收敛信号强**——ACP（IBM）已合并入 A2A，说明 Agent-Agent 通信标准正在向 A2A 收敛。
6. **6种语言 SDK**——未来跨框架 Agent 接入时不需要自行实现协议栈。
7. **Linux Foundation 治理**——非单厂商控制，适合作为长期标准。

### 第二方案：MCP（partial use）

MCP 适合作为 AIM 网络的**工具暴露层**——通过 MCP Server 将 AIM 网络内的工具/资源暴露给外部 AI（Claude、ChatGPT 等），但不适合作为 AIM 的主通信协议。

---

## 八、参考文档

| 文档 | 链接 |
|------|------|
| A2A 规范 v1.0 | https://a2a-protocol.org/latest/specification |
| A2A Python SDK | https://github.com/a2aproject/a2a-python |
| A2A Samples | https://github.com/a2aproject/a2a-samples |
| MCP 规范 v2025-11-25 | https://modelcontextprotocol.io/specification/2025-11-25 |
| MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk |
| FastMCP | https://github.com/jlowin/fastmcp |
| ACP → A2A 合并 | https://modelcontextprotocol.io （2025年8月更新） |
| AIM v4.2 标准方案 | ~/shared/aim/aim-standard-v4.md |
