# AIM Client 方案优化思路 — 吉量分析

> 基于：大哥定调 + 咕呱可行性分析 + 小火鸡儿模块设计 + 三方评审意见 + 火鸡儿补充思路
> 日期：2026-06-16

---

## 一、方案站得住的部分（无需大改）

评审意见没有质疑这几个核心方向，说明方向是对的：

- Agent ≠ Runtime 分离 ✅
- 六模块架构 ✅
- Transport 协议抽象 ✅
- Scheduler 独立于 Runtime ✅
- 三层身份模型 ✅
- Phase 0 嵌入 V3 策略 ✅

这几块不动，继续往下走。

---

## 二、需要工程化补充的部分（按优先级）

### P0（做 Phase 0 前必须先明确）

#### 1. Scheduler 判定逻辑 — 谁是谁的 source of truth

当前方案问题：Scheduler 和 Monitor 职责边界模糊，都不知道谁来判断状态。

**修正：**
```
Monitor 只做一件事：探针检测，输出 StateReport
Scheduler 只做一件事：读 StateReport，决定状态转换

Monitor 是 source of truth。
Scheduler 不做自己的判定。
```

```
Monitor 探针 → StateReport → Scheduler 读 → 状态转换 (idle/busy/offline)
     ↕ (每 5-30s)
adapter.sh health
```

**状态转换触发条件明确化：**

| 当前状态 | 触发条件 | 下一状态 |
|---------|---------|---------|
| offline | adapter.sh health 返回 healthy | idle |
| idle | Scheduler 开始投递消息 | busy |
| busy | adapter.sh process 返回 / 超时 | idle |
| idle/busy | adapter.sh health 连续 N 次返回 unhealthy | offline |

#### 2. Queue 持久化策略 — offline 时消息怎么办

当前方案：P0 内存, M1 JetStream。但没说 offline 时消息怎么处理。

**修正：**

```
offline 时：
  消息不清空，入 pending 队列
  pending 队列持久化到 JetStream KV
  等 Monitor 检测到 Runtime 恢复后，从头开始投递
  
dead 队列：
  超时/失败的消息进入 dead 队列
  dead 队列 TTL = 24 小时
  超期自动清除
```

#### 3. 错误处理三级降级

当前方案：完全没有错误处理章节。

**新增三级降级模型：**

```
L0 — Runtime 繁忙
    Scheduler: 消息入 Queue，探针轮询等待
    不影响其他消息

L1 — Runtime 挂（进程不存在）
    Scheduler: 标记 OFFLINE
    Queue: 消息持久化到 JetStream KV
    Monitor: 定时探针，检测到恢复后通知 Scheduler
    Scheduler: 切 IDLE，从头消费 Queue

L2 — AIM Client 自身崩溃
    Transport 层兜底：消息在 NATS JetStream 里
    launchd/systemd 自动重启 AIM Client
    重启后从 JetStream 恢复 pending 队列

L3 — NATS 全挂 / 网络断连
    Transport 层：离线模式，本地文件缓存
    Queue: 回退到文件队列
    Transport 恢复后：补发缓存消息
```

---

### P1（做 Phase 1 之前明确）

#### 4. Transport 方法统一命名

当前方案：说"5 方法"但实际列了 7 个，而且没统一写全。

**修正为 7 个方法，写全：**

```python
class Transport(ABC):
    async def connect(self) -> bool
    async def disconnect(self)
    async def authenticate(self, credential: dict) -> str
    async def verify_peer(self, peer_id: str, signature: bytes) -> bool
    async def subscribe(self, subject: str, callback) -> str
    async def publish(self, subject: str, payload: dict) -> bool
    async def request(self, subject: str, payload: dict, timeout: float) -> dict
```

#### 5. Client:Runtime 1:1 的语义明确

当前方案：写了 1:1 但没说是逻辑关系还是物理关系。

**修正为：**

```
Client:Runtime 是逻辑 1:1，不是物理 1:1。

逻辑含义：
  一套 AIM Client 实例 = 一个 Agent 公民身份 = 一个 Runtime 实例
  max_concurrency 控制 Runtime 同时处理能力
  
所以 CrewAI 多 agent 协作走的是同一个 AIM Client 投递消息，
不是多个 AIM Client 各自绑一个子 agent。
```

#### 6. capabilities 自声明 + 标记

当前方案：capabilities 自己填，没有校验。

**短期（P1-P2）：**
```
Agent Card 中 capabilities 是自声明
加 verified: false 标记，表示"未经 Server 校验"
```

**长期（P3/L2 Citizenship+）：**
```
Server 端注册时做能力校验
或至少版本化描述（capabilities.v2）
```

#### 7. 安全模型（白名单 + 限流 + 群聊准入）

当前方案：完全没有。

**最小可行安全（P1）：**

```
1. 白名单模式
   Agent Card 里加 allowlist: ["ZS0001", "ZS0002"]
   不在白名单的消息在 Transport 层丢弃

2. 速率限制
   Transport 层每 Agent 每秒最多 N 条
   超过直接丢弃，不进入 Queue

3. 群聊准入
   群主审批新成员
   Agent Card 里的 groups 字段列允许加入的群
```

#### 8. 版本兼容性

当前方案：只有 client.version，没有协议版本。

**修正：**

```
Transport 握手时交换 protocol_version
不匹配时降级到双方都支持的最高版本

Agent Card 里加：
  "protocol_version": "1.0"
  "min_protocol_version": "0.8"
```

---

### P2（做 Phase 2 之前明确）

#### 9. fire-and-forget 投递模式

原因：TOP100 里有纯监控 Agent，只收不发，不需要等回复。

**修正：**

```
delivery.mode 支持三种：
  realtime        — 实时回复，发完等回
  deferred         — 延迟回复，消息进 Queue 等空闲
  fire-and-forget — 纯接收，不期待回复

delivery.expects_reply: bool
  false = 消息投递后直接 ACK，不等回复
```

#### 10. max_concurrency 的语义

原因：Letta=1, Hermes=5, API 服务=100。Scheduler 需要知道能同时投几个。

**修正：**

```
max_concurrency 是 Runtime 同时能处理的请求数
直接影响 Scheduler 的投递策略：
  如果 max_concurrency=5，Scheduler 可以同时投递 5 条消息
  如果=1，串行投递
```

---

## 三、不做和延后的部分

| 事项 | 决策 | 原因 |
|------|------|------|
| DID 信任模型 | 延后到 Phase 3 | UUID v4 + JWT 够用，DID 生态太重 |
| 消息签名（不可否认性） | 延后到 Phase 3 | L3-L4 Citizenship 才需要 |
| Server 端能力校验 | 延后到 Phase 3 | P1-P2 接受自声明 |
| TDD 技术设计文档 | 先做 Phase 0 | 用实际跑的结果反哺设计 |

---

## 四、结论

方案的核心方向不需要大改。需要补充的是：

1. **StateReport 作为 source of truth** — 明确 Monitor → Scheduler 的数据流
2. **三级降级模型** — L0 Runtime 忙 → L1 Runtime 挂 → L2 Client 崩溃 → L3 NATS 断连
3. **最小可行安全** — 白名单 + 限流 + 群聊准入
4. **版本兼容** — protocol_version 握手降级
5. **delivery 模式补全** — fire-and-forget + max_concurrency 控制

我的建议：**先不做 TDD，先做 Phase 0。** 把 Scheduler + Queue 在 V3 上跑通，用实际运行结果验证这些工程化假设。现在纸上谈兵 offline 降级策略不如让 Scheduler 跑一天看看真实场景会出什么问题。
