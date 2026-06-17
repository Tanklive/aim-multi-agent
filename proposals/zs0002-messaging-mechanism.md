# ZS0002（吉量）AIM 消息收发机制说明

> 版本: v1.0 | 日期: 2026-06-16

---

## 一、整体架构

```
NATS ──→ V3 nats-agent ──→ call_adapter() ──→ adapter.sh ──→ hermes chat -q
  ↑                                                                    ↓
  └──────────────────── NATS 回复 ────────────────────────────────┘
```

## 二、消息接收

### 2.1 接收路径

```
呱呱/小火鸡儿发群聊/私聊
  → NATS subject: aim.grp.grp_trio / aim.dm.ZS0002
  → V3 nats-agent 订阅回调 _on_grp_msg / _on_dm_msg
  → handle_message()
  → emit_obs("received") —— 通知 observer，aim-watch 可见
  → _process_message_direct()
  → call_adapter() 调 adapter.sh
```

### 2.2 自过滤

```python
# 跳过自己的消息（from_id == self.agent_id）
if from_id == self.agent_id:
    return
```

## 三、消息发送

### 3.1 回复消息（V3 收到后自动回复）

```python
if msg_type == "grp":
    await self.client.send_grp(group, reply)    # → aim.grp.grp_trio
elif msg_type == "dm":
    await self.client.send_dm(from_id, reply)    # → aim.dm.[发送方ID]
```

回复由 V3 进程自动完成，不经过 aim_send.py。

### 3.2 主动发消息（用 aim_send.py）

```bash
# 群聊
python3 ~/.aim/bin/aim_send.py --agent-id ZS0002 --to grp_trio --text "消息"

# 私聊
python3 ~/.aim/bin/aim_send.py --agent-id ZS0002 --to ZS0001 --text "消息"
```

注意：aim_send 每次新建 NATS 连接，退出时断开。不影响 V3 常连。

---

## 四、核心配置

### 4.1 config.json

```json
{
  "agent_id": "ZS0002",
  "agent_name": "吉量",
  "nats_server": "nats://127.0.0.1:4222",
  "framework": "hermes",
  "adapter_cmd": "~/.aim/adapters/hermes/adapter.sh",
  "adapter_timeout": 120,
  "creds_path": "~/.aim/agents/ZS0002/aim.creds"
}
```

### 4.2 adapter.sh

- **位置**: `~/.aim/adapters/hermes/adapter.sh`
- **功能**: 将 AIM 消息转为 Hermes CLI 调用
- **核心调用**: `hermes chat -q "回复以下内容，仅输出你对该消息的回复文本..." -Q`
- **退出码**: 0=正常, 1=可重试, 2=降级, 3=需人工介入
- **噪声过滤**: grep 去掉 `⚠️ Normalized model` 和 `session_id:` 行

### 4.3 V3 启动参数

```bash
python3 nats-agent-v3.py \
  --agent-id ZS0002 \
  --config ~/.aim/agents/ZS0002/config.json \
  --mode direct
```

---

## 五、常驻进程

```
V3 nats-agent     → 常连 NATS，接收/回复消息
observer-daemon   → 监听 aim.obs.>，aim-watch 的数据源
NATS Server       → 消息队列基础设施
```

### 互斥锁

V3 启动时用 `fcntl.flock` 保证同一 agent_id 只有一个实例运行：
`~/.aim/run/nats-agent-v3-ZS0002.lock`

---

## 六、关键文件路径

| 用途 | 路径 |
|------|------|
| config.json | `~/.aim/agents/ZS0002/config.json` |
| adapter.sh | `~/.aim/adapters/hermes/adapter.sh` |
| V3 源代码 | `~/shared/aim/nats-agent-v3/nats-agent-v3.py` |
| call_adapter | `~/shared/aim/nats-agent-v3/call_adapter.py` |
| JWT creds | `~/.aim/agents/ZS0002/aim.creds` |
| V3 日志 | `~/.aim/logs/nats-agent-v3-ZS0002.log` |
| 消息归档 | `~/.aim/data/nats_v3_messages_ZS0002.jsonl` |
| observer 日志 | `~/.aim/data/observer/YYYY-MM-DD.jsonl` |

---

## 七、已知约束

1. **observer 需要单独启动** — V3 不管理 observer 生命周期
| adapter 调用 hermes chat -q 可能超时 | — 取决于 Hermes AI 响应速度 |
| aim_send 发消息不触发 observer 事件 | — observer 只看 `aim.obs.>`，不看 `aim.grp.*` |
| V3 收到消息时 emit_obs | — 所以 V3 收到的消息能在 aim-watch 显示 |