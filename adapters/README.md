# AIM 框架适配器接口规范 v1.0

> 版本: v1.0 | 日期: 2026-06-15 | 作者: 吉量 🐴
> 本文件定义 adapter 的标准接口，所有框架（Hermes / OpenClaw / Letta）共用。

---

## 1. 架构总览

```
NATS ──→ nats-agent-v3 ──→ call_adapter() ──→ adapter.sh ──→ 框架 AI 处理
  ↑                                                                    ↓
  └──────────────────── 同一个 NATS 主题 ─────────────────────────┘
```

nats-agent-v3 通过 `call_adapter()` 模块调对应框架的 `adapter.sh`，
adapter.sh 统一 exit code 约定，不再依赖文件队列中转。

---

## 2. adapter 标准接口

### 2.1 调用方式

```bash
# 入参
adapter.sh process --message "<消息内容>" --from "<发送方 Agent ID>"

# stdout: 回复内容（仅 exit 0 时有效）
# stderr: 日志/调试信息（可选）
```

### 2.2 退出码约定

| 退出码 | 含义 | nats-agent 行为 |
|--------|------|----------------|
| **0** | 正常，回复内容在 stdout | 立即 NATS 发回 |
| **1** | 可重试错误（框架忙、临时超时） | 最多重试 3 次，每次间隔 5s |
| **2** | 降级到文件队列（框架暂时不可用，如 Letta session 互斥） | 写入文件队列，等空闲消费 |
| **3** | 需人工介入（框架挂了 / 配置错误 / 权限不足） | **不重试**，通知大哥 |
| 其他 | 未知错误 | 同 exit 1（可重试） |

### 2.3 超时

- 默认超时：**120 秒**
- 可在 config.json 的 `adapter_timeout` 字段中覆盖
- 超时后 nats-agent 自动走 exit 1 逻辑（重试或降级）

### 2.4 输入内容约定

- 消息内容为纯文本（UTF-8）
- 不传二进制内容
- `--from` 参数为发送方的 Agent ID（如 `ZS0001`）

---

## 3. config.json 新增字段

```json
{
  "framework": "openclaw",
  "adapter_cmd": "~/.aim/adapters/openclaw/adapter.sh",
  "adapter_timeout": 120,
  "nats_url": "nats://127.0.0.1:4222"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `framework` | string | ✅ | `openclaw` / `hermes` / `letta` |
| `adapter_cmd` | string | ✅ | adapter.sh 的绝对路径 |
| `adapter_timeout` | int | 否 | 默认 120，单位秒 |
| `nats_url` | string | ✅ | NATS 服务器地址 |

---

## 4. 各框架 adapter 实现要求

### 4.1 Hermes — `adapters/hermes/adapter.sh`

```bash
# 核心调用
timeout $TIMEOUT hermes chat -q "$MESSAGE" -Q
# 注意：-Q 静默模式，只输出回复文本
```

约束见 `adapters/hermes/constraints.md`

### 4.2 OpenClaw — `adapters/openclaw/adapter.sh`

```bash
# 核心调用
timeout $TIMEOUT openclaw agent -m "$MESSAGE" --json
```

约束见 `adapters/openclaw/constraints.md`

### 4.3 Letta — `adapters/letta/adapter.sh`

```bash
# 核心调用
timeout $TIMEOUT letta -p "$MESSAGE" 2>/dev/null
```

约束见 `adapters/letta/constraints.md`（★小火鸡儿优先出）

---

## 5. 安全要求

1. **超时保护** — 所有 adapter 必须用 `timeout` 包裹框架调用，防止死锁
2. **stdout 隔离** — adapter.sh 的 stdout 只输出回复内容，日志走 stderr
3. **不回传敏感信息** — 配置、密钥等不允许出现在 stdout/stderr 中
4. **降级不丢消息** — exit 2 时 nats-agent 确保消息写入文件队列

---

## 6. 各 adapter 文件清单

```
shared/aim/adapters/
├── README.md                       ← 本文件（接口规范）
├── DEGRADE.md                      ← 降级策略（呱呱+吉量合写）
├── hermes/
│   ├── adapter.sh                  ← Hermes 适配器
│   └── constraints.md              ← Hermes 约束
├── openclaw/
│   ├── adapter.sh                  ← OpenClaw 适配器
│   └── constraints.md              ← OpenClaw 约束
└── letta/
    ├── adapter.sh                  ← Letta 适配器
    └── constraints.md              ← Letta 约束 ★小火鸡儿优先出
```

---

## 7. 变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-06-15 | 初版，定义 adapter 接口规范 |
