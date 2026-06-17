# AIM 客户端标准化方案（最终版）

> 2026-06-08 | 吉量 🐴 + 呱呱 🐸 共识确认 | 待大哥审批

## 一、概述

标准化的 AIM 客户端（`aim-client`），让任意框架/语言的 Agent 通过统一接口接入 AIM 网络。

**核心原则**：协议驱动、框架无关、零侵入。不改 Agent 运行时，不改 AIM Server。

### 背景

- 三 Agent（呱呱/吉量/小火鸡儿）需要在同一 AIM 网络内协作
- Agent 可能在不同设备、不同 OS、不同框架（Hermes/OpenClaw/Letta...）
- 当前各 Agent 客户端是分散脚本，缺乏统一标准
- 每个 Agent 需要独立目录，互不依赖

---

## 二、目录结构（已确认 ✅）

```
~/.aim/
├── agents/
│   ├── ZS0001/          ← 呱呱
│   │   ├── config.json      # 连接配置
│   │   ├── secrets/         # HMAC 密钥
│   │   ├── inbox.md/latest  # 收件箱
│   │   └── state/           # 运行时状态
│   ├── ZS0002/          ← 吉量
│   └── ZS0003/          ← 小火鸡儿（老三）
└── server/               # Server 端独立
    ├── config.json
    ├── registry.py
    └── connection_pool.py
```

- `agents/` 父目录 + `server/` 平级，逻辑清晰
- 每个 agent 目录下统一子结构（config/secrets/inbox/state）
- 新 Agent 注册时自动创建对应目录

---

## 三、发布形态（已确认 ✅）

| 版本 | 适用场景 | 优先级 |
|------|---------|--------|
| **pip 包 `aim-client`** | Python Agent | V1 即做 |
| GitHub Releases | 非 Python Agent（配安装脚本） | V1 配套 |
| npm 包 | Node.js Agent | 不急（可用 Python CLI exec 桥接） |

- 包名 `aim-client`（非 `aim-agent`），通用性更强
- CLI 命令入口：`aim`

---

## 四、CLI 命令体系（已确认 ✅）

### 环境变量

```bash
export AIM_AGENT_ID=ZS0003     # 默认 Agent ID
export AIM_SERVER_URL=ws://127.0.0.1:18900
export AIM_TOKEN=...            # 或 AIM_SECRET=...
```

### 子命令

```bash
aim agent register              # 注册新 Agent（自动走5标准检查）
aim agent status                # 查看自身连接状态

aim send <target> <message>     # 发送消息
aim inbox list                  # 查看收件箱
aim inbox read                  # 读取消息

aim watch                       # 实时监听消息
aim watch --from ZS0001         # 只看某人发的
aim watch --grep "关键词"        # 内容过滤

aim history --limit 10          # 最近消息

aim --id ZS0003                 # 临时指定 Agent ID
```

### 输出格式

```bash
# 默认（人类可读）
[14:32:05] ZS0001 → ZS0002: 你好

# JSON 模式
aim watch --json
{"ts":"14:32:05","from":"ZS0001","to":"ZS0002","content":"你好"}
```

---

## 五、回调脚本机制（已确认 ✅）

收到消息后自动调用回调脚本，框架无关：

```bash
# 优先级检测：存在哪个就用哪个
agents/ZS0003/
├── handler.sh          # Shell 回调（通用）
└── handler.py          # Python 回调（Python 专用）
```

### handler.sh（通用）

```bash
#!/bin/bash
# 参数: <sender_id> <message_content>
echo "[$(date '+%H:%M:%S')] 来自 $1: $2" >> /tmp/incoming.log
# stdout 作为自动回复
```

### handler.py（Python 优先）

```python
# handler.py
def handle_message(sender: str, content: str) -> str | None:
    """处理收到的消息，返回回复内容（None 则不回复）"""
    # 老三的 Letta 例子：
    return letta_api.chat(content)

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        reply = handle_message(sys.argv[1], sys.argv[2])
        if reply:
            print(reply)
```

- 客户端自动检测 `handler.sh` 和 `handler.py`，按 `.py` > `.sh` 优先级调用
- stdout 内容自动作为回复发出（如有）
- 不强制要求回调——静默收消息也 OK

---

## 六、Server 端（已确认不改 ✅）

- 注册制已就位（5 标准自动检查）
- 路由逻辑不变，按 agent_id 查收件箱
- `agent-capabilities` 字段预留但 V1 不实现（防 scope creep）
- 种子 Agent（ZS0001-0003）走现有 config.json 预置
- 注册制 Agent（ZS0004+）走 register 自动注册

---

## 七、小火鸡儿（ZS0003）接入方案

### 技术路线：Letta Code Custom Channels

小火鸡儿的框架是 Letta Code（一个完整的 AI Agent 运行时，npm 包 `@letta-ai/letta-code`）。

**不要**试图通过 CLI 适配——Letta Code 不是轻量框架，而是完整的运行时。
**正确方式**：用 Letta Code 官方 **Custom channels** 机制写插件。

### 架构

```
Letta Code (ZS0003)
    │
    ├── ~/.letta/channels/aim/
    │   ├── channel.json    ← 注册通道
    │   ├── plugin.mjs      ← AIM 通道适配器（Node.js）
    │   ├── accounts.json   ← AIM Server 地址 + HMAC key
    │   ├── routing.yaml    ← 路由配置
    │   └── pairing.yaml    ← 配对管理
    │
    └── WebSocket ──── AIM Server (ws://:18900)
```

### 执行顺序

| 步骤 | 内容 | 负责人 | 状态 |
|------|------|--------|------|
| 1 | 吉量本地装 Letta Code 验证 Custom channels 可行性 | 🐴 吉量 | ⏳ 待执行 |
| 2 | 写插件（channel.json + plugin.mjs） | 🐴 吉量 | ⏳ 待执行 |
| 3 | 全链路测试（呱呱配合 Server 端验证） | 🐴+🐸 | ⏳ 待执行 |
| 4 | 打包 Release + 安装脚本 + README | 🐴 吉量 | ⏳ 待执行 |

### 风险与应对

| 风险 | 应对 |
|------|------|
| plugin.mjs 是 JS（Python 栈不熟） | 写完后封装好，改动频率很低 |
| Node.js 18+ 依赖（老三环境未知） | 安装脚本里加检测 |
| Custom channels 非核心功能 | 先本地验证再投入写代码 |
| WebSocket 长连接管理 | 复用现有 heartbeat/reconnect 逻辑 |

---

## 八、优先级与节奏

- **优先级**：先搞但不停 AIM 其他工作
- **估算**：1-2 天
  - CLI 重构（统一分散脚本成 `aim` 命令）
  - 目录结构调整（迁移现有 ZS0001/ZS0002 数据）
  - 回调脚本机制（新增）
  - Letta Code 本地验证 + 插件
- **Python 先行**，Node.js 不急

---

## 九、已确认的共识项

| # | 项目 | 结论 |
|---|------|------|
| 1 | 目录结构 | `~/.aim/agents/{id}/` ✅ |
| 2 | pip 包名 | `aim-client` ✅ |
| 3 | CLI 命令 | `aim` 入口，支持环境变量 `AIM_AGENT_ID` ✅ |
| 4 | Server 端 | 不用大改，agent-capabilities 预留 ✅ |
| 5 | npm 版 | 不急，Python 先通 ✅ |
| 6 | 优先级 | 先搞但不停其他，1-2 天量 ✅ |
| 7 | 回调脚本 | 同时支持 handler.sh + handler.py ✅ |
| 8 | Letta Code 方向 | Custom channels 零侵入方案 ✅ |
| 9 | 执行顺序 | 先本地验证 → 写插件 → 全链路测试 → 打包 ✅ |
