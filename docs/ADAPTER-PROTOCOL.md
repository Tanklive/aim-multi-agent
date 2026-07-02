# AIM Adapter Protocol v1.0

> **版本**: v1.0-draft
> **状态**: 三方评审通过 (L1) ✅ | 待大哥终审
> **日期**: 2026-07-02
> **作者**: 呱呱 (ZS0001)
>
> **适用范围**: 所有接入 AIM 的 Agent 运行时适配器 (adapter)
> **协议层级**: L1 — Runtime 适配层 (位于 NATS Transport 之上)

---

## 目录

1. [设计原则](#一设计原则)
2. [通信模型](#二通信模型)
3. [消息格式规范](#三消息格式规范)
4. [字段定义](#四字段定义)
5. [Lifecycle 命令](#五lifecycle-命令)
6. [超时规范](#六超时规范)
7. [退出码约定](#七退出码约定)
8. [错误处理](#八错误处理)
9. [适配器实现检查清单](#九适配器实现检查清单)
10. [参考实现](#十参考实现)
11. [变更历史](#变更历史)

---

## 一、设计原则

### 1.1 适配器定位

```
┌─────────────────────────────────────────┐
│              AIM Core                    │
│  (SessionManager / ContextManager /      │
│   Queue / Scheduler / Dispatch)         │
│                                          │
│  职责:                                     │
│  - 管理 Session 生命周期                  │
│  - 组装上下文 (SOUL + context-card)      │
│  - 消息路由与调度                         │
│  - 超时与重试策略                         │
└──────────────┬──────────────────────────┘
               │ 标准协议 (JSON)
┌──────────────┴──────────────────────────┐
│            Adapter (你的实现)             │
│                                          │
│  唯一职责: 调 LLM 返回回复               │
│                                          │
│  不做的:                                  │
│  - 不管 Session 生命周期                  │
│  - 不读上下文文件                         │
│  - 不拼 prompt                           │
│  - 不定超时策略                           │
└─────────────────────────────────────────┘
```

### 1.2 核心原则

| 原则 | 说明 |
|------|------|
| **薄适配器** | adapter ≤ 30 行核心逻辑，只做"收 JSON → 调 LLM → 吐 JSON" |
| **语言无关** | 任何语言实现 (bash/Python/Go/Node.js)，只要 stdin→stdout JSON |
| **协议先行** | 先定协议标准，再实现适配；新 Agent 接入只需读本协议 |
| **兼容优先** | 不要求外部 Agent 改协议，AIM Bridge 层做翻译 |
| **渐进增强** | 必选字段最小化，扩展字段通过 metadata 传递 |

---

## 二、通信模型

### 2.1 两种模式

| 模式 | 适用场景 | 通信方式 |
|------|---------|---------|
| **CLI pipe** | CLI 启动的 runtime (Letta, OpenClaw CLI) | stdin JSON → stdout JSON |
| **API Server** | 常驻 HTTP 服务 (Hermes API, Dify, Coze) | HTTP POST → JSON response |

### 2.2 CLI 模式

```
AIM Core                               Adapter Process
    │                                       │
    ├─ fork + exec adapter.sh               │
    │                                       │
    ├─ stdin: {"action":"process",...} ───→ │
    │                                       ├─ 调 LLM
    │←── stdout: {"reply":"...","usage":{}} │
    │                                       │
    ├─ waitpid (或 timeout kill)             │
```

**要求**:
- 输入: stdin 一行完整 JSON，以 `\n` 结尾
- 输出: stdout 一行完整 JSON，以 `\n` 结尾
- stderr: 仅用于调试日志，不影响协议输出
- 退出码: 遵循本协议 [退出码约定](#七退出码约定)

### 2.3 API Server 模式

```
AIM Core                               Adapter (HTTP Server)
    │                                       │
    ├─ POST /v1/chat                        │
    │  Body: {"action":"process",...} ───→ │
    │                                       ├─ 调 LLM
    │←── 200 {"reply":"...","usage":{}}     │
    │                                       │
    ├─ GET /v1/health                       │
    │←── 200 {"status":"ok",...}            │
```

**要求**:
- Content-Type: `application/json`
- 超时由 HTTP client 控制，响应内包含 `timeout` 字段
- API Server 自行管理并发，不阻塞

### 2.4 连接管理

| 场景 | Core 行为 |
|------|----------|
| adapter 未响应 | 等待 `timeout` ms 后 kill (CLI) 或 abort (HTTP) |
| adapter 崩溃 | 捕获退出码，根据 [退出码约定](#七退出码约定) 决定重试/降级/告警 |
| 连续失败 ≥3 次 | 标记 DEGRADE，暂停该 adapter 投递 30s |
| adapter 恢复 | health 探活通过后恢复投递 |

---

## 三、消息格式规范

### 3.1 请求 (Core → Adapter)

```json
{
  "version": "1.0",
  "action": "process",
  "session_id": "pool:ZS0002:1",
  "context": "你是呱呱🐸 个人助手...\n[前3条群聊记录]\n项目上下文...",
  "message": "@ZS0002 检查 NATS 连接状态",
  "from": "ZS0002",
  "timeout": 10000,
  "metadata": {
    "trace_id": "abc123def456",
    "priority": "normal",
    "grp_id": "grp_trio"
  }
}
```

### 3.2 响应 (Adapter → Core)

```json
{
  "version": "1.0",
  "reply": "🐸 NATS 连接正常，JetStream stream aim-messages 在线，延迟 <5ms",
  "usage": {
    "input_tokens": 180,
    "output_tokens": 42,
    "total_tokens": 222
  },
  "metadata": {
    "model": "deepseek-v4-pro",
    "latency_ms": 2340
  }
}
```

### 3.3 错误响应

```json
{
  "version": "1.0",
  "reply": null,
  "error": {
    "code": "ADAPTER_TIMEOUT",
    "message": "LLM 推理超时 (10000ms)",
    "details": "deepseek API 未在 10s 内返回"
  }
}
```

---

## 四、字段定义

### 4.1 请求字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `version` | string | ✅ | — | 协议版本，当前 `"1.0"` |
| `action` | string | ✅ | — | Lifecycle 命令，见 [Lifecycle 命令](#五lifecycle-命令) |
| `session_id` | string | ✅ | — | Core 分配的会话标识，格式: `pool:{from_id}:{n}` |
| `context` | string | ✅ | — | Core 组装好的完整上下文，包含人格、项目、历史 |
| `message` | string | ✅ | — | 当前消息内容，可能为空 (如 health 探测) |
| `from` | string | ✅ | — | 消息发送者 ID |
| `timeout` | uint32 | ✅ | — | **毫秒**。Core 下发的期望超时，adapter 尽力保证 |
| `metadata` | object | ❌ | `{}` | 扩展字段，见下方 |

### 4.2 metadata 扩展字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `trace_id` | string | 请求追踪 ID，用于日志关联 |
| `priority` | string | `"normal"` / `"high"` / `"low"` |
| `grp_id` | string | 群聊 ID |
| `dm_id` | string | 私聊 ID (与 grp_id 互斥) |

### 4.3 响应字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | string | ❌ | 协议版本 |
| `reply` | string\|null | ✅ | 回复文本，错误时为 null |
| `usage` | object | ❌ | token 用量统计 |
| `error` | object | ❌ | 错误信息，见 [错误处理](#八错误处理) |
| `metadata` | object | ❌ | 扩展 (model, latency_ms 等) |

### 4.4 usage 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `input_tokens` | uint32 | 输入 token 数 |
| `output_tokens` | uint32 | 输出 token 数 |
| `total_tokens` | uint32 | 总计 |

---

## 五、Lifecycle 命令

### 5.1 命令列表

| action | 用途 | 退出码 | 说明 |
|--------|------|--------|------|
| `health` | 健康检查 | 0=正常, 2=DEGRADE, 3=FATAL | Core 定期探活 |
| `info` | 获取能力信息 | 0 | 返回 adapter 版本、runtime 类型 |
| `process` | 处理消息 | 0=成功, 1=TEMP_FAIL, 2=DEGRADE, 3=FATAL | 核心命令 |
| `cancel` | 取消进行中的任务 | 0=已取消, 2=不支持 | 尽力而为语义 |
| `trim` | 清理/重置会话 | 0=成功 | 释放上下文 |
| `reload` | 热刷新缓存 | 0=成功 | 个性/记忆变更后无需重启 |
| `status` | 运行时指标 | 0=成功 | 返回会话数、队列深度等 |

### 5.2 health

**请求**:
```json
{
  "version": "1.0",
  "action": "health",
  "session_id": "",
  "context": "",
  "message": "",
  "from": "",
  "timeout": 3000,
  "metadata": {}
}
```

**响应**:
```json
{
  "reply": "ok",
  "metadata": {"uptime_ms": 12345678}
}
```

**CLI 模式退出码**: 0=正常, 2=降级(可恢复), 3=致命(需人工介入)

### 5.3 info

**请求**:
```json
{
  "action": "info",
  "session_id": "",
  "context": "",
  "message": "",
  "from": "",
  "timeout": 3000,
  "metadata": {}
}
```

**响应**:
```json
{
  "reply": null,
  "metadata": {
    "adapter_version": "1.5.0",
    "protocol_version": "1.0",
    "runtime": "hermes-agent",
    "runtime_version": "0.18.0",
    "mode": "api_server",
    "supported_actions": ["health","info","process","cancel","trim","reload","status"]
  }
}
```

### 5.4 process (核心)

请求和响应见 [消息格式规范](#三消息格式规范)。

### 5.5 reload

用于 personality/memory 变更后热刷新，无需重启 adapter。

**请求**:
```json
{
  "action": "reload",
  "session_id": "",
  "context": "",
  "message": "",
  "from": "",
  "timeout": 5000,
  "metadata": {}
}
```

**CLI 模式**: 清空上下文缓存，下次 process 重新加载
**API Server 模式**: 触发重新加载 SOUL.md / context-card

### 5.6 status

返回运行时指标。

**响应**:
```json
{
  "reply": null,
  "metadata": {
    "active_sessions": 3,
    "total_requests": 156,
    "avg_latency_ms": 2340,
    "errors_last_hour": 2
  }
}
```

---

## 六、超时规范

### 6.1 超时精度

所有 timeout 字段使用 **毫秒 (uint32)**。

**设计理由** (三方评审通过):
- 吉量: 协议层 ms，各 adapter 内部换算，一步到位
- 火鸡儿: Letta 冷启动场景 (35000ms) 和常规 dispatch (8000ms) 差异大，ms 精度足够
- 呱呱: 避免未来 precision 不够改协议

### 6.2 场景推荐值

| 场景 | 推荐 timeout (ms) | 说明 |
|------|------------------|------|
| `health` | 3000 | 快速探活 |
| `info` | 3000 | 获取能力信息 |
| `process` (API Server / 已热) | 10000 | 无冷启动，纯推理 |
| `process` (Letta `--new` 冷启动) | 35000 | 冷启动 17s + 推理 15s + 缓冲 3s |
| `process` (OpenClaw session 复用) | 8000 | 消除 1.5s 冷启动后纯推理 |
| `reload` | 5000 | 文件读取 + 缓存刷新 |
| `status` | 3000 | 获取运行时指标 |
| `cancel` | 5000 | 尽力而为止 |
| `trim` | 5000 | 清理会话 |

### 6.3 动态超时

Core 根据以下因素动态调整 timeout:

| 因素 | 调整 |
|------|------|
| Queue 深度 ≤3 | 正常值 |
| Queue 深度 4-8 | ×1.5 |
| Queue 深度 ≥9 | ×2.0 |
| HealthProbe 连续 OK | 逐步降低至正常值 |
| 该 `from_id` 历史平均延迟 | 取 P95 + 2s |
| message 含 `TASK` 标记 | 不降低 timeout |

### 6.4 超时处理流程

```
Core 发送 request (timeout=T)
  ├─ 收到响应 (≤T) → 正常处理
  └─ 超时 (≥T)
       ├─ CLI 模式: SIGTERM (2s) → SIGKILL
       ├─ API Server: HTTP abort
       ├─ 记录 DEGRADE 计数
       └─ 按退出码约定决定后续
```

---

## 七、退出码约定

| 退出码 | 名称 | 语义 | Core 后续行为 |
|--------|------|------|-------------|
| 0 | OK | 正常完成 | 投递回复 |
| 1 | TEMP_FAIL | 临时故障，可重试 | 退避后重试 (最多 3 次) |
| 2 | DEGRADE | 降级，暂停投递 | 暂停该 `from_id` 投递 30s，触发 health 探测 |
| 3 | FATAL | 致命错误，需人工介入 | 停止投递，告警通知 |

**注**: exit code 3 (FATAL) 的触发条件：
- 连续 3 次 health 探测失败
- adapter 进程无法启动
- 模型 API 密钥过期/额度耗尽

---

## 八、错误处理

### 8.1 错误码

| code | 说明 | 建议处理 |
|------|------|---------|
| `ADAPTER_TIMEOUT` | adapter 处理超时 | 退避重试 |
| `LLM_API_ERROR` | LLM API 调用失败 | 检查 API key / 额度 |
| `LLM_TIMEOUT` | LLM 推理超时 | 降低 context 大小重试 |
| `SESSION_LIMIT` | Session 数量达上限 | trim 旧 session 后重试 |
| `INVALID_REQUEST` | JSON 格式错误 | 检查协议版本兼容性 |
| `NOT_IMPLEMENTED` | action 不支持 (如 cancel) | 降级处理 |
| `INTERNAL_ERROR` | adapter 内部错误 | 重启 adapter |

### 8.2 降级路径

```
process 失败
  ├─ exit 1 (TEMP_FAIL) → 退避重试 (1s, 2s, 4s)
  │     ├─ 3次成功 → 恢复
  │     └─ 3次失败 → 升级为 DEGRADE (exit 2)
  │
  ├─ exit 2 (DEGRADE) → 暂停该 from_id 30s
  │     ├─ health 通过 → 恢复投递
  │     └─ health 失败 → 升级为 FATAL (exit 3)
  │
  └─ exit 3 (FATAL) → 停止投递 + 通知大哥
```

---

## 九、适配器实现检查清单

### 9.1 必选

- [ ] 接收 stdin JSON (CLI) 或 POST JSON (API Server)
- [ ] 解析 `action` 字段，至少实现 `health` / `info` / `process`
- [ ] `process` 使用 Core 提供的 `context`，不自己读取文件
- [ ] 使用 Core 提供的 `session_id`，不自己生成
- [ ] 遵守 `timeout` (ms) 约定，超时返回退出码 1
- [ ] 输出 stdout JSON (CLI) 或 200 JSON (API Server)
- [ ] 退出码遵循协议约定 (0/1/2/3)
- [ ] stderr 仅用于日志，不混入 stdout

### 9.2 推荐

- [ ] 支持 `reload` (热刷新缓存)
- [ ] 支持 `status` (运行时指标)
- [ ] 返回 `usage` 字段 (token 统计)
- [ ] 返回 `metadata.model` / `metadata.latency_ms`
- [ ] `info` 返回完整能力清单

### 9.3 可选

- [ ] 支持 `cancel` (未实现返回 exit 2)
- [ ] 支持 `trim` (CLI per-message 模式自然满足)
- [ ] CLI 模式 pre-fork daemon 加速启动

---

## 十、参考实现

### 10.1 CLI 模式 (适配器核心约 30 行)

```bash
#!/bin/bash
# adapter.sh — AIM Adapter Protocol v1.0 参考实现

read -r REQUEST
ACTION=$(echo "$REQUEST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action','process'))")

case "$ACTION" in
  health)
    echo '{"reply":"ok"}'
    exit 0
    ;;
  info)
    echo '{"reply":null,"metadata":{"adapter_version":"1.5.0","protocol_version":"1.0","runtime":"openclaw","mode":"cli"}}'
    exit 0
    ;;
  process)
    MSG=$(echo "$REQUEST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))")
    CTX=$(echo "$REQUEST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('context',''))")
    SID=$(echo "$REQUEST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))")
    # 调 OpenClaw — 使用 Core 提供的 session_id，不自己生成
    REPLY=$(openclaw -p "$MSG" --session "$SID" --context "$CTX" 2>/dev/null)
    echo "{\"reply\":\"$REPLY\"}"
    exit 0
    ;;
  reload)
    echo '{"reply":"ok"}'
    exit 0
    ;;
  status)
    echo '{"reply":null,"metadata":{"active_sessions":1}}'
    exit 0
    ;;
  *)
    echo '{"reply":null,"error":{"code":"NOT_IMPLEMENTED","message":"Unknown action"}}'
    exit 2
    ;;
esac
```

### 10.2 API Server 模式 (适配器核心约 5 行)

```python
# adapter_api.py
@app.post("/v1/chat")
async def process(req: Request):
    body = await req.json()
    reply = await hermes.chat(
        message=body["message"],
        session_id=body["session_id"]
    )
    return {"reply": reply}
```

---

## 变更历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-02 | v1.0-draft | 初稿，三方 L1 评审通过，包含 timeout(ms)、7 lifecycle、退出码约定 |

---

## 待决议项

- [ ] L2 Protocol Bridges (MCP/A2A/REST) — 方向已确认 (大哥: "兼容天下")，优先级和时机待群内确认
- [ ] 身份/安全 (外部接入时的 trust/did) — 归入 OAS Phase 4
- [ ] 流式输出 (streaming) — v1.0 不做，预留 metadata 扩展
