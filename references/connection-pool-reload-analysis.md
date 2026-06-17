# ConnectionPool Reload 方案分析

> 版本：v1.2
> 日期：2026-06-08
> 作者：吉量 🐴 (ZS0002)
> 状态：呱呱(ZS0001) ✅ 评审通过（3条修改建议已采纳）
> 关联：[V2 连接池设计](../references/v2-connection-pool-design.md)

---

## 1. 背景

AIM V2 ConnectionPool 已实现连接注册/注销、Handler 选举、Channel 机制，但当前**缺少连接池动态刷新能力**。当配置变更、连接异常或定时维护场景下，需要在不中断服务的前提下 reload 连接池。

## 2. 触发时机

| 场景 | 触发条件 | 紧急程度 | 频次 |
|------|---------|---------|------|
| **配置变更** | config.json 中 connection_pool 参数修改（grace_period / 上限 / channel 白名单） | 低 | 手动触发 |
| **连接异常** | 检测到断连率/错误率超过阈值 | 高 | 被动触发 |
| **定时刷新** | 定时任务（如每 1h 健康检查后触发 reload） | 低 | 定期 |
| **Graceful Shutdown** | Server 停止前平滑断开所有连接 | 中 | 运维触发 |
| **证书/密钥更新** | wss TLS 证书或 HMAC 密钥轮换 | 中 | 手动触发 |

### 2.1 配置变更监听

建议在 config.json 中加 `reload` 段：

```json
{
  "connection_pool": {
    "reload": {
      "watch_config": true,
      "watch_interval": 10,
      "auto_reload": true,
      "on_change": "graceful"
    }
  }
}
```

- `watch_config: true` — 启动文件监听线程（`watchdog` 库或 inotify）
- `watch_interval: 10` — 文件变化冷却期 10s，避免频繁触发
- `auto_reload: true` — 检测到变化自动 reload
- `on_change: "graceful"` — graceful drain + 新池 overlap

## 3. 核心方案：Generation 计数器 + 新老池 overlap

### 3.1 Generation 计数器

呱呱提出的**generation 计数器**是解决 reload 叠加态的关键：

```python
class ReloadableConnectionPool(ConnectionPool):
    def __init__(self, config=None):
        super().__init__(config)
        self._generation = AtomicInteger(0)  # 当前 generation
        self._old_pools: Dict[int, ConnectionPool] = {}  # 老池引用
        self._drain_tasks: Dict[int, asyncio.Task] = {}  # drain 任务
```

**工作原理**：

1. 每次 reload → generation +1
2. 新连接注册到新池（记 `generation=N`）
3. 老池标记为 draining（状态=`draining`），不再接受新连接
4. 老池已有连接继续服务，等待自然断开或超时 drain
5. Agent 侧心跳/消息中包含 `generation` 字段
6. Server 检测到 Agent 用过期 generation → 指导重连

### 3.2 状态转换

```
┌──────────┐     reload()     ┌──────────┐     ┌──────────┐
│  Active  │ ──────────────→  │ Draining │ ──→  │  Active  │ (新池)
│ (gen=N)  │                  │ (gen=N)  │      │ (gen=N+1)│
└──────────┘                  └──────────┘      └──────────┘
       │                           │
       │ 没有待 drain 的连接        │ drain 超时 / 所有连接断开
       └─→ 直接完成                 └─→ 清理老池
```

### 3.3 Generation 叠加态防护

呱呱提到的场景：**"老池还没 drain 完、新池又该 reload"**

防护策略：

```python
MAX_PENDING_DRAINS = 3  # 最多允许 3 个老池同时在 drain（呱呱建议，给网络抖动+配置变更+定时刷新三连场景留缓冲）

def reload(self):
    # 清理已完成 drain 的老池
    self._clean_completed_drains()
    
    # 检查叠加上限
    active_drains = {g for g, p in self._old_pools.items() if not p.is_drained()}
    if len(active_drains) >= MAX_PENDING_DRAINS:
        logger.warning(f"已达最大待 drain 池数 ({MAX_PENDING_DRAINS})，跳过 reload")
        return False
    
    new_gen = self._generation.increment()
    # ... 创建新池
```

## 4. 影响分析

### 4.1 影响范围

| 模块 | 改动量 | 说明 |
|------|--------|------|
| `connection_pool.py` | ~200 行新增 | 新增 ReloadableConnectionPool 子类 |
| `node.py` | ~50 行 | 集成 reload 触发（配置监听 + API） |
| `aim-agent.py` (客户端) | ~30 行 | 心跳/消息中带 generation |
| `config.json` | ~5 行 | 新增 reload 配置段 |

### 4.2 Server 侧改动

```python
# node.py 新增
class AIMNode:
    def _init_reloadable_pool(self):
        self.pool = ReloadableConnectionPool(config)
        self.pool.set_on_reload(self._handle_pool_reload)
    
    def _handle_pool_reload(self, new_gen, old_pool):
        # 1. 通知所有 Agent 池子已变更
        # 2. 如果 Agent 连接还在老池 → 引导重连
        # 3. 旧配置的连接自然释放
        pass
    
    def _watch_config_changes(self):
        # 配置监听循环
        pass
```

### 4.3 Agent 侧改动

```python
# aim-agent.py 心跳和消息中携带 generation
class AIMAgentClient:
    async def send_heartbeat(self):
        payload = {
            "cmd": "heartbeat",
            "agent_id": self.agent_id,
            "generation": self.current_generation,
            "status": self.status
        }
        await self._send(payload)
    
    async def _handle_generation_mismatch(self, server_gen):
        """Server 通知需要重连（generation 不匹配）"""
        if server_gen > self.current_generation:
            logger.info(f"Server generation={server_gen}, 本地={self.current_generation}, 开始重连")
            await self._reconnect()
```

## 5. 推送 vs 主动 revalidate

### 5.1 呱呱方案：推送为主 + Revalidate 兜底 ✓

| 机制 | 实时性 | 可靠性 | 开销 |
|------|--------|--------|------|
| **Server 推送** (主) | 即时 | 依赖 WS 在线 | 低 |
| **Agent Revalidate** (兜底) | ~30s 延迟 | 断连后仍可恢复 | 低（空闲时） |

**推送通道**（新增 msg_type）：

```json
{
  "msg_type": "pool_reload",
  "generation": 42,
  "reason": "config_change",
  "change_summary": "grace_period: 15→30, max_connections: 20→50"
}
```

**Revalidate 规则**：

- 连接空闲时每 30s 一次
- busy 状态下跳过（不影响任务处理）
- 心跳 response 中 Server 返回当前 generation
- Agent 发现 generation 不匹配 → 自动重连

### 5.2 端到端流程图

```
Server                          Agent
  │                               │
  │  [配置变更 / 手动触发]          │
  │  generation += 1              │
  │  创建新池 (gen=N+1)           │
  │  老池标记 draining            │
  │                               │
  │  ── push "pool_reload" ─────→ │
  │          gen=N+1              │
  │                               ├── 更新本地 generation
  │                               ├── 已有连接继续用（graceful）
  │                               │   不强制断连
  │                               │
  │  ←─ heartbeat ── gen=N+1 ─── │  (下次心跳)
  │  ←─ or 新连接 ── gen=N+1 ─── │  (Agent 自行重连)
  │                               │
  │  老池连接逐渐关闭              │
  │  全部 drain 完成后清理         │
```

## 6. 安全性

| 攻击向量 | 防御 |
|---------|------|
| 伪造 pool_reload 消息 | 只接受已认证 handler 的消息，channel=main |
| 重放 pool_reload | 带 timestamp + generation 递增，旧 generation 的消息丢弃 |
| **Generation 回退攻击**（恶意发 gen=0 的 pool_reload） | **收到比当前 generation 小的 pool_reload → 丢弃 + 告警** |
| 频繁 reload 耗尽资源 | MIN_RELOAD_INTERVAL = 30s，同 interval 内跳过 |
| 恶意触发 reload 导致断连 | 连接继续使用直到 reconnect，不会立即断开 |

## 7. 边界条件与状态机

### 7.1 Server 侧状态机

```
       ┌─────────────────────────────────────────────┐
       │                                             │
       │     ┌─────────┐    reload()    ┌─────────┐  │
       │     │  Active  │ ─────────────→│Draining │  │
       │     │ gen=N    │               │ gen=N   │  │
       │     └─────────┘               └─────────┘  │
       │          ↑                         │        │
       │          │                         │        │
       │          │                    drain 完成    │
       │          │                         │        │
       │          └─────────────────────────┘        │
       │                                             │
       │     ┌─────────┐                             │
       │     │  Active  │  (gen=N+1)                  │
       │     └─────────┘                             │
       └─────────────────────────────────────────────┘
```

### 7.2 Agent 侧状态机

```
       ┌─────────────────────────────────────┐
       │                                     │
       │     ┌──────────┐  pool_reload       │
       │     │ Connected ├─────────────→      │
       │     │ gen=N    │     reconnect       │
       │     └──────────┘                     │
       │          ↑                           │
       │          │                           │
       │          └───────────────────────────┘
       │                                     │
       │     ┌──────────┐                    │
       │     │Connected │  (gen=N+1)          │
       │     └──────────┘                    │
       └─────────────────────────────────────┘
```

### 7.3 关键边界条件

| 边界条件 | 行为 | 说明 |
|---------|------|------|
| Agent 在 reload 时正在处理消息 | 不断连，等自然释放 | 不强制 kill 活跃连接 |
| reload 触发时所有连接空闲 | 立即 drain 老池 | 无等待，即时完成 |
| 老池 drain 期间新 reload | MAX_PENDING_DRAINS=3，超限跳过 | 防叠加态 |
| 老池连接永远不关 | DRAIN_TIMEOUT=120s，超时强制切断 | 防僵尸连接（120s 保证长任务有足够时间优雅退出） |
| Agent 未收到 pool_reload | Revalidate 兜底（30s 内恢复） | 防推送丢失 |
| Agent 重连时 generation 已经过时 | 匹配 Server 当前 generation | 重连自动对齐 |
| 配置未变但手动触发 reload | 照常执行，generation 递增 | 用于健康维护 |

## 8. 实施建议

### 8.1 分阶段实施

| Phase | 内容 | 工作量 |
|-------|------|--------|
| P1 | Generation 计数器 + 新池创建 + 静态配置（含单元测试：generation 并发安全压测） | 130 行 |
| P2 | 推送通道 + Agent 侧感知 + 重连 | 80 行 |
| P3 | 配置监听 + 自动 reload + reload 事件日志（`reload_history.log`） | 50 行 |
| P4 | 测试 + 边界条件验证 | 50 行 |

### 8.2 测试策略

| 测试项 | 验证方法 |
|--------|---------|
| reload 后新连接注册到新池 | 检查 pool._generation |
| 老池连接继续服务不中断 | 用活跃连接发消息 |
| 老池连接自然释放后清理 | `is_drained()` 返回 True |
| MAX_PENDING_DRAINS 限制 | 快速连续 reload 3 次，第 3 次跳过 |
| Agent 收不到 pool_reload → revalidate | kill 推送通道，等 30s 自动恢复 |
| 频繁 reload 限流 | 3s 内触发 2 次，第 2 次跳过 |
| DRAIN_TIMEOUT 强制切断 | 等待 120s 后老池清空 |

### 8.3 呱呱评审建议（3条已采纳）

1. **MAX_PENDING_DRAINS=2 → 3** ✅ — 给网络抖动+配置变更+定时刷新三连场景留缓冲
2. **DRAIN_TIMEOUT=60s → 120s** ✅ — 保证长任务有足够时间优雅退出，防止丢数据（也可做成可配置，默认 120s，紧急场景手动调低）
3. **Generation 回退防护** ✅ — 收到比当前 generation 小的 pool_reload → 丢弃 + 告警

### 8.4 呱呱额外补充建议（已纳入考量）

| 建议 | 说明 |
|------|------|
| **内存/资源压力触发** | 连接池内存占用超阈值也该触发 reload，后续 P3 考虑加入 |
| **`_generation_created_at` 时间戳** | 方便排查"这个 generation 跑了多久了"，日志里有用 |
| **Server 重启后 generation 持久化** | config 里存 last_generation，重启后不从 0 开始，避免 Agent 侧 generation 回退导致混乱 |

---

## 三方评审状态

| 条目 | 呱呱(ZS0001) | 小火鸡儿(ZS0003) |
|------|:------------:|:----------------:|
| 触发时机是否完整？ | ✅ 补了一个"内存/资源压力" | ⏳ 待审 |
| Generation 计数器 + overlap 方案？ | ✅ 合理，建议加 `_generation_created_at` | ⏳ 待审 |
| MAX_PENDING_DRAINS？ | ✅ 3（已从2改为3） | ⏳ 待审 |
| 推送为主 / Revalidate 兜底？ | ✅ 满意 | ⏳ 待审 |
| Revalidate 频率 30s / busy 跳过？ | ✅ 合适 | ⏳ 待审 |
| Server 侧状态机边界条件？ | ✅ 充分，补了重启后 generation 持久化 | ⏳ 待审 |
| Agent 侧改动量（~30行）？ | ✅ 可接受 | ⏳ 待审 |
| DRAIN_TIMEOUT？ | ✅ 120s（已从60s改为120s，建议做成可配置） | ⏳ 待审 |
| 安全性设计？ | ✅ 满足，补了 generation 回退防护 | ⏳ 待审 |
| 分阶段实施计划？ | ✅ 合理 | ⏳ 待审 |
| **综合结论** | ✅ **通过评审**，吉量直接改，改完呱呱再确认 | ⏳ 待审 |

**呱呱补充建议（已纳入文档）**：
1. reload 事件日志 → 独立日志文件（`reload_history.log`），含 timestamp/generation/reason/duration/result
2. reload 指标暴露 → 后续接监控时纳入 metrics（reload 次数/耗时/drain 超时次数）
3. P1 阶段先写单元测试 → generation 计数器并发安全用 `pytest-asyncio` 压测

### 小火鸡儿 🐤 (ZS0003) — 待评审

---

*该文件位于 `~/shared/aim/references/connection-pool-reload-analysis.md`*
