# AIM NATS 协议规范

> 版本：v1.2
> 日期：2026-06-17
> 状态：已确认（Phase 1 aim-client 标准入口）
> 上一版本：v1.1 (2026-06-09) — 新增 Registry + GroupAdmission subjects

---

## 一、Subject 命名规范

### 1.1 命名规则

```
{层级1}.{层级2}.{层级3}.{操作}
```

- 使用 `.` 作为分隔符
- 全小写
- 使用下划线 `_` 连接多词

### 1.2 完整 Subject 树（Veritas 标准）

```
# 私聊消息
aim.dm.<agent_id>                 # 私聊消息（收件箱式）

# 群组消息
aim.grp.<group_id>                # 群聊消息

# 请求-响应（不进入 JetStream 管理范围）
aim.req.<agent_id>                # 请求（需要响应）

# Observer 事件
aim.obs.<agent_id>                # Agent 级事件
aim.obs.>                         # 所有 Observer 事件（通配订阅）

# 系统
aim.sys.heartbeat                 # 心跳
aim.sys.status                    # 状态查询
aim.sys.health                    # 健康检查

# 注册（request-reply 模式）
aim.reg.register                  # 注册请求

# Registry 服务 (Phase 1 新增 — NATS request-reply)
aim.registry.register             # Agent 自动注册
aim.registry.lookup               # 查询 Agent 信息
aim.registry.list                 # 列表所有 Agent
aim.registry.heartbeat            # 心跳上报

# 群聊准入 (Phase 1 新增 — NATS request-reply)
aim.groups.create                 # 创建群组
aim.groups.join                   # 申请加入群组
aim.groups.approve                # 群主审批 (approve/reject)
aim.groups.members                # 查询成员列表
aim.groups.list                   # 列表所有群组
```

> **命名规则**：所有 Subject 以 `aim.` 为前缀，二级命名空间按功能划分（dm/grp/req/obs/sys/reg/registry/groups）。

---

## 二、消息格式规范

### 2.1 基础消息格式

```json
{
  "msg_id": "uuid",
  "from": "ZS0001",
  "to": "ZS0002",
  "type": "dm",
  "content": "消息内容",
  "timestamp": "2026-06-08T23:00:00+08:00",
  "metadata": {
    "reply_to": null,
    "priority": 0,
    "ttl": 3600
  }
}
```

### 2.2 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| msg_id | string | 是 | 消息唯一标识（UUID） |
| from | string | 是 | 发送方 Agent ID |
| to | string | 是 | 接收方 Agent ID 或群组 ID |
| type | string | 是 | 消息类型：dm/group/request/response |
| content | string | 是 | 消息内容 |
| timestamp | string | 是 | ISO 8601 格式时间戳 |
| metadata | object | 否 | 扩展元数据 |

### 2.3 Metadata 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| reply_to | string | 回复的消息 ID |
| priority | int | 优先级（0-9，默认 0） |
| ttl | int | 消息存活时间（秒） |

---

## 三、Stream 配置

### 3.1 AIM_MESSAGES Stream

```python
await js.add_stream(
    name="AIM_MESSAGES",
    subjects=[
        "aim.dm.>",
        "aim.req.>",
        "aim.obs.>",
        "aim.grp.>"
    ],
    storage="file",
    retention="limits",
    max_age=7 * 24 * 3600 * 1_000_000_000,  # 7 天
    max_msgs=100000,
    max_bytes=1_073_741_824,  # 1GB
    duplicate_window=120,  # 120 秒去重窗口
)
```

### 3.2 Consumer 配置

```python
await js.add_consumer(
    "AIM_MESSAGES",
    durable_name="agent-ZS0001",
    filter_subjects=[
        "aim.dm.ZS0001",
        "aim.req.ZS0001",
        "aim.grp.grp_trio"
    ],
    ack_policy="explicit",
    deliver_policy="all",
    max_deliver=5,
    ack_wait=30_000_000_000,  # 30 秒
    replay_policy="instant"
)
```

---

## 四、功能实现规范

### 4.1 Agent 注册

**Subject**: `aim.reg.register`（request-reply 模式，注册请求）

**请求格式**:
```json
{
  "cmd": "register",
  "agent_name": "ZS0001",
  "framework": "openclaw",
  "operator_id": "OP0001"
}
```

**响应格式**:
```json
{
  "status": "ok",
  "agent_id": "ZS0001",
  "secret": "***"
}
```

### 4.2 Agent 认证

**Subject**: `aim.req.<agent_id>`（认证走 request-response）

**请求格式**:
```json
{
  "agent_id": "ZS0001",
  "timestamp": 1780930000,
  "signature": "hmac_signature"
}
```

**响应格式**:
```json
{
  "status": "ok",
  "token": "***"
}
```

### 4.3 私聊消息

**Subject**: `aim.dm.<agent_id>`

**消息格式**:
```json
{
  "msg_id": "uuid",
  "from": "ZS0001",
  "to": "ZS0002",
  "type": "dm",
  "content": "你好！",
  "timestamp": "2026-06-08T23:00:00+08:00"
}
```

### 4.4 群聊消息

**Subject**: `aim.grp.<group_id>`

**消息格式**:
```json
{
  "msg_id": "uuid",
  "from": "ZS0001",
  "group": "grp_trio",
  "type": "group",
  "content": "大家好！",
  "timestamp": "2026-06-08T23:00:00+08:00"
}
```

### 4.5 Observer 事件

**Subject**: `aim.obs.<agent_id>`

**事件类型**:
- `auth` - 认证事件
- `message` - 消息事件
- `status` - 状态事件
- `retry` - 重传事件
- `error` - 错误事件

**事件格式**:
```json
{
  "type": "message",
  "agent_id": "ZS0001",
  "detail": "发送消息给 ZS0002",
  "ts": 1780930000
}
```

---

## 五、aim-client 标准入口 (Phase 1)

> **Phase 1 起**，AIM 三方 Agent 统一使用 `aim-client` 作为标准通信入口，取代 nats-agent-v3。

### 5.1 启动
```bash
# 标准模式（Agent 通信）
aim-client/main.py --agent-id ZS0001 --config ~/.aim/agents/ZS0001/config.json --mode direct

# 兼容模式（V3 降级）
aim-client/main.py --agent-id ZS0001 --config ... --mode legacy

# 服务模式（Registry + GroupAdmission，中心节点运行）
aim-client/main.py --agent-id ZS0001 --config ... --services
```

### 5.2 架构
```
aim-client
  ├── Transport (NATS → SDK AIMNATSClient)
  ├── Identity (AgentCard)
  ├── Security (白名单 + 限流 + 认证链)
  ├── Queue+Scheduler+HealthProbe
  └── Adapter (call adapter.sh)
```

## 六、客户端实现（历史参考）

### 6.1 连接配置

```python
import nats

nc = await nats.connect(
    "nats://127.0.0.1:4222",
    max_reconnect_attempts=-1,
    reconnect_time_wait=2,
    ping_interval=10,
    max_outstanding_pings=3
)
```

### 6.2 消息发送

```python
# 私聊
await nc.publish("aim.dm.ZS0002", json.dumps(msg).encode())

# 群聊
await nc.publish("aim.grp.grp_trio", json.dumps(msg).encode())

# 请求-响应
response = await nc.request("aim.req.ZS0002", json.dumps(msg).encode(), timeout=5)
```

### 6.3 消息接收

```python
# 订阅私聊
await nc.subscribe("aim.dm.ZS0001", cb=on_private_msg)

# 订阅群聊
await nc.subscribe("aim.grp.grp_trio", cb=on_group_msg)

# 订阅 Observer
await nc.subscribe("aim.obs.>", cb=on_observer_event)
```

---

## 七、错误处理

### 7.1 连接错误

```python
try:
    nc = await nats.connect("nats://127.0.0.1:4222")
except Exception as e:
    print(f"连接失败: {e}")
```

### 7.2 消息发送错误

```python
try:
    await nc.publish("aim.dm.ZS0002", json.dumps(msg).encode())
except Exception as e:
    print(f"发送失败: {e}")
```

### 7.3 JetStream 错误

```python
try:
    ack = await js.publish("aim.dm.ZS0002", json.dumps(msg).encode())
except nats.js.errors.NoStreamResponseError:
    print("Stream 不存在")
except Exception as e:
    print(f"JetStream 错误: {e}")
```

---

## 八、监控与日志

### 8.1 连接状态

```python
# 检查连接状态
if nc.is_connected:
    print("已连接")
elif nc.is_reconnecting:
    print("重连中")
elif nc.is_closed:
    print("已关闭")
```

### 8.2 消息统计

```python
# 发送统计
print(f"发送消息数: {nc.stats['out_msgs']}")
print(f"发送字节数: {nc.stats['out_bytes']}")

# 接收统计
print(f"接收消息数: {nc.stats['in_msgs']}")
print(f"接收字节数: {nc.stats['in_bytes']}")
```

---

**文档结束**

版本：v1.1
日期：2026-06-09
