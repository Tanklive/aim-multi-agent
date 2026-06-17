# AIM 通知闭环 — 主会话推送方案 v1.0

> 大哥指令：AIM 收到消息后 AI 分析反馈了，但主会话不知道。给主会话推送进度，实现任务正常推进。  
> 基于 AIM-STANDARD-INTERFACE-PROPOSAL.md 优化  
> 日期: 2026-06-14  
> 状态: Round 1 讨论

---

## 一、现状问题

### 1.1 当前消息流
```
Agent A (主会话) → AIM DM → nats-agent → AI 处理 → 回复 ← AIM DM ← Agent B
                                                                    ↓
                                                              Agent A AIM 客户端收到回复
                                                              Agent A 主会话 ❌ 不知道
```

### 1.2 核心断层
| 层 | 知道？ | 说明 |
|-----|--------|------|
| nats-agent | ✅ 知道 | 它调的 AI |
| AIM NATS 通道 | ✅ 知道 | 消息在总线上 |
| aim-watch (Observer) | ✅ 知道 | 实时看板 |
| **主会话 (Letta/OpenClaw/Hermes)** | ❌ **不知道** | 这是断层点 |

### 1.3 影响
- 大哥发任务后，不知道 Agent 收到了没、处理了没、回复了没
- 只有手动开 aim-watch 才能看到进度
- 任务推进依赖"人盯着看板"，不是自动推送

---

## 二、优化目标

### 2.1 用户体验目标
```
1. 大哥在 Letta Code CLI 里发 AIM 消息给呱呱
2. Letta Code CLI 自动显示：🐸 呱呱已收到 → 呱呱正在处理 → 呱呱回复: xxx
3. 不需要额外开 aim-watch
4. 不需要手动轮询
```

### 2.2 技术目标
- AIM 消息闭环：发 → 收 → 处理 → 回复 → **通知发起方主会话**
- 不破坏现有 AIM Envelope / NATS 架构
- 三框架（Letta/OpenClaw/Hermes）统一接入
- 可扩展到未来 10 框架

---

## 三、设计方案

### 3.1 整体架构

```
Agent A 主会话 (Letta)
    │ ① send(text) via AIM
    ▼
AIM Adapter (envelope + notification_mode)
    │
    ▼
NATS (aim.dm.ZS0001)
    │
    ▼
Agent B nats-agent (ZS0001)
    │ ② receive → AI process
    ▼
Agent B AI 处理
    │ ③ reply via AIM
    ▼
NATS (aim.dm.ZS0003 + aim.notify.ZS0003)
    │
    ▼
Agent A AIM Adapter
    │ ④ 收到通知 → 推送给主会话
    ▼
Agent A 主会话
    🐸 呱呱已回复: xxx
```

### 3.2 AIM Envelope 扩展

新增 `meta` 字段中的通知配置：

```json
{
  "ver": "1.0",
  "id": "msg-uuid",
  "ts": "2026-06-14T04:30:00Z",
  "from": "ZS0003",
  "type": "dm",
  "payload": {"text": "呱呱，调研好了没？"},
  "meta": {
    "reply_to": "nats://127.0.0.1:4222/aim.dm.ZS0003",
    "notification_mode": "full",       // "silent" | "ack_only" | "full"
    "callback_subject": "aim.notify.ZS0003",  // 进度推送 NATS subject
    "expires_in": 300,                 // 通知有效期（秒）
    "task_id": "research-2026-06-14"   // 关联任务 ID
  }
}
```

### 3.3 通知模式

| 模式 | 发送通知 | 内容 | 适用场景 |
|------|---------|------|----------|
| `silent` | ❌ | 无通知 | 日常闲聊 |
| `ack_only` | ✅ 仅确认 | `{"status":"received"}` | 低优先级任务 |
| **`full`** | ✅ 全流程 | `received → processing → completed(含回复)` | 大哥发任务（默认） |

### 3.4 通知消息格式

```json
// aim.notify.ZS0003
{
  "type": "notification",
  "task_id": "research-2026-06-14",
  "msg_id": "original-msg-uuid",
  "from_agent": "ZS0001",
  "to_agent": "ZS0003",
  "timestamp": "2026-06-14T04:30:15Z",
  "status": "completed",           // received | processing | completed | error
  "detail": "🐸 调研完成，已写入 shared/aim/research/",
  "payload": {
    "text": "🐸 调研完成，已写入 shared/aim/research/"  // 完整回复
  }
}
```

### 3.5 三框架主会话接入

| 框架 | 通知推送方式 | 实现 |
|------|------------|------|
| **Letta** (ZS0003) | `letta send --agent <id> -p "<通知>"` | nats-agent 子进程调用 |
| **OpenClaw** (ZS0001) | HTTP POST `http://localhost:{port}/callback` | 框架内置 HTTP endpoint |
| **Hermes** (ZS0002) | stdin 写入 `{"type":"notification",...}` | 框架进程 stdin |

### 3.6 防风暴机制

- **同 task_id 通知合并**: 同一任务的连续状态变更合并为 1 条
- **最小间隔**: 同一 task_id 通知间隔 ≥ 5 秒
- **过期丢弃**: `expires_in` 超时的通知不推送
- **优先级过滤**: 主会话可配置 `min_notification_level: ack_only`

---

## 四、与标准接口方案的关系

在 `AIM-STANDARD-INTERFACE-PROPOSAL.md` 的 `AIMAdapter` 接口上 **增加一个方法**：

```
class AIMAdapter:
    async def connect() -> bool
    async def send(text: str, mode: str = "full") -> str   # 增加 mode 参数
    async def receive() -> str
    async def notify_host_session(notification: dict) -> bool   # ← 新增！
    def capabilities() -> dict
```

`notify_host_session()` 是三框架各自实现：
- Letta: `letta send --agent <id> -p <text>`
- OpenClaw: `POST http://localhost:PORT/callback`
- Hermes: `stdin.write(json)`

---

## 五、三框架评审矩阵

需要吉量、呱呱分别从自己框架角度确认以下事项：

| 评审项 | 🐤 Letta | 🐸 OpenClaw | 🐴 Hermes |
|--------|---------|------------|----------|
| 1. 主会话可以接收外部推送吗？ | 待验证 | **✅ 可以。cron.wake (systemEvent) 注入主会话，或 sessions_send 跨会话推送，或 HTTP POST /callback** | **✅ 可以，通过 subscribe aim.notify.ZS0002 + FrameworkCLI 注入。现有 push_to_bridge() 和 _inject_to_main_session() 机制可直接复用** |
| 2. 推送接口最小调用方式 | `letta send` | **HTTP POST /callback 或 cron.wake systemEvent** | **stdin 写入 JSON（现有 hermes chat -q 已支持管道输入）。也可用 hermes send --text "通知"** |
| 3. 是否需要持续连接（SSE/WS） | 否 | **不需要。cron.wake 按需推送，无长连接依赖** | **否。NATS 订阅保持常连即可，收到通知后直接注入主会话。不需要额外 SSE/WS** |
| 4. 防风暴：能否按 task_id 合并 | 能 | **✅ 能。cron systemEvent 同 msg_id 5s 内 NO_REPLY 跳过** | **✅ 能。现有 MessageDedup（内存 LRU）+ Pin（SQLite 持久化去重）双去重机制，5 秒间隔可通过 SDK 级合并实现** |
| 5. 用户侧体验：推送消息如何显示 | 终端输出 | **终端输出（systemEvent 注入主会话上下文）** | **终端输出。通过 FrameworkCLI hermes chat -q 注入到主会话终端** |

---

## 六、讨论回合

| Round | 内容 | 状态 |
|-------|------|------|
| Round 1 | 问题分析 + 方案起草（小火鸡儿） | ✅ 完成 |
| Round 2 | 吉量确认 Hermes 侧（通知推送方式） | ✅ 吉量已确认（16:56 ZS0002 5项通过） |
| Round 3 | 呱呱确认 OpenClaw 侧（HTTP callback） | ✅ 呱呱已确认（17:16） |
| Round 4 | 三方对齐 + 修改定稿 | 📌 待 ZS0003 发起 |
| Round 5 | 最终方案给大哥评审 | 待定 |

---

*按核心规则二.6：团队讨论 3-9 轮出结论。目标 ≤5 轮。*
