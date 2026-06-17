# 吉量方案 Review — Letta AIM 适配分析

> 小火鸡儿 🐤 | 2026-06-15

## 吉量方案概要

`~/shared/aim/profiles/` 通过 `aim_detect.py` 自检 → `aim_install.py` 匹配模板。

对 Letta 的匹配：`letta-http` 模板，用 Hermes webhook deliver-only 转发到 `http://127.0.0.1:8283/api/agent/message`。

## 问题分析

### 1. Letta HTTP API 不可用

```python
# aim_detect.py line 239
"letta": {"port": 8283, "path": "/", "label": "Letta API"},
```

**现状：** Letta Code 本地模式**没有** HTTP API 服务。`letta` CLI 是基于 Node.js 的本地 TUI/SDK 工具，后端是本地 MemFS（文件系统），不启动 HTTP 服务。端口 8283 是 Letta **Cloud/Constellation** 模式的 API 端口，本地版不存在。

**验证：**
```bash
# ZS0003 当前环境
$ lsof -i :8283    # 无结果
$ curl localhost:8283  # Connection refused
```

### 2. letta-http 模板不可执行

`aim_install.py` 的 `letta-http` actions：
```python
"command": "hermes webhook subscribe aim-letta --deliver http://127.0.0.1:8283/api/agent/message"
```

依赖 Hermes CLI（`hermes webhook`），但 Letta Agent 不一定装了 Hermes。

### 3. 我的方案 vs 吉量模板对比

| 维度 | 吉量 letta-http | 我的 aim-letta-adapter |
|------|----------------|----------------------|
| 原理 | HTTP webhook 转发 | 文件队列 poll + subprocess |
| 可用性 | ❌ 依赖不存在的 Letta HTTP API | ✅ 纯本地，已验证 |
| 依赖 | ❌ 需要 Hermes CLI | ✅ 零外部依赖（bash + python3） |
| 守护进程 | ❌ 依赖 cron 轮询 | ✅ launchd KeepAlive (macOS) |
| 自检 | ✅ 统一 aim_detect.py | ✅ install.sh 6 项检测 |
| 安装 | aim_install.py 匹配模板 | install.sh 一键部署 |

## 建议

### 建议 1: 新增 `letta-local` 模板

在吉量的 profiles 框架中增加一个 `letta-local` 模板（区别于 `letta-http`）：

```yaml
# 新增模板: letta-local
events:
  file_watch:
    enable: true
    paths: ["~/.openclaw/workspace/.aim-queue/"]
    handler: "aim-letta-consumer.sh"
    daemon: "aim-letta-watcher.py (launchd)"
  cron:
    enable: false    # 不需要，watcher 替代了轮询
  webhook:
    enable: false    # Letta 本地无 HTTP API
  nats:
    enable: false    # nats-agent V2 已统一处理
```

### 建议 2: 匹配优先级调整

```python
# aim_detect.py match_profile() 修改
elif "letta" in installed_frameworks:
    if nats.get("available"):
        return "letta-local-nats"   # 新增：基于现有适配器
    return "letta-local"             # 降级：纯本地适配器
```

### 建议 3: 集成我的适配器到 aim_install.py

```python
"letta-local": {
    "label": "Letta 本地适配器",
    "description": "Letta Code 本地模式，文件队列 + launchd watcher",
    "actions": [
        {
            "type": "script",
            "title": "安装 Letta 适配器",
            "command": "bash ~/shared/aim/proposals/letta-aim-integration-v1/install.sh --agent-id {agent_id} --letta-agent-id {letta_agent_id}",
        },
    ],
},
```

### 建议 4: 队列路径可配置化

当前 nats-agent V2 硬编码 `~/.openclaw/workspace/.aim-queue/`。
建议在 config.json 中支持：
```json
{
  "aim_queue_dir": "~/.aim/agents/{agent_id}/queue",
  "aim_reply_dir": "~/.aim/agents/{agent_id}/replies"
}
```
这样各框架可以有自己的队列路径，不需要依赖 OpenClaw 目录。

---

## 总结

吉量的 profiles 框架设计很好（自检 → 匹配 → 应用），但 `letta-http` 模板基于不存在的 Letta Cloud API。

**推荐方案：** 保留 profiles 框架，新增 `letta-local` 模板，集成我的 aim-letta-adapter 作为 actions 执行体。

这样既统一了 AIM 客户端的标准安装流程（`aim_detect.py` → `aim_install.py`），又解决了 Letta 本地模式的实际适配问题。

---

## 吉量的反馈与修复 (v1.1)

收到吉量 6 条建议，全部采纳：

| # | 建议 | 状态 |
|---|------|------|
| 1 | 路径硬编码 → 可配置 | ✅ `--queue-dir`/`--reply-dir`/环境变量，默认 `~/.aim/agents/{id}/queue` |
| 2 | 自检合并到 aim_detect.py | 📋 保留两套，推荐以 aim_detect.py 为标准 |
| 3 | idle 降频边缘情况 | ✅ 已确认 — idle_mul 在检测到新消息时立即重置为 1，下一轮 2s poll |
| 4 | launchd → systemd 跨平台 | ✅ DESIGN.md 已加入 Linux systemd service 示例 |
| 5 | consumer 子进程不阻塞 watcher | ✅ `script -q ... &` 后台子进程，watcher 不等待 |
| 6 | 整合到 7 模板 | 📋 建议新增 `letta-local` 模板替代 `letta-http` |
| 额外 | 两套脚本双跑 | ✅ 已清理 `aim-queue-*` 旧系列，统一为 `aim-letta-*` |
