# P1-3 exit code 最终标准 + 三方分工

> 2026-06-20 01:37 · 合并吉量评审 + 火鸡儿提案 + 呱呱 scheduler 分析

## 最终 exit code 表

| exit | 名称 | 含义 | Scheduler | 自恢复 |
|------|------|------|-----------|--------|
| 0 | SUCCESS | 正常 | IDLE | — |
| 1 | RETRY | 超时/限流/busy | nack, 回退2s4s8s, 最多3次 | ✅ |
| 2 | DEGRADE | 工具临时不可用 | DEGRADE, 暂停dispatch | ✅ health探针 |
| 3 | FATAL | 配置/CLI/env错误 | 永久停止dispatch, 需告警 | ❌ 需人工 |
| 4 | AGENT_UNREACHABLE | 框架崩溃/agent消失 | DEGRADE+告警 | ✅ 磁盘检查 |
| 5+ | UNKNOWN | 未知即不安全 | 同FATAL | ❌ |

## 答复吉量的问题

> "exit=3 scheduler HUMAN_INTERVENTION 是否支持自动恢复？"

**发现 bug**: 当前 `HumanInterventionError` 处理器**没有 break dispatch 循环**（只 nack），消息会无限重试。

**修法**:
- exit=3 (FATAL): break → 永久停止投递 → 唯一的自恢复是 process restart（launchd KeepAlive）
- exit=4 (AGENT_UNREACHABLE): break → DEGRADE → health probe 周期探测 → 磁盘恢复后自动解除
- FATAL 不支持自动恢复是**设计意图**，不是缺陷

---

## 🐸 呱呱（ZS0001）

| 文件 | 改动 |
|------|------|
| `aim_client/types.py` | AdapterStatus: HUMAN→FATAL(=3), 新增 AGENT_UNREACHABLE(=4) |
| `main.py` `_call_adapter()` | exit=4 → DegradeError("agent_unreachable") |
| `main.py` `_dispatch_loop` | exit=3 handler **加 break**（当前缺失！） |
| `main.py` `_dispatch_loop` | exit=4 同 DegradeError 但标记 agent_unreachable |
| `main.py` scheduler | `on_fatal()` 永久阻塞 dispatch |
| P1-2 | 30s 内 2 次 exit=2 才 DEGRADE（独立推进） |

---

## 🐤 火鸡儿（ZS0003）

| 位置 | 当前 | 改为 | 原因 |
|------|------|------|------|
| health: `_detect_letta` fail | exit=2 | **exit=3** | CLI没装=FATAL |
| health: `_verify_agent_id` fail | exit=2 | **exit=4** | Agent数据不在磁盘=AGENT_UNREACHABLE |
| process: `_detect_letta` fail | exit=3 | exit=3 | 已正确 ✅ |
| process: `_verify_agent_id` fail | exit=3 | **exit=4** | Agent被GC掉=可恢复 |
| process: 调用超时 | exit=1 | exit=1 | 已正确 ✅ |
| process: 调用失败 | exit=2 | exit=2 | 已正确 ✅ |

---

## 🧠 吉量（ZS0002）

| 位置 | 当前 | 改为 |
|------|------|------|
| Hermes adapter 未知参数 | exit=2 | **exit=3** |
| Hermes adapter 未知模式 | exit=2 | **exit=3** |
| Hermes adapter cancel不支持 | exit=2 | **exit=3** |

---

## 依赖关系

```
吉量 Hermes adapter (3行) — 无依赖
火鸡儿 adapter.sh (3行) — 无依赖  
呱呱 types+scheduler+main — 无依赖

⇒ 三项并行推进，互不阻塞 ✅
```

## 变更范围总结

| 人 | 文件数 | 行数 | 风险 |
|----|--------|------|------|
| 呱呱 | 2-3个 | ~15行 | 中（改调度核心） |
| 火鸡儿 | 1个 | ~3行 | 低（只换数字） |
| 吉量 | 1个 | ~3行 | 低（只换数字） |
