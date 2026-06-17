# AIM Watch 通用设计方案 v2.0

> **版本**: v2.0 | **整合**: 呱呱 🐸 (ZS0001)
> **日期**: 2026-06-10
> **状态**: 方案稿，待三方 review
> **基于**: 小火鸡儿 v1.0 + 吉量 v6 + 呱呱 OpenClaw 实战经验
> **目标**: AIM 客户端标配功能，适配 TOP10 Agent 框架

---

## 一、设计原则（大哥定调）

1. **兼容优先** — 支持所有主流 Agent 框架，不是要求别人接入，而是兼容一切
2. **零侵入** — 现有框架不需要改代码，通过 Bridge 旁路监听
3. **渐进增强** — 先做能用的，再做好用的
4. **实战验证** — 每个功能必须在真实环境中跑通

---

## 二、TOP10 框架分析

### 2.1 框架分类

| 类型 | 框架 | 特征 | Observer 事件来源 |
|------|------|------|------------------|
| **CLI 本地** | Hermes, OpenClaw, Letta | 子进程调 CLI | nats-agent.py 内置 emit_obs() |
| **Python SDK** | CrewAI, AutoGen, LangGraph, Semantic Kernel | Python API 调用 | Bridge 进程 emit_obs() |
| **HTTP API** | Dify, Coze, OpenAI Assistants | REST API 调用 | Bridge 进程 emit_obs() |

### 2.2 关键差异

| 维度 | CLI | Python SDK | HTTP API |
|------|-----|-----------|----------|
| 调用方式 | 子进程 | Python 函数 | HTTP 请求 |
| 输入输出 | stdin/stdout | Python 对象 | JSON |
| 上下文保持 | session_id | 对象状态 | thread_id |
| 部署形态 | 本地安装 | 本地 Python | 远程服务 |
| Observer 接入 | 已完成 | 需 Bridge | 需 Bridge |

---

## 三、架构设计（三层解耦）

```
┌─────────────────────────────────────────────────────────┐
│                   aim-watch CLI                         │
│              (展示层 — 框架无关)                          │
│  ┌─────────────────────────────────────────────────────┐│
│  │  WatchEvent → TerminalRenderer / JSONRenderer       ││
│  │  EventFilter → Agent/类型/时间 过滤                  ││
│  │  EventBuffer → 环形缓冲 + 历史回放                   ││
│  └─────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────┤
│                   EventBus (事件总线)                    │
│           统一事件格式 WatchEvent — 框架无关              │
│  ┌─────────────────────────────────────────────────────┐│
│  │  event_id, agent_id, event_type, timestamp,         ││
│  │  payload, source, framework                         ││
│  └─────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────┤
│              Transport Adapters (传输适配层)             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ NATS Adapter  │  │ HTTP Adapter  │  │ File Adapter  │  │
│  │ (已完成)      │  │ (轮询/SSE)    │  │ (JSONL 日志)  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
├─────────────────────────────────────────────────────────┤
│              Framework Bridges (框架桥接层)              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ CLI Bridge│ │SDK Bridge│ │HTTP Bridge│ │File Bridge│  │
│  │Hermes/   │ │CrewAI/   │ │Dify/Coze/ │ │JSONL/Log │  │
│  │OpenClaw  │ │AutoGen/  │ │OpenAI     │ │Tail      │  │
│  │Letta     │ │LangGraph │ │Assistants │ │          │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 四、统一事件格式

### 4.1 WatchEvent 数据模型

```python
@dataclass
class WatchEvent:
    """AIM Watch 统一事件格式 — 框架无关"""
    event_id: str           # 唯一事件 ID
    agent_id: str           # Agent 标识 (ZS0001/ZS0002/ZS0003/...)
    event_type: str         # 事件类型 (见 4.2)
    timestamp: float        # Unix 时间戳
    payload: dict           # 事件载荷
    source: str             # 事件来源 ("nats" / "http" / "file")
    framework: str          # Agent 框架 ("hermes" / "openclaw" / "letta" / ...)
    metadata: dict = None   # 扩展元数据
```

### 4.2 标准事件类型（12 种）

| 事件类型 | 图标 | 说明 | 来源 |
|---------|------|------|------|
| **消息类** | | | |
| `msg_sent` | 📤 | Agent 发出消息 | NATS/HTTP |
| `msg_received` | 📨 | Agent 收到消息 | NATS/HTTP |
| `msg_group` | 📢 | 群聊消息 | NATS/HTTP |
| **处理类** | | | |
| `received` | 📥 | 消息进入处理队列 | Observer |
| `processing` | ⚙️ | 开始处理 | Observer |
| `ai_start` | 🤖 | 调用 AI 框架 | Observer |
| `ai_done` | ✅ | AI 返回非空回复 | Observer |
| `ai_empty` | ⚠️ | AI 返回空 | Observer |
| `completed` | ✅ | 处理完成，回复已发送 | Observer |
| `error` | ❌ | 处理出错 | Observer |
| **生命周期类** | | | |
| `agent_online` | 🟢 | Agent 上线 | Observer |
| `agent_offline` | 🔴 | Agent 下线 | Observer |
| `heartbeat` | 💓 | 心跳 | Observer |

### 4.3 与现有 Observer 事件兼容

```python
OBS_TO_WATCH = {
    "received":    "received",
    "processing":  "processing",
    "ai_start":    "ai_start",
    "ai_done":     "ai_done",
    "ai_empty":    "ai_empty",
    "completed":   "completed",
    "error":       "error",
    "online":      "agent_online",
    "offline":     "agent_offline",
    "heartbeat":   "heartbeat",
}
```

---

## 五、传输适配层

### 5.1 TransportAdapter 基类

```python
class TransportAdapter(ABC):
    """传输适配器基类"""

    def __init__(self, config: dict):
        self.config = config
        self._handlers: list[Callable[[WatchEvent], None]] = []

    def on_event(self, handler: Callable[[WatchEvent], None]):
        self._handlers.append(handler)

    def _emit(self, event: WatchEvent):
        for h in self._handlers:
            try:
                h(event)
            except Exception as e:
                log.error(f"Event handler error: {e}")

    @abstractmethod
    async def start(self): ...

    @abstractmethod
    async def stop(self): ...
```

### 5.2 NATS 适配器（已完成，重构）

```python
class NATSTransport(TransportAdapter):
    """NATS 传输适配器 — 复用 aim_nats_sdk.py"""

    async def start(self):
        nc = await nats.connect(servers=[self.server], token=self.token, ...)
        await nc.subscribe("aim.dm.>", cb=self._on_dm)
        await nc.subscribe("aim.grp.>", cb=self._on_grp)
        await nc.subscribe("aim.obs.>", cb=self._on_obs)
```

### 5.3 HTTP 轮询适配器（新增）

```python
class HTTPPollTransport(TransportAdapter):
    """HTTP 轮询适配器 — Dify/Coze/OpenAI 等 HTTP API 框架"""

    async def start(self):
        while True:
            await self._poll_conversations()
            await asyncio.sleep(self.poll_interval)

    async def _poll_conversations(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.api_url}/v1/conversations") as resp:
                data = await resp.json()
                for conv in data.get("data", []):
                    self._emit(self._to_watch_event(conv))
```

### 5.4 文件适配器（新增）

```python
class FileTransport(TransportAdapter):
    """文件适配器 — 监听 JSONL 日志文件变化"""

    async def start(self):
        # 使用 watchdog 监听文件变化
        observer = Observer()
        observer.schedule(self._handler, str(self.log_dir), recursive=False)
        observer.start()
```

---

## 六、框架桥接层

### 6.1 FrameworkBridge 基类

```python
class FrameworkBridge(ABC):
    """框架桥接器 — 将特定框架的原生事件转换为 Observer 事件"""

    @abstractmethod
    def supports(self, framework: str) -> bool: ...

    @abstractmethod
    def get_transport_config(self) -> dict: ...
```

### 6.2 CLI 框架桥接器（已完成）

```python
class CLIBridge(FrameworkBridge):
    """CLI 框架桥接 — Hermes / OpenClaw / Letta"""

    def supports(self, framework: str) -> bool:
        return framework in ("hermes", "openclaw", "letta")

    def get_transport_config(self) -> dict:
        # CLI 框架通过 nats-agent.py 接入，Observer 已内置
        return {"transport": "nats", "observer_events": True}
```

### 6.3 Python SDK 框架桥接器（新增）

```python
class PythonSDKBridge(FrameworkBridge):
    """Python SDK 框架桥接 — CrewAI / AutoGen / LangGraph"""

    def supports(self, framework: str) -> bool:
        return framework in ("crewai", "autogen", "langgraph", "semantic_kernel")

    def get_transport_config(self) -> dict:
        # SDK 框架通过 Bridge 进程接入，Bridge 发布 Observer 事件到 NATS
        return {"transport": "nats", "observer_events": True}
```

### 6.4 HTTP API 框架桥接器（新增）

```python
class HTTPAPIBridge(FrameworkBridge):
    """HTTP API 框架桥接 — Dify / Coze / OpenAI Assistants"""

    def supports(self, framework: str) -> bool:
        return framework in ("dify", "coze", "openai")

    def get_transport_config(self) -> dict:
        # HTTP API 框架通过轮询接入，Bridge 模拟 Observer 事件
        return {"transport": "http_poll", "observer_events": False}
```

---

## 七、核心模块

### 7.1 模块结构

```
~/.aim/bin/
├── aim-watch.py              # CLI 入口 (重构)
├── aim_watch/
│   ├── __init__.py
│   ├── core.py               # WatchEvent + EventBus
│   ├── renderer.py           # 终端/JSON 渲染器
│   ├── filter.py             # 事件过滤器
│   ├── buffer.py             # 环形缓冲区
│   ├── transports/
│   │   ├── base.py           # TransportAdapter 基类
│   │   ├── nats.py           # NATS 适配器
│   │   ├── http.py           # HTTP 轮询适配器
│   │   └── file.py           # 文件适配器
│   └── bridges/
│       ├── base.py           # FrameworkBridge 基类
│       ├── cli.py            # CLI 框架桥接
│       ├── http_api.py       # HTTP API 框架桥接
│       └── python_sdk.py     # Python SDK 框架桥接
└── aim_nats_sdk.py           # 现有 SDK (不变)
```

### 7.2 AIMWatch 核心引擎

```python
class AIMWatch:
    """AIM Watch 核心引擎 — 框架无关的多 Agent 监控"""

    def __init__(self, config: dict):
        self.config = config
        self.event_bus = EventBus()
        self.buffer = EventBuffer(max_size=10000)
        self.renderer = TerminalRenderer()
        self.filters: list[EventFilter] = []
        self.transports: list[TransportAdapter] = []

    def add_transport(self, transport: TransportAdapter):
        transport.on_event(self._on_event)
        self.transports.append(transport)

    def _on_event(self, event: WatchEvent):
        # 过滤 → 缓冲 → 渲染
        for f in self.filters:
            if not f.match(event):
                return
        self.buffer.push(event)
        self.renderer.render(event)

    async def start(self):
        await asyncio.gather(*[t.start() for t in self.transports])
```

---

## 八、配置格式

### 8.1 aim-watch 配置文件

```json
{
  "version": "1.0",
  "watch": {
    "default_agent": "ZS0001",
    "output": "terminal",
    "history_default": 20
  },
  "transports": {
    "nats": {
      "enabled": true,
      "nats_server": "nats://127.0.0.1:4222",
      "nats_token": "${NATS_TOKEN}"
    },
    "http_poll": {
      "enabled": false,
      "endpoints": []
    }
  },
  "frameworks": {
    "hermes": {"type": "cli", "transport": "nats", "observer": true},
    "openclaw": {"type": "cli", "transport": "nats", "observer": true},
    "letta": {"type": "cli", "transport": "nats", "observer": true},
    "crewai": {"type": "python_sdk", "transport": "nats", "observer": true},
    "dify": {"type": "http_api", "transport": "http_poll", "observer": false},
    "coze": {"type": "http_api", "transport": "http_poll", "observer": false},
    "openai": {"type": "http_api", "transport": "http_poll", "observer": false}
  }
}
```

---

## 九、CLI 命令

```bash
# 基础用法
aim-watch                          # 看自己
aim-watch --agent ZS0003           # 看指定 Agent
aim-watch --all                    # 看所有 Agent
aim-watch --history 20             # 回放历史
aim-watch --json                   # JSON 输出

# 过滤
aim-watch --framework hermes       # 按框架过滤
aim-watch --events ai_start,ai_done  # 按事件类型过滤

# 管理
aim-watch status                   # 查看 Agent 状态
aim-watch agents                   # 查看已注册 Agent
aim-watch frameworks               # 查看支持的框架
aim-watch ping                     # 测试连接
```

---

## 十、TOP10 框架接入方案

### 10.1 接入矩阵

| # | 框架 | 类型 | 传输层 | Observer | 接入难度 | 接入方式 |
|---|------|------|--------|----------|---------|---------|
| 1 | **Hermes** | CLI | NATS | 内置 | ✅ 已完成 | 现有 |
| 2 | **OpenClaw** | CLI | NATS | 内置 | ✅ 已完成 | 现有 |
| 3 | **Letta** | CLI | NATS | 内置 | ✅ 已完成 | 现有 |
| 4 | **CrewAI** | Python SDK | NATS | Bridge | 🟡 中等 | SDK Bridge |
| 5 | **AutoGen** | Python SDK | NATS | Bridge | 🟡 中等 | SDK Bridge |
| 6 | **LangGraph** | Python SDK | NATS | Bridge | 🟡 中等 | SDK Bridge |
| 7 | **Semantic Kernel** | SDK | NATS | Bridge | 🟡 中等 | SDK Bridge |
| 8 | **Dify** | HTTP API | HTTP 轮询 | Bridge 模拟 | 🔴 较难 | HTTP Bridge |
| 9 | **Coze** | HTTP API | HTTP 轮询 | Bridge 模拟 | 🔴 较难 | HTTP Bridge |
| 10 | **OpenAI Assistants** | HTTP API | HTTP 轮询 | Bridge 模拟 | 🔴 较难 | HTTP Bridge |

### 10.2 三类接入路径

#### 路径 A: CLI 框架（Hermes / OpenClaw / Letta）

```
现状: nats-agent.py → emit_obs() → NATS → aim-watch
状态: ✅ 已完成，零改动
```

#### 路径 B: Python SDK 框架（CrewAI / AutoGen / LangGraph）

```
Bridge 进程:
  1. 连接 NATS (复用 aim_nats_sdk.py)
  2. 订阅 aim.dm.<agent_id> + aim.grp.>
  3. 收到消息 → 调用 Python SDK → 得到回复
  4. 全程发射 Observer 事件到 NATS
  5. 回复通过 NATS 发送

Bridge 代码量: ~200 行 (通用模板 + 框架特定调用)
Observer: Bridge 进程内置发射
```

#### 路径 C: HTTP API 框架（Dify / Coze / OpenAI）

```
方案: HTTP → NATS Bridge
  1. Bridge 进程连接 NATS
  2. 监听 AIM 消息 → 转发到 HTTP API
  3. HTTP API 回复 → 通过 NATS 发回
  4. 全程发射 Observer 事件

Observer: Bridge 模拟（通过响应时间差推断 processing/ai_start）
```

### 10.3 新框架接入步骤

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| 1 | 确定框架类型 (CLI/SDK/HTTP) | 0 |
| 2 | 选择对应 Bridge 模板 | 0 |
| 3 | 配置 `config.json` 的 `frameworks` 段 | 5 分钟 |
| 4 | 实现框架特定的 AI 调用逻辑 | 1-4 小时 |
| 5 | 测试: 发消息 → 验证 Observer 事件 → aim-watch 展示 | 30 分钟 |

---

## 十一、实施计划

### Phase 1: 核心重构（3天）— 呱呱负责

| 任务 | 内容 | 代码量 |
|------|------|--------|
| 1.1 | WatchEvent 数据模型 + EventBus | ~50 行 |
| 1.2 | TransportAdapter 基类 + NATSTransport | ~100 行 |
| 1.3 | TerminalRenderer + JSONRenderer | ~80 行 |
| 1.4 | EventFilter + EventBuffer | ~60 行 |
| 1.5 | AIMWatch 核心引擎 | ~80 行 |
| 1.6 | CLI 入口重构 | ~60 行 |
| **小计** | | **~430 行** |

### Phase 2: 传输层扩展（2天）— 呱呱负责

| 任务 | 内容 | 代码量 |
|------|------|--------|
| 2.1 | HTTPPollTransport 适配器 | ~100 行 |
| 2.2 | FileTransport 适配器 | ~60 行 |
| 2.3 | 配置系统 | ~50 行 |
| **小计** | | **~210 行** |

### Phase 3: 框架桥接（3天）— 三方协作

| 任务 | 内容 | 负责 | 代码量 |
|------|------|------|--------|
| 3.1 | FrameworkBridge 基类 | 呱呱 | ~30 行 |
| 3.2 | CLIBridge | 呱呱 | ~50 行 |
| 3.3 | PythonSDKBridge | 吉量 | ~100 行 |
| 3.4 | HTTPAPIBridge | 小火鸡儿 | ~100 行 |
| 3.5 | 通用 SDK Bridge 模板 | 吉量 | ~200 行 |
| **小计** | | | **~480 行** |

### Phase 4: 测试 + 文档（2天）— 三方

| 任务 | 内容 | 负责 |
|------|------|------|
| 4.1 | NATS 传输层回归测试 | 呱呱 |
| 4.2 | HTTP 传输层测试 | 小火鸡儿 |
| 4.3 | 多框架 Bridge 测试 | 吉量 |
| 4.4 | 接入文档 + 配置模板 | 呱呱 |
| 4.5 | aim-watch 集成测试 | 三方 |

### 总工作量

| 阶段 | 代码量 | 时间 | 负责 |
|------|--------|------|------|
| Phase 1 | ~430 行 | 3 天 | 呱呱 |
| Phase 2 | ~210 行 | 2 天 | 呱呱 |
| Phase 3 | ~480 行 | 3 天 | 三方 |
| Phase 4 | ~200 行测试 | 2 天 | 三方 |
| **合计** | **~1320 行** | **~10 天** | |

---

## 十二、与现有方案的关系

| 现有方案 | 关系 | 说明 |
|---------|------|------|
| `aim-watch.py` (275行) | **重构** | 核心逻辑保留，架构重构为分层模式 |
| `aim_nats_sdk.py` (1690行) | **复用** | NATS 传输层复用 SDK |
| `PLAN-standard-client-integration-v6.md` | **继承** | Observer 事件标准完全继承 |
| `aim-ai-adapter-standard.md` | **继承** | AI 适配器三层设计继承 |
| `p3-ai-adapter-integration.md` | **继承** | AIRequest/AIResponse 数据模型继承 |
| `OAS-DESIGN.md` | **兼容** | 能力声明/信任路由是后续扩展 |
| `openclaw-aim-integration-plan.md` | **兼容** | OpenClaw 接入方案是本方案的子集 |

---

## 十三、关键设计决策

### 13.1 为什么重构而不是扩展？

| 维度 | 扩展 | 重构 |
|------|------|------|
| 代码组织 | 单文件 500+ 行 | 分层模块化 |
| 新框架接入 | 每次改主文件 | 新增 Adapter/Bridge |
| 传输层切换 | 硬编码 NATS | 配置驱动 |
| 测试难度 | 高 (耦合) | 低 (模块独立) |

**决策: 重构为分层架构，但保留现有 NATS 传输层的全部逻辑。**

### 13.2 HTTP API 用轮询还是 SSE？

| 维度 | HTTP 轮询 | SSE | WebSocket |
|------|----------|-----|-----------|
| 实现简单度 | ✅ 最简单 | 🟡 中等 | 🔴 较复杂 |
| 实时性 | 🔴 差 (5s 延迟) | ✅ 好 | ✅ 最好 |
| 框架支持 | ✅ 所有 HTTP API | ⚠️ 部分 | ⚠️ 部分 |

**决策: Phase 2 先实现 HTTP 轮询 (最通用)，SSE/WebSocket 后续优化。**

### 13.3 Bridge 进程独立还是嵌入？

| 维度 | 独立进程 | 嵌入 Agent |
|------|---------|-----------|
| 隔离性 | ✅ 好 | 🔴 差 |
| 框架侵入 | ✅ 零侵入 | 🔴 需改代码 |
| 灵活性 | ✅ 可独立升级 | 🔴 绑定版本 |

**决策: Bridge 作为独立进程，通过 launchd/systemd 保活。**

---

## 十四、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| HTTP API 框架无标准事件接口 | 高 | Observer 事件不完整 | 用轮询模拟，接受 5s 延迟 |
| Bridge 进程崩溃 | 中 | 监控中断 | launchd 自动重启 + JetStream 缓存 |
| 多传输层事件乱序 | 低 | 展示混乱 | 用 timestamp 全局排序 |
| 配置复杂度高 | 中 | 用户体验差 | 默认配置 + `aim-watch init` |

---

## 十五、FAQ

### Q: 现有 aim-watch.py 能直接用吗？
**A:** Phase 1 重构后，现有功能 100% 保留，新增框架无关能力。

### Q: 不用 NATS 的框架也能用 aim-watch？
**A:** 能。HTTP API 框架通过 HTTP 轮询适配器接入，不需要 NATS。

### Q: 新框架接入需要改 aim-watch 代码吗？
**A:** 不需要。只需要实现 Bridge + 配置注册。

### Q: 和小火鸡儿的 v1.0 方案有什么区别？
**A:** 核心架构一致，主要调整：
1. 分工更明确（呱呱负责核心重构，三方协作 Bridge）
2. 砍掉了 WebSocket 传输层（大哥说弃用）
3. 强调实战验证（每个功能必须跑通）

### Q: 工作量 10 天靠谱吗？
**A:** Phase 1-2 (5天) 呱呱独立完成，确定性高。Phase 3 (3天) 需要吉量和小火鸡儿配合。Phase 4 (2天) 三方联调。

---

*方案完毕。呱呱 🐸 出品，基于小火鸡儿 v1.0 + 吉量 v6 + 实战经验整合。*
