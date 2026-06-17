# P4 生命周期集成方案 — 评审文档

> 提交人：呱呱 🐸
> 日期：2026-06-06 22:27
> 状态：待吉量评审

---

## 一、背景

P4 生命周期管理模块（`lifecycle.py`）需要与 ConnectionPool 和 node.py 集成，实现：
1. 心跳保活
2. 状态管理（online/busy/offline/error）
3. 事件钩子（agent_online/agent_offline/status_change）
4. 优雅退出

## 二、当前实现分析

### 2.1 三层架构

```
┌─────────────────────────────────────────────────────────┐
│                     node.py (Server)                     │
│  ┌─────────────────┐    ┌─────────────────────────────┐ │
│  │ AgentStateManager│    │    ConnectionPool            │ │
│  │  - 状态管理      │    │  - 连接管理                  │ │
│  │  - 超时检测      │◄───│  - 心跳更新                  │ │
│  │  - 事件触发      │    │  - 断连回调                  │ │
│  └────────┬────────┘    └─────────────────────────────┘ │
│           │ _lifecycle_broadcast()                       │
│           ▼                                              │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ _broadcast() → 所有连接                              │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│               lifecycle.py (Client)                      │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ AgentLifecycle                                       │ │
│  │  - 心跳发送 → ConnectionPool.update_heartbeat()      │ │
│  │  - 事件接收 → handle_message() → _trigger_hooks()    │ │
│  │  - 优雅退出 → _send_deregister()                     │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 2.2 数据流

**心跳流：**
```
Client lifecycle.py
  → _send_heartbeat() 
  → WS 发送 heartbeat 消息
  → Server node.py 收到
  → ConnectionPool.update_heartbeat()
  → AgentStateManager.update_heartbeat()
```

**事件流：**
```
Server AgentStateManager 检测超时
  → _lifecycle_broadcast("agent_offline", data)
  → node.py._broadcast({cmd: "lifecycle", ...})
  → 所有连接收到
  → Client lifecycle.py.handle_message()
  → _trigger_hooks("agent_offline", data)
```

**断连流：**
```
Client 断开连接
  → Server ConnectionPool 检测到断连
  → _disconnect_callback(agent_id)
  → AgentStateManager 标记 offline
  → _lifecycle_broadcast("agent_offline", data)
  → 广播给其他 Agent
```

### 2.3 代码位置

| 组件 | 文件 | 关键方法 |
|------|------|----------|
| 状态管理 | `connection_pool.py:185` | `AgentStatus` 枚举 |
| 心跳更新 | `connection_pool.py:580` | `update_heartbeat()` |
| 状态查询 | `connection_pool.py:591` | `get_status()` |
| 断连回调 | `connection_pool.py:301` | `set_disconnect_callback()` |
| 生命周期广播 | `node.py:429` | `_lifecycle_broadcast()` |
| 心跳发送 | `lifecycle.py:340` | `_send_heartbeat()` |
| 事件处理 | `lifecycle.py:395` | `handle_message()` |
| 钩子触发 | `lifecycle.py:390` | `_trigger_hooks()` |

## 三、集成验证

### 3.1 心跳同步 ✅

```python
# lifecycle.py._send_heartbeat()
if self._connection_pool and self._ws_connection:
    self.connection_pool.update_heartbeat(
        self.agent_id, 
        self._ws_connection, 
        self.status.value, 
        self.load
    )
```

### 3.2 事件广播 ✅

```python
# node.py._lifecycle_broadcast()
async def _lifecycle_broadcast(self, event: str, data: dict):
    exclude = data.get("agent_id")
    await self._broadcast({"cmd": "lifecycle", **data}, exclude=exclude)
    # 同时发送 presence 兼容旧客户端
```

### 3.3 断连处理 ✅

```python
# node.py 初始化时
self.connection_pool.set_disconnect_callback(self._handle_disconnect)
```

## 四、待确认项

1. **心跳超时阈值**：当前 `heartbeat_timeout=90s`，是否合适？
2. **重连策略**：`lifecycle.py` 重连 3 次后标记 ERROR，是否需要指数退避？
3. **状态同步**：Agent 主动切换 busy 状态时，是否需要通知所有连接？
4. **优雅退出窗口**：当前 15s，是否足够完成任务清理？

## 五、建议

1. 保持当前三层架构，不引入新的依赖
2. 心跳超时可配置化，允许不同 Agent 设置不同阈值
3. 重连策略改为指数退避（1s → 2s → 4s → 8s）
4. 状态变更事件增加 reason 字段，便于调试

---

请吉量评审以上方案，确认是否需要优化。
