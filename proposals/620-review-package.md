# 620 评审包 — 问题清单 + 优化方案 + 待办分工

> 整理：小火鸡儿 (ZS0003) | 日期：2026-06-20 02:30
> 转：呱呱 (ZS0001) + 吉量 (ZS0002)
> 大哥已阅 L1/L2/L3 三层方案，待三方评审后分工执行

---

## 一、6.20 凌晨发现的问题（8 项）

| ID | 类别 | 问题 | 严重度 | 责任方 |
|----|------|------|:--:|--------|
| 620-01 | Scheduler | **StallWatchdog 自愈无效** — 复位 busy→IDLE 后 dispatch_loop 未重新投递 | 🔴 | 呱呱 |
| 620-02 | 进程 | Letta TUI session 占用导致 adapter process 超时 exit 1 | 🔴 | 火鸡儿适配 |
| 620-03 | 队列 | ZS0003 queue.jsonl 积压 88 条（跨天旧消息），Scheduler 未消费 | 🔴 | 呱呱修 / 火鸡儿清 |
| 620-04 | adapter | health 探针 `letta agents list` 假阴性 → 切换到 memfs 磁盘检查 | ✅ | 火鸡儿 |
| 620-05 | adapter | adapter v1.7 升级（memfs 探针 + exit code 对齐 P1-3）| ✅ | 火鸡儿 |
| 620-06 | 部署 | **ZS0001 同样陷入 StallWatchdog 自愈循环**（28 条积压）| 🔴 | 呱呱 |
| 620-07 | 协议 | adapter health exit 3/4 是否被 main.py 正确解读 | 🟡 | 呱呱验证 |
| 620-08 | 架构 | **单点 Runtime 故障 → 全群通讯中断** — 三方同时自愈循环 | 🔴 | 三方讨论 |

### 核心问题：620-01/06 StallWatchdog 自愈无效

**现象**：ZS0003 01:53 重启后第一条消息超时 exit 1 → StallWatchdog 每 30s 触发一次自愈 → 10+ 分钟从未真正消费队列。ZS0001 同样模式（OpenClaw 无回复 → 自愈循环）。

```
01:53:25 [ERROR] 投递循环异常: [letta-adapter] 处理超时 (15s)
01:54:00 [WARNING] StallWatchdog: 30s 无投递, queue=8, 自愈(#1)
 Scheduler: 强制复位 busy → IDLE (StallWatchdog)
01:54:30 [WARNING] StallWatchdog: 30s 无投递, queue=8, 自愈(#2)
... (持续 10+ 分钟, 从未真正 dispatch)
```

**分析**：StallWatchdog 复位状态后 dispatch_loop 未重新触发投递——可能是 dispatch_event 没被 set 或 dispatch_loop 在另一分支卡住。这是 v1.3.1 回归——之前 P0-1 修复的 `DegradeError → break 后 dispatch_event.set()` 在不同代码路径下可能未覆盖。

---

## 二、大哥三层优化方案（补充现有 stability plan）

> 现有 `proposals/aim-stability-optimization-2026-06-20.md` 覆盖 P0-1 ~ P1-3（adapter 验证路径修复、Queue 隔离、env 注入、DEGRADE 容错、exit code 标准化），对应「修现有逻辑」。
> 大哥的三层方案覆盖「**暴露 + 推送 + 自修复**」，属于稳定性增强——不是替代，是叠加。

### 盲区分析

1. Registry 只关心"aim-client 心跳在不在"，不管"Runtime 还活着不"
2. Scheduler 检测到 degrade 后 emit_obs，但 aim-watch 只在终端打印，不会主动推送
3. degrade 风暴时 Watchdog 连续自愈 → 反复 degrade → 没有人收到告警

### L1 暴露层（出问题早知道）

**Registry 加 Runtime 健康追踪**。不只看 agent 心跳，还要暴露 Agent Card 里的 Runtime 状态。

- 新增 subject: `aim.obs.registry`
- 已有: `agent_online` / `agent_offline`
- 新增: `agent_degraded`（Scheduler emit_obs 时 aim-client 同步发给 Registry）
- 新增: `agent_stalled`（连续 3 次 Watchdog 自愈失败 → 告警）
- aim-watch 加告警持久化写入 `~/.aim/system/alerts.log`，大哥回头也能看到

### L2 推送层（出问题被通知）

degrade/offline 事件到达阈值后触发推送：

| 条件 | 告警级别 | 动作 |
|------|----------|------|
| 连续 3 次 degrade | warning | NATS `aim.obs.alert` 广播 |
| 连续 5 次 degrade | critical | NATS `aim.obs.alert` 广播 |
| agent offline > 5 分钟 | critical | NATS `aim.obs.alert` 广播 |

推送通道：

> **吉量建议改为独立 alertd 守护进程**（不绑 Agent），订阅 `aim.obs.alert`，统一写日志 + 推群聊。
> 理由：Agent 自己可能正在 degrade，让 degrade 的 Agent 推送自己的告警不可靠。独立 alertd 不受影响。

### L3 自修复层（自己修）

| 场景 | 检测 | 自动修复 |
|------|------|----------|
| Letta agent 不在 agents list | adapter health exit=4 + agent_id not found | `letta -p "ping" --agent` 触发 lazy init 重新注册 |
| adapter timeout 累积 3 次 | exit=1 计数 | `letta conversations trim --keep-last 5` 清上下文 |
| conversation 膨胀 | 磁盘 > 阈值 | Cron 定期清理旧 conv |

**⚠️ 自修复护栏**（吉量提出）：自修复 N 次仍失败（N=3）→ 触发 `agent_stalled` 告警 → 发 `aim.obs.alert(level=critical)` → 停止自修复，等人工介入。防止自修复无限循环掩盖根因。

**⚠️ DEGRADE 恢复后无端到端验证**（吉量提出）：health probe 只查进程活着，不查能不能处理消息。恢复后应自动发 ping 测试。

### 分工

| 模块 | 谁 | 做什么 |
|------|-----|------|
| **Registry 健康追踪** (L1) | 呱呱 | `_health_monitor` 加 Runtime degrade 追踪 + `agent_degraded`/`agent_stalled` 告警 |
| **alertd 守护进程** (L2) | 吉量 | 独立进程订阅 `aim.obs.alert`，阈值累计 + 写 `alerts.log` + 推群聊 |
| **aim-watch 持久化** (L2) | 吉量 | 告警持久化到 `~/.aim/system/alerts.log` |
| **自修复护栏** (L3) | 吉量 | N=3 升级 agent_stalled + 停止自修复 |
| **adapter 自修复** (L3) | 火鸡儿 | Letta agent 离线自动 ping 触发注册；conv 膨胀清理 cron；adapter timeout 自愈 |
| **DEGRADE 恢复验证** | 三方 | 复用 deploy-verify 模板做端到端 ping |

---

## 三、待评审 / 待对齐项

| 项 | 内容 | 谁 | 当前状态 |
|----|------|-----|----------|
| **P1-3 exit code** | adapter exit=2/3/4 语义三方统一 | 三方 | ✅ 全部对齐（含 Hermes v1.2 exit=3×3） |
| **620-07** | adapter health exit 3/4 是否被 main.py 健康探针路径正确解读 | 呱呱验证 | 待确认 |
| **620-08** | 单点 Runtime 故障全群静默 → 架构层面需要降级通知机制 | 三方讨论 | 待讨论 |
| **L3 自修复** | `letta conversations trim` 命令可用性、`letta -p "ping"` 触发注册、cron 清理策略 | 火鸡儿验证 | 待验证 |

---

## 四、执行顺序建议

```
Phase 1（呱呱紧急修复）:
  StallWatchdog 自愈 bug (620-01/06) → 三方恢复通讯

Phase 2（三方并行）:
  火鸡儿: 清理 ZS0003 队列 + L3 自修复验证
  呱呱: L1 Registry 健康追踪 + 620-07 exit code 验证
  吉量: Hermes exit code 对齐 + L2 Observer 推送设计

Phase 3（三方串行）:
  吉量: L2 aim-watch alers.log + 推送通知实现
  火鸡儿: L3 adapter 自修复 cron + conv 清理脚本
  呱呱: 全链路端到端验证
```

---

## 参考文件

- `shared/aim/issues/ISSUES-620.md` — 完整问题清单（含详细日志和分析）
- `shared/aim/proposals/aim-stability-optimization-2026-06-20.md` — 呱呱起草的稳定性修复方案
- `shared/aim/adapters/letta/adapter.sh` — adapter v1.7（memfs 健康探针 + exit code 对齐）
- `~/.aim/agents/ZS0003/logs/agent.err.log` — ZS0003 完整日志
