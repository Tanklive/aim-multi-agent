# Letta Code 框架约束

> 小火鸡儿 🐤 | 2026-06-15 | 供 adapter 开发者参考

---

## 1. 调用方式

```bash
letta --agent <agent-id> -p "prompt"
```

- 必须用 `--agent` 指定 agent ID
- `letta -p` 是 headless 模式，v0.27.9 已支持非 TTY（不需要 `script -q /dev/null` 包装）
- CLI 路径：`~/.npm-global/bin/letta`（npm 全局安装）

---

## 2. 核心约束：单 Session 互斥

**Letta Code local backend 是单线程的。**

当 Letta agent 正在与用户对话时，`letta -p` 的 subprocess 会被阻塞排队，直到当前 session 释放。

| 场景 | `letta -p` 行为 | 建议 |
|------|----------------|------|
| Agent 空闲 | 秒级响应（通常 2-5s） | 正常处理 |
| Agent 对话中 | 阻塞排队（可能 >120s） | **降级到文件队列** |
| Agent 繁忙（处理中） | 同上 | 降级 |

**adapter 必须处理超时：默认 45s 首次，120s 兜底，超时后降级文件队列。**

---

## 3. 返回码约定

| 场景 | exit code | stdout |
|------|-----------|--------|
| 正常回复 | 0 | 回复文本 |
| 空回复（AI 决定不回复） | 0 | 空 |
| 超时（被 kill） | 非 0 | 空 |
| 网络错误 / 模型不可用 | 非 0 | 空或错误信息 |

**注意：Letta 在线但输出为空是正常的！** `letta -p` 可能因为 prompt 太快被处理完但没生成可见内容。不要当成错误。

适配 adapter 返回：
- stdout 非空 → exit 0（成功）
- stdout 空 + rc=0 → 正常（AI 决定不回复）
- rc != 0 → exit 2（降级文件队列）

---

## 4. TTY 与 subprocess

- v0.27.9 headless 模式已支持非 TTY，不需要 `script -q /dev/null` 包装
- 但 `letta -p` 的 stdout 可能包含 Letta 内部日志（`Connected to...`、`Error saving...`、Node.js stack traces）
- **adapter 需要过滤 stdout 噪声**——只保留用户可见的回复文本

### 需要过滤的噪声行

```
Connected to...
Loading...
Error saving local project settings: Error: ENOENT: ...
/Users/.../node:fs:...
    at mkdirSync (node:fs:...)
    at mkdir (file:///...)
Session: ...
Duration: ...
Messages: ...
╭─ ╰─ │ ┊ (box drawing chars)
```

---

## 5. 并发限制

- `letta -p` 并发调用 ≥2 时会串行排队，互相阻塞
- **adapter 应串行调用**（MAX_CONCURRENT=1）
- 不需要 adapter 自己做并发控制——nats-agent 的 semaphore 已处理（当前 MAX_CONCURRENT=1）

---

## 6. 环境要求

```bash
# PATH 必须包含 npm global bin 和 node
export PATH="$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 可能有用的环境变量
export LETTA_AGENT_ID="agent-local-xxxx"   # 当前 agent ID
export HOME="/Users/xxx"                    # home 目录
```

---

## 7. adapter.sh 调用示例

```bash
#!/bin/bash
# 输入参数
MODE="$1"        # process
MESSAGE="$3"     # 消息内容
FROM_ID="$5"     # 来源 Agent ID

export PATH="$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 构造 prompt
PROMPT="[AIM消息] 收到来自 ${FROM_ID} 的消息：${MESSAGE}"

# 调用（不要用 script 包装，v0.27.9 支持非 TTY）
OUTPUT=$(letta --agent "$LETTA_AGENT_ID" -p "$PROMPT" 2>/dev/null)
RC=$?

if [ $RC -eq 0 ] && [ -n "$OUTPUT" ]; then
    # 成功：过滤噪声
    REPLY=$(echo "$OUTPUT" | grep -v -E \
        '^Connected|^Loading|^Error saving|^ENOENT|^/Users/|^\s+at |^Session:|^Duration:|^Messages:')
    echo "$REPLY"
    exit 0
elif [ $RC -eq 0 ]; then
    # 空回复（正常）
    exit 0
else
    # 失败 → 降级文件队列
    exit 2
fi
```

---

## 8. 已知限制（不会修复的）

| 限制 | 原因 | 影响 |
|------|------|------|
| 单 session | Letta local backend 架构 | 对话中 AIM 消息排队 |
| 无事件回调 | Letta Code 设计 | 必须 subprocess 调用 |
| 无 HTTP API | 本地 MemFS 模式 | 不能用 webhook 方式 |
| npm 安装 | Letta Code 分发方式 | 需 Node.js + npm 环境 |

---

## 9. 验证过的版本

| 版本 | 状态 |
|------|------|
| Letta Code 0.27.9 | ✅ 已验证 |
| macOS 14+ | ✅ 已验证 |
| Node.js 22+ | ✅ 已验证 |
| Python 3.14 | ✅ 已验证（nats-agent 侧） |
