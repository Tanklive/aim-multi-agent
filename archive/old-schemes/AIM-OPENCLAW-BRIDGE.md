# AIM-OpenClaw 标准桥接协议 v1.0

> AIM 标准的一部分，覆盖 Server 端和 Client 端。
> 任何 OpenClaw 架构的 Agent 按本协议接入 AIM，开箱即用。
> 版本：v1.0 | 日期：2026-06-14

---

## 一、背景

### 1.1 问题

OpenClaw 架构的 Agent（如呱呱 ZS0001）与其他框架（Hermes/Letta）的 Agent 不同：

| 架构 | 消息处理方式 |
|------|------------|
| Hermes/Letta | nats-agent 进程内直接调 AI，一站式 |
| **OpenClaw** | nats-agent 收消息 → 主会话处理 → nats-agent 发回复（两进程分离） |

OpenClaw 主会话是**被动响应式**的（靠用户消息/心跳/cron 唤醒），不像独立 Python 进程那样事件驱动。因此需要一个标准化的桥接层。

### 1.2 目标

- **即时性**：消息到达后 <2 秒内主会话开始处理
- **可复用**：任意 OpenClaw 架构 Agent 安装时自动配置
- **零侵入**：不影响其他框架（Hermes/Letta）的 Agent
- **标准可验证**：Server 端和 Client 端均可独立验证协议合规

---

## 二、协议定义

### 2.1 环境变量（Client 端配置）

nats-agent 通过环境变量读取路径，安装时自动注入：

```bash
# 队列目录：nats-agent 写入收到的消息
AIM_OPENCLAW_QUEUE_DIR=~/.openclaw/workspace/.aim-queue

# 回复目录：主会话写入 AI 回复
AIM_OPENCLAW_REPLY_DIR=~/.openclaw/workspace/.aim-replies

# 触发标记：nats-agent 写入，主会话检测
AIM_OPENCLAW_TRIGGER_FILE=~/.openclaw/workspace/.aim-trigger

# 轮询间隔（秒）
AIM_OPENCLAW_POLL_INTERVAL=2
```

**Server 端无需改动**：消息仍通过 NATS（`aim.dm.*`, `aim.grp.*`）广播，Server 不感知 Client 端处理方式。

### 2.2 消息流

```
NATS 消息到达
  │
  ▼
┌─────────────────────────────────────┐
│  nats-agent (Python)                │
│                                     │
│  1. 写队列文件:                       │
│     $QUEUE_DIR/{msg_id}.json        │
│  2. touch $TRIGGER_FILE             │
│  3. 轮询等待回复文件（超时 120s）       │
│     $REPLY_DIR/{msg_id}.txt         │
│  4. 读到回复 → NATS 发出             │
└─────────────────────────────────────┘
  │
  │  （<2s）
  ▼
┌─────────────────────────────────────┐
│  OpenClaw 主会话 (Node)              │
│                                     │
│  1. cron (每2s) 检测 TRIGGER_FILE    │
│  2. 发现 → 注入 systemEvent          │
│  3. 主会话醒来 → 遍历 QUEUE_DIR      │
│  4. AI 处理每条消息                   │
│  5. 写回复到 REPLY_DIR/{msg_id}.txt  │
│  6. 清理触发标记和队列文件             │
└─────────────────────────────────────┘
```

### 2.3 文件格式

**队列文件** (`{msg_id}.json`):
```json
{
  "msg_id": "fa097ea2...",
  "from": "ZS0003",
  "type": "dm|grp",
  "content": "消息正文",
  "ts": 1718370000.0,
  "meta": {
    "group": "grp_trio"
  }
}
```

**回复文件** (`{msg_id}.txt`):
```
回复内容（纯文本）
NO_REPLY（表示不回复）
```

### 2.4 错误处理

| 场景 | nats-agent 行为 | 主会话行为 |
|------|----------------|-----------|
| 队列文件写入失败 | 记录 error，不设 trigger | - |
| trigger 未在 120s 内被处理 | 清理队列，记录 warning | - |
| trigger 被检测到但队列为空 | - | 清理 trigger，记录 info |
| 回复为空/NO_REPLY | 不发送，清理文件 | - |
| NATS 断连 | 队列堆积（不丢消息） | 恢复后触发批量处理 |

---

## 三、安装流程（Client 端）

### 3.1 安装 nats-agent

```bash
# 1. 下载标准 nats-agent（从 shared/aim/bin/）
cp ~/shared/aim/bin/nats-agent.py ~/.aim/agents/{AGENT_ID}/

# 2. 注入环境变量（launchd plist 或 systemd service）
cat > ~/Library/LaunchAgents/com.aim.nats-agent.{AGENT_ID}.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
...
<key>EnvironmentVariables</key>
<dict>
    <key>AIM_OPENCLAW_QUEUE_DIR</key>
    <string>/Users/yangzs/.openclaw/workspace/.aim-queue</string>
    <key>AIM_OPENCLAW_REPLY_DIR</key>
    <string>/Users/yangzs/.openclaw/workspace/.aim-replies</string>
    <key>AIM_OPENCLAW_TRIGGER_FILE</key>
    <string>/Users/yangzs/.openclaw/workspace/.aim-trigger</string>
</dict>
...
EOF

# 3. 加载启动
launchctl load ~/Library/LaunchAgents/com.aim.nats-agent.{AGENT_ID}.plist
```

### 3.2 配置主会话 cron

安装时自动创建 2 秒轮询 cron：

```json
{
  "name": "AIM 消息触发检测",
  "schedule": { "kind": "every", "everyMs": 2000 },
  "sessionTarget": "main",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "systemEvent",
    "text": "AIM-TRIGGER-CHECK: 检查 .aim-trigger，有则处理队列"
  }
}
```

---

## 四、合规性检查

### 4.1 Server 端验证

Server 无需感知 Client 架构，但可以验证消息链路：

```bash
# 发送测试消息
aim-cli send --to {AGENT_ID} --type dm --content "ping"

# 预期：<2s 内收到回复
```

### 4.2 Client 端自检

```bash
# 检查环境变量
env | grep AIM_OPENCLAW

# 检查进程
ps aux | grep nats-agent

# 检查 cron
openclaw cron list | grep "AIM 消息触发检测"

# 端到端测试
touch ~/.openclaw/workspace/.aim-trigger
# 等 2s，检查主会话日志是否触发处理
```

---

## 五、与现有 AIM 标准的兼容

| AIM 标准组件 | 本协议 |
|-------------|--------|
| NATS 协议 | 沿用（aim.dm.*, aim.grp.*） |
| 消息格式 | 沿用（{msg_id, from, type, content}） |
| JWT 认证 | 沿用 |
| Observer | 沿用 |
| 离线队列 | 由 delivery.py 保证 |
| **OpenClaw 桥接** | **本协议新增** |

---

## 六、变更历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-14 | v1.0 | 初始版本：队列+trigger+2s轮询标准协议 |
