# AIM 标准接口需求方案 v1.0

> 作者: 小火鸡儿 🐤 (ZS0003)  
> 协作: 吉量 🐴 (ZS0002)  
> 日期: 2026-06-14  
> 状态: 待大哥评审

---

## 一、原始需求回顾

### 1.1 AIM 是什么
AIM (Agent Instant Messaging) 是一个**跨框架的 Agent 即时通信系统**，让不同框架（Hermes、OpenClaw、CrewAI等）的 Agent 之间能够实时收发消息，并自动触发 AI 处理。

### 1.2 OAS 愿景
> "先兼容天下，再形成标准，最后兼并。兼容一切已有协议（WS/REST/ACP等），让任何框架只要能发JSON消息就能接入。"

### 1.3 大哥本次指令
> "做一个能适配全球TOP不同架构智能体的AIM标准平台，通过AIM客户端标准接口接入，不对智能体架构调整。"

---

## 二、全球 TOP10 框架调研结论

### 2.1 三大分类

| 分类 | 特征 | 框架 | AIM 适配策略 |
|------|------|------|-------------|
| **A. 协议原生类** | 自带 Agent 通信协议 | MCP, A2A | 一级兼容，envelope 直转 |
| **B. API 暴露类** | REST/SSE 暴露能力 | LangGraph, Dify, Coze, n8n | HTTP client 适配 |
| **C. 进程内框架** | Python 进程内调用 | CrewAI, AutoGen, MetaGPT, OpenAI SDK | subprocess wrapper 桥接 |

### 2.2 横向关键技术指标

| 框架 | 传输层 | 消息格式 | 认证 | 流式 | 就绪度 | 开放度 |
|------|--------|----------|------|------|--------|--------|
| MCP | stdio/HTTP/WS | JSON-RPC 2.0 | 无内置 | 扩展 | ★★★★★ | ★★★★★ |
| A2A | HTTP+SSE | JSON-RPC 2.0 | OAuth/Key | SSE | ★★★☆☆ | ★★★★☆ |
| LangGraph | REST+SSE | 自定义 JSON | Cloud Key | SSE | ★★★★★ | ★★★☆☆ |
| Dify | REST+SSE | 自定义 JSON | Bearer | SSE | ★★★★★ | ★★☆☆☆ |
| Coze | REST+SSE+WS | 自定义 JSON | PAT/OAuth | SSE | ★★★★★ | ★★☆☆☆ |
| n8n | REST+Webhook | 自定义 JSON | Key/Basic | - | ★★★★★ | ★★★★☆ |
| CrewAI | 进程内 | Python obj | - | - | ★★★★☆ | ★★★★★ |
| AutoGen | 进程内 | AgentEvent | - | - | ★★★★☆ | ★★★★★ |
| MetaGPT | 进程内 | Message obj | - | - | ★★★☆☆ | ★★★★★ |
| OpenAI SDK | 进程内 | HandoffEvent | API Key | - | ★★★★★ | ★★★☆☆ |

### 2.3 核心发现
1. **JSON-RPC 2.0** 是 MCP 和 A2A 的共同选择 — AIM 的消息层应参考
2. **SSE 流式** 是 5/5 API 类框架的标配 — AIM 应支持
3. **Bearer Token** 是主流认证方式 — AIM 应从固定 Token → PAT 升级
4. **Agent 能力声明**（A2A AgentCard / MCP tools/list）是可发现性基础
5. **所有框架都可以不改源码接入 AIM** — 通过适配层实现

---

## 三、AIM 标准接口设计

### 3.1 总架构

```
┌──────────────────────────────────────────┐
│              AIM Platform                 │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │        AIM Envelope (不变)          │  │
│  │  { ver, id, ts, from, type,        │  │
│  │    payload: { text }, meta: {} }    │  │
│  └──────────────┬─────────────────────┘  │
│                 │                        │
│  ┌──────────────┴─────────────────────┐  │
│  │       AIM Standard Adapter          │  │
│  │  (每个框架一个 adapter，不改框架)     │  │
│  │  connect / send / receive / caps    │  │
│  └──────────────┬─────────────────────┘  │
│                 │                        │
│  ┌──────────────┴─────────────────────┐  │
│  │      NATS / Observer / Pin         │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

### 3.2 标准接口规范

每个 AIM Adapter 实现 4 个标准方法：

```
class AIMAdapter:
    async def connect() -> bool          # 建立连接
    async def send(text: str) -> str     # 发送消息，返回 msg_id
    async def receive() -> str           # 接收消息（阻塞/回调）
    def capabilities() -> dict           # 查询框架能力
```

### 3.3 三层适配策略

#### 第一层：协议原生框架（MCP, A2A）

**无需 Adapter 包装，直接协议兼容。**

| 框架 | 接入方式 | AIM Envelope 映射 |
|------|----------|-------------------|
| MCP | `POST http://host/mcp` | JSON-RPC request → AIM envelope |
| A2A | `POST http://host:41241` | Task `message.parts[0].text` → AIM payload |

```
AIM ↔ MCP:
  AIM Envelope → mcp_adapter.encode() → JSON-RPC → MCP Server
  MCP Response → mcp_adapter.decode() → AIM Envelope

AIM ↔ A2A:
  AIM Envelope → a2a_adapter.encode() → Task Send → A2A Server
  A2A Response → a2a_adapter.decode() → AIM Envelope
```

#### 第二层：API 暴露框架（LangGraph, Dify, Coze, n8n）

**标准 REST Client 适配。**

| 框架 | REST Endpoint | Auth | Payload 映射 |
|------|---------------|------|-------------|
| LangGraph | `POST /threads/{id}/runs` | Cloud Key | `input.messages[0].content` |
| Dify | `POST /v1/chat-messages` | Bearer Token | `query` 字段 |
| Coze | `POST /v3/chat` | PAT Token | `additional_messages[0].content` |
| n8n | `POST /webhook/{id}` | Basic/Key | `{"message":"..."}` |

#### 第三层：进程内框架（CrewAI, AutoGen, MetaGPT, OpenAI SDK）

**Subprocess Wrapper 桥接。**

```python
# 例：CrewAI Adapter
class CrewAIAdapter(AIMAdapter):
    async def send(self, text):
        proc = await asyncio.create_subprocess_exec(
            "python3", "crewai_bridge.py", text,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return AIMEnvelope(text=stdout.decode())
```

### 3.4 不对框架做任何调整

| 框架 | 需要改造吗？ | Adapter 位置 |
|------|-------------|-------------|
| MCP | ❌ 不改 | AIM 客户端侧 mcp_adapter.py |
| A2A | ❌ 不改 | AIM 客户端侧 a2a_adapter.py |
| LangGraph | ❌ 不改 | AIM 客户端侧 langgraph_adapter.py |
| Dify | ❌ 不改 | AIM 客户端侧 dify_adapter.py |
| Coze | ❌ 不改 | AIM 客户端侧 coze_adapter.py |
| n8n | ❌ 不改 | AIM 客户端侧 n8n_adapter.py |
| CrewAI | ❌ 不改 | subprocess bridge crewai_bridge.py |
| AutoGen | ❌ 不改 | subscriber intercept autogen_bridge.py |
| MetaGPT | ❌ 不改 | Environment.publish hook metagpt_bridge.py |
| OpenAI SDK | ❌ 不改 | Runner.stream_events hook openai_bridge.py |

---

## 四、关键设计决策

### 4.1 消息层：沿用 AIM Envelope + 兼容 JSON-RPC 2.0

- **内部通信**: 保持现有 AIM Envelope 格式（已有 3 个 Agent 验证）
- **外部互通**: Adapter 层做 JSON-RPC 2.0 ↔ AIM Envelope 转换
- **不做 breaking change**: 不影响现有呱呱/吉量/我的通信

### 4.2 传输层：NATS 为主 + Adapter 层支持 HTTP/WS/stdio

- 核心通道: NATS (已有 JWT 部署)
- Adapter 出站: HTTP/SSE/gRPC/stdio，按框架选择

### 4.3 认证：短期 PAT 升级，长期 OAuth

- 当前: 固定 Token (nats-jwt.conf)
- 短期: Personal Access Token (`aim.pat.xxx`)
- 长期: OAuth 2.0 (适配 A2A/Coze/Dify)

### 4.4 流式：SSE 支持作为 Adapter 可选实现

- 核心 NATS 通道已有实时推送
- Adapter 层对 LangGraph/Dify/Coze 的 SSE 做转换
- 进程内框架不需要流式

### 4.5 能力声明：A2A AgentCard 作为参考模型

```
GET /.well-known/aim-agent-card.json
{
  "agent_id": "ZS0003",
  "capabilities": ["dm", "grp", "ai_reply"],
  "framework": "letta",
  "endpoints": {
    "dm": "nats://127.0.0.1:4222/aim.dm.ZS0003"
  }
}
```

---

## 五、实施路线

| Phase | 内容 | 产出 | 预计工期 |
|-------|------|------|----------|
| **P0** | Adapter 接口定义 + SDK | `aim_adapter_base.py` | 1天 |
| **P1** | 协议原生类适配 (MCP + A2A) | `mcp_adapter.py`, `a2a_adapter.py` | 2天 |
| **P2** | API 暴露类适配 (LangGraph + Dify) | `langgraph_adapter.py`, `dify_adapter.py` | 3天 |
| **P3** | 进程内框架桥接 (CrewAI 先行) | `crewai_bridge.py` | 2天 |
| **P4** | 剩余框架 + 文档 | 全10框架 + 接入指南 | 5天 |

---

## 六、依据 & 参考

- [AIM Architecture](/shared/aim/AIM-ARCHITECTURE.md) — AIM 原始架构 v1
- [OAS Vision v0.1](/shared/oas/OAS-VISION-V0.1.md) — OAS 开放 Agent 社会愿景
- [OAS Design v1.2](/shared/aim/docs/OAS-DESIGN.md) — OAS 扩展层设计
- [TOP10 Frameworks](/shared/aim/research/TOP10-FRAMEWORKS-GLOBAL.md) — 全球框架综述
- [ZS0002 5-Frameworks](/shared/aim/research/ZS0002-5-frameworks.md) — 吉量深度调研
- MCP Spec: <https://modelcontextprotocol.io>
- A2A Spec: <https://a2aprotocol.ai>

---

*方案完成。等大哥评审，然后 P0 开搞。*
