# ConnectionPool Reload 最终方案文档

> 版本：v1.0
> 日期：2026-06-08
> 作者：吉量 🐴 (ZS0002)
> 状态：✅ 呱呱评审通过，待大哥审批
> 关联：[V2 连接池设计](v2-connection-pool-design.md) | [分析报告](connection-pool-reload-analysis.md)

---

## 1. 概述

AIM V2 ConnectionPool 已实现连接注册/注销、Handler 选举、Channel 机制，但当前**缺少连接池动态刷新能力**。当配置变更、连接异常或定时维护场景下，需要在不中断服务的前提下 reload 连接池。

### 核心设计目标

| 目标 | 说明 |
|------|------|
| **零中断** | reload 期间已有连接继续服务，不强制断连 |
| **无状态污染** | 新旧池严格隔离，generation 计数器防叠加态 |
| **推送+兜底** | Server 推送主通道 + Agent Revalidate 兜底 |
| **安全可控** | 防重放、防频繁 reload、认证隔离 |

### 三方讨论确认的共识

> 以下 7 项由呱呱 🐸 (ZS0001) 与吉量 🐴 (ZS0002) 讨论达成一致

1. ✅ **Generation 计数器** — 每次 reload 递增，Agent 侧 revalidate 时比对，防过期池状态污染
2. ✅ **New + Old Pool overlap** — 新老池共存，老池 graceful drain，不强制 kill 活跃连接
3. ✅ **MAX_PENDING_DRAINS=2** — 最多允许 2 个老池同时在 drain，超过跳过 reload
4. ✅ **推送为主 + Revalidate 兜底** — Server 推送 pool_reload 消息主通道，Agent 心跳 revalidate 兜底（防推送丢失）
5. ✅ **Revalidate 频率** — 空闲时每 30s 一次，忙时跳过
6. ✅ **DRAIN_TIMEOUT=60s** — 老池超时强制切断僵尸连接
7. ✅ **安全性设计** — 只接受已认证 handler 消息、generation 递增防重放、MIN_RELOAD_INTERVAL=30s

---

## 2. 架构设计

### 2.1 Generation 计数器

```python
class ReloadableConnectionPool:
    def __init__(self, config=None):
        super().__init__(config)
        self._generation = 0  # 当前 generation
        self._old_pools: Dict[int, ConnectionPool] = {}  # 老池引用
        self._drain_tasks: Dict[int, asyncio.Task] = {}  # drain 任务

    def reload(self):
        """触发连接池 reload"""
        now = time.time()
        if now - self._last_reload_time < MIN_RELOAD_INTERVAL:
            logger.warning(f"reload 过于频繁，跳过")
            return False

        # 清理已完成 drain 的老池
        self._clean_completed_drains()

        # 检查叠加上限
        active_drains = sum(1 for p in self._old_pools.values() if not p.is_drained())
        if active_drains >= MAX_PENDING_DRAINS:
            logger.warning(f"已达最大待 drain 池数 ({MAX_PENDING_DRAINS})，跳过 reload")
            return False

        new_gen = self._generation + 1
        # 创建新池...
        self._generation = new_gen
        self._last_reload_time = now
        return True
```

### 2.2 状态转换

```
┌──────────┐     reload()     ┌──────────┐     drain 完成    ┌──────────┐
│  Active  │ ──────────────→  │ Draining │ ──────────────→  │  Active  │ (新池)
│ (gen=N)  │                  │ (gen=N)  │                  │ (gen=N+1)│
└──────────┘                  └──────────┘                  └──────────┘
       │                           │
       │ 没有待 drain 的连接        │ 60s 超时强制清理
       └─→ 直接完成                 └─→ 清理老池
```

### 2.3 Agent 侧状态

```
┌──────────┐  收到 pool_reload    ┌──────────┐
│Connected │ ─────────────────→  │Connected │ (继续用旧连接)
│ gen=N    │                     │ gen=N+1  │ (更新本地 generation)
└──────────┘                     └──────────┘
       ↑  心跳 revalidate 比对成功     │
       └───────────────────────────┘
       或 generation 不匹配 → 自动重连
```

---

## 3. 触发机制

| 场景 | 触发条件 | 紧急程度 | 频次 | 实现 |
|------|---------|---------|------|------|
| **配置变更** | config.json connection_pool 参数修改 | 低 | 手动触发 | watch_config 监听 + 手动 API |
| **连接异常** | 断连率/错误率超过阈值 | 高 | 被动触发 | 错误计数器 + 阈值判断 |
| **定时刷新** | 定时任务健康检查后 | 低 | 定期 | cron 或定时器 |
| **Graceful Shutdown** | Server 停止前 | 中 | 运维触发 | 手动 API |
| **证书/密钥更新** | wss TLS 或 HMAC 密钥轮换 | 中 | 手动触发 | 手动 API |

---

## 4. 消息协议

### 4.1 Server → Agent 推送

```json
{
  "msg_type": "pool_reload",
  "generation": 42,
  "reason": "config_change",
  "change_summary": "grace_period: 15→30, max_connections: 20→50"
}
```

### 4.2 Agent → Server 心跳（含 revalidate）

```json
{
  "cmd": "heartbeat",
  "agent_id": "ZS0001",
  "generation": 42,
  "status": "online",
  "channel": "main"
}
```

Server 心跳 response 携带当前 Server generation：

```json
{
  "cmd": "heartbeat_ack",
  "status": "online",
  "server_generation": 42
}
```

### 4.3 Generation 不匹配处理

Agent 发现 `server_generation > local_generation` 时自动重连。
重连后注册到新池，generation 对齐。

---

## 5. 安全性

| 攻击向量 | 防御 |
|---------|------|
| 伪造 pool_reload 消息 | 只接受已认证 handler 的消息，channel=main |
| 重放 pool_reload | 带 timestamp + generation 递增，旧 generation 丢弃 |
| 频繁 reload 耗尽资源 | MIN_RELOAD_INTERVAL = 30s，同 interval 内跳过 |
| 恶意触发导致断连 | 连接继续使用直到 reconnect，不会立即断开 |

---

## 6. 关键边界条件

| 边界条件 | 行为 | 说明 |
|---------|------|------|
| Agent 在 reload 时正在处理消息 | 不断连，等自然释放 | 不强制 kill 活跃连接 |
| reload 触发时所有连接空闲 | 立即 drain 老池 | 无等待，即时完成 |
| 老池 drain 期间新 reload | MAX_PENDING_DRAINS=2，超限跳过 | 防叠加态 |
| 老池连接永远不关 | DRAIN_TIMEOUT=60s，超时强制切断 | 防僵尸连接 |
| Agent 未收到 pool_reload | Revalidate 兜底（30s 内恢复） | 防推送丢失 |
| Agent 重连时 generation 已经过时 | 匹配 Server 当前 generation | 重连自动对齐 |
| 配置未变但手动触发 reload | 照常执行，generation 递增 | 用于健康维护 |

---

## 7. 实施计划

### 7.1 P1 — Generation 计数器 + 新池创建 + 静态配置（~100 行）

**Server 侧（connection_pool.py 新增 ReloadableConnectionPool）：**
- `_generation` 计数器 + `reload()` 方法
- 新池创建、老池标记 `draining`
- `_clean_completed_drains()` 清理已完成 drain 的老池
- MAX_PENDING_DRAINS 限制
- DRAIN_TIMEOUT=60s 超时强制清理
- MIN_RELOAD_INTERVAL=30s 防频繁触发

### 7.2 P2 — 推送通道 + Agent 侧感知 + 重连（~80 行）

**Server 侧（node.py ~50 行）：**
- `_init_reloadable_pool()` 集成 ReloadableConnectionPool
- 推送 `pool_reload` 消息到所有 handler
- 心跳 response 携带 `server_generation`

**Agent 侧（aim-agent.py ~30 行）：**
- 心跳中携带 `generation` 字段
- 收到 `pool_reload` 更新本地 generation
- 心跳 response 检测 `server_generation` 不匹配 → 自动重连

### 7.3 P3 — 配置监听 + 自动 reload（~40 行）

**Server 侧（node.py ~40 行）：**
- `_watch_config_changes()` 配置变化监听
- 检测到 connection_pool 配置变更 → 自动触发 reload

### 7.4 P4 — 测试 + 边界条件验证（~50 行）

**测试项：**

| # | 测试项 | 验证方法 |
|---|--------|---------|
| T1 | reload 后新连接注册到新池 | 检查 pool._generation |
| T2 | 老池连接继续服务不中断 | 用活跃连接发消息 |
| T3 | 老池连接自然释放后清理 | is_drained() 返回 True |
| T4 | MAX_PENDING_DRAINS 限制 | 快速连续 reload 3 次，第 3 次跳过 |
| T5 | Agent 收不到 pool_reload → revalidate | kill 推送通道，等 30s 自动恢复 |
| T6 | 频繁 reload 限流 | 3s 内触发 2 次，第 2 次跳过 |
| T7 | DRAIN_TIMEOUT 强制切断 | 等待 60s 后老池清空 |

### 7.5 总改动量

| 模块 | 改动量 | 负责人 |
|------|--------|--------|
| `connection_pool.py` | ~100 行新增（P1） | 呱呱 🐸 |
| `node.py` | ~90 行新增（P2 + P3） | 吉量 🐴 |
| `aim-agent.py` (客户端) | ~30 行（P2） | 各 Agent 自行同步 |
| 测试用例 | ~50 行（P4） | 呱呱+吉量 |

---

## 8. 回滚方案

| 阶段 | 回滚方式 |
|------|---------|
| P1（Server 侧新增类） | 不启用 ReloadableConnectionPool，回退到 ConnectionPool |
| P2（推送 + Agent 重连） | Server 端丢弃 pool_reload 推送，Agent 端回退旧心跳 |
| P3（自动监听） | 关闭 watch_config 或移除定时器 |

---

## 9. 验证标准

**验收条件：**
1. ✅ reload 后新连接注册到新池，旧连接自然释放
2. ✅ 连续频繁 reload 受 MAX_PENDING_DRAINS 和 MIN_RELOAD_INTERVAL 限制
3. ✅ push 丢失时 revalidate 兜底 30s 内恢复
4. ✅ DRAIN_TIMEOUT 后老池强制清理
5. ✅ 安全性设计覆盖（防重放、防频繁、防伪造）
6. ✅ 3 轮基本测试 → 修复优化 → 5 轮全面测试

---

*本方案已通过呱呱 🐸 (ZS0001) 评审。*
*最终位置：`~/shared/aim/references/connection-pool-reload-final-plan.md`*
