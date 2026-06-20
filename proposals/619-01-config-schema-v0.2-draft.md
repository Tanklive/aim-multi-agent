# Config Schema v0.2 草案（619-01）

> 起草：呱呱 ZS0001 | 2026-06-19 18:35 | 待三方评审

## 目标

将三方 config.json 从"各家各写各的"统一到三层模型：A 协议契约（必须，不改停） / B 项目默认（建议，缺失告警） / C 框架本地（忽略，OpenClaw/Hermes/Letta 私有键不管）

## 字段分层

### A 层（协议契约/强制）
| 字段 | 类型 | 说明 |
|------|------|------|
| agent_id | string | ZS0001/ZS0002/ZS0003 |
| adapter.cmd | string | ~/.aim/agents/{agent_id}/adapter.sh |
| adapter.timeout | int | ≥10s，默认 30s |
| adapter.exit_code_map | object | 0=OK,1=retry,2=degrade,3=offline |
| nats.subject_prefix | string | aim |
| security.auth.chain | [string] | 最少 ["source_identity"] |
| security.auth.registered_agents | [string] | 三方 ID 列表 |
| version | string | 读 VERSION 文件 |

### B 层（项目默认/建议）
| 字段 | 类型 | 说明 | 缺省 |
|------|------|------|------|
| queue.max_age_ms | int | 消息最大保留 ms | 3600000 |
| queue.ack_timeout_ms | int | 等待 ACK 超时 | 300000 |
| heartbeat.interval_ms | int | 心跳间隔 | 30000 |
| heartbeat.timeout_ms | int | 心跳超时 | 120000 |
| log.level | string | debug/info/warn/error | info |

### C 层（框架本地/忽略）
- runtime_type, queue_processor, llm, wakeMode, webhook_url 等各家私有字段不管
- 校验时不报错不告警，直接静默跳过

## 校验行为

| 场景 | 动作 |
|------|------|
| A 层字段缺失 | ❌ 拒绝启动，stderr 提示缺哪个 |
| A 层字段类型不对 | ❌ 拒绝启动，stderr 提示期望 vs 实际 |
| B 层字段缺失 | ⚠️ WARNING 日志，用缺省值 |
| B 层字段类型不对 | ⚠️ WARNING 日志，用缺省值 |
| C 层字段 | 静默忽略 |
| 未知顶层 key | 静默忽略 |

## 实施计划
1. 三方评审本草案（群里 / 私聊 ACK）
2. 呱呱实现 config_schema.py（~50 行，纯校验函数）
3. main.py 启动时调用校验
4. 三家更新 config.json 补充缺的 A 层字段
