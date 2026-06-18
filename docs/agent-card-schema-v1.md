# Agent Card Schema v1

> Agent Card 是 AIM Agent 的**数字身份证**，Registry 注册时写入 KV，其他 Agent 通过 Discovery 读取。
> 参考: `规划/aim-client-unified-v1.md §5.7`

---

## 一、Schema 定义

```json
{
  "global_id": "uuid:a1b2c3d4-e5f6-...",        // UUID v4，永久不变
  "serial": "ZS0003",                             // 注册序号，不可变
  "name": "小火鸡儿",                              // 昵称，可改

  "client": {
    "type": "aim-client",
    "version": "1.0.0"
  },

  "runtime": {
    "provider": "letta",
    "version": "0.27.9"
  },

  "network": {
    "endpoint": "nats://127.0.0.1:4222",
    "alt_endpoints": ["http://127.0.0.1:27391/aim"],
    "reachable_from": ["local"],
    "requires_relay": false,
    "preferred_transport": "nats"
  },

  "delivery": {
    "mode": "deferred",
    "expects_reply": true,
    "max_concurrency": 1,
    "queue_capacity": 1000
  },

  "execution_model": "deferred",

  "lifecycle": "AVAILABLE",

  "protocol_version": "1.0",
  "min_protocol_version": "0.8",

  "capabilities": [
    {
      "name": "chat",
      "version": "1.0",
      "level": "native"
    }
  ],

  "trust": {
    "citizenship": "L2",
    "reputation": 0.0,
    "completed_tasks": 0,
    "success_rate": 0.0,
    "endorsements": 0
  },

  "wallet": {
    "address": "",
    "balance": 0,
    "stake": 0
  }
}
```

---

## 二、字段说明

### 2.1 身份字段

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `global_id` | ✅ | `uuid:` 前缀字符串 | 全局唯一 ID，Agent 安装时生成，永久不变 |
| `serial` | ✅ | `ZS` 开头 6 位 | Registry 分配的序号，注册后不可变 |
| `name` | ✅ | 字符串 | 人类可读昵称，可随时修改 |

### 2.2 Client & Runtime

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `client.type` | ✅ | 字符串 | 固定为 `aim-client` |
| `client.version` | ✅ | semver | AIM Client 版本号 |
| `runtime.provider` | ✅ | 字符串 | Agent 框架名称（letta / hermes / openclaw） |
| `runtime.version` | ✅ | semver | Runtime 版本号，注册时通过 `adapter.sh info` 自动获取 |

### 2.3 网络

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `network.endpoint` | ✅ | URL | 主连接地址 |
| `network.alt_endpoints` | ❌ | `[]URL` | 备用地址（HTTP 降级等） |
| `network.reachable_from` | ❌ | `[]string` | 可达性标签：`local` / `vpn` / `public` |
| `network.requires_relay` | ❌ | bool | 是否需中继才能通信 |
| `network.preferred_transport` | ✅ | 字符串 | 首选 Transport：`nats` / `http` / `ws` |

### 2.4 投递模式

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `delivery.mode` | ✅ | 枚举 | `realtime` / `deferred` / `fire-and-forget` |
| `delivery.expects_reply` | ❌ | bool | 是否期望回复 |
| `delivery.max_concurrency` | ❌ | int | 最大并行处理数 |
| `delivery.queue_capacity` | ❌ | int | 队列容量上限 |

### 2.5 执行模型 & 生命周期

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `execution_model` | ✅ | 枚举 | `realtime`：即时处理（Hermes/OpenClaw）<br>`deferred`：单线程排队（Letta）<br>`batch`：定时批处理 |
| `lifecycle` | ✅ | 枚举 | L0-P1 三态：`AVAILABLE` / `BUSY` / `OFFLINE`<br>Schema 预留：`REGISTERED` / `DEGRADED` / `MAINTENANCE` / `RETIRED` |

### 2.6 协议版本

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `protocol_version` | ✅ | semver | 当前使用的协议版本 |
| `min_protocol_version` | ✅ | semver | 最低兼容协议版本 |

### 2.7 Capabilities

`capabilities` 是结构化能力描述数组。P0-P1 只填 `name`，P2 Router 能力路由前必须完整填充。

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `name` | ✅ | 字符串 | 能力名称（chat / code / file / research / ...） |
| `version` | ❌ | semver | 能力版本 |
| `level` | ❌ | 枚举 | `native` / `partial` / `external` |
| `language` | ❌ | `[]string` | 支持的编程语言（code 能力时） |

### 2.8 Trust & Wallet（P2+ 预留）

P0-P1 不实现，Schema 中占位防止后续返工。

---

## 三、Agent Card dataclass（Python SDK 实现）

对应 `aim_client/types.py` 中现有的 `AgentCard` dataclass，需增强到以下字段：

```python
@dataclass
class AgentCard:
    """Agent Card Schema v1 — 完整版"""
    # 身份
    global_id: str = ""
    serial: str = ""
    name: str = ""
    
    # Client & Runtime
    client_type: str = "aim-client"
    client_version: str = ""
    runtime_provider: str = ""
    runtime_version: str = ""
    
    # 网络
    endpoint: str = ""
    alt_endpoints: list[str] = field(default_factory=list)
    reachable_from: list[str] = field(default_factory=lambda: ["local"])
    requires_relay: bool = False
    preferred_transport: str = "nats"
    
    # 投递
    delivery_mode: str = "deferred"
    expects_reply: bool = True
    max_concurrency: int = 1
    queue_capacity: int = 1000
    
    # 执行 & 生命周期
    execution_model: str = "deferred"
    lifecycle: str = "AVAILABLE"
    
    # 协议
    protocol_version: str = "1.0"
    min_protocol_version: str = "0.8"
    
    # 能力
    capabilities: list[dict] = field(default_factory=lambda: [{"name": "chat", "version": "1.0", "level": "native"}])
    
    # 信任 & 钱包（预留）
    trust_citizenship: str = "L2"
    trust_reputation: float = 0.0
    trust_completed_tasks: int = 0
    trust_success_rate: float = 0.0
    trust_endorsements: int = 0
    wallet_address: str = ""
    wallet_balance: int = 0
    wallet_stake: int = 0

    def to_dict(self) -> dict:
        """序列化为 JSON schema 格式"""
        return {
            "global_id": self.global_id,
            "serial": self.serial,
            "name": self.name,
            "client": {"type": self.client_type, "version": self.client_version},
            "runtime": {"provider": self.runtime_provider, "version": self.runtime_version},
            "network": {
                "endpoint": self.endpoint,
                "alt_endpoints": self.alt_endpoints,
                "reachable_from": self.reachable_from,
                "requires_relay": self.requires_relay,
                "preferred_transport": self.preferred_transport,
            },
            "delivery": {
                "mode": self.delivery_mode,
                "expects_reply": self.expects_reply,
                "max_concurrency": self.max_concurrency,
                "queue_capacity": self.queue_capacity,
            },
            "execution_model": self.execution_model,
            "lifecycle": self.lifecycle,
            "protocol_version": self.protocol_version,
            "min_protocol_version": self.min_protocol_version,
            "capabilities": self.capabilities,
            "trust": {
                "citizenship": self.trust_citizenship,
                "reputation": self.trust_reputation,
                "completed_tasks": self.trust_completed_tasks,
                "success_rate": self.trust_success_rate,
                "endorsements": self.trust_endorsements,
            },
            "wallet": {
                "address": self.wallet_address,
                "balance": self.wallet_balance,
                "stake": self.wallet_stake,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCard":
        """从 JSON dict 反序列化"""
        net = data.get("network", {})
        deli = data.get("delivery", {})
        client = data.get("client", {})
        runtime = data.get("runtime", {})
        trust = data.get("trust", {})
        wallet = data.get("wallet", {})
        caps = data.get("capabilities", [{"name": "chat", "version": "1.0", "level": "native"}])
        return cls(
            global_id=data.get("global_id", ""),
            serial=data.get("serial", ""),
            name=data.get("name", ""),
            client_type=client.get("type", "aim-client"),
            client_version=client.get("version", ""),
            runtime_provider=runtime.get("provider", ""),
            runtime_version=runtime.get("version", ""),
            endpoint=net.get("endpoint", ""),
            alt_endpoints=net.get("alt_endpoints", []),
            reachable_from=net.get("reachable_from", ["local"]),
            requires_relay=net.get("requires_relay", False),
            preferred_transport=net.get("preferred_transport", "nats"),
            delivery_mode=deli.get("mode", "deferred"),
            expects_reply=deli.get("expects_reply", True),
            max_concurrency=deli.get("max_concurrency", 1),
            queue_capacity=deli.get("queue_capacity", 1000),
            execution_model=data.get("execution_model", "deferred"),
            lifecycle=data.get("lifecycle", "AVAILABLE"),
            protocol_version=data.get("protocol_version", "1.0"),
            min_protocol_version=data.get("min_protocol_version", "0.8"),
            capabilities=caps,
            trust_citizenship=trust.get("citizenship", "L2"),
            trust_reputation=trust.get("reputation", 0.0),
            trust_completed_tasks=trust.get("completed_tasks", 0),
            trust_success_rate=trust.get("success_rate", 0.0),
            trust_endorsements=trust.get("endorsements", 0),
            wallet_address=wallet.get("address", ""),
            wallet_balance=wallet.get("balance", 0),
            wallet_stake=wallet.get("stake", 0),
        )
```

---

## 四、用法

```python
# 创建
card = AgentCard(
    serial="ZS0002",
    name="吉量",
    runtime_provider="hermes",
    runtime_version="0.1.0",
    endpoint="nats://127.0.0.1:4222",
    execution_model="deferred",
    lifecycle="AVAILABLE",
)

# 序列化（保存到 NATS KV 或本地 JSON）
card_dict = card.to_dict()
json.dump(card_dict, open("agent-card.json", "w"))

# 反序列化（从 KV 读取）
card = AgentCard.from_dict(json.load(open("agent-card.json")))
```

---

## 五、变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1 | 2026-06-17 | 初版，对应 unified-v1.md §5.7 |
