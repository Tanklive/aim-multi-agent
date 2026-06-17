# AIM Client — OAS 公民标准接入方案

> 版本: v1.2 | 日期: 2026-06-16 | 状态: 待三方评审
> 整合: 大哥定调 + 呱呱可行性分析 + 小火鸡儿模块设计 + 吉量 OAS 对齐 + 三方评审意见
> 本轮更新: 吸收大哥桌面两份文档意见（边界红线 / Message-Task分层 / Lifecycle / Adapter标准化 / Schema预留）

---

## 一、核心理念

```
Agent ≠ Runtime
Agent = AIM Client (公民层) + Runtime (大脑层)
```

**AIM 不直接对接任何 Runtime。AIM 只对接 AIM Client。**

OAS 社会管理的不是 Runtime，而是具有标准身份、标准协议、标准信誉的 Agent Citizen。
AIM Client 就是 Agent Citizen 的数字身份证 + 邮箱 + 通讯终端。
Runtime 只是 Citizen 背后的思维引擎——可换、可挂、可迁移。

> **AIM Client 架构定调**："这次小火鸡儿暴露的问题，恰恰说明 AIM 后面应该把 Agent Runtime 和 AIM Client（Bridge）彻底分离。"
>
> 当前你看到的是 Letta 的问题。但未来接入 AutoGen / CrewAI / LangGraph / OpenHands / Claude Code / OpenClaw / Hermes / 自研 Agent——每个 Agent 的运行机制都不一样。如果 AIM 直接对接 Agent Runtime，后面会越来越乱。
>
> **AIM Client 才是 OAS 世界的"公民"，Runtime 只是 Citizen 背后的大脑。Runtime 可换（Letta → Hermes → OpenClaw），公民身份不变。**
>
> **未来 OAS 的 Agent 接入流程**：Agent 安装 → 安装 AIM Client → 注册 Agent Card → 连接 NATS → 加入 Agent Society。AIM 永远不知道 Runtime 内部是什么架构。AIM 只认识 AIM Client。

---

## 二、设计依据

### 2.1 原始需求

> "兼容天下，不需要别的框架智能体改本身架构，安装 AIM 客户端，就可以实现沟通、协作、建群。"

AIM 不要求：
- ❌ Agent 框架改核心代码
- ❌ Agent 框架写消息处理循环
- ❌ Agent 框架适配 NATS SDK
- ❌ Agent 框架理解 AIM 协议细节

AIM 只要求：
- ✅ Agent 框架有一个 CLI 或 API 可以调
- ✅ 接收文本，返回文本

> **原始需求原文**："兼容天下，不需要别的框架智能体改本身架构，安装 AIM 客户端，就可以实现沟通、协作、建群。"
>
> **对 ID 的要求**："ID（ZS0001/ZS0002/ZS0003等）只是当前测试环境的示例ID，不代表绝对约束。AIM 设计目标：兼容天下，任何 Agent/框架安装 AIM 客户端即可沟通/协作/建群，不需要改本身架构。开发/规划必须以此出发，不以现有 ID 固化为前提。"
>
> **部署模式**："对于 AIM 客户端的不同架构部署模式，肯定是有一个适配过程的，可以在安装的时候用户手动选择，也可以自检的方式确认。再通过 AIM 客户端的规则选择适配的配置文件和配置模式。"

### 2.2 问题驱动

这次 V3 联调暴露的问题，根因全是 Runtime 承担了通信职责：

| 事件 | 表现 | 根因 |
|------|------|------|
| Letta session 忙 | ZS0003 失联，消息降级 | Runtime 状态 = Agent 状态 |
| Hermes adapter 超时 | ZS0002 回复慢堵住 V3 | Runtime 处理 = Agent 通信 |
| 无独立消息缓存 | Runtime 卡了消息就丢 | Queue 不在 Client 侧 |
| register 未实现 | SDK 报 AttributeError | 身份绑定在 Runtime 上 |

**结论：Runtime 不该承担通信职责。通信是 Client 的事。**

### 2.3 互联网架构类比

```
互联网               AIM/OAS
─────────            ──────────
HTTP 协议            AIM 协议（Transport 层）
curl 客户端          aim-client
URL / 域名           Agent ID / 全局唯一 ID
DNS 解析             Agent Card 发现
网关 / 代理          Transport 协议转换
Web 服务器           Agent Runtime
```

互联网没有要求每个 Web 服务器改架构才能接入——只要它支持 HTTP。同样，AIM 不要求每个 Agent 框架改架构——只要它有 CLI/API。

### 2.4 模式总结

```
AIM = 通讯协议 + 标准客户端（类似 HTTP + curl）

aim-client = 标准接入点
     Adapter = 唯一适配代码（30 行脚本）
     Runtime = 任意 Agent 框架
```

---

## 三、架构总览

```
                    AIM Network / OAS Society
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
    aim-client          aim-client        aim-client
    (ZS0001)            (ZS0002)          (ZS0003)
          │                 │                 │
    ┌─────┴──────┐    ┌─────┴──────┐    ┌─────┴──────┐
    │  Transport  │    │  Transport  │    │  Transport  │
    │  Queue      │    │  Queue      │    │  Queue      │
    │  Scheduler  │    │  Scheduler  │    │  Scheduler  │
    │  Monitor    │    │  Monitor    │    │  Monitor    │
    │  Adapter    │    │  Adapter    │    │  Adapter    │
    │  Identity   │    │  Identity   │    │  Identity   │
    └─────┬──────┘    └─────┬──────┘    └─────┬──────┘
          │                 │                 │
     OpenClaw           Hermes             Letta
     Runtime            Runtime            Runtime
```

**AIM Client 核心 5 模块（P0-P1）：** Transport, Queue, Scheduler, Monitor, Adapter, Identity。
Router / Discovery / Relay 属于 **OAS 网络层**，不内置在 AIM Client 中（P2+）。

**每层只通过标准化接口通信：**

| 层 | 接口 | 变化频率 |
|----|------|---------|
| 通信协议 | Transport 7 方法 + 认证 | 低（协议极少变） |
| 消息路由 | Router 1 方法 route()（OAS 网络层，非 Client 核心） | 中（按需扩展） |
| 公民身份 | AIM Client 6 模块 | 中（功能迭代） |
| 思维引擎 | Adapter 4 模式 process/health/info/cancel | 高（每换 Runtime 一套） |

### 3.1 AIM Client 不做清单

AIM Client 只负责通信，不负责思考。以下职责 **明确不属于 AIM Client**：

| ❌ 不属于 AIM Client | 理由 |
|---------------------|------|
| 思考 / 推理 / 规划 | 这是 Runtime 的事。Client 不解析消息语义 |
| 执行（代码运行、工具调用、浏览器操作） | Runtime 的事。Client 只投递消息，不执行任务 |
| 学习 / 记忆（长期存储、经验积累） | Runtime 的事。Client 不替 Runtime 记东西 |
| 决策（"这条消息怎么回复"） | Runtime 的事。Client 只负责送达 |
| 能力判断（"我能不能做这个任务"） | Runtime 的事。Client 只报告 Card 上的能力描述 |

**区分：Client 不做思考，但必须知道自己要投递的对象是什么类型的 Agent。** `execution_model`（见 5.6 Agent Card）描述的是 Runtime 的执行模式，Client 读它来调整投递策略（realtime 用 request、deferred 入队列、batch 定时投递），跟读 `delivery_mode` 和 `preferred_transport` 是同一类决策——通信策略，不是思考。

---

## 四、Transport 层 — 协议抽象

### 4.1 接口定义（7 个方法）

```python
class Transport(ABC):
    """协议无关的传输层抽象

    每种底层协议（NATS/A2A/HTTP/WS）只要实现这7个方法，
    就能无缝接入 AIM Client，不影响上层任何逻辑。
    """

    @abstractmethod
    async def connect(self, credential: dict = None) -> bool:
        """连接到通信网络"""

    @abstractmethod
    async def disconnect(self):
        """断开连接"""

    @abstractmethod
    async def authenticate(self, credential: dict) -> str:
        """认证，返回 AuthToken。方式由 Transport 实现决定"""
        # NATS Transport    → JWT creds 文件
        # HTTP Transport    → Bearer token / mTLS
        # A2A Transport     → OAuth2 / API Key

    @abstractmethod
    async def verify_peer(self, peer_id: str, signature: bytes) -> bool:
        """验证对端身份签名（高安全场景可选）"""

    @abstractmethod
    async def subscribe(self, subject: str, callback: Callable) -> str:
        """订阅主题，返回订阅 ID"""

    @abstractmethod
    async def publish(self, subject: str, payload: dict) -> bool:
        """发布消息到指定主题"""

    @abstractmethod
    async def request(self, subject: str, payload: dict, timeout: float) -> dict:
        """请求-回复模式"""
```

### 4.2 协议无关的收益

| 场景 | NATS | A2A | HTTP | WebSocket |
|------|------|-----|------|-----------|
| 同机多 Agent | ✅ 最佳 | 可用 | 可用 | 可用 |
| 跨机器公网 | ✅ 最佳 | ✅ 标准 | 可用 | 可用 |
| 浏览器环境 | ❌ | ❌ | ✅ 原生 | ✅ 原生 |
| 嵌入式设备 | ❌ 太重 | 可用 | ✅ 轻量 | ✅ 轻量 |
| 防火墙友好 | ❌ 自定义 | ❌ 自定义 | ✅ 80/443 | ✅ 80/443 |
| 消息持久化 | ✅ 内置 | ❌ 自建 | ❌ 自建 | ❌ 自建 |
| 一对多广播 | ✅ 原生 | ❌ 自建 | ❌ 自建 | ❌ 自建 |

选择哪种 Transport 取决于部署场景。AIM Client 核心代码**一行不改**，切换 Transport 实现类即可。

---

## 五、六大模块

### 5.1 Transport — 传输层

```
接口（7 个方法）：
  connect(credential) → bool         连接到通信网络
  disconnect()                        断开连接
  authenticate(credential) → token   认证（方式由实现决定）
  verify_peer(peer_id, sig) → bool   验证对端身份签名
  subscribe(subject, callback) → id  订阅主题
  publish(subject, payload) → bool   发布消息
  request(subject, payload, timeout) 请求-回复模式
```

### 5.2 Queue — 消息队列（含持久化策略）

```
队列结构：
  pending: List[Message]         # 等待投递（持久化到 JetStream KV）
  processing: Optional[Message]  # 正在处理（内存）
  dead: List[Message]            # 超时/失败（TTL 24h）

offline 时行为：
  Runtime 离线 ➔ 消息入 pending 队列
  pending 持久化到 JetStream KV
  Monitor 检测到 Runtime 恢复后从头投递

dead 队列：
  超时/失败的消息进入 dead 队列
  TTL = 24 小时，超期自动清除

接口：
  enqueue(msg) → msg_id   dequeue() → Optional[Message]
  ack(msg_id)             nack(msg_id)
  peek() → List[Message]  size() → int
```

**持久化策略**：P0 用内存队列，但 pending 消息同时写入 JetStream KV。重启后从 KV 恢复，不丢消息。

### 5.3 Scheduler — 调度器

调度器根据 Monitor 的 StateReport 决定投递策略，不自己做判定。

**OAS 标准生命周期（6 态 + MAINTENANCE）：**

```
REGISTERED ──→ AVAILABLE ──→ BUSY ──→ DEGRADED ──→ OFFLINE ──→ RETIRED
                                   ↕
                             MAINTENANCE
```

| 状态 | 含义 | AIM Client 行为 |
|------|------|----------------|
| REGISTERED | 已注册身份，但 Runtime 尚未就绪 | 等待 health 探针首次成功 |
| AVAILABLE | Runtime 健康，可接收消息 | 正常投递 |
| BUSY | Runtime 正在处理消息 | 消息入 pending 队列 |
| DEGRADED | Runtime 响应慢 / 部分异常 | 降级节奏，降低投递频率 |
| MAINTENANCE | Runtime 升级/重启中 | 暂停投递，队列继续累积 |
| OFFLINE | Runtime 连续 N 次不可达 | 暂停投递，延长探针间隔 |
| RETIRED | Agent 永久下线 | 清队列，发起身份注销 |

**Phase 0-1 只实现 AVAILABLE / BUSY / OFFLINE 三态**，其余在 Schema 中预留。

```
┌──────────┐  Monitor: health 返回 healthy   ┌──────────┐
│ OFFLINE  │ ────────────────────────────→    │AVAILABLE │
└──────────┘                                  └────┬─────┘
     ↑                                              │ Scheduler 开始投递
     │ Monitor: health 连续 N 次 unhealthy          ↓
     │                                        ┌──────────┐
     └────────────────────────────────────────│   BUSY   │
         Runtime 恢复 / 空闲                  └──────────┘
                                                    │
                                                process 完成
                                                    │
                                                    ↓
                                               ┌──────────┐
                                               │AVAILABLE │ → 有 pending → dequeue
                                               └──────────┘

触发条件明细：
  offline     ➔ health 返回 healthy                    ➔ available
  available   ➔ Scheduler 开始投递消息                   ➔ busy
  busy        ➔ process 返回 / 超时                     ➔ available
  available/busy ➔ health 连续 N 次 unhealthy          ➔ offline
```

### 5.4 State Monitor — 状态监控（source of truth）

```
职责：持续探测 Runtime 健康状态，输出 StateReport

探针接口（统一走 Adapter，不依赖具体 Runtime）：
  adapter.sh health
  stdout: {"status":"healthy","active_sessions":1}
  exit 0: 健康  |  exit 1: 降级（框架忙）  |  exit 2: 挂

输出 StateReport：
  { status, active_sessions, queue_depth, avg_latency_ms, last_heartbeat }

Monitor 是 source of truth。
Scheduler 不做自己的判定，只读 StateReport。
```

### 5.5 Adapter — 适配器

AIM Client 与 Runtime 之间的唯一适配层。标准化为 4 个接口：

| 接口 | 命令 | 描述 |
|------|------|------|
| process | `adapter.sh process --message "<内容>" --from "<发送方>"` | 处理消息 |
| health | `adapter.sh health` | 健康探针 |
| info | `adapter.sh info` | 返回 Runtime 元信息 |
| cancel | `adapter.sh cancel --task-id "<task_id>"` | 取消任务（deferred 模式） |

**1. process — 处理消息**

```
adapter.sh process --message "<内容>" --from "<发送方>"
退出码: 0=正常, 1=可重试, 2=降级, 3=人工介入
超时：默认 120s，config 中可配置
```

**2. health — 健康探针**

```
adapter.sh health
退出码: 0=健康, 1=降级, 2=挂
stdout: {"status":"healthy","active_sessions":1}
```

**3. info — Runtime 元信息（新增）**

```
adapter.sh info
退出码: 0=正常
stdout: {
  "provider": "letta",
  "version": "0.28.0",
  "execution_model": "deferred",
  "max_concurrency": 1
}
```

`info` 替代 Agent Card 中手动填写的 `runtime.provider` 和 `runtime.version`——让 Runtime 自己报，而不是配置文件里手动维护。Client 注册时自动调用一次，写入 Agent Card。

**4. cancel — 取消任务（新增）**

```
adapter.sh cancel --task-id "task-abc123"
退出码: 0=已取消, 1=任务不存在, 2=无法取消
```

对 deferred 模式特别重要——Letta 排队中的任务可以通过 cancel 接口撤回，不需要等它被处理。cancel 是 Client→Runtime 的本地接口，**不跨 Agent**。取消请求由本 Client 的 Adapter 消费，不是跨 Agent 消息。

### 5.6 Message / Task 分层 [P1 定义]

目前 AIM 只有 `Message` 一个概念——`你好` 和 `帮我分析这个仓库` 混在一起同路径处理。这会导致：
- `帮我分析这个仓库` 被当成普通消息，没有任务追踪
- Agent 接收方不知道这是"需要回复的聊天"还是"需要执行的任务"
- Scheduler 对不同类型消息无法差异化投递

**分层方案：**

```
Transport Layer
     │
     ▼
Message Layer (通用投递)
     │
     ├── Chat  ─── 即时对话（你好、收到了）
     │
     └── Task  ─── 工作指令（帮我分析这个仓库）
```

**Chat（即时对话）：**
- 无状态，发完即完
- Transport 层 publish 就好
- Scheduler 直接投递到 Adapter process

**Task（工作指令）：**
- 有状态，需要追踪 task_id / status / owner / executor
- 需要 Task Contract 定义
- Scheduler 根据 execution_model 选择投递策略

**Phase 1 定义（schema 先行）：**

```python
@dataclass
class AIMChat:
    """即时对话——无状态"""
    content: str
    from_id: str
    reply_to: str | None = None       # 回复链

@dataclass
class AIMTask:
    """工作指令——有状态"""
    task_id: str                       # 全局唯一任务 ID
    type: str                          # log-analysis / code-review / ...
    input: dict                        # 任务输入
    owner: str                         # 发任务的人
    executor: str                      # 执行的人（谁接的任务）
    status: Literal["pending", "processing", "done", "failed", "cancelled"]
    deadline: str | None = None        # 截止时间（可选）
    expect: dict | None = None         # 期望输出格式
```

**Phase 0**：不做分层，所有消息当 Chat 处理。
**Phase 1**：引入 `AIMTask` 定义，Scheduler 识别任务并创建 task_id 追踪。
**Phase 2+**：Task Contract 完整落地——negotiation / result / cancellation 生命周期。

### 5.7 Identity — 身份层

```
Agent Card Schema v1:

```json
{
  "global_id": "uuid:a1b2c3d4-e5f6-...",          // UUID v4，永久不变
  "serial": "ZS0003",                               // 注册序号，不可变
  "name": "小火鸡儿",                                // 昵称，可改

  "client":       { "type": "aim-client", "version": "1.0.0" },
  "runtime":      { "provider": "letta", "version": "0.27.9" },

  "network": {
    "endpoint": "nats://agent1.example.com:4222",
    "alt_endpoints": ["https://agent1.example.com/aim"],
    "reachable_from": ["public", "vpn", "internal"],
    "requires_relay": false,
    "preferred_transport": "nats"
  },

  "delivery": {
    "mode": "deferred",              // realtime | deferred | fire-and-forget
    "expects_reply": true,
    "max_concurrency": 1,
    "queue_capacity": 1000
  },

  "execution_model": "deferred",     // [P0] realtime | deferred | batch
  // realtime → Hermes/OpenClaw：即时处理，可用 request 等待回复
  // deferred → Letta：单线程排队，消息入列后异步回复
  // batch    → 数据分析 Agent：定时批处理，非即时

  "lifecycle": "AVAILABLE",          // [P1] AVAILABLE | BUSY | DEGRADED | MAINTENANCE | OFFLINE | RETIRED
  // Phase 0-1 只实现 AVAILABLE / BUSY / OFFLINE，其余预留

  "protocol_version": "1.0",
  "min_protocol_version": "0.8",

  "capabilities": [
    {
      "name": "chat",
      "version": "1.0",
      "level": "native"
    },
    {
      "name": "code",
      "language": ["python"]
    }
  ],
  // capabilities 为结构化数组，每项含 name/version/level/language 等
  // 当前阶段（P0-P1）只填 name，结构先做准备。P2 Router 能力路由前必须完整填充

  "trust": {                         // [P2+ 预留] 信誉系统
    "citizenship": "L2",
    "reputation": 0.0,
    "completed_tasks": 0,
    "success_rate": 0.0,
    "endorsements": 0
  },

  "wallet": {                        // [P3+ 预留] Agent 钱包
    "address": "",
    "balance": 0,
    "stake": 0
  }
}
```

> **Phase 0-1 mindset**：execution_model 和结构化 capabilities 现在就用；trust/wallet 只在 Schema 中占位，实现不落地。这样可以避免未来 Router/Discovery 实现时返工改 Card 格式。

---

## 六、Router — 跨协议消息路由 [Phase 2 / OAS 网络层]

> **注意**：Router 属于 OAS 网络层，不内置在 AIM Client 核心模块中。
> Client 只需要 Transport 7 方法 + Agent Card 的网络信息。
> Router 是 OAS 社会的基础设施，管理跨 Transport 的消息流转。

```
职责：根据 Agent Card 中的网络信息，选择正确的 Transport 发送消息

场景：Agent A (NATS) 想发消息给 Agent B (HTTP)
  ➔ Router 查询 B 的 Card
  ➔ 发现 B 走 HTTP
  ➔ Router 选择 HTTPTransport
  ➔ 发送
  ➔ 如果都不可达 → Relay Agent 中继

class Router:
    async def route(self, target_global_id, message) -> bool:
        card = await self.discovery.resolve(target_global_id)
        transport = self.pick_transport(card.network.preferred_transport)
        if transport:
            return await transport.publish(card.network.endpoint, message)
        return await self.relay(card, message)
```

---

### 7.1 群聊 Schema 预留

当前群聊使用 NATS Subject 实现。Schema 增加 `group_type` 字段：

```json
{
  "group_id": "grp_trio",
  "group_type": "chat",         // chat | workspace [预留]
  "members": ["ZS0001", "ZS0002", "ZS0003"],
  "created_at": "..."
}
```

- `chat`：普通聊天室，即时消息广播
- `workspace` [Phase 2+ 预留]：协作空间，可关联 Task Board / 共享上下文 / 分工

Phase 0-1 只实现 `chat`，`workspace` 在 Schema 中占位。

### 7.2 Discovery — Agent 发现协议 [Phase 1 最小实现 → Phase 2 完整]

```
**Phase 1 最小实现**（Agent Card KV 注册 + 在线查询）：
- Agent Card 写入 NATS KV `aim.kv.cards.{global_id}`
- 启动时查询已有 Cards 打印在线列表
- 订阅 `aim.events.card.*` 感知其他 Agent 上下线（谁加入/离开）
- 不实现能力协商

**Phase 2 完整实现**：
1. aim-client 启动
2. Transport 连接成功 + 认证
3. Agent Card 注册到 Registry（NATS KV aim.kv.cards.{global_id}）
4. 其他 Agent 检测到新 Card
5. 可选：能力协商（Publish / Discover / Handshake / Trust）
```

---

## 八、身份三层模型

### 8.1 三层定义

| 层 | 示例 | 生成方式 | 变更规则 | 用途 |
|----|------|---------|---------|------|
| **昵称** | 小火鸡儿 | 用户自定义 | **随时可改** | 日常称呼、群聊 @ |
| **注册序号** | ZS0003 | Server 按注册顺序分配 | **不可变** | 排序、标识、日志 |
| **全局唯一 ID** | `uuid:a1b2c3...` | 注册时 UUID v4 生成 | **永久不可变** | 身份凭证、信誉、JWT 签发 |

### 8.2 三层关系

```
全局 ID（UUID v4，永久不可变）
    ┃ 绑定真人信息
    ┃ 信誉、贡献值、关系网络都绑这个
    ┃ Agent 消失重建、换框架，ID 不变
    ┃
注册序号（Server 分配，不可变）
    ┃ 反映接入顺序，方便日志排查
    ┃ 不体现身份
    ┃
昵称（用户自定义，可改）
    ┃ 日常交流用
```

### 8.3 DID 预留说明

**当前阶段（Phase 0-3）**：UUID v4 + JWT。简单、成熟、无额外依赖。

**未来 OAS 全球规模**：Schema 中预留 `identity.verification` 字段，届时基于 DID Registry + Verifiable Credential + DIDComm 的完整信任模型可无缝替换。

### 8.4 场景适配

| 场景 | 昵称 | 注册序号 | 全局 ID |
|------|------|---------|---------|
| Runtime 崩溃 | 不变 | 不变 | 不变 |
| 换框架 | 不变 | 不变 | 不变 |
| 换机器 | 可改 | 不变 | 不变 |
| 改名 | 改 | 不变 | 不变 |
| 注销重建 | 可改 | 新序号 | 不变 |

**底线**：全局 ID 一旦绑定真人，永久不可变。这是 OAS 社会信任的根基。

---

## 九、错误处理与降级策略

### 9.1 四级降级模型

```
L0 — Runtime 繁忙
  触发：health 返回 degraded 或 process 超时
  Scheduler：消息入 Queue，探针轮询等待
  Queue：pending 持久化到 JetStream

L1 — Runtime 挂（进程不存在）
  触发：health 连续 N 次 unhealthy（默认 N=3）
  Scheduler：标记 OFFLINE
  Queue：继续持久化
  Monitor：探针间隔递增 5s→30s→60s
  Runtime 恢复后：从头消费 pending 队列

L2 — AIM Client 自身崩溃
  触发：进程崩溃 / kill / OOM
  Transport 兜底：消息在 NATS JetStream 中不丢
  launchd / systemd 自动重启
  重启后从 JetStream 恢复 pending

L3 — NATS 断连
  触发：Transport.connect 失败 / 心跳超时
  Transport：离线模式，本地文件缓存
  Queue：回退到文件队列
  Transport 恢复后：补发缓存消息
```

### 9.2 降级路线图

```
正常 → L0 → L1 → L2 → L3
每一步降级独立触发，不依赖前一步。
```

### 9.3 不可恢复的场景

| 场景 | 后果 | 措施 |
|------|------|------|
| Agent ID + JWT creds 同时丢失 | 身份不可恢复 | 备份 creds |
| Runtime 永久损坏 | 换 Runtime | 更新 Agent Card |
| dead 队列积压超容量 | 丢弃最早消息 | 告警 |

---

## 十、安全模型

### 10.1 消息白名单

```json
{ "allowlist": ["ZS0001", "ZS0002"], "allowlist_enabled": true }
```

不在白名单的发件人消息在 Transport 层直接丢弃。allowlist 为空 = 允许所有。

### 10.2 速率限制

Transport 层每 Agent 每秒最多 N 条（默认 10），超限直接丢弃。

### 10.3 群聊准入

群主审批新成员。成员列表存储在 NATS KV `aim.kv.groups.{group_id}`。

### 10.4 认证链

```
Transport.connect → authenticate → subscribe → (可选 verify_peer) → Scheduler
```

Phase 0-1: JWT, Phase 2+: mTLS/OAuth2, Phase 3: 评估 DID。

---

## 十一、版本兼容性

### 11.1 协议版本

Agent Card 中的 `protocol_version` 和 `min_protocol_version` 字段。

Transport 握手时交换：双方最高版本优先，不匹配降级到 min，无法降级断开。

### 11.2 升级策略

- AIM Client 向后兼容：新 Client 可连旧 Transport
- Adapter 接口不变：process 和 health 两个模式的入参出参固化

---

## 十二、迁移路径

```
Phase 0 — Scheduler + Queue 验证（~1天）
├── 在 V3 上嵌入 Queue + Scheduler（内存+JetStream 双写）
├── Adapter 增加 health 模式
├── 明确 Monitor 为 source of truth
├── Agent Card 增加 execution_model（realtime/deferred/batch）
├── Adapter 增加 info 接口（启动时自动获取 Runtime 元信息）
└── 三方各验证一次

Phase 1 — AIM Client 独立进程（~2-3天）
├── 从 V3 抽取 Transport + Adapter → 独立 aim-client
├── Transport 7 方法 + authenticate/verify_peer
├── Agent Card v1（含 execution_model / 结构化 capabilities / lifecycle / trust/wallet 预留）
├── Adapter 4 接口标准化（process / health / info / cancel）
├── Message/Task 分层定义（AIMChat + AIMTask schema）
├── 三级降级模型（L0/L1/L2）
├── 安全模型 v1（白名单 + 限流）
├── 三层身份落地（UUID v4 + JWT）
├── Discovery 最小实现（KV 注册 + 在线列表查询）+ Agent 上下线通知
├── Group 预留 workspace 类型（chat 先行）
└── V3 降级为兼容模式

Phase 2 — 多 Runtime + 路由（~1-2周）
├── OpenClaw/Hermes/Letta 三种 Adapter 正式版
├── Router 跨协议路由（OAS 网络层）
├── Discovery 完整实现（能力协商）
├── Task Contract 完整落地（negotiation / result / cancellation 生命周期）
├── JetStream KV 持久化
├── Transport 多协议扩展（HTTP/WS）
├── Relay 中继机制
├── 群聊准入
├── 生命周期 6 态完整实现（含 MAINTENANCE / DEGRADED / RETIRED）
└── 安全模型 v2（mTLS/OAuth2）

Phase 3 — OAS 公民（后续）
├── 信誉系统 + Trust Layer（L0-L4 Citizenship）
├── Agent Wallet（算力 / 资源 / 积分）
├── DID 评估（全球规模时）
├── 消息签名（高信誉场景不可否认性）
├── Constitution Layer（宪法层）
└── 对齐 RFC-0001/0002/0003/0004/0005
```

---

## 十三、关键决策

| 决策 | 结论 | 说明 |
|------|------|------|
| Agent ≠ Runtime | ✅ 确定 | AIM Client 独立于 Runtime |
| 全局 ID 生成 | UUID v4 + JWT | DID 做 Schema 预留，不做实现 |
| 三层身份模型 | 昵称/序号/全局ID | 昵称可改，序号不可变，全局ID永久 |
| DID 生态 | 延后评估 | 等 OAS 全球规模再评估 DID Registry |
| Transport 协议抽象 | 7 方法 | connect/disconnect/authenticate/verify_peer/subscribe/publish/request |
| Client:Runtime 关系 | 逻辑 1:1 | 非物理绑定 |
| Phase 0 策略 | 嵌入 V3 | 不推翻现有代码 |
| Queue 持久化 | 内存 + JetStream 双写 | 重启不丢消息 |
| 谁是谁的 source of truth | Monitor | Scheduler 只消费 StateReport |
| 交付模式 | realtime/deferred/fire-and-forget | 新增纯监控 Agent 支持 |

---

## 十四、术语表

| 术语 | 定义 |
|------|------|
| AIM Client | Agent 的通信终端，负责身份、消息、调度。OAS 公民身份载体 |
| Runtime | Agent 的思维引擎（Letta/Hermes/OpenClaw）。可更换 |
| Agent Card | 描述 Agent 身份、能力、网络信息的 JSON 文档 |
| Transport | 通信层，屏蔽 NATS/A2A/HTTP/WS 差异 |
| Scheduler | 根据 StateReport 决定消息投递策略 |
| StateReport | Monitor 输出的 Runtime 健康状态报告 |
| Adapter | 唯一适配 Runtime 的脚本层，4 标准接口（process / health / info / cancel） |
| Queue | 消息缓存队列，Runtime 忙/离线时暂存 |
| execution_model | Runtime 执行模式：realtime / deferred / batch |
| lifecycle | Agent 生命周期状态：AVAILABLE / BUSY / DEGRADED / MAINTENANCE / OFFLINE / RETIRED |
| AIMChat | 无状态即时消息（对话） |
| AIMTask | 有状态的工作指令，带 task_id / status / owner / executor |
| global_id | 全局唯一身份 ID（UUID v4），绑定真人，永久不变 |
| serial | 注册序号（ZS000X），Server 分配，不可变 |
| delivery_mode | 投递模式：realtime/deferred/fire-and-forget |

---

## 附录：代码复用度

| 模块 | 现有资产 | 可复用 | 需新建 | 阶段 |
|------|---------|--------|--------|------|
| Transport | SDK 1933行 | ~800行 | ~100行 | P1 |
| Queue | 降级队列 | ~200行 | ~300行 | P0 |
| Scheduler | 无 | 0 | ~200行 | P0 |
| Monitor | Observer 459行 | ~300行 | ~300行 | P1 |
| Adapter | call_adapter.py 188行 | ~150行 | ~200行 | P1 |
| Identity | config.json | ~30行 | ~300行 | P1 |
| Router | 无 | 0 | ~400行 | P2 |
| Discovery | 无 | 0 | ~300行 | P2 |
| Relay | 无 | 0 | ~500行 | P2 |

**总工作量：Phase 0 ~1 天，Phase 1 ~2-3 天，Phase 2 ~1-2 周。**
