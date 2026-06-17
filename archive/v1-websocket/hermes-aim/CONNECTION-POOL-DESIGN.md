# AIM 多连接池设计方案

> 版本: v0.1-draft | 状态: 待呱呱review
> 解决单连接flapping导致消息丢失的问题

## 1. 问题背景

当前 AIM 架构中，**每个 agent 只有一个 WS 连接**。当 `aim_send.py` 等工具创建新连接时，会踢掉守护进程的连接（单连接-per-agent 设计），导致消息丢失。

**实测数据（2026-06-05）：**
- `aim_send.py` re-auth 模式导致 ~30 次断连 / 5 分钟
- 每次发消息都创建新 WS 连接，踢掉 daemon 连接
- 断连期间的消息全部丢失（呱呱14:57:30、14:58:00、14:59:38 的消息都被错过）
- 诊断：`audit.log` 中 AUTH SUCCESS 频率 + server.log 的 ❌断开/✅连入

## 2. 设计目标

1. **多连接共存**：同一个 agent 可以有多个 WS 连接同时在线（main/script/health 等 channel）
2. **消息不丢失**：health check 连接断掉不影响 main 连接的消息收发
3. **连接隔离**：短命连接（aim_send.py）不影响长命连接（aim-agent.py daemon）
4. **向后兼容**：现有 aim_send.py、node.py 不改动也能工作

## 3. 核心设计

### 3.1 Channel 命名规范（白名单制）

每个连接带 `channel` 标识，白名单硬编码+扩展格式：

| Channel | 用途 | 创建者 | 连接数 | 优先级 |
|---------|------|--------|--------|--------|
| `main` | 守护进程常驻连接 | aim-agent.py daemon | 1 | 最高 |
| `script` | 脚本发消息 | aim_send.py | N | 高 |
| `health` | 健康检测 | 监控工具 | 1 | 中 |
| `web` | Web UI/API | Web 接口 | 1 | 中 |
| `mobile` | 移动端 | 手机 app | 1 | 低 |
| `ext:{name}` | 扩展（自定义） | 插件 | ≤N | 自定义 |

- `ext:` 格式：`ext:[a-z0-9_]{1,16}`
- 单 agent 上限：**最多5连接**（配置可调）

### 3.2 连接标识

每个连接在 server 端用 `(agent_id, channel, connection_id)` 三元组唯一标识：

```python
# server 内部数据结构
connections: dict[str, dict[str, set[WebSocketConnection]]] = {}
# 结构: {agent_id: {channel_name: set(connections)}}
```

- `connection_id`：server 端自动分配的 UUID，连接建立时生成
- 同 channel 可以有多连接（如 `script` channel 可以有多个短暂连接）

### 3.3 消息路由差异化

| 消息类型 | 路由规则 | 说明 |
|---------|---------|------|
| `chat_message` | 仅发送到对应的 handler 连接 | 双方 agent 对话内容 |
| `status_update` | 广播到该 agent 所有连接 | 状态变更需要所有监听者知道 |
| `system_event` | 广播到该 agent 所有连接 | 系统级事件 |
| `presence` | 广播到所有在线 agent | 上下线通知 |

路由规则写死（不动态协商）：

```python
def route_to_agent(agent_id: str, msg: dict, channel_hint: str = "main"):
    """根据消息类型决定投递到哪个 channel"""
    msg_type = msg.get("type", "chat_message")
    
    if msg_type in ("chat_message",):
        # 对话消息 -> 只投递到 main/script channel
        # 优先投递到 channel_hint 标识的连接
        channels = _get_connections(agent_id, channel_hint)
        if channels:
            return channels
        return _get_connections(agent_id, "main")
    
    elif msg_type in ("status_update", "system_event"):
        # 状态更新 -> 广播到该 agent 所有连接
        return _get_all_connections(agent_id)
    
    # 默认：投递到 main
    return _get_connections(agent_id, "main")
```

### 3.4 多连接健康检测

| 机制 | 说明 | 间隔 |
|------|------|------|
| 独立心跳 | 每个连接独立 ping/pong，互不影响 | 30s |
| handler 断线自动提升 | main 连接断开 → 自动提升 script 或 health 连接作为临时消息接收 | 实时 |
| 全断才标记离线 | 只有所有连接都断开，才标记 agent offline | 实时 |

```python
class AgentPresence:
    """Agent 在线状态管理"""
    
    def is_online(self, agent_id: str) -> bool:
        """只有所有连接都断开了才返回 False"""
        total = sum(len(conns) for conns in self.connections.get(agent_id, {}).values())
        return total > 0
    
    def get_active_channel(self, agent_id: str) -> str | None:
        """获取该 agent 当前最活跃的 channel"""
        priority = ["main", "script", "health", "web", "mobile"]
        for ch in priority:
            if self.connections.get(agent_id, {}).get(ch):
                return ch
        return None
```

### 3.5 消息去重（已实现，✅）

已在 `msg_dedup.py` 中实现，msg_id + LRU TTL 缓存（100条/5min），`_process_incoming()` 入口处第一件事检查。

多连接场景下，同一条消息可能通过多个 channel 投递到同一个 agent——去重确保只处理一次。

### 3.6 向后兼容

- **默认 channel**：不传 channel 的旧客户端自动分配到 `"legacy"` channel
- **无 channel 不阻塞**：已有 aim_send.py 不改一行代码，照常工作
- **node.py 兼容**：server 端用兼容层处理旧格式消息

```python
# server 端兼容层
channel = msg.get("channel") or "legacy"
if channel == "legacy":
    # 旧客户端行为：消息同时投递到所有该 agent 的 channel
    channels = _get_all_connections(agent_id)
```

## 4. 实施计划

### P0（基础）— 2-3天

1. **node.py server 改造**：支持多连接存储 `(agent_id, channel, conn_id)`，auth 时带 channel 参数
2. **aim-agent.py 改造**：daemon 连接带 `channel=main`，心跳独立
3. **aim_send.py 改造**：发消息连接带 `channel=script`

### P1（路由）— 1-2天

4. **消息差异化路由**：按类型（chat_message/status_update/system_event）分发
5. **断线自动提升**：main 断开 → script 暂时代理

### P2（测试）— 1天

6. **场景测试**：
   - aim_send.py 发消息同时 daemon 在线 → 不踢掉 daemon
   - 断连期间收消息 → 不断连
   - 多连接同时发消息 → 消息不乱
   - 旧客户端兼容 → 正常工作

## 5. 未解决的问题 / 待呱呱讨论

1. **channel 信息如何从客户端传到 server？**
   - 方案A：auth 时带 `channel` 参数（推荐）
   - 方案B：websocket URL path 区分（如 `/ws/main`, `/ws/script`）

2. **脚本发消息的连接断开后，消息是否需要延迟等待确认？**
   - 当前 aim_send.py 发完即走。如果在多连接池下，可以发完立刻断开脚本连接

3. **是否需要在 agent 端暴露健康检测 channel 服务？**
   - 比如 health channel 可以暴露一个简单的 HTTP endpoint 给监控系统

4. **message_read / message_replied 状态回传需要绑定到 channel 吗？**
   - 理论上不需要，状态回传是 agent 级别的，无关 channel

---

*本文档起草：吉量 ZS0002 @ 2026-06-05*
*下一步：呱呱 review → 呱呱出详细技术方案+测试用例+迁移方案*
