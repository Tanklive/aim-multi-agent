# Observer + aim-watch v2 + JWT 认证方案

> 创建：2026-06-10 | 作者：吉量 (ZS0002)
> 状态：草案 → 呱呱评审 → 实现
> 前置依赖：Phase 2 已全部完成（NATS 迁移 + 三方联调）

## 一、现状评估

### 已有的（已投产可用的基础设施）

| 组件 | 位置 | 成熟度 |
|------|------|--------|
| AIMObserverClient (SDK) | `aim_nats_sdk.py:1541-1734` | ✅ 成熟：connect/subscribe/get_history/from_config/worker_pool |
| aim-observe.py (终端) | `~/.aim/bin/aim-observe.py` | ✅ 成熟：基于 SDK，支持 --agent/--history/--json |
| AIMNATSClient.emit_obs() | `aim_nats_sdk.py:1100-1138` | ✅ 成熟：限流+JS双发 |
| ObserverSkeleton (高级框) | `aim_nats_sdk.py:1381-1497` | ✅ 成熟：过滤/缓冲/报告 |
| aim-watch v2 (呱呱架构) | `~/.aim/bin/aim-watch-v2.py` | ✅ 成熟：引擎+渲染器+过滤器+传输层 |
| NATSTransport (watch) | `aim_watch/transports/nats.py` | ✅ 成熟：订阅 aim.obs.> |
| 节流策略 | `~/.hermes/aim/status_feedback.py` | ✅ 成熟：快步骤/长步骤区分 |
| 显示格式规范 | `observer-display-format-spec.md` | ✅ 大哥已指定 |

### 缺失的（当前方案要解决的问题）

| 问题 | 影响 |
|------|------|
| **1. 无 Observer 服务进程** | 没有后台 daemon 持续收集 Agent 状态，重启后丢失当前会话态 |
| **2. aim-watch v2 直接裸连 NATS** | 不走 AIMObserverClient，不能复用 SDK 的 from_config/worker_pool |
| **3. JWT 认证未实际启用** | SDK 已支持 credentials 参数但 aim-observe/aim-watch 仍用 Token，没切换到 NKEY/JWT |
| **4. Observer 无持久化选型** | 现仅 JetStream 存储 1 天，缺少冷存储策略 |

## 二、架构总览

### 三件并行推进

```
                          ┌─────────────────────────────┐
                          │     NATS JetStream           │
                          │  aim-observations (1 天)     │
                          └──────────┬──────────────────┘
                                     │ aim.obs.>
            ┌────────────────────────┼────────────────────┬──────────────────┐
            │                        │                    │                  │
    ┌───────▼──────┐     ┌─────────▼────────┐    ┌──────▼───────┐    ┌──────▼──────────┐
    │   Observer    │     │   aim-watch v2   │    │ aim-observe  │    │ JWT 认证           │
    │  Service Daemon│     │  (终端监控)       │    │ (临时观察)     │    │ (所有客户端)       │
    │  (新增)        │     │  (重构复用SDK)    │    │ (已有不改)     │    │ (SDK 已有改配置)   │
    └───────┬──────┘     └───────────────────┘    └───────────────┘    └──────────────────┘
            │
    ┌───────▼──────┐
    │   HTTP API   │
    │  :18901      │
    └──────────────┘
```

### 组件关系

```
Observer Daemon ───→ 持续收集: 所有 Agent 的 aim.obs.> 事件
     │
     ├──→ HTTP API :18901 (GET /status, GET /history, GET /report)
     │
     ├──→ 事件持久化: JSONL 文件 (~/aim-data/observer/ 按天)
     │
     └──→ 状态聚合: 内存中保持最近 5min 的 Agent 活跃状态
```

### 与呱呱 Server 瘦身的关系

```
Phase 0: 吉量出 Observer 方案并落地
    ↓ (Observer 框架就绪后)
Phase 1: 呱呱在 Server 瘦身中嵌入 Observer 支持
    ↓
Phase 2: 三方联调 Observer + Server + 各 Agent 端
```

呱呱等我 Observer 框架出了再动，**避免改早返工**。Observer 框架 = 协议定义 + SDK 接口 + 服务进程。

## 三、任务拆解 & 优先级

### P0：Observer 服务进程（新代码，本周）

实现一个后台 Observer Daemon，作为「系统观察者」持续运行。

**文件名**：`~/.aim/bin/aim-observer.py`

```
AIMObserverDaemon(observer_id="obs-daemon")
  ├── AIMObserverClient (复用 SDK)
  │     ├── connect(from_config) → 自动读 nats_token
  │     └── subscribe(handler, agent_filter=">") → 收所有
  ├── 状态缓存：dict[agent_id][msg_id] 最近状态
  ├── HTTP API：aiohttp :18901
  │     ├── GET /status           → 所有 Agent 最近状态摘要
  │     ├── GET /status/{agent}   → 某 Agent 最近状态详情
  │     ├── GET /history?agent=&limit= → 历史事件
  │     └── GET /report           → 聚合统计报告
  └── JSONL 日志：~/.aim/data/observer/YYYY-MM-DD.jsonl
```

**接口设计**：

```
GET /status
{
  "agents": {
    "ZS0001": {"last_seen": "...", "last_status": "processing", "events_5m": 12},
    "ZS0002": {"last_seen": "...", "last_status": "completed", "events_5m": 8},
    "ZS0003": {"last_seen": "...", "last_status": "heartbeat", "events_5m": 3}
  },
  "total_events": 23,
  "uptime": 3600
}

GET /history?agent=ZS0001&limit=20
[event1, event2, ...]  (从 JSONL 读取)
```

**依赖**：
- `aim_nats_sdk.py`（已有，AIMObserverClient）
- `aiohttp`（需 pip install）

### P0：aim-watch v2 NATSTransport 重构

**问题**：当前 `NATSTransport` 直接 `nats.connect()`，不走 `AIMObserverClient`。
**修复**：让 `NATSTransport` 复用 `AIMObserverClient` 的能力。

**改动点**（`aim_watch/transports/nats.py`）：
```python
class NATSTransport(TransportAdapter):
    def __init__(self, config):
        ...
        # 新增：使用 AIMObserverClient 替代裸 nats.connect
        self._obs_client = AIMObserverClient(
            observer_id=f"watch-{self.agent_id}",
            server=self.server,
            credentials=self.token,
        )
    
    async def start(self):
        await self._obs_client.connect()
        await self._obs_client.subscribe(self._on_obs_event, agent_filter=">")
        # 同时订阅 DM/GRP（已有代码保持）
        self.nc = self._obs_client.nc  # 复用连接
        ...
```

**效果**：
- 复用 SDK 的 reconnection/error_cb/from_config
- 统一认证方式
- 减少 50+ 行重复代码

### P0：JWT 认证接入

**现状**：SDK 的 `AIMNATSClient` 和 `AIMObserverClient` 均已支持 `credentials` 参数：
- 空字符串 → 裸连（调试用）
- 字符串 → Token 认证
- `.creds`/`.nkey` 文件路径 → NKEY/JWT 认证

**需要做的**：

1. **NATS Server 开启 JWT**（呱呱负责，Server 侧配置）
   - 生成 Operator / Account / User JWT
   - 配置 nats.conf 加载

2. **客户端切换**（吉量负责）
   - `aim-observe.py`: 修改 from_config 或 --credentials 参数，支持读 JWT 凭据文件
   - `aim-watch-v2.py`: 同上
   - `aim-observer-daemon`: 一开始就支持 JWT

3. **配置变更**
   - `~/.aim/config/aim.json` 中新增 `nats_jwt_path` 字段
   - `from_config()` 自动优先加载 JWT 文件

**标准化**：JWT 文件统一放 `~/.aim/credentials/` 目录
```
~/.aim/credentials/
  ├── ZS0001.creds   # 呱呱 JWT 凭据
  ├── ZS0002.creds   # 吉量 JWT 凭据
  └── observer.creds # Observer 只读 JWT（权限最小化）
```

**优先级**：JWT 不做 P0 阻塞。先让 Observer Daemon + aim-watch v2 能用 Token 跑通，JWT 作为 P0 但不阻塞前两项。呱呱确认 Server 侧 JWT 就绪后再切。

## 四、实施步骤

### Step 1：Observer Daemon（吉量，1天）

1. 创建 `~/.aim/bin/aim-observer.py`
   - 基于 AIMObserverClient 构建
   - HTTP API 用 aiohttp
   - JSONL 日志
   - launchd 自动启动

2. 验证：
   - 启动后能看到全部 3 个 Agent 的 Observer 事件
   - HTTP API 能查到实时状态
   - 日志按天轮转

### Step 2：aim-watch v2 NATSTransport 重构（吉量，半天）

1. 修改 `aim_watch/transports/nats.py` 复用 AIMObserverClient
2. 验证：
   - `aim-watch --all` 事件流正常
   - 断线重连正常
   - 历史回放正常

### Step 3：呱呱配合（呱呱，Observer 框架就绪后）

1. 呱呱在 Server 瘦身中嵌入 Observer 支持
2. 三方联调 Observer + Server + Agent

### Step 4：JWT 认证（吉量 + 呱呱联动）

1. 呱呱 Server 侧配置 JWT（nats.conf）
2. 吉量客户端侧切换
3. 三方统一测试（Token→JWT 无感过渡）

## 五、非功能性要求

### 安全
- Observer Daemon **只读**：不 publish 任何 subject，只 subscribe aim.obs.>
- HTTP API 绑定 127.0.0.1，不暴露公网
- JWT 凭据文件权限 600

### 可靠性
- Observer Daemon 使用 nats-py 内置断线重连（max_reconnect_attempts=-1）
- AIMObserverClient 自带 worker_pool，事件处理不阻塞 NATS 消息回调
- JSONL 日志自动按天轮转

### 性能
- Observer Daemon 缓存最多 5000 条事件/Agent（LRU evict）
- HTTP API 内存缓存 5s TTL，不穿透磁盘
- 限流机制由 SDK 自带（5条/s/Agent）

## 六、与呱呱协作

| 动作 | 触发条件 | 协作方式 |
|------|---------|---------|
| Observer Daemon 上线 | Step 1 完成 | 吉量告知 ready，呱呱启动自己的 observer 验证 |
| aim-watch v2 重构 | Step 2 完成 | 呱呱升级自己的 aim-watch v2 |
| Server 瘦身 Observer 支持 | Observer 框架就绪 | 呱呱按 Observer 协议在 Server 侧集成 |
| JWT 切换 | 双方确认 | 呱呱 Server 配置 + 吉量客户端改配置 |

## 七、验收标准

- [ ] Observer Daemon 可后台运行，HTTP API 正常
- [ ] 三方 Agent 的 Observer 事件全部准确收集
- [ ] aim-watch v2 用重构后的 NATSTransport 可视化正常
- [ ] JSONL 日志按天轮转，可回溯最近 30 天
- [ ] JWT 认证无感替换 Token
