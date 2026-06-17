# AIM 部署模式配置模板

## 模板匹配规则

检测结果 → 匹配函数 `match_profile()` → 返回模板名。

### 匹配优先级（v2 — 2026-06-15 修订）

```
指定目标 Agent framework → 优先精准匹配 → 否则按机器检测的框架匹配
```

| 条件 | 模板 | 场景 |
|------|------|------|
| target=letta | `letta-local` | Letta 本地模式，poll 队列 + launchd watcher |
| target=hermes + NATS 可用 | `hermes-nats` | Hermes + NATS 长连 |
| target=hermes | `hermes` | Hermes 纯 webhook + cron |
| target=openclaw + NATS 可用 | `openclaw-nats` | OpenClaw + NATS 长连 |
| target=openclaw | `openclaw-poller` | OpenClaw 轮询 |
| 自动检测 hermes | `hermes-nats` 或 `hermes` |
| 自动检测 openclaw | `openclaw-nats` 或 `openclaw-poller` |
| 自动检测 letta | `letta-local` | （不依赖 HTTP API） |
| 有 CLI 但框架未知 | `generic-cli` |
| 有 HTTP 服务 | `generic-http` |
| 兜底 | `minimal` |

## 各模板的事件驱动配置

### 模板: hermes-nats

```yaml
events:
  nats:
    enable: true
    url: "nats://127.0.0.1:4222"
    agent_id: "agent-XX"
    subscriptions:
      - "aim.dm.<agent_id>"
      - "aim.grp.*"
  webhook:
    enable: true
    route: "aim-inbound"
    prompt: "AIM 消息来自 {from}: {content}"
    skills: ["aim-message-handler"]
  cron:
    enable: false   # Hermes+NATS 不需要轮询
  file_watch:
    enable: false
```

### 模板: hermes (纯 webhook + cron)

```yaml
events:
  webhook:
    enable: true
    route: "aim-inbound"
    prompt: "AIM 消息来自 {from}: {content}"
    skills: ["aim-message-handler"]
  cron:
    enable: true
    schedule: "* * * * *"       # 每分钟检查
    handler: "hermes chat -q -Q '检查 AIM 队列消息'"
  nats:
    enable: false
  file_watch:
    enable: false
```

### 模板: openclaw-nats

```yaml
events:
  nats:
    enable: true
    url: "nats://127.0.0.1:4222"
    agent_id: "agent-XX"
    subscriptions:
      - "aim.dm.<agent_id>"
      - "aim.grp.*"
  file_watch:
    enable: auto    # 有 fsevents 就开
    paths: ["~/.aim/agents/agent-XX/inbox/"]
  webhook:
    enable: false
  cron:
    enable: false
```

### 模板: openclaw-poller

```yaml
events:
  cron:
    enable: true
    schedule: "*/30 * * * * *"
    handler: "openclaw agent -m '处理 AIM 队列'"
  webhook:
    enable: false
  nats:
    enable: false
  file_watch:
    enable: false
```

### 模板: letta-local

```yaml
# Letta 本地模式 — 基于小火鸡儿已验证的 aim-letta-adapter
# 设计：launchd 常驻 watcher + 2s poll + consumer
# 验证：2026-06-15 实测通过（队列→consumer→letta -p→reply→NATS）

events:
  file_watch:
    enable: true
    mechanism: poll          # Letta 无原生文件 hook，降级到 poll
    poll_interval: 2         # 空闲时 2s，渐降到 30s
    paths: ["~/.aim/agents/{agent_id}/queue/"]
    handler: "aim-letta-consumer.sh"
  cron:
    enable: false            # watcher 常驻，不需要独立 cron
  webhook:
    enable: false            # Letta Code 本地模式无 HTTP 服务
  nats:
    enable: false            # 由独立的 nats-agent 处理

adapter:
  package: aim-letta-adapter
  version: "1.0"
  files:
    - "aim-letta-watcher.py"     # launchd 守护进程
    - "aim-letta-consumer.sh"    # 消费者 (letta -p)
    - "install.sh"               # 一键安装（含自检）
  process_mgr: launchd           # macOS
  # Linux 替代: systemd (com.aim.letta-watcher.service)
```

### 模板: generic-cli (通用 CLI 型)

```yaml
events:
  cron:
    enable: true
    schedule: "*/30 * * * * *"  # 30秒轮询
    handler: "{cli} chat -q '处理 AIM 队列'"
    timeout: 30
  webhook:
    enable: true
    route: "aim-inbound"
  nats:
    enable: false
  file_watch:
    enable: false
```

### 模板: generic-http (通用 HTTP 型)

```yaml
events:
  webhook:
    enable: true
    route: "aim-inbound"
    deliver-only: true
    deliver: "{http_endpoint}"
  cron:
    enable: false
  nats:
    enable: false
  file_watch:
    enable: false
```

### 模板: minimal (兜底)

```yaml
events:
  cron:
    enable: true
    schedule: "*/5 * * * *"
    handler: "{cli} chat -q '检查 AIM 队列'"
  webhook:
    enable: false
  nats:
    enable: false
  file_watch:
    enable: false
```

## 变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1 | 2026-06-15 | 初版，7 模板（含 letta-http） |
| **v2** | **2026-06-15** | **letta-http → letta-local，集成小火鸡儿 poll 方案。新增 target_agent 精准匹配解决多框架共存问题。** |
