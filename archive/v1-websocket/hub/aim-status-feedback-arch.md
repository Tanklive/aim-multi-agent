# AIM Status Feedback — 架构示意图

> 2026-06-07

## 整体链路

```
┌─────────────────────────────────────────────────────────────────────┐
│                        大哥的视角                                    │
│                                                                     │
│  终端窗口1                 终端窗口2                                 │
│  ┌──────────────────┐    ┌──────────────────┐                      │
│  │ 吉量 CLI 会话    │    │ aim watch ZS0001 │                      │
│  │ (任务沟通/安排)   │    │ (实时看呱呱处理) │                      │
│  │ 大哥←→吉量对话   │    │                  │                      │
│  └────────┬─────────┘    └────────┬─────────┘                      │
└───────────┼──────────────────────┼──────────────────────────────────┘
            │                      │
            │  (observer 通道)     │  observer 只收不发
            ▼                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        AIM Server (node.py)                         │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                observer_bindings 路由表                      │   │
│  │  ZS0001 → [observer_A (watch窗口), observer_B]              │   │
│  │  ZS0002 → [observer_C]                                      │   │
│  │  收到 status_feedback → 查 from → 推给绑定的 observer       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│   ┌──────────┐   ┌──────────────┐   ┌─────────────┐               │
│   │消息路由   │   │observer管理  │   │超时检测(60s)│               │
│   │_deliver() │   │绑定/解绑    │   │自动timeout  │               │
│   └──────────┘   └──────────────┘   └─────────────┘               │
│                                                                     │
└────────────────────┬────────────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │呱呱Agent │ │吉量Agent │ │小火鸡儿  │
  │ZS0001    │ │ZS0002    │ │ZS0003    │
  │aim-agent │ │aim-agent │ │aim-agent │
  └──────────┘ └──────────┘ └──────────┘
```

## 消息处理 + 状态回推流程

```
时间线：

呱呱收到吉量的消息
  │
  ├── step: "task_start"     ───→ Server ───→ watch呱呱的observer + 吉量挂起队列
  │
  ├── step: "memory_search"  (快，<3s, 不推)
  │
  ├── step: "web_fetch"      ───→ Server ───→ watch呱呱的observer + 吉量挂起队列
  │    (长步骤，≥3s, 推)
  │
  ├── step: "reasoning"      ───→ Server ───→ watch呱呱的observer + 吉量挂起队列
  │    (长步骤，≥3s, 推)
  │
  └── step: "task_end"       ───→ Server ───→ watch呱呱的observer + 吉量挂起队列
       (始终推送)

Server 端超时检测：同一 session_id 60s 无更新 → 自动推 timeout
```

## 协议格式对比

### 现有消息协议（不变）
```json
{
  "cmd": "message",
  "msg_id": "xxx",
  "from": "ZS0001",
  "to": "ZS0002",
  "content": "回复内容"
}
```

### 新增 Status Feedback（复用WS连接，msg_type区分）
```json
{
  "msg_type": "status_feedback",
  "from": "ZS0001",
  "session_id": "msg_xxx",
  "step": "web_fetch",
  "status": "running",
  "progress": "正在抓取 URL: https://...",
  "timestamp": 1717737600
}
```

### Observer 连接认证
```json
{
  "cmd": "auth",
  "agent_id": "ZS0002",
  "channel": "observer",
  "watch_target": "ZS0001",
  "verbose": false
}
```

## 三条路径清晰分开

| 路径 | 内容 | 流向 |
|------|------|------|
| **大哥↔吉量CLI** | 任务沟通、安排（主会话） | 大哥终端 ←→ 吉量 AI |
| **aim watch** | 看呱呱/吉量的AI处理过程 | Server → observer |
| **AIM 消息** | Agent间通信（原始消息） | Agent ↔ Server ↔ Agent |

## aim watch 输出示例

```
─────────────────────────────────────────────
aim watch ZS0001
─────────────────────────────────────────────
[13:22:05] ZS0001 收到 ZS0002 消息: "查一下..."
[13:22:06] ZS0001 ▸ memory_search    ✅ done    (0.8s)
[13:22:08] ZS0001 ▸ web_fetch        🟡 running (已过 2s, URL: https://...)
[13:22:12] ZS0001 ▸ reasoning        🟡 running (已过 4s)
[13:22:15] ZS0001 ▸ task_end         ✅ done    (总耗时 10s)
[13:22:15] ZS0001 ▸ 结论: "查到了，结果是..."
─────────────────────────────────────────────
```

方案文档全量在 `~/shared/hub/aim-status-feedback-proposal.md`，这是我的架构理解，你看看对不对？
