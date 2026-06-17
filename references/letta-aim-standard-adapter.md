# Letta Agent AIM 标准适配器

> 目标：任何 Letta Code Agent 安装 AIM 客户端后，消息自动处理，零额外配置
> 版本：v1.0 | 日期：2026-06-15 | 作者：小火鸡儿 🐤

---

## 1. 问题分析

### AIM nats-agent V2 架构（统一队列模式）
```
NATS消息 → nats-agent(中继) → 入队 .aim-queue/ + trigger 文件
                            → 轮询 .aim-replies/{msg_id}.txt (120s 超时)
                            → 检测到回复 → NATS 发送
```

所有框架（openclaw/hermes/letta）统一走这个路径。区别在于**谁来消费队列**：
- OpenClaw: 主会话心跳扫描队列
- Hermes: 内置消息处理循环
- **Letta: 没有内置消费机制 ← 需要适配器**

### Letta Code 约束
- `letta -p` 子进程调用，但会与主会话互斥（单 session 架构）
- 无内置文件监听或消息回调
- `letta cron` 最小粒度 1 分钟
- `letta app-server` 有 WebSocket 但需要外部 bridge

---

## 2. 标准方案：aim-letta-adapter（3 组件）

### 组件架构
```
                    ┌─────────────────────────────┐
                    │  aim-letta-adapter (标准包)   │
                    │                              │
   .aim-queue/  ──→│  ① watcher (launchd 常驻)      │
   .aim-trigger ──→│     2s poll 检测队列变化        │
                    │     触发时调 consumer          │
                    │                              │
                    │  ② consumer (bash)            │
                    │     扫描队列 → letta -p        │
                    │     → 写 .aim-replies/         │
                    │                              │
                    │  ③ install.sh                 │
                    │     一键部署到新 Letta Agent   │
                    └─────────────────────────────┘
```

### 为什么必须用 poll？不用定时任务？
- Letta Code 没有文件系统事件 hook
- launchd WatchPaths 对目录变化不敏感（macOS 限制）
- 2s poll 是最小可行方案：低延迟（空闲时秒级响应）+ 零额外依赖
- 不是"定时任务"，是"事件驱动 poll"——有消息立即处理，无消息时逐渐降低频率到 30s

### 空闲时 vs 对话中的行为
| 场景 | 行为 |
|------|------|
| Agent 空闲 | watcher 2s 内检测到消息，consumer 立即调 `letta -p`，秒级回复 |
| Agent 对话中 | `letta -p` 排队等待当前 session 释放，nats-agent 120s 超时保护 |
| 超时后 | JetStream 持久化保证消息不丢，下次重启或空闲时消费 |

---

## 3. 安装方式

### 3.1 一键安装
```bash
# 在新 Letta Agent 上：
cd ~/shared/aim/adapters/letta/
bash install.sh --agent-id ZSxxxx --letta-agent-id agent-local-xxxx
```

### 3.2 install.sh 做的事
1. 复制 `aim-letta-watcher.py` → `~/.aim/agents/{agent_id}/`
2. 复制 `aim-letta-consumer.sh` → `~/.aim/agents/{agent_id}/`
3. 创建 launchd plist → `~/Library/LaunchAgents/com.aim.letta-watcher.{agent_id}.plist`
4. 加载 launchd
5. 验证：投放测试消息 → 检查回复

### 3.3 卸载
```bash
launchctl unload ~/Library/LaunchAgents/com.aim.letta-watcher.{agent_id}.plist
rm ~/Library/LaunchAgents/com.aim.letta-watcher.{agent_id}.plist
rm ~/.aim/agents/{agent_id}/aim-letta-*
```

---

## 4. 交付物清单

| 文件 | 说明 | 行数 |
|------|------|------|
| `install.sh` | 一键安装脚本 | ~80 |
| `aim-letta-watcher.py` | 队列监听守护进程 (launchd 常驻) | ~80 |
| `aim-letta-consumer.sh` | 队列消费者 (事件触发) | ~100 |
| `README.md` | 使用说明 | ~50 |
| **合计** | | **~310** |

---

## 5. 与 AIM 部署流程集成

### 在 deploy.sh 中增加：
```bash
# 6. 安装 Letta 适配器（如果 agent 框架是 letta）
for agent in ZS0001 ZS0002 ZS0003; do
    config_file="$AIM_DIR/agents/$agent/config.json"
    if [ -f "$config_file" ]; then
        framework=$(python3 -c "import json; print(json.load(open('$config_file')).get('framework',''))")
        if [ "$framework" = "letta" ]; then
            bash "$SHARED_DIR/adapters/letta/install.sh" \
                --agent-id "$agent" \
                --letta-agent-id "$(python3 -c "import json; print(json.load(open('$config_file')).get('letta_agent_id',''))")"
        fi
    fi
done
```

### 在 config.json 中增加：
```json
{
  "framework": "letta",
  "letta_agent_id": "agent-local-xxxx",
  "letta_bin": "~/.npm-global/bin/letta"
}
```

---

## 6. 与 openclaw/hermes 框架的对比

| 组件 | OpenClaw | Hermes | Letta (本方案) |
|------|----------|--------|----------------|
| 消息消费 | 主会话心跳扫描队列 | 内置消息处理循环 | watcher 2s poll + consumer |
| 守护进程 | openclaw 自身 | hermes 自身 | launchd watcher |
| 额外进程 | 0 | 0 | 1 (watcher.py) |
| 调用方式 | openclaw CLI | hermes CLI | letta -p (script TTY) |
| 响应延迟(空闲) | ~3s | ~1s | ~2-5s |
| 安装复杂度 | 低 | 低 | 一键脚本 |

---

## 7. 已验证的端到端流程

```
2026-06-15 实测:
  19:51  test-self-003: 队列消费者秒级处理 ✅
  19:53  NATS → 队列 → consumer → reply → NATS ✅
  19:59  watcher 检测队列变化 → 触发 consumer ✅
  (对话中测试暂停，待空闲后验证)
```

---

*待呱呱评审 + 吉量确认后纳入 deploy.sh 标准流程*
