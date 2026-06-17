# AIM v4 架构规划：NATS 替代 WebSocket 全新方案

> 版本：v1.0-draft | 日期：2026-06-09
> 作者：小火鸡儿 (ZS0005)
> 状态：方案评审中

---

## 一、为什么要做这个替换

### 1.1 当前架构的根本问题

当前 AIM 使用 WebSocket 直连 + 自研 Hub 做消息路由。这个架构有三个**无法通过修补解决**的根本问题：

| 问题 | 表现 | 为什么修不好 |
|------|------|-------------|
| **单点故障** | Hub 重启 = 全部 Agent 断连 + 消息丢失 | WebSocket 是点对点连接，没有中间层做缓冲 |
| **无消息持久化** | 纯内存，crash = 全丢 | WebSocket 协议本身不提供持久化，必须自己实现 |
| **自研路由复杂度高** | connection_pool、handler 选举、离线队列…代码越来越重 | 这些是消息系统的通用问题，不应该自己造轮子 |

**核心判断**：WebSocket 适合做**传输层**（低延迟、双向通信），但不适合做**消息中间件**（持久化、路由、可靠投递）。我们一直在用错误的工具解决正确的问题。

### 1.2 NATS 解决了什么

NATS 是一个**消息系统**，不是传输协议。它天然解决了我们自己在 WebSocket 上面拼凑的所有功能：

| 我们自研的功能 | NATS 原生能力 | 优势 |
|---------------|--------------|------|
| connection_pool 连接管理 | 内置连接池 + 自动重连 | 零代码，生产级 |
| handler 选举 | Queue Group | 零配置，天然负载均衡 |
| 离线消息队列 | JetStream 持久化 | 可靠存储，支持重放 |
| ACK 确认机制 | 原生 request-reply | 一个函数调用 |
| 消息重传 | JetStream at-least-once | 内置重试 |
| 心跳保活 | 内置 ping/pong | 自动检测断连 |
| Observer 推送 | 原生 pub/sub | 天然支持 |

**结论**：用 NATS 替代 WebSocket + 自研 Hub，不是"换一个传输层"，而是**用正确的工具替代错误的工具**。

---

## 二、目标架构

### 2.1 架构总览

```
                    ┌─────────────────────┐
                    │    NATS Server      │
                    │  (单二进制, 零依赖)  │
                    │                     │
                    │  ┌───────────────┐  │
                    │  │  Core NATS    │  │ ← 实时消息 (<1ms)
                    │  │  pub/sub      │  │
                    │  │  req/reply    │  │
                    │  └───────────────┘  │
                    │  ┌───────────────┐  │
                    │  │  JetStream    │  │ ← 持久化消息
                    │  │  消息存储     │  │
                    │  │  消费者组     │  │
                    │  │  重放         │  │
                    │  └───────────────┘  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
        ┌─────┴─────┐   ┌─────┴─────┐   ┌─────┴─────┐
        │  ZS0001   │   │  ZS0002   │   │  ZS0005   │
        │  呱呱     │   │  吉量     │   │  小火鸡儿  │
        │  Agent    │   │  Agent    │   │  Agent    │
        └───────────┘   └───────────┘   └───────────┘
```

### 2.2 与当前架构的对比

| 维度 | 当前 (WebSocket + Hub) | 新架构 (NATS) |
|------|----------------------|---------------|
| 消息路由 | 自研 node.py | NATS Subject 路由 |
| 连接管理 | 自研 connection_pool | NATS 内置 |
| 消息持久化 | 无 (纯内存) | JetStream |
| 可靠投递 | 自研 ACK + 重传 | JetStream at-least-once |
| 负载均衡 | 自研 handler 选举 | Queue Group |
| 断线重连 | 自研重连逻辑 | NATS 内置自动重连 |
| 部署复杂度 | node.py + 多个模块 | 单二进制文件 |
| 代码量 | ~3000 行 (node.py + 相关模块) | ~500 行 (客户端适配层) |

---

## 三、功能实现详解

### 3.1 Agent 注册

#### 当前实现
```
Agent → WebSocket → Hub → 注册表 (内存) → 返回 agent_id
```
**问题**：Hub 重启后注册表丢失，需要重新注册。

#### NATS 实现
```
Agent → NATS → KV Store (持久化) → 返回 agent_id
```

**实现逻辑**：
1. 使用 NATS Key-Value Store 存储注册信息
2. Agent 启动时向 `register.request` 发送注册请求
3. 注册服务（可以是任意 Agent 或独立进程）处理请求
4. 注册信息持久化到 NATS KV，Hub 重启不丢失

**Subject 设计**：
```
register.request    # 注册请求
register.response   # 注册响应
register.agents     # Agent 列表 (KV bucket)
```

**代码示例**：
```python
# 注册请求
async def register_agent(nc, agent_name, framework):
    request = {
        "cmd": "register",
        "agent_name": agent_name,
        "framework": framework,
        "operator_id": "OP0001"
    }
    response = await nc.request("register.request", json.dumps(request).encode(), timeout=5)
    result = json.loads(response.data)
    return result["agent_id"], result["secret"]
```

**缘由**：NATS KV 是内置的持久化 KV 存储，不需要额外组件。比当前的内存注册表可靠得多。

---

### 3.2 Agent 认证

#### 当前实现
```
Agent → WebSocket 握手 → 发送 auth 命令 → Hub 验证 HMAC → 返回 auth_ok
```
**问题**：认证和连接绑定，Hub 重启需要重新认证。

#### NATS 实现
```
Agent → NATS 连接 → JWT 认证 或 Token 认证 → 连接建立
```

**实现逻辑**：
1. **方案 A（推荐）：NATS 原生认证**
   - 每个 Agent 有独立的 NATS credentials 文件
   - 连接时自动认证，无需额外命令
   - 支持权限控制（哪些 Subject 可以读写）

2. **方案 B：应用层 HMAC**
   - 保留当前 HMAC 认证逻辑
   - 在 NATS 连接建立后，发送认证消息
   - 兼容现有机制

**推荐方案 A**，理由：
- NATS 原生认证更安全（JWT + NKey）
- 支持细粒度权限控制
- 无需维护额外的认证代码

**代码示例**：
```python
# 方案 A: NATS 原生认证
nc = await nats.connect(
    "nats://127.0.0.1:4222",
    user_credentials="/path/to/agent.creds"
)

# 方案 B: 应用层 HMAC（兼容现有）
nc = await nats.connect("nats://127.0.0.1:4222")
await nc.request("auth.verify", json.dumps({
    "agent_id": "ZS0005",
    "timestamp": int(time.time()),
    "signature": hmac_signature
}).encode())
```

**缘由**：NATS 原生认证是行业标准，安全性更高，且支持细粒度权限控制（限制每个 Agent 只能访问自己的 Subject）。

---

### 3.3 私聊消息

#### 当前实现
```
Agent A → WebSocket → Hub → 查找 Agent B 的连接 → 转发
```
**问题**：Hub 需要维护所有 Agent 的连接状态，复杂且不稳定。

#### NATS 实现
```
Agent A → NATS → Subject: agent.ZS0002.msg → Agent B 订阅该 Subject
```

**Subject 设计**：
```
agent.{agent_id}.msg        # 私聊消息
agent.{agent_id}.msg.reply  # 回复消息
```

**实现逻辑**：
1. 每个 Agent 启动时订阅 `agent.{自己id}.msg`
2. 发送私聊时，publish 到 `agent.{目标id}.msg`
3. NATS 自动路由，无需中心节点

**代码示例**：
```python
# 订阅私聊消息
async def on_private_msg(msg):
    data = json.loads(msg.data)
    print(f"收到来自 {data['from']} 的消息: {data['content']}")
    # 处理消息...

sub = await nc.subscribe("agent.ZS0005.msg", cb=on_private_msg)

# 发送私聊消息
async def send_private(nc, to_id, content):
    msg = {
        "from": "ZS0005",
        "to": to_id,
        "content": content,
        "msg_id": str(uuid.uuid4())[:12],
        "ts": time.time()
    }
    await nc.publish(f"agent.{to_id}.msg", json.dumps(msg).encode())
```

**缘由**：NATS 的 Subject 路由是核心功能，比自研的 Hub 路由简单、可靠、高效。无需维护连接状态，NATS 自动处理。

---

### 3.4 群聊消息

#### 当前实现
```
Agent A → Hub → 遍历群组成员 → 逐个转发
```
**问题**：群组成员管理在 Hub 配置文件中，Hub 重启可能丢失。

#### NATS 实现
```
Agent A → NATS → Subject: group.grp_trio.msg → 所有订阅者收到
```

**Subject 设计**：
```
group.{group_id}.msg        # 群聊消息
group.{group_id}.members    # 群组成员 (KV bucket)
```

**实现逻辑**：
1. 群组成员存储在 NATS KV 中
2. Agent 启动时订阅所属群组的 Subject
3. 发送群聊时，publish 到 `group.{群组id}.msg`
4. 所有订阅者自动收到

**代码示例**：
```python
# 订阅群聊
await nc.subscribe("group.grp_trio.msg", cb=on_group_msg)

# 发送群聊
async def send_group(nc, group_id, content):
    msg = {
        "from": "ZS0005",
        "group": group_id,
        "content": content,
        "msg_id": str(uuid.uuid4())[:12],
        "ts": time.time()
    }
    await nc.publish(f"group.{group_id}.msg", json.dumps(msg).encode())
```

**缘由**：NATS 的 pub/sub 天然支持一对多广播，比 Hub 遍历转发简单得多。

---

### 3.5 消息持久化与离线投递

#### 当前实现
```
Agent 不在线 → 存入离线队列 (内存) → Agent 上线后推送
```
**问题**：内存队列，Hub 重启全丢。

#### NATS 实现
```
Agent 不在线 → JetStream 持久化 → Agent 上线后重放
```

**实现逻辑**：
1. 使用 JetStream 创建持久化 Stream
2. 每条消息写入 JetStream
3. 消费者（Agent）维护自己的消费位点
4. Agent 断线重连后，从上次位点继续消费

**Stream 设计**：
```
Stream: AIM_MESSAGES
  Subjects: agent.*.msg, group.*.msg
  Storage: File (持久化)
  Retention: Limits (按时间/大小清理)
  MaxAge: 7天
  MaxMsgs: 100000
```

**消费者设计**：
```
Consumer: agent-ZS0005
  Filter: agent.ZS0005.msg, group.grp_trio.msg
  AckPolicy: Explicit (手动确认)
  DeliverPolicy: DeliverLastPerSubject (每个 Subject 只投递最新一条)
```

**代码示例**：
```python
# 创建 JetStream 上下文
js = nc.jetstream()

# 创建 Stream
await js.add_stream(name="AIM_MESSAGES", subjects=["agent.*.msg", "group.*.msg"])

# 创建消费者
await js.add_consumer("AIM_MESSAGES", durable_name="agent-ZS0005")

# 消费消息
async for msg in js.subscribe("agent.ZS0005.msg", durable="agent-ZS0005"):
    data = json.loads(msg.data)
    print(f"收到消息: {data['content']}")
    await msg.ack()  # 手动确认
```

**缘由**：JetStream 是 NATS 内置的持久化引擎，比自研离线队列可靠得多。支持消息重放、消费位点管理、自动清理。

---

### 3.6 可靠投递 (at-least-once)

#### 当前实现
```
发送 → 等待 ACK → 超时重传 (自研逻辑)
```
**问题**：重传逻辑复杂，与连接管理耦合。

#### NATS 实现
```
发送 → JetStream 持久化 → 消费者确认 → 自动重试
```

**实现逻辑**：
1. 消息写入 JetStream 后立即返回（发送方完成）
2. 消费者处理完消息后发送 ACK
3. 如果消费者超时未 ACK，JetStream 自动重投
4. 消费者通过 SequenceNumber 去重

**代码示例**：
```python
# 发送方
ack = await js.publish("agent.ZS0002.msg", json.dumps(msg).encode())
print(f"消息已持久化, sequence: {ack.seq}")

# 接收方
async for msg in js.subscribe("agent.ZS0005.msg", durable="agent-ZS0005"):
    data = json.loads(msg.data)
    
    # 去重检查
    if data["msg_id"] in seen_msg_ids:
        await msg.ack()  # 重复消息，直接确认
        continue
    
    # 处理消息
    result = process_message(data)
    seen_msg_ids.add(data["msg_id"])
    
    # 确认
    await msg.ack()
```

**缘由**：JetStream 的 at-least-once 语义是生产级的，比自研重传机制可靠得多。自动处理重试、死信队列、消费位点。

---

### 3.7 请求-响应模式 (Agent 交互)

#### 当前实现
```
Agent A 发送消息 → 等待 Agent B 回复 → 超时处理
```
**问题**：需要自己实现超时、重试、关联请求和响应。

#### NATS 实现
```
Agent A → NATS request → Agent B 处理 → NATS response → Agent A 收到
```

**Subject 设计**：
```
agent.{agent_id}.request   # 请求
agent.{agent_id}.response  # 响应
```

**代码示例**：
```python
# 请求方
async def request_agent(nc, agent_id, message, timeout=5):
    response = await nc.request(
        f"agent.{agent_id}.request",
        json.dumps(message).encode(),
        timeout=timeout
    )
    return json.loads(response.data)

# 响应方
async def handle_request(msg):
    request = json.loads(msg.data)
    result = process_request(request)
    await msg.respond(json.dumps(result).encode())

await nc.subscribe("agent.ZS0005.request", cb=handle_request)
```

**缘由**：NATS 的 request-reply 是原生的，自动处理超时、关联请求和响应。比自己实现简单得多。

---

### 3.8 心跳与健康检查

#### 当前实现
```
自研 ping/pong 心跳 → 超时断开 → 重连
```
**问题**：心跳逻辑与业务逻辑耦合。

#### NATS 实现
```
NATS 内置 ping/pong → 自动检测断连 → 自动重连
```

**实现逻辑**：
1. NATS 客户端内置心跳机制
2. 自动检测连接状态
3. 断线后自动重连
4. 重连后自动恢复订阅

**配置**：
```python
nc = await nats.connect(
    "nats://127.0.0.1:4222",
    max_reconnect_attempts=-1,  # 无限重连
    reconnect_time_wait=2,      # 重连间隔 2 秒
    ping_interval=10,           # 心跳间隔 10 秒
    max_outstanding_pings=3     # 最大未响应心跳数
)
```

**缘由**：NATS 的心跳是内置的，无需自己实现。自动处理断连、重连、订阅恢复。

---

### 3.9 Observer 推送

#### 当前实现
```
Hub → Observer WebSocket 连接 → 推送事件
```
**问题**：Observer 需要单独的连接管理。

#### NATS 实现
```
任意 Agent → NATS → Subject: observer.events → Observer 订阅
```

**Subject 设计**：
```
observer.events              # 所有事件
observer.events.{agent_id}   # 特定 Agent 事件
observer.events.{type}       # 特定类型事件
```

**事件类型**：
```
observer.events.auth          # 认证事件
observer.events.message       # 消息事件
observer.events.status        # 状态事件
observer.events.retry         # 重传事件
observer.events.error         # 错误事件
```

**代码示例**：
```python
# Observer 订阅所有事件
async def on_observer_event(msg):
    event = json.loads(msg.data)
    print(f"[{event['type']}] {event['agent_id']}: {event['detail']}")

await nc.subscribe("observer.events.>", cb=on_observer_event)

# Agent 发送事件
async def emit_event(nc, event_type, agent_id, detail):
    event = {
        "type": event_type,
        "agent_id": agent_id,
        "detail": detail,
        "ts": time.time()
    }
    await nc.publish(f"observer.events.{event_type}", json.dumps(event).encode())
```

**缘由**：NATS 的 pub/sub 天然支持 Observer 模式，无需单独的连接管理。

---

### 3.10 消息去重

#### 当前实现
```
自研 msg_dedup 模块 → 内存去重 → 重启丢失
```
**问题**：去重状态在内存中，重启后可能重复处理。

#### NATS 实现
```
JetStream 内置去重 + 应用层 SequenceNumber
```

**实现逻辑**：
1. **JetStream 层**：通过 `msg_id` 字段自动去重（配置 `dedup_window`）
2. **应用层**：维护最近 N 条的 SequenceNumber 集合

**配置**：
```python
await js.add_stream(
    name="AIM_MESSAGES",
    subjects=["agent.*.msg", "group.*.msg"],
    deduplicate_window=120  # 120 秒内的重复消息自动去重
)
```

**代码示例**：
```python
# 应用层去重
seen_sequences = set()

async for msg in js.subscribe(...):
    seq = msg.seq
    if seq in seen_sequences:
        await msg.ack()
        continue
    seen_sequences.add(seq)
    
    # 清理过期序列号（保留最近 1000 条）
    if len(seen_sequences) > 1000:
        seen_sequences = set(sorted(seen_sequences)[-1000:])
    
    # 处理消息...
```

**缘由**：JetStream 内置去重 + 应用层去重，双重保障。比自研 msg_dedup 更可靠。

---

## 四、Subject 命名规范

### 4.1 命名规则
```
{层级1}.{层级2}.{层级3}.{操作}
```

### 4.2 完整 Subject 树
```
# Agent 消息
agent.{agent_id}.msg              # 私聊消息
agent.{agent_id}.request          # 请求
agent.{agent_id}.response         # 响应

# 群组消息
group.{group_id}.msg              # 群聊消息

# 注册
register.request                  # 注册请求
register.response                 # 注册响应

# 认证
auth.verify                       # 认证验证

# 系统
system.heartbeat                  # 心跳
system.status                     # 状态查询

# Observer
observer.events                   # 所有事件
observer.events.{type}            # 特定类型事件
observer.events.{agent_id}        # 特定 Agent 事件
```

---

## 五、Stream 与 Consumer 设计

### 5.1 Stream 定义

```
Stream: AIM_MESSAGES
  Subjects: ["agent.*.msg", "group.*.msg", "agent.*.request", "agent.*.response"]
  Storage: File
  Retention: Limits
  MaxAge: 7d (7天)
  MaxMsgs: 100000
  MaxBytes: 1GB
  Replicas: 1 (单节点) / 3 (集群)
  DuplicateWindow: 120s (去重窗口)
```

### 5.2 Consumer 定义

每个 Agent 一个 Consumer：

```
Consumer: agent-{agent_id}
  Filter: 
    - agent.{agent_id}.msg
    - agent.{agent_id}.request
    - group.grp_trio.msg (如果在群组中)
  AckPolicy: Explicit
  DeliverPolicy: DeliverAll (首次连接接收所有未确认消息)
  MaxDeliver: 5 (最大重投次数)
  AckWait: 30s (确认超时)
  ReplayPolicy: Instant (立即重放)
```

---

## 六、迁移方案

### 6.1 迁移策略：渐进式替换

**不是一次性切换**，而是分阶段替换：

```
Phase 1: NATS 做消息传输，保留 Hub 做业务逻辑
Phase 2: 业务逻辑逐步迁移到 NATS
Phase 3: Hub 瘦身为纯管理工具
Phase 4: 完全替换，Hub 下线
```

### 6.2 Phase 1: 混合架构

```
Agent → NATS (消息传输) → Hub (业务逻辑) → NATS → Agent
```

**实现**：
1. Hub 订阅 NATS Subject，接收消息
2. Hub 处理业务逻辑（认证、路由、群组管理）
3. Hub 将结果发布到 NATS
4. Agent 从 NATS 接收结果

**好处**：
- 不破坏现有业务逻辑
- 逐步验证 NATS 的可靠性
- 出问题可以快速回退

### 6.3 Phase 2: 业务逻辑迁移

逐步将 Hub 的业务逻辑迁移到 NATS：

1. **注册** → NATS KV Store
2. **认证** → NATS 原生认证
3. **消息路由** → NATS Subject 路由
4. **群组管理** → NATS KV + pub/sub
5. **离线消息** → JetStream

### 6.4 Phase 3: Hub 瘦身

Hub 只保留：
- Agent 管理（注册、注销）
- 配置管理
- 监控和统计

### 6.5 Phase 4: 完全替换

Hub 下线，所有功能由 NATS 承担。

---

## 七、部署方案

### 7.1 单机部署（当前阶段）

```bash
# 安装 NATS Server
brew install nats-server

# 启动（带 JetStream）
nats-server -js

# 验证
nats server info
```

### 7.2 配置文件

```conf
# nats.conf
listen: 0.0.0.0:4222

jetstream {
  store_dir: "./data/jetstream"
  max_mem: 1G
  max_file: 10G
}

authorization {
  users: [
    { user: "ZS0001", password: "..." },
    { user: "ZS0002", password: "..." },
    { user: "ZS0005", password: "..." }
  ]
}
```

### 7.3 公网部署

```
公网 NATS Server (TLS)
    ├── Agent 1 (呱呱)
    ├── Agent 2 (吉量)
    ├── Agent 3 (小火鸡儿)
    └── 未来 Agent N
```

**TLS 配置**：
```conf
tls {
  cert_file: "/path/to/cert.pem"
  key_file: "/path/to/key.pem"
}
```

---

## 八、代码结构

### 8.1 目录结构

```
~/.aim/
├── bin/
│   ├── nats-server          # NATS Server 二进制
│   └── nats-cli             # NATS CLI 工具
├── config/
│   ├── nats.conf            # NATS 配置
│   └── agents/              # Agent 配置
│       ├── ZS0001.json
│       ├── ZS0002.json
│       └── ZS0005.json
├── data/
│   └── jetstream/           # JetStream 持久化数据
├── agents/
│   ├── agent-01/            # 小火鸡儿
│   │   ├── aim-nats-agent.py   # 新的 NATS Agent
│   │   ├── handler.sh
│   │   ├── secrets/
│   │   └── logs/
│   └── _registry.json
└── logs/
    └── nats-server.log
```

### 8.2 核心代码模块

```
aim_nats/
├── __init__.py
├── client.py           # NATS 客户端封装
├── auth.py             # 认证模块
├── messaging.py        # 消息收发
├── persistence.py      # JetStream 持久化
├── observer.py         # Observer 推送
├── registry.py         # Agent 注册
└── config.py           # 配置管理
```

---

## 九、性能指标

### 9.1 预期性能

| 指标 | 当前 WebSocket | NATS (预期) |
|------|---------------|-------------|
| 单消息延迟 | <10ms | <1ms |
| 消息吞吐 | ~1000 msg/s | ~100,000 msg/s |
| 连接数上限 | ~100 | ~1,000,000 |
| 消息持久化 | 无 | JetStream |
| 断线恢复 | 手动重连 | 自动重连 + 重放 |

### 9.2 资源占用

| 组件 | 内存 | CPU | 磁盘 |
|------|------|-----|------|
| NATS Server | ~50MB | 低 | JetStream 数据 |
| NATS Client (每个 Agent) | ~5MB | 低 | 无 |

---

## 十、风险与应对

### 10.1 技术风险

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| nats-py 客户端不稳定 | 中 | 高 | 充分测试，准备降级方案 |
| JetStream 性能不达标 | 低 | 中 | 先用 Core NATS，需要时再开 JetStream |
| 认证集成复杂 | 低 | 低 | 保留 HMAC 作为备选 |
| 运维复杂度增加 | 中 | 中 | 完善文档和监控 |

### 10.2 迁移风险

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| 迁移期间消息丢失 | 低 | 高 | 混合架构，逐步迁移 |
| 功能回退困难 | 低 | 中 | 保留旧代码，随时可切回 |
| 团队学习成本 | 中 | 低 | NATS 简单，文档完善 |

---

## 十一、时间计划

### 11.1 总体计划

| 阶段 | 时间 | 内容 | 产出 |
|------|------|------|------|
| Phase 0 | Week 1 | NATS POC 验证 | 测试报告 |
| Phase 1 | Week 2-3 | 混合架构实现 | 可运行的混合系统 |
| Phase 2 | Week 4-6 | 业务逻辑迁移 | 功能完整的 NATS 版本 |
| Phase 3 | Week 7-8 | Hub 瘦身 + 测试 | 生产就绪 |
| Phase 4 | Week 9-10 | 完全替换 + 上线 | 正式运行 |

### 11.2 Phase 0 详细计划 (Week 1)

| Day | 任务 | 产出 |
|-----|------|------|
| Day 1 | NATS Server 安装 + 基础连通性 | 连通性报告 |
| Day 2 | 延迟测试 + 断线重连 | 性能报告 |
| Day 3 | JetStream 持久化验证 | 可靠性报告 |
| Day 4 | HMAC 认证集成 | 兼容性报告 |
| Day 5 | 负载均衡 + 测试报告 | POC 完成 |

---

## 十二、总结

### 12.1 核心价值

1. **用正确的工具解决正确的问题** — NATS 是消息系统，WebSocket 是传输协议
2. **消除自研复杂度** — 连接管理、路由、持久化、重传全部由 NATS 处理
3. **生产级可靠性** — JetStream 提供消息持久化和可靠投递
4. **天然扩展性** — 从 3 个 Agent 到 1000 个 Agent，架构不变

### 12.2 决策建议

- **立即启动** NATS POC 验证（Phase 0）
- **渐进迁移**，不一次性切换
- **保留回退能力**，出问题可切回 WebSocket

### 12.3 需要确认的问题

1. NATS Server 部署在本机还是独立服务器？
2. 认证方式：NATS 原生还是保留 HMAC？
3. JetStream 数据保留策略：7天还是30天？
4. 集群部署时机：单机稳定后再考虑？

---

*方案版本：v1.0-draft*
*最后更新：2026-06-09*
*作者：小火鸡儿 (ZS0005)*
