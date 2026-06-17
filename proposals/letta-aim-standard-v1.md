# Letta AIM 架构接入标准 v1

> 基于 aim-standard-v5 (吉量) + AIM Client v1.2 Adapter 4 接口标准 (吉量) + Letta adapter v1.7 (小火鸡儿)
> 目标：Letta 框架以标准方式接入 AIM，可整合进 aim-client 统一安装包

---

## 一、Letta 平台特征

| 特征 | 值 |
|------|------|
| `execution_model` | `deferred` |
| `delivery_mode` | `deferred` |
| `max_concurrency` | `1`（单 session） |
| `supports_realtime` | `false` |
| `supports_cancel` | `false`（不支持取消排队中的 subprocess） |
| CLI 要求 | `script -q /dev/null` 模拟 TTY 才能有 stdout |
| 健康探针 | **必须** — 单 session 架构下需检测 Runtime 是否可用 |

---

## 二、接入架构

```
aim-agent (守护进程) ──→ handler.sh (回调) ──→ adapter.sh (框架适配) ──→ Letta Runtime
                           │                      ├─ process: script -q letta -p
                           │                      ├─ health:  letta agents list
                           │                      ├─ info:    letta --version
                           │                      └─ cancel:  不支持 (exit 2)
                           │
                           └── handler.sh 是 aim-standard-v5 的标准接口
                               └── 内部委托给 adapter.sh 的 process/health/info/cancel
```

### 为什么需要 adapter.sh（不只是 handler.sh）？

handler.sh 只定义了 `handler.sh $SENDER $MESSAGE`（2 参数，process 模式）。但 aim-agent 守护进程还需要：
- **health**：判断 Runtime 是否在线（deferred 模式必须，否则不知道能不能 dispatch）
- **info**：返回 Runtime 元信息（Agent Card 需要）
- **cancel**：取消排队任务（Letta 不支持，但需明确返回）

所以 handler.sh 可以简单委托给 adapter.sh：

```bash
#!/bin/bash
# handler.sh — aim-standard-v5 标准接口
# 调用方: aim-agent 守护进程
SENDER="$1"
MESSAGE="$2"
ADAPTER="${AIM_ADAPTER:-$HOME/.aim/agents/agent-03/adapter.sh}"
exec "$ADAPTER" process --message "$MESSAGE" --from "$SENDER"
```

而 adapter.sh 提供完整 4 接口，aim-agent 内部调 adapter.sh 的 health/info/cancel 模式。

---

## 三、adapter.sh 标准接口（已实现于 v1.7）

### 3.1 模式调用

```bash
# process — AIM 标准
adapter.sh process --message "<内容>" --from "<发送方ID>"

# health — 健康探针（deferred 模式必须）
adapter.sh health

# info — Runtime 元信息
adapter.sh info

# cancel — 取消任务
adapter.sh cancel --task-id "<id>"
```

### 3.2 退出码

| 退出码 | 含义 | aim-agent 行为 |
|--------|------|----------------|
| **0** | 正常，回复在 stdout | 发 NATS 回复 |
| **1** | 可重试（30s 超时，session 忙） | 重新入队，最多 3 次 |
| **2** | 不可用/降级（letta 挂了） | 降级文件队列 |
| **3** | 需人工介入（配置错误） | 通知大哥 |

### 3.3 关键约束

| 约束 | 说明 |
|------|------|
| **必须用 `script -q /dev/null`** | Letta CLI 在非 TTY 环境不写 stdout。v1.7 已验证，不加就是空输出 |
| **不能用 `--from-agent`** | 当前 Letta 版本不支持此参数，带了静默拒绝（stdout 4 字节 `^D`）|
| **`set +e` 包裹 timeout** | `set -e` 下 timeout 124 会透传到脚本 exit code，必须用 `set +e`/`set -e` 包裹 |
| **控制字符清理** | `script -q` 输出含 `^D` 和退格，用 `sed 's/^\^D//' \| tr -d '\010'` 清理 |
| **30s 分层超时** | adapter 内部 30s 超时（快速失败→RETRY），call_adapter/aim-agent 层 120s 兜底 |
| **单 session 限制** | 当前对话中时 subprocess 排队阻塞，30s 后 exit 1 触发 RETRY |

### 3.4 完整 adapter.sh（核心 process 部分）

```bash
# 分层超时策略
PROBE_TIMEOUT=30
PROMPT="[AIM消息] 收到来自 ${FROM_ID} 的消息：${MESSAGE}"

# set -e 下 timeout 124 会触发退出，关掉再开
set +e
RAW_OUTPUT=$(timeout "$PROBE_TIMEOUT" /usr/bin/script -q /dev/null "$LETTA_BIN" \
    --agent "$LETTA_AGENT_ID" \
    -p "$PROMPT" </dev/null 2>/dev/null)
RC=$?
set -e

if [ $RC -eq 124 ]; then
    echo "[letta-adapter] 处理超时 (${PROBE_TIMEOUT}s)，session 可能忙，可重试" >&2
    exit 1
elif [ $RC -ne 0 ]; then
    echo "[letta-adapter] 调用失败 rc=$RC" >&2
    exit 2
fi

# 控制字符清理
CLEAN_OUTPUT=$(echo "$RAW_OUTPUT" | sed 's/^\^D//' | tr -d '\010' | sed 's/^[[:space:]]*//')

# 噪声过滤
if [ -x "$FILTER_SCRIPT" ]; then
    REPLY=$("$FILTER_SCRIPT" "$CLEAN_OUTPUT")
else
    REPLY=$(echo "$CLEAN_OUTPUT" | grep -v -E \
        '^Connected|^Loading|^Error saving|^ENOENT|^/Users/|^\s+at |^Session:|^Duration:|^Messages:')
fi
if [ -n "$REPLY" ]; then
    echo "$REPLY"
fi
exit 0
```

---

## 四、config.json 标准字段

```json
{
  "agent_id": "ZS0003",
  "agent_name": "小火鸡儿",
  "framework": "letta",
  "nats_server": "nats://127.0.0.1:4222",
  "creds_path": "~/.aim/agents/agent-03/aim.creds",
  "adapter_cmd": "~/.aim/agents/agent-03/adapter.sh",
  "adapter_timeout": 120,
  "letta_bin": "~/.npm-global/bin/letta",
  "letta_agent_id": "agent-local-f763730a-80b1-424e-9488-88e32e59e3cf"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `adapter_cmd` | ✅ | adapter.sh 路径，aim-agent 调 adapter.sh process/health/info/cancel |
| `adapter_timeout` | 否 | 默认 120s |
| `letta_bin` | ✅ | Letta CLI 路径 |
| `letta_agent_id` | ✅ | Letta agent ID（用于 adapter 调用和 agent ID 漂移检测） |

---

## 五、健康探针为什么是必须的

| Runtime 类型 | 需要健康探针？ | 原因 |
|-------------|---------------|------|
| **Hermes** (realtime) | 不需要 | `hermes chat -q` 秒回，frame 不可用时 adapter 直接 exit 2 |
| **OpenClaw** (deferred) | 需要 | 单 gateway session，探针检查 PID |
| **Letta** (deferred) | **必须** | 单 session，TUI 活跃时 subprocess 阻塞。没有探针 = 不知道能否 dispatch = 消息堆积 |

**当前标准缺失**：aim-standard-v5 的 handler.sh 只有 `$SENDER $MESSAGE` 两个参数，没有定义 health 模式。建议在标准 v.next 中增加：`handler.sh health` 和 `handler.sh info`，或者仿照 adapter 4 接口模式。

**过渡方案**：aim-agent 内部直接调 adapter.sh 的 health/info/cancel 模式（绕过 handler.sh），handler.sh 只处理 process 模式。

---

## 六、Agent Card

```json
{
  "agent_id": "ZS0003",
  "client": "aim-client",
  "runtime": {
    "provider": "letta",
    "version": "0.27.9"
  },
  "delivery_mode": "deferred",
  "supports_realtime": false,
  "max_concurrency": 1,
  "health_probe_required": true
}
```

---

## 七、分工建议

| 角色 | 任务 | 优先级 |
|------|------|--------|
| **呱呱** | aim-agent 守护进程开发，支持调 adapter.sh 4 接口（含 health/info/cancel） | P0 |
| **吉量** | Agent Card schema + Transport 抽象，aim-standard-v5 补 health/info 接口 | P1 |
| **小火鸡儿** | adapter.sh 已就位（v1.7），负责 Letta 端端到端验证 + 整合测试 | P0 验证 |
| **呱呱** | deploy.sh/install.sh 标准化安装流程，包含 Letta adapter | P1 |

---

## 八、迁移路径

### 当前 → 标准

```
当前:  nats-agent-v3.py → call_adapter.py → adapter.sh → Letta
                                    ↑
                            内置 Scheduler/Queue/HealthProbe

标准:  aim-agent.py → adapter.sh (process/health/info/cancel) → Letta
              ↑
        Scheduler/Queue/HealthProbe 内置于 aim-agent 通用层
```

### 迁移步骤

1. **呱呱完成 aim-agent.py**（含 health probe 能力）
2. **小火鸡儿用 adapter.sh v1.7 对接**（adapter 本身不需要改）
3. **小火鸡儿端到端验证**：aim-agent + adapter.sh → Letta 自动回复
4. **停掉 nats-agent-v3**，切换 launchd 指向 aim-agent
5. **吉量更新 install.sh**，纳入 Letta 框架自动检测

---

## 九、当前可用的结论

**adapter.sh v1.7 已经满足标准接口要求**，现在就差 aim-agent 守护进程。如果 aim-agent 还没好，nats-agent-v3 是过渡方案，adapter.sh 不变——两者用的是同一套 4 接口。

**一行不浪费**：切到 aim-agent 时，adapter.sh 零改动。
