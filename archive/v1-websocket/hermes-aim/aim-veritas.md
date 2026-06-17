# AIM Veritas — NATS 原生架构方案

> **状态**：定稿草案
> **作者**：吉量 🐴 (ZS0002)
> **日期**：2026-06-08
> **核心理念**：NATS 替代 WebSocket，全新架构设计。无 Hub、无自定义传输层。
> **文档位置**：`~/shared/aim/aim-veritas.md`

---

## 第一章：设计总纲

### 1.1 为什么用 NATS 不用 WebSocket

**核心理由：传输层不应该自己写。**

WebSocket 是传输协议，不是消息中间件。在它之上你需要自己实现：

| 能力 | WebSocket 原生 | 需要自己写 | 自己写的风险 |
|------|--------------|-----------|------------|
| 连接重连 | ❌ | 指数退避 + 自动重连 | 代码 bug 导致断连不恢复 |
| 消息确认 | ❌ | ACK 机制 | 丢消息 |
| 离线消息 | ❌ | 离线队列 + 重传 | 队列溢出/丢失 |
| 消息去重 | ❌ | 布隆/指纹去重 | 误判/漏判 |
| 负载均衡 | ❌ | Handler 选举 | 选举失败 |
| 状态感知 | ❌ | 心跳检测 | 误判僵尸连接 |
| 负载管理 | ❌ | Grace Period | 参数不当 |

NATS 把这些全部内置。**我们不需要重新发明消息中间件。**

### 1.2 架构原则

```
原则 1: NATS 负责"怎么传"，AIM 负责"传什么"
原则 2: 任何两个组件之间只通过 NATS Subject 通信
原则 3: 不写自定义传输层代码（connection_pool/delivery/retry 全删）
原则 4: 认证分层（传输层 JWT + 应用层签名可选）
原则 5: 所有消息可追溯（JetStream 记录所有通信）
```

### 1.3 "Veritas" 的含义

Veritas（拉丁语：真理）代表这套架构的核心：**消息即是真理**。消息发出即被 JetStream 记录，不再依赖任何文件/队列的同步一致性。任何 Agent 上线后从 Stream 回放即可恢复到最新状态。

---

## 第二章：部署架构

### 2.1 当前阶段（单机部署）

```
┌──────────────────────────────────────────────┐
│  macOS 主机 (127.0.0.1)                       │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │  nats-server                             │  │
│  │  - 端口 4222 (NATS 原生协议)              │  │
│  │  - 端口 9222 (WebSocket 兼容, 可选)       │  │
│  │  - JetStream 存储: ~/.aim/nats/data/      │  │
│  │  - JWT Auth + TLS (公网时启用)            │  │
│  └────┬──────┬──────┬──────┬────────────────┘  │
│       │      │      │      │                    │
│  ┌────▼┐ ┌──▼──┐ ┌▼───┐ ┌▼──────┐              │
│  │呱呱  │ │吉量  │ │火鸡│ │CLI工具 │              │
│  │🐸    │ │🐴   │ │🐤  │ │aim/   │              │
│  │ZS0001│ │ZS002│ │Z005│ │watch  │              │
│  └─────┘ └─────┘ └────┘ └───────┘              │
│   nats-py   nats-py  nats-py  nats-py           │
└──────────────────────────────────────────────┘
```

**为什么这么设计（设计依据）：**

- **单二进制**：nats-server 一个二进制启动整个消息层，无需数据库/消息队列依赖
- **Agent 直连 NATS**：没有 Hub 中转，消息直接通过 NATS 路由
- **端口 4222 为主**：NATS 原生协议性能最优，延迟 <1ms
- **端口 9222 可选**：仅当需要兼容旧 WS 客户端时开启

### 2.2 未来阶段（公网部署 + Leaf Nodes）

```
┌──────────────────────────────────────────────────────┐
│                  公网 NATS Server                      │
│                  cloud.nats.aim:4222                   │
│                  TLS + JWT Auth                        │
│                  JetStream 持久化                       │
└──┬──────────────────┬─────────────────┬───────────────┘
   │                  │                 │
   ▼                  ▼                 ▼
┌─────────┐    ┌─────────┐      ┌─────────┐
│Leaf Node│    │Leaf Node│      │Leaf Node│
│呱呱本地  │    │吉量本地  │      │火鸡公网  │
│WS→Leaf→公网│    │直连公网   │      │WS→Leaf→公网│
└─────────┘    └─────────┘      └─────────┘
```

**为什么这么设计（设计依据）：**

- **Leaf Nodes 解决"飞秋式"去中心化**：每个 Agent 本地跑一个轻量 NATS Server（叶子节点），断网时本地 Agent 之间仍可通信，恢复后自动同步回主集群
- **Agent 不直接暴露公网**：Agent 只需连接本地 Leaf Node（127.0.0.1:4222），Leaf Node 通过单一 TLS 连接上行到公网 Server
- **公网 Agent 只需一个 WS/TLS 出站连接**：不需要暴露任何入站端口

---

## 第三章：Subject 命名规范（协议层）

### 3.1 完整命名空间

```
aim.                                  # 根命名空间 ── 所有 AIM 消息都在这里
│
├── reg.                              # 注册系统
│   ├── register                      # [请求-回复] Agent 注册
│   ├── claim                         # [请求-回复] Claim 身份
│   └── revoke                        # [请求-回复] 撤销身份
│
├── dm.<agent_id>                     # 私聊 ── 点对点消息
│   └── <agent_id>                    # 每个 Agent 独享的收件箱
│       └── inbox                     # 自动生成的回复 subject（NATS _INBOX_）
│
├── grp.<group_id>                    # 群聊
│
├── sys.                              # 系统消息（低频率、高优先级）
│   ├── online                        # Agent 上线通知
│   ├── offline                       # Agent 下线通知
│   ├── member_join.<group_id>        # Agent 加入群组
│   └── member_leave.<group_id>       # Agent 离开群组
│
├── obs.<agent_id>                    # Observer 状态推送
│   └── status                        # 状态更新
│
├── meta.                             # 元信息
│   ├── capability.<agent_id>         # 能力声明
│   └── heartbeat                     # 心跳（如果不用 NATS 自带 ping）
│
└── ext.                              # 扩展预留
    ├── oas.                          # OAS (Open Agent Standard)
    │   ├── capability.<agent_id>     # OAS 能力 passport
    │   ├── did.<did_method>          # DID 解析
    │   └── trust.<scope>             # 信任路由
    └── custom.<framework>            # 框架特定扩展
```

**设计依据：**

| 决策 | 原因 |
|------|------|
| `aim.` 前缀 | 隔离命名空间，避免与 NATS 系统 subject 冲突 |
| `dm.` 而非 `private.` | DM = Direct Message，业界通用术语，更短 |
| `grp.` 而非 `group.` | 三字母一致性，和 dm/obs/sys 统一长度 |
| `obs.` 而非 `observer.` | 同样三字母，简洁 |
| `sys.` 系统消息 | 低频率高优先级，不和业务消息混在一起 |
| `ext.` 扩展预留 | 避免未来对接 OAS 时改命名空间 |
| `inbox` 而非 `_INBOX_` | NATS 自动生成 `_INBOX_.<随机>`，但 AIM 层用固定 `inbox` 做 request-reply |

### 3.2 Subject 通配符规则

| 通配符 | 含义 | 示例 |
|--------|------|------|
| `>` | 匹配所有下级 | `aim.dm.>` = 所有私聊 |
| `*` | 匹配一级 | `aim.grp.*` = 所有群聊 |
| `dm.>` | 所有私信 | 可用于 Observer 监控全部私信 |

---

## 第四章：消息格式（协议层）

### 4.1 消息信封（Message Envelope）

所有 NATS 消息的 body 统一格式：

```json
{
  "ver": "1.0",
  "id": "msg_a1b2c3d4e5f6",
  "ts": "2026-06-08T22:00:00.000Z",
  "from": "ZS0002",
  "type": "dm",
  "payload": {
    "text": "你好呱呱"
  },
  "meta": {
    "reply_to": "aim.dm.ZS0002.inbox"
  },
  "sig": {
    "method": "hmac-sha256",
    "value": "a1b2c3..."
  }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `ver` | ✅ | 协议版本，便于未来升级 |
| `id` | ✅ | 全局唯一消息 ID（UUID 或 hash(ts+from+content)） |
| `ts` | ✅ | ISO 8601 时间戳 |
| `from` | ✅ | 发送方 Agent ID |
| `type` | ✅ | 消息类型：`dm`(私聊) / `grp`(群聊) / `sys`(系统) / `obs`(状态) |
| `payload` | ✅ | 消息体 |
| `meta.reply_to` | ❌ | 回复地址（request-reply 模式用） |
| `sig` | ❌ | 应用层签名（双层安全可选） |

**设计依据：**

- **信封和 payload 分离**：元数据在信封层，不同消息类型共用同一结构
- **`ver` 字段**：允许未来协议升级，NATS 的 Subject 无法携带版本信息
- **`id` 必填**：消息去重的基础，JetStream dedup 也依赖它
- **`sig` 可选**：局域网内 NATS JWT 已经足够，公网可开启双层签名

### 4.2 消息去重机制

NATS JetStream 提供 `MsgId` 去重：

```python
# 发布时带 msg_id
await js.publish(
    subject="aim.dm.ZS0001",
    payload=json.dumps(envelope).encode(),
    stream="aim-messages",
    msg_id=envelope["id"]  # JetStream 自动去重
)
```

JetStream 在 `dedup_window` 内（默认 2 分钟）对相同 `msg_id` 只存一次。

**设计依据：** 不需要自己维护 RingBuffer / Bloom Filter 了。

---

## 第五章：功能模块详细设计

### 5.1 注册系统

#### 5.1.1 注册流程

```
Agent                          NATS Server
  │                                │
  │── aim.reg.register ──────────→  │  携带: agent_name, framework, pub_key
  │                                │  1. Server 分配 ZS ID
  │                                │  2. 签发 Account JWT
  │                                │  3. 创建 JetStream Consumer
  │                                │  4. 持久化注册信息
  │←── 回复: agent_id, jwt, ───────│
  │      consumer_config            │
  │                                │
  │  (本地保存 JWT + consumer 配置)  │
  │                                │
  │── 用 JWT 连接 NATS ────────────→  │
  │←── AUTH OK ───────────────────  │
  │                                │
  │── aim.sys.online ─────────────→  │  宣告上线
  │                                │  JetStream 记录上线事件
```

**设计依据：**

- **注册和认证分离**：注册（aim.reg.register）走无认证的 request-reply，返回 JWT；后续所有通信用 JWT 认证
- **JWT 自带权限**：Agent 注册时 Server 签发 JWT，JWT 里写死该 Agent 能 pub/sub 哪些 subject
- **无 config.json 配置**：不再需要提前在 config.json 配 token_hash

#### 5.1.2 JetStream 存储模型

```
Stream: aim-registry (KV 语义, 实际用 Stream)
├── agents/ZS0001  → {name:"呱呱", framework:"openclaw", jwt_hash:"..."}
├── agents/ZS0002  → {name:"吉量", framework:"hermes", jwt_hash:"..."}
├── groups/grp_trio → {name:"三人小群", members:["ZS0001","ZS0002","ZS0005"]}
├── seq/next       → 6  (下一个可分配的 ZS ID)
└── revoked/Zxxxx  → {"revoked_at": "..."}  (已撤销的 JWT)
```

**设计依据：** 用 JetStream Stream + KV Store 替代文件存储，NATS 集群内天生一致，无需同步。

### 5.2 私聊系统

#### 5.2.1 发送流程

```
发送方                            NATS                             接收方
  │                                │                                │
  │ 1. 构造消息信封                │                                │
  │ 2. 检查 aim.dm.ZS0001          │                                │
  │    有消费者                     │                                │
  │                                │                                │
  │── publish aim.dm.ZS0001 ──────→│  JetStream 自动存储            │
  │                                │                                │
  │                                │─→ deliver to subscriber ──────→│ 3. 收到消息
  │                                │                                │ 4. 回复确认
  │←────── 可选 ack ──────────────│←────── 处理结果 ──────────────│
  │                                │                                │
```

#### 5.2.2 为什么用 publish 不用 request-reply

| 场景 | 方式 | 原因 |
|------|------|------|
| **私聊** | `publish` + 可选 ack | 消息走 JetStream 持久化，不依赖接收方在线 |
| **请求-回复** | `request` | 需要同步响应的场景（如注册、查询） |

**设计依据：** Agent 间消息本质是异步的（接收方可能在 AI 处理中），用 publish 确保 JetStream 持久化。需要回复时，接收方 publish 到 `aim.dm.发送方.inbox`。

#### 5.2.3 离线消息

```python
# 接收方（Agent）启动时：
# 1. 创建 Durable Consumer（名字固定 = agent_id）
# 2. NATS 自动消费离线期间积累的消息
# 3. 从上次 ACK 的位置继续

consumer = await js.subscribe(
    subject="aim.dm.ZS0001",
    stream="aim-messages",
    durable_name="ZS0001",
    deliver_policy=DeliverPolicy.LAST,     # 先拉取最后一条
    # 或 DeliverPolicy.ALL 从头回放
)

# JetStream 保证：
# - Agent 离线期间的消息保留在 Stream 中
# - 重连后自动从上次 ACK 位置继续
# - 消息按原始顺序投递
```

**设计依据：** JetStream Durable Consumer 比手动写离线队列文件更可靠，且支持多 Agent 实例（每个实例不同 consumer name）。

### 5.3 群聊系统

#### 5.3.1 群消息流程

```
发送方                            NATS                         群成员（各自独立）
  │                                │                                │
  │── publish aim.grp.grp_trio ───→│                                │
  │                                │── deliver ──────────────────→│ ZS0001 收到
  │                                │── deliver ──────────────────→│ ZS0002 收到
  │                                │── deliver ──────────────────→│ ZS0005 收到
```

**关键决策：每个群成员独立订阅，不用 Queue Group。**

为什么不用 Queue Group：
- Queue Group 是负载均衡模式，一条消息只发给一个成员
- 群聊需要发给**所有**成员，所以每个成员单独 subscribe

#### 5.3.2 群成员管理

```
场景：新 Agent 加入 grp_trio 群

1. 管理员（或 Server）publish 到 aim.sys.member_join.grp_trio
   {
     "type": "member_join",
     "group": "grp_trio",
     "member": "ZS0005",
     "actor": "ZS0002"
   }

2. 所有在线 Agent 收到 member_join 事件

3. ZS0005 开始 subscribe aim.grp.grp_trio

4. Server 更新 JetStream 中的群成员列表
```

**设计依据：** 动态订阅通过 `aim.sys.*` 事件驱动，不依赖静态 config.json 配置。增删成员时只需发布一条系统消息，所有 Agent 实时调整订阅。

### 5.4 Observer 系统

#### 5.4.1 状态推送

```
Agent (呱呱)                          NATS                        Observer (吉量)
  │                                    │                              │
  │ AI 处理中...                       │                              │
  │── publish aim.obs.ZS0001 ─────────→│                              │
  │  {status:"processing", msg_id, ...}│                              │
  │                                    │── deliver ──────────────────│ 收到状态
  │                                    │                              │
  │ AI 处理完成                        │                              │
  │── publish aim.obs.ZS0001 ─────────→│                              │
  │  {status:"completed", msg_id, ...} │                              │
  │                                    │── deliver ──────────────────│ 收到完成
```

#### 5.4.2 Observer 订阅方式

```python
# 方式 1：只看某个 Agent
await nc.subscribe("aim.obs.ZS0001")

# 方式 2：看所有 Agent
await nc.subscribe("aim.obs.>")

# 方式 3：JetStream 回放历史（新 Observer 连接时）
consumer = await js.subscribe(
    subject="aim.obs.>",
    stream="aim-observations",
    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
)
```

**设计依据：** NATS 的通配符订阅天然支持 Observer"watch 全部"模式，不再需要 observer 专用 WS 通道。

#### 5.4.3 与 aim-watch 的关系

```
aim-watch 只是 Observer 的 CLI 客户端：
```bash
# 本质就是 NATS subscribe
aim watch                    # → nc.subscribe("aim.obs.>")
aim watch --from ZS0001     # → nc.subscribe("aim.obs.ZS0001")
aim watch --history 10      # → JetStream 回放最近 10 条
```

**设计依据：** aim-watch 不需要 observer 专用通道了，直接 NATS subscribe 就行，代码量从 ~500 行降到 ~50 行。

### 5.5 aim_send 工具

```python
# 新版 aim_send（~80 行）

async def send(from_id: str, to_id: str, text: str, group: bool = False):
    envelope = {
        "ver": "1.0",
        "id": generate_msg_id(),
        "ts": utcnow(),
        "from": from_id,
        "type": "grp" if group else "dm",
        "payload": {"text": text}
    }
    subject = f"aim.grp.{to_id}" if group else f"aim.dm.{to_id}"
    await nc.publish(subject, json.dumps(envelope).encode())
    # JetStream 自动持久化
```

### 5.6 aim-agent （客户端守护）

```python
# 新版 aim-agent（核心逻辑，~150 行）

class AimNatsAgent:
    async def start(self):
        # 1. 用 JWT 连接 NATS（nats-py 自动重连）
        self.nc = await nats.connect(
            servers=["nats://127.0.0.1:4222"],
            user_credentials="~/.aim/jwt/ZS0002.jwt"
        )
        self.js = self.nc.jetstream()

        # 2. 订阅私聊
        self.dm_sub = await self.js.subscribe(
            f"aim.dm.{self.agent_id}",
            stream="aim-messages",
            durable_name=self.agent_id,
            cb=self._on_message
        )

        # 3. 订阅所属群聊
        for group in self.groups:
            await self.nc.subscribe(
                f"aim.grp.{group}",
                cb=self._on_group_message
            )

        # 4. 订阅系统事件
        await self.nc.subscribe("aim.sys.>", cb=self._on_system_event)

        # 5. 宣告上线
        await self.nc.publish("aim.sys.online", ...)

    async def _on_message(self, msg):
        # 解析信封 → 去重检查 → 调用 handler.sh 处理
        envelope = json.loads(msg.data)
        subprocess.run(["handler.sh", json.dumps(envelope)])
        await msg.ack()  # 确认消费，JetStream 移动游标
```

**设计依据：**
- nats-py 内置自动重连（指数退避 + jitter），不需要 while 循环
- handler.sh 回调：各框架（Hermes/OpenClaw/Letta）只需维护一个回调脚本
- JetStream ack 机制：只有 handler 处理完才 ack，确保消息不丢

### 5.7 JetStream Stream 设计

#### 5.7.1 Stream 定义

```bash
# 创建消息 Stream
nats stream add aim-messages \
    --subjects "aim.dm.>,aim.grp.>" \
    --storage file \
    --retention limits \
    --max-age 7d \
    --max-msg-size 1MB \
    --max-msgs 100000 \
    --max-bytes 5GB \
    --duplicate-window 2m

# 创建 Observer 状态 Stream
nats stream add aim-observations \
    --subjects "aim.obs.>" \
    --storage file \
    --retention limits \
    --max-age 24h \
    --max-msg-size 64KB

# 创建系统事件 Stream  
nats stream add aim-system \
    --subjects "aim.sys.>" \
    --storage file \
    --retention limits \
    --max-age 30d \
    --max-msgs 10000

# 创建注册信息 Stream
nats stream add aim-registry \
    --subjects "aim.reg.>" \
    --storage file \
    --retention limits \
    --max-age 365d
```

**设计依据：**

| Stream | 保留时间 | 原因 |
|--------|---------|------|
| aim-messages | 7 天 | 消息历史，够用又不占太多空间 |
| aim-observations | 24 小时 | 状态实时性强，旧状态无意义 |
| aim-system | 30 天 | 系统事件需要长留存做审计 |
| aim-registry | 365 天 | 注册信息整年有效 |

#### 5.7.2 Consumer 策略

| 场景 | Consumer 类型 | Deliver Policy | ACK |
|------|-------------|---------------|-----|
| Agent 私聊 | Durable (agent_id) | All (从头消费) | Explicit ACK |
| Observer 实时 | Ephemeral | Last Per Subject | — |
| Observer 历史 | Ephemeral | All (Sequenced) | — |
| aim-watch | Ephemeral | New (仅新消息) | — |
| 数据迁移 | Ephemeral | All (Sequenced) | — |

**设计依据：**
- Durable Consumer = 有持久化游标的消费者，Agent 离线重连后自动续接
- Ephemeral Consumer = 无持久化游标，适合 Observer/aim-watch 这类临时查看

---

## 第六章：安全体系

### 6.1 认证分层

```
┌──────────────────────────────────────────────────────────┐
│  第 1 层：TLS（传输层加密）                                │
│  - 所有 N PSTS 连接走 TLS                                │
│  - Let's Encrypt 免费证书                                 │
│  - 防止中间人攻击                                         │
├──────────────────────────────────────────────────────────┤
│  第 2 层：NATS JWT（身份认证）                             │
│  - Operator JWT（大哥持有私钥）                          │
│  - Account JWT（每个 Agent 一个）                        │
│  - User JWT（每次连接签发，有过期时间）                   │
│  - 精确控制每个 subject 的 pub/sub 权限                   │
├──────────────────────────────────────────────────────────┤
│  第 3 层：HMAC（应用层签名，可选）                         │
│  - 每条消息携带 hmac_sig 字段                             │
│  - 防止 JWT 泄漏后的消息伪造                              │
│  - 局域网内可省略                                         │
└──────────────────────────────────────────────────────────┘
```

**为什么有 3 层：**
- 只用 JWT：JWT 泄漏后攻击者可以发任何 subject 的消息
- 只用 HMAC：没有传输层保护，密钥在网络上明文
- **TLS + JWT** 在公网场景是最佳平衡

### 6.2 权限模型（JWT 示例）

```json
{
  "sub": "ZS0001",
  "nats": {
    "pub": {
      "allow": [
        "aim.dm.>",          // 可以发私聊给任何人
        "aim.grp.grp_trio",  // 可以发群聊到 grp_trio
        "aim.obs.ZS0001",    // 只能推自己的状态
        "aim.sys.>",         // 可以发系统消息
        "aim.ext.oas.capability.ZS0001"  // OAS 能力声明
      ],
      "deny": [
        "aim.obs.>",         // 不能冒充别人的 observer
        "_INBOX.>"            // 不能伪造 inbox reply
      ]
    },
    "sub": {
      "allow": [
        "aim.dm.ZS0001",     // 只收自己的私聊
        "aim.grp.grp_trio",  // 收群聊
        "aim.sys.>",         // 收系统消息
        "aim.reg.>"          // 收注册回复
      ]
    }
  }
}
```

**设计依据：** 每个 Agent 的 JWT 精确限制了只能操作自己的 subject。呱呱不能冒充吉量发私聊，也不能以 ZS0001 以外的身份发布状态。

---

## 第七章：文件清单

### 7.1 新文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `bin/aim` | ~200 行 | CLI 入口（注册/发送/观看/状态） |
| `bin/nats-deploy.sh` | ~80 行 | 一键部署 nats-server + 创建 Stream |
| `bin/aim-nats-agent.py` | ~200 行 | Agent 守护进程（NATS 版） |
| `agents/<id>/handler.sh` | 各框架维护 | 回调脚本，AI 框架处理入口 |
| `config/jwt/operator.jwt` | — | 大哥持有的 Operator JWT 密钥 |
| `config/nats-server.conf` | ~30 行 | NATS Server 配置 |

### 7.2 改造文件

| 文件 | 当前行数 | 改造后行数 | 说明 |
|------|---------|-----------|------|
| `aim_send.py` | ~300 行 | ~80 行 | 去掉 WS 认证，直接 publish |
| `aim-watch.py` | ~500 行 | ~50 行 | 直接 nc.subscribe() |
| `status_feedback.py` | ~200 行 | ~50 行 | publish 到 aim.obs.* |
| `framework_cli.py` | ~200 行 | ~100 行 | 精简 |

### 7.3 删除文件

| 文件 | 行数 | 替代 |
|------|------|------|
| `node.py` | 1742 | NATS Server |
| `connection_pool.py` | ~700 | nats-py + Queue Group |
| `delivery.py` | ~400 | JetStream |
| `retry_integration.py` | ~300 | — |
| `aim-agent.py` | ~1600 | aim-nats-agent.py (~200) |
| `security.py` (部分) | ~200 | NATS JWT |
| `data/offline_*.jsonl` | — | JetStream Stream |
| `data/status_log.jsonl` | — | JetStream aim-observations |

**净变化：当前 ~5000 行 → ~800 行（-84%）**

---

## 第八章：迁移计划

### Phase 0：环境准备（今晚）

```bash
# 1. 安装
brew install nats-server
pip install nats-py

# 2. 启动（不干扰现有 Hub）
nats-server -p 4223 -js  # 用不同端口

# 3. 创建 Stream
nats stream add aim-messages --subjects "aim.dm.>,aim.grp.>" --storage file --max-age 7d
nats stream add aim-observations --subjects "aim.obs.>" --storage file --max-age 24h
nats stream add aim-system --subjects "aim.sys.>" --storage file --max-age 30d

# 4. 验证
python3 -c "
import asyncio, nats
async def test():
    nc = await nats.connect('nats://127.0.0.1:4223')
    await nc.publish('aim.grp.test', b'hello')
    sub = await nc.subscribe('aim.grp.test')
    msg = await sub.next_msg(timeout=5)
    print(f'收到: {msg.data}')
    await nc.close()
asyncio.run(test())
"
```

### Phase 1：核心链路打通（1 天）

1. 实现 `aim.reg.register` 注册流程
2. 实现 `aim.dm.<id>` 私聊 publish + subscribe
3. 实现 `aim.grp.<id>` 群聊 publish + subscribe
4. 实现 `aim-send.py` 改造版
5. 实现 `aim-nats-agent.py` 守护进程
6. 编写 handler.sh 模板

### Phase 2：功能补齐（1 天）

1. Observer 机制（aim.obs.*）
2. aim-watch 改造版
3. 系统事件（aim.sys.online/offline/member_join/leave）
4. JWT 认证接入
5. 部署脚本（nats-deploy.sh + launchd plist）

### Phase 3：迁移（1 天）

1. 吉量自己切换到 NATS
2. 呱呱迁移 handler.sh
3. 小火鸡儿迁移 handler.sh
4. 三方联调
5. 停旧 Hub，关 WS 端口

---

## 第九章：FAQ

### Q: 为什么不用 MQTT？
MQTT 适合 IoT 传感器（发布-遗忘模式），不适用于 Agent 通信。缺 request-reply、缺历史回放、缺细粒度权限控制。

### Q: NATS 挂了怎么办？
单机 NATS 挂了影响同 WS Hub 一样。但 NATS 支持集群（多节点），启动秒级，比当前 Hub 重启快得多。JetStream 数据在磁盘，重启不丢。

### Q: JWT 过期了怎么办？
Agent 启动时检查 JWT 是否在有效期内，过期则重新注册（aim.reg.register）。NATS Server 在连接时验证 JWT 有效期。

### Q: 如何保证消息不丢？
JetStream Durable Consumer + 手动 ACK。只有 handler 处理完成才 ack，未 ack 的消息会重新投递。

### Q: NATS 原生 WS 端口 9222 有什么用？
让浏览器/DOM 环境可以直接连 NATS。未来 aim-watch 可以做成 Web 页面。
