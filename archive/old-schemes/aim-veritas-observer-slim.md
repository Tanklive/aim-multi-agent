# AIM Veritas — Observer 瘦身方案

> **版本**：v1.0-draft
> **作者**：吉量 🐴 (ZS0002)
> **日期**：2026-06-09
> **背景**：Phase 2 功能补齐阶段 — Observer 机制从 WS+RPC 瘦身到 NATS Pub/Sub
> **关联方案**：`aim-veritas.md` §4.5 Observer 机制
> **目标**：删除 Server 端 Observer 代码，Agent 端直推 NATS

---

## 一、现状分析

### 1.1 Observer 功能目前散落在 4 个模块

| 文件 | 行数 | 角色 | 状态 |
|------|------|------|------|
| `aim_observer.py` | 115 | CLI 观察器（终端展示） | ✅ 已适配 Veritas，基于 `aim.obs.*` |
| `aim_server_nats.py` | ~20 | Server 端 `emit_observer_event()` | ❌ 应删除 — NATS pub/sub 不需要中转 |
| `aim_nats_client.py` | ~15 | 客户端 `emit_observer_event()` / `subscribe_observer_events()` | ⚠️ 拆分：emit 留，subscribe 挪到 aim-watch |
| `aim-agent.py` (旧 WS) | ~30 | status_feedback 推送 | ❌ 应删除 — 旧协议 |
| `aim_obs_stream.jsonl` | 文件 | Server 落盘日志 | ❌ 应删除 — JetStream 替代 |

### 1.2 当前 Observer 流程图

```
┌──────────┐     ┌───────────┐     ┌──────────┐
│  Agent   │ WS  │   Hub     │ WS  │ Observer │
│ (推送)   │───▶ │ (中转)   │───▶ │ (消费)   │
└──────────┘     └───────────┘     └──────────┘
                    │
                    ▼
              status_log.jsonl
               (落盘日志)
```

**问题**：
1. Server 做事件中转 —— NATS 本身是 pub/sub，不需要中间层
2. `observer.events.*` 是自定义 subject —— 与 Veritas §3 `aim.obs.*` 不一致
3. 落盘日志和 JetStream 重复 —— 增加复杂度
4. Server 代码越界 —— Server 应只有注册/路由，不掺和 Observer

---

## 二、目标架构

### 2.1 精简后流程

```
┌──────────┐              ┌──────────┐
│  Agent   │  NATS pub    │ Observer │
│ (推送)   │─────────────▶│ (消费)   │
│          │ aim.obs.<id> │          │
└──────────┘              └──────────┘
```

**代码变化**：`~65 行 Server 代码 + 旧 WS 逻辑` → `~10 行 Agent SDK 封装`

### 2.2 架构原则

```
原则 1: Observer = Agent 直推 NATS，Server 不掺和
原则 2: aim.obs.<agent_id> 是唯一 Subject，不用 observer.events.*
原则 3: 推送调用链：业务代码 → Agent SDK → nc.publish("aim.obs.<id>")
原则 4: 消费调用链：nc.subscribe("aim.obs.>") → CLI/watch 展示
原则 5: 落盘由 JetStream 处理，不需要 JSONL 文件
```

---

## 三、详细设计

### 3.1 Agent 端推送（改 aim_nats_client.py）

保留 `emit_observer_event()`，微调 event 字段：

```python
async def emit_obs(self, status: str, detail: str = "", msg_id: str = ""):
    """推 Observer 状态到 aim.obs.<agent_id>"""
    event = {
        "ver": "1.0",
        "agent_id": self.agent_id,
        "status": status,        # processing / completed / error / heartbeat
        "detail": detail,
        "msg_id": msg_id,
        "ts": time.time()
    }
    await self.nc.publish(f"aim.obs.{self.agent_id}", json.dumps(event).encode())
```

**二合一命名**：旧名 `emit_observer_event` 有 2 个调用方（aim_nats_client.py 自己 + aim_server_nats.py）。改成 `emit_obs`，Server 调用全删。

### 3.2 Observer 消费端（改 aim_observer.py + aim-watch）

`aim_observer.py` 已经正确实现：

```python
# 现有的 aim_observer.py 几乎不需改动
await self.client.subscribe_obs(obs_handler, agent_id=watch_subject)
# 内部: nc.subscribe(f"aim.obs.{agent_id}")
```

**aim-watch** CLI 封装（新增）：

```python
# aim watch [--agent-id ZSxxxx] [--from ZS0001]
# 等价于: nc.subscribe("aim.obs.>") 或 nc.subscribe("aim.obs.ZS0001")
```

### 3.3 Server 端删除

在 `aim_server_nats.py` 中：

| 删除项 | 行数 | 理由 |
|--------|------|------|
| `emit_observer_event()` 方法 | 12 行 | Agent 直推 NATS |
| `on_observer_event` 订阅 | 6 行 | 不需要监视监视器 |
| `observer.events.*` subject | — | Veritas 规范非标 |
| 在 `handle_private_message`/`handle_group_message` 中调用 | 2 处 | 不再中转 |

**删除后净效果**：-20 行，-1 个 subject，Server 职责更清晰。

### 3.4 JetStream 配置

Veritas §4.6 已经定义好：

```bash
# Observer 状态 Stream（已有）
nats stream add aim-observations \
    --subjects "aim.obs.>" \
    --storage file \
    --max-age 24h \
    --max-msg-size 64KB
```

注意：Observer 事件 Ephemeral Consumer（仅新消息），不持久化游标。JetStream 这里只是存档，不用于定阅消费。

---

## 四、改动清单

### 4.1 删除文件（0 个）

无需删除独立文件。

### 4.2 改造文件（3 个）

| 文件 | 当前行数 | 改造后 | 改动 |
|------|---------|--------|------|
| `aim_server_nats.py` | 302 | ~282 | 删除 Observer 相关 20 行 |
| `aim_nats_client.py` | 230 | ~225 | 保留 emit_obs, 简化接口 |
| `aim-agent.py` (旧 WS) | — | — | 删除 status_feedback observer 逻辑 |

### 4.3 新增文件（0 个）

无新增文件。aim_observer.py 已经存在且可正常工作。

---

## 五、接口兼容性

| 旧接口 | 替代 | 兼容期 |
|--------|------|--------|
| `AIMNatsClient.emit_observer_event()` | `emit_obs()` | 改名后同步更新调用方 |
| `AIMNatsServer.emit_observer_event()` | 删除 | 无兼容需求 |
| `observer.events.*` subject | `aim.obs.*` | 旧接口已废弃（Veritas Phase 1 已切换） |

Observer 的调用方只有：
- `aim_nats_client.py` 自身 — 改后同步更新
- `aim_server_nats.py` — 全部删除
- `aim_observer.py` — 已用 `aim.obs.*`
- `aim_nats_sdk.py` — 已用 `aim.obs.*`

---

## 六、Phase 2 分工

| 任务 | 负责人 | 预估时间 | 说明 |
|------|--------|---------|------|
| Server 删除 Observer 代码 | 🐸 呱呱 | 10min | 删 emit_observer_event + on_observer_event |
| Client SDK 简化 | 🐴 吉量 | 10min | emit_observer_event → emit_obs |
| aim-watch CLI 封装 | 🐴 吉量 | 15min | 封装 subscribe_obs + 终端显示 |
| 系统事件 aim.sys.online/offline | 🐸 呱呱 | 20min | sync 用 publish 推系统事件 |
| 三方联调验证 | 🐴 + 🐸 + 🐤 | 30min | 发消息看 observer 实时展示 |

---

## 七、迁移步骤

### Step 1: Server 端瘦身（呱呱）

```diff
# aim_server_nats.py
- async def emit_observer_event(self, event_type, agent_id, detail):
-     event = {...}
-     await self.nc.publish("observer.events.event_type", ...)
- 
  async def handle_private_message(self, msg):
      data = json.loads(msg.data)
-     await self.emit_observer_event("message", from_id, ...)
      ...

- async def on_observer_event(msg):
-     ...
- sub_observer = await self.nc.subscribe("observer.events.>", ...)
```

### Step 2: Client SDK 简化（吉量）

```diff
# aim_nats_client.py
- async def emit_observer_event(self, event_type, detail):
+ async def emit_obs(self, status, detail="", msg_id=""):
-     event = {"type": event_type, "agent_id": ...}
+     event = {"ver":"1.0","agent_id":self.agent_id,"status":status,...}
-     subject = f"aim.obs.{self.agent_id}"
-     await self.nc.publish(subject, json.dumps(event).encode())
+     await self.nc.publish(f"aim.obs.{self.agent_id}", json.dumps(event).encode())
```

### Step 3: aim-watch CLI 封装（吉量）

```python
# 新 aim-watch 封装（也可用现有 aim_observer.py）
async def aim_watch(target=">"):
    async def handler(msg):
        data = json.loads(msg.data)
        print(format_obs_event(data))
    nc = await nats.connect(...)
    await nc.subscribe(f"aim.obs.{target}", cb=handler)
    await asyncio.Event().wait()
```

---

## 八、FAQ

### Q: Observer 事件不经过 Server，认证怎么搞？
A: NATS 基于 Subject 的 ACL 控制。Agent 只允许 publish 到 `aim.obs.{自己ID}`，不能冒充别人。

### Q: 旧 Observer 的 status_log.jsonl 怎么办？
A: 停止写入。历史数据可保留或归档。JetStream 提供 24h 回放能力。

### Q: aim-watch 和 aim_observer.py 是什么关系？
A: aim-watch 是 CLI 入口（命名统一为 aim 命令），aim_observer.py 是内部实现。长期 aim-watch 会调用 aim_observer.py。

### Q: 瘦身后 Observer 还能看全部 Agent 吗？
A: 能。subscribe `aim.obs.>` 即可收到所有 Agent 的状态推送。

---

## 九、评审要点

1. **Server 不中转** — 符合「NATS 负责怎么传，AIM 负责传什么」原则
2. **Subject 规范** — 全部使用 `aim.obs.<id>`，废弃 `observer.events.*`
3. **去重** — JetStream 2min 去重窗口 + 应用层 msg_id 去重
4. **零新增文件** — 全部复用现有代码
5. **兼容性** — aim_observer.py 不改，aim_nats_client.py 接口微调
