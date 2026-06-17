# AIMP 连接池重构技术方案

> 版本: v1.0 | 状态: 待评审 | 作者: 吉量 🐴
> 相关: node.py (`_server_clients`/`_peer_conns`) → 连接池系统

---

## 1. 动机与目标

### 当前痛点

| 问题 | 现状 | 影响 |
|------|------|------|
| **单连接** | agent 连入 Hub 只开放 1 个 WS 连接，后一个顶掉前一个（"踢旧连接"） | 多设备同 ID 无法共存 |
| **连接不可区分** | `_server_clients: Dict[str, ws]` 是 agent_id → 单 WS 映射 | 丢失来源信息（渠道/设备/用途） |
| **无角色分层** | 所有连接无差别对待，断连即全断 | 心跳连接和业务处理耦合 |
| **无优雅降级** | 连接断开后没有 handler 提升机制 | 业务中断直到重连 |

### 目标

1. **多连接共存** — 同 agent_id 可通过不同渠道/设备同时在线
2. **连接角色化** — 每个连接有明确的 channel 归属和角色
3. **handler 机制** — 同一 agent 的多个连接中，只有一个负责 AI 处理
4. **优雅降级** — handler 断连后自动提升新 handler
5. **向后兼容** — 现有单连接客户端零改动

---

## 2. 数据结构：map-of-list

核心设计：`agent_id → {channel: [ConnectionInfo, ...]}`

```python
@dataclass
class ConnectionInfo:
    """连接池中的单个连接"""
    agent_id: str
    channel: str          # 渠道标识: "main" / "script" / "health" / "mobile" / "legacy"
    ws: WebSocket         # 底层 WS 连接
    is_handler: bool      # 是否当前 handler
    connected_at: float   # 连接建立时间
    last_ping: float      # 最后心跳时间
    role: str             # "primary" | "secondary"（同 channel 内的优先级）
    metadata: dict        # 额外信息（客户端版本、设备类型等）

class ConnectionPool:
    """连接池 — map-of-list 结构"""
    _connections: dict[str, dict[str, list[ConnectionInfo]]]
    #   ^ agent_id   ^ channel   ^ 该 channel 下的连接列表
```

### 2.1 三层索引

```
第一层: agent_id            → "ZS0002"
第二层: channel             → "main" / "script" / "health" / "mobile"
第三层: 连接列表             → [ConnectionInfo, ConnectionInfo, ...]

示例 ─ ZS0002 有 2 个连接：
  {
    "ZS0002": {
      "main": [
        {ws: ws_a, is_handler: True, connected_at: t1}    # main-主 → handler
      ],
      "health": [
        {ws: ws_b, is_handler: False, connected_at: t2}   # health 连接
      ]
    }
  }
```

### 2.2 Channel 白名单

预定义白名单制，非白名单 channel 统一归为 `"custom"`：

| channel | 用途 | 说明 |
|---------|------|------|
| `main` | 主要 AI 客户端 | AI 框架连接（Hermes/OpenClaw），**handler 优先在此 channel 选举** |
| `script` | 脚本工具 | `hub-send.py`、watchdog 等自动化工具 |
| `health` | 健康检查 | 心跳/监控/晨检连接 |
| `web` | Web 管理面板 | Web 端连接 |
| `mobile` | 移动端 | 手机 App |
| `legacy` | 旧版兼容 | 不带 channel 的旧客户端自动归入 |

---

## 3. Handler 选举规则

### 3.1 定义

Handler = 负责 AI 处理消息的连接，同一 agent 同一时间至多 1 个 handler。

### 3.2 选举优先级

```
1. channel 优先级:   main > web > mobile > script/health/legacy
2. 同 channel 内:    primary > secondary (通过 role 字段区分)
3. 同优先级下:       最早建立的连接
```

### 3.3 触发时机

| 事件 | 动作 |
|------|------|
| 新连接认证成功 | 检查是否需要重新选举 handler |
| handler 断开 | 立即触发选举，提升备选连接 |
| 降级/提升指令 | 客户端可主动请求成为/放弃 handler |

### 3.4 选举算法

```python
def elect_handler(agent_id: str) -> Optional[ConnectionInfo]:
    """按优先级选出当前 agent 的最佳 handler"""
    pool = self._connections.get(agent_id, {})
    candidates = []
    for channel, conns in pool.items():
        for conn in conns:
            priority = HANDLER_CHANNEL_PRIORITY.get(channel, 99)
            candidates.append((priority, conn.role == "primary", -conn.connected_at, conn))
    if not candidates:
        return None
    # 按 (channel优先级, 是否primary, 连接时间早) 排序
    candidates.sort(key=lambda x: (x[0], not x[1], x[2]))
    return candidates[0][3]
```

**channel 优先级映射：**
```python
HANDLER_CHANNEL_PRIORITY = {
    "main": 0,    # 最高 — AI 主框架
    "web": 1,     # Web 管理端
    "mobile": 2,  # 移动端
    "custom": 3,  # 自定义
    "script": 4,  # 脚本工具
    "health": 5,  # 健康检查
    "legacy": 9,  # 旧版兼容
}
```

---

## 4. 断连重连窗口 (30s Grace Period)

### 4.1 设计

不采用"立即清理"策略；断连后进入 30s **优雅窗口期**：

```
事件: WebSocket 断开
  │
  ├─ 立即: 将对应 ConnectionInfo 标记为 "disconnecting"
  ├─ 立即: 触发 handler 选举（提升备选）
  ├─ 立即: 重新选举的 handler 收到 "promote_to_handler" 通知
  │
  └─ 30s 内:
       ├─ 同 agent_id + 同 channel 的新连接到来
       │   └─ 替换 "disconnecting" 标记的连接
       │   └─ 如果是原 handler → 恢复 handler 角色
       │   └─ 如果是非 handler → 保持现有 handler 不变
       │
       └─ 30s 后:
           └─ 清理所有 "disconnecting" 标记的连接
           └─ 如该 agent 在该 channel 下无活跃连接 → 清理 channel 空映射
```

### 4.2 实现要点

```python
# 断连处理
async def _on_disconnect(self, conn: ConnectionInfo):
    conn.mark_disconnecting()  # 标记为"即将断开"，不清除连接
    new_handler = self._elect_handler(conn.agent_id)
    if new_handler and new_handler != conn:
        await self._notify_promote(new_handler)
    # 启动 30s 清理协程（可取消）
    asyncio.create_task(self._grace_cleanup(conn, delay=30))

# 30s 优雅窗口
async def _grace_cleanup(self, conn: ConnectionInfo, delay: int):
    await asyncio.sleep(delay)
    if conn.is_disconnecting() and not conn.is_replaced():
        self._remove_connection(conn)
```

### 4.3 多断连场景

```
正常断连（单连接断开）:
  [WS断开] → [标记 disconnecting] → [handler 选举] → [30s]
                                                       ├─ 重连成功 → 恢复
                                                       └─ 超时 → 清理

全断场景（所有连接断开）:
  [所有 WS 断开] → [标记全部 disconnecting] → [agent 标记"离线"]
  → [30s 后] → [清理所有 disconnecting] → [确认离线]

全断后重连（30s 内）:
  [新 WS 接入] → [选举 handler] → [清理其他 disconnecting]
  → [agent 恢复在线]
```

---

## 5. 消息路由差异化

### 5.1 消息类型 → 投递规则

| 消息类型 | 投递目标 | 说明 |
|---------|----------|------|
| `chat_message` | 仅 handler | 用户/AI 对话消息，只需 AI 处理 |
| `status_update` | 所有连接 | 状态变更通知，所有客户端应同步 |
| `system_event` | 所有连接 | 系统级事件（升级、重启等） |
| `ack` | 发送方连接 | 回执，仅回给发送方 |
| `presence` | 所有连接 | 上下线通知 |

### 5.2 路由实现

```python
def _get_delivery_targets(self, agent_id: str, msg_type: str) -> list[WebSocket]:
    """根据消息类型获取投递目标"""
    if msg_type == "ack":
        # ack 由调用方指定连接
        return [specific_ws]
    
    if msg_type in ("chat_message", "handler_only"):
        # 仅投递到 handler
        handler = self._get_handler(agent_id)
        return [handler.ws] if handler else []
    
    # status_update / system_event / presence → 广播所有连接
    targets = []
    for channel_conns in self._connections.get(agent_id, {}).values():
        for conn in channel_conns:
            if not conn.is_disconnecting():
                targets.append(conn.ws)
    return targets
```

---

## 6. 消息去重增强

### 6.1 现有去重

当前 `_seen_msgs`（Set）和 `_sent_ids`（Set）已有基本去重。新增连接级去重：

### 6.2 Handler 端去重

```
消息流入:
  [WS 接收到消息] → msg_id hash()
  ├─ 命中 100 条短缓存 (5min TTL) → 丢弃
  └─ 未命中 → 处理 + 写入缓存

缓存结构:
  msg_dedup_cache: dict[str, float]  # msg_id → timestamp
  max_size: 100
  ttl: 300s
```

### 6.3 去重层级

| 层级 | 范围 | 数据位置 | TTL |
|------|------|---------|-----|
| L1 接收端去重 | 全 Hub | `_sent_ids: Set` (node.py) | 当前（5000 上限） |
| L2 连接端去重 | 单 handler | `msg_dedup_cache: dict` (agent) | 5min |
| L3 接入去重 | 全局 | 内容指纹 `_fingerprints` | 60s |

---

## 7. API 协议变更

### 7.1 认证消息 (新增 channel 字段)

客户端认证时携带 channel 字段：

```json
// 请求
{
  "cmd": "auth",
  "agent_id": "ZS0002",
  "token": "xxx",
  "channel": "main",       // ← 新增，默认 "legacy"
  "role": "primary",       // ← 新增，默认 "primary"
  "client_version": "2.0"  // ← 新增，信息用途
}

// 响应（扩展）
{
  "cmd": "auth_ok",
  "agent": {...},
  "groups": [...],
  "unread": [...],
  "is_handler": true,                    // ← 新增：当前连接是否为 handler
  "handler_info": {                      // ← 新增：当前 handler 信息
    "agent_id": "ZS0002",
    "channel": "main",
    "connected_at": 1234567890.0
  },
  "pool_info": {                         // ← 新增：连接池摘要
    "total_connections": 2,
    "channels": ["main", "health"]
  }
}
```

### 7.2 新增指令

| 指令 | 方向 | 说明 |
|------|------|------|
| `promote_to_handler` | 服务端→客户端 | 通知该连接成为 handler |
| `demote_from_handler` | 服务端→客户端 | 通知该连接不再是 handler |
| `pool_status` | 双向 | 查询/报告连接池状态 |
| `move_channel` | 客户端→服务端 | 请求迁移到其他 channel |

示例：

```json
// 服务端 → 客户端：提升为 handler
{
  "cmd": "promote_to_handler",
  "reason": "previous_handler_disconnected",
  "previous_handler": "ZS0002:main",
  "pool_summary": {
    "total_connections": 2,
    "handlers": {"ZS0002": "main"}
  }
}

// 客户端 → 服务端：查询连接池状态
{
  "cmd": "pool_status",
  "agent_id": "ZS0002"  // 可选，不填则查自己
}

// 服务端 → 客户端：连接池状态响应
{
  "cmd": "pool_status_result",
  "agent_id": "ZS0002",
  "connections": [
    {"channel": "main", "is_handler": true, "connected_at": 1234567890.0, "role": "primary"},
    {"channel": "health", "is_handler": false, "connected_at": 1234567891.0, "role": "primary"}
  ]
}
```

### 7.3 向后兼容

| 场景 | 策略 |
|------|------|
| 旧客户端不传 channel | server 端自动赋值为 `"legacy"` |
| 旧客户端不传 role | server 端自动赋值为 `"primary"` |
| 旧客户端不处理 `promote_to_handler` | server 端只会将消息路由到 handler，旧客户端收到正常消息，不影响基础功能 |
| 全旧版环境 | 全 `legacy` channel，第一个连接自动成为 handler，第二个连接不替换已有 handler（变化：从"踢旧"改为"共存"） |

---

## 8. 配置变更

### 8.1 config.json 新增

```json
{
  "aim": {
    "connection_pool": {
      "enabled": true,
      "max_connections_per_agent": 5,
      "grace_period": 30,
      "channels": ["main", "script", "health", "web", "mobile"],
      "handler": {
        "auto_election": true,
        "channel_priority": ["main", "web", "mobile", "custom", "script", "health", "legacy"]
      },
      "dedup": {
        "handler_cache_size": 100,
        "handler_cache_ttl": 300
      }
    }
  }
}
```

### 8.2 与现有配置的兼容

现有 config.json 的 `security`、`groups`、`agents`、`cli_paths` 等字段完全不变。连接池配置为新增独立区块，默认 `enabled: true`。

---

## 9. msg_id 生成规范（新增）

新增 `msg_id` 格式规范，便于追踪：

```
格式: ZS<send_seq><channel_code><action_code>
示例: ZS0001M01  → ZS0001, main channel, 第1条动作消息

channel 编码: M=main, S=script, H=health, W=web, L=legacy
action 编码:  01-99 递增
```

> 注意：此规范为可选推荐，现有客户端不强制要求，server 端兼容任意 msg_id 格式。

---

## 10. 日志格式变更

连接池相关日志统一格式：

```
[AIMP] [agent_id:channel] action details
示例:
[AIMP] [ZS0002:main] 新连接接入 (role=primary)
[AIMP] [ZS0002:main] 提升为 handler (previous=ZS0002:health)
[AIMP] [ZS0002:health] 断开连接 (grace_period=30s)
[AIMP] [ZS0001:legacy] 降级为非 handler
```

---

## 11. 数据流图

```
                      ┌──────────────────┐
                      │    AIM Hub       │
                      │  (node.py)       │
                      │                  │
                      │  ConnectionPool  │
                      │  ┌──────────────┐│
                      │  │ ZS0002:      ││
                      │  │  main: [ws_a]││  ← handler ★
                      │  │  health:[ws_b]││
                      │  │ ZS0001:      ││
                      │  │  main: [ws_c]││  ← handler ★
                      │  │  script:[ws_d]││
                      │  │              ││
                      │  │  elect()     ││
                      │  │  route()     ││
                      │  │  cleanup()   ││
                      │  └──────────────┘│
                      └──────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   ZS0002:main            ZS0002:health        ZS0001:main
   (AI Client)           (Watchdog)            (AI Client)
   is_handler=true        is_handler=false      is_handler=true
```

### 消息投递流程

```
发送方 → Hub → route(msg_type) → 
  ├─ chat_message  → 仅 handler
  ├─ status_update → 所有连接
  ├─ system_event  → 所有连接
  ├─ presence      → 所有连接
  └─ ack           → 仅发送方连接
```

### 连接生命周期

```
[新 WS 接入]
  → auth(channel=main, role=primary)
  → ConnectionPool.register(agent_id, channel, ws)
  → elect_handler(ZS0002) → main-channel 成为 handler
  → auth_ok(is_handler=true, handler_info={...})
  → [正常运行 | 每条消息走 route()]

[WS 断开 - handler]
  → _on_disconnect(handler_conn)
  → 标记 disconnecting
  → elect_handler(ZS0002) → 提升备选
  → 通知新 handler (promote_to_handler)
  → 30s 优雅窗口
    ├─ 原连接重连 → 恢复（如果同 channel 则恢复 handler）
    └─ 超时 → 清理

[WS 断开 - 非 handler]
  → _on_disconnect(conn)
  → 标记 disconnecting
  → 不需要选举（handler 不变）
  → 30s 优雅窗口
    ├─ 重连成功 → 恢复
    └─ 超时 → 清理
```

---

## 12. 迁移计划

### Phase 1: 数据模型 + 连接池类（纯新增，不修改现有逻辑）

1. 新建 `connection_pool.py`，包含 `ConnectionInfo` / `ConnectionPool`
2. 实现 `register()` / `unregister()` / `elect_handler()` / `get_delivery_targets()`
3. 单元测试覆盖

### Phase 2: 集成到 node.py（逐步替换）

1. 在 `AIMNode.__init__` 中初始化 `ConnectionPool`
2. 修改 `_handle_server_client` 认证逻辑：接收 channel/role 参数，注册到连接池
3. 修改 `_broadcast` → `ConnectionPool._get_delivery_targets()`
4. 处理断连：`finally` 块不再直接 `del self._server_clients[agent_id]`，改为连接池处理

### Phase 3: handler 机制 + 优雅降级

1. 实现 `elect_handler()` 在断连后自动触发
2. 实现 `promote_to_handler` / `demote_from_handler` 指令
3. 实现 30s grace period
4. 集成测试：handler 切换、多连接共存

### Phase 4: 旧客户端兼容性

1. 旧客户端无 channel → `"legacy"`
2. 旧客户端单连接 → 自动成为 handler（如同行为）
3. 回归测试

---

## 13. 测试用例

呱呱要求的测试场景：

| 测试编号 | 场景 | 预期 |
|---------|------|------|
| T01 | ZS0002:main + ZS0002:health 同时在线 | 两个连接共存，main 为 handler |
| T02 | handler (main) 断连 → 自动提升 health | health 收到 promote_to_handler |
| T03 | 30s 内 health 重连 | health 恢复为 handler |
| T04 | 30s 后 health 未重连 → 清理 | health 连接从池中移除 |
| T05 | 旧客户端无 channel 参数 | 自动归入 legacy channel |
| T06 | 旧客户端认证后，新客户端同 ID 接入 | 不踢旧连接，两个共存 |
| T07 | chat_message 发送给多连接 agent | 仅 handler 收到 |
| T08 | status_update 发送给多连接 agent | 所有连接都收到 |
| T09 | 同一 channel 下 2 个连接 | primary 为 handler，secondary 为备选 |
| T10 | 全断后 30s 内重连 | 恢复在线状态，handler 选举正常 |

---

## 14. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| **全断后 30s 窗口期内消息丢失** | handler 断开立即选举备选，30s 窗口主要是给重连用，消息不丢失 |
| **旧客户端 break** | 所有旧客户端自动归入 legacy channel，无行为差异 |
| **handler 选举竞态** | 选举操作加锁，保证原子性 |
| **连接过多** | `max_connections_per_agent: 5` 硬限制 |
| **channel 拼写错误** | 白名单制，非白名单自动归入 custom |
| **消息重复投递** | 三层去重机制覆盖 |
| **内存泄漏（断连连接未清理）** | 30s 定时清理 + 主动心跳检测 |

---

## 15. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `connection_pool.py` | **新增** | 连接池核心逻辑 |
| `node.py` | 修改 | 集成连接池，修改认证/投递/断连 |
| `aim-agent.py` | 修改 | 支持 channel 参数，处理 promote/demote |
| `config.json` | 修改 | 新增 connection_pool 配置段 |
| `docs/aimp-connection-pool-redesign.md` | 新增 | 本文档 |

---

*— 方案完，等待呱呱 🐸 review 后进入代码实现阶段。*
