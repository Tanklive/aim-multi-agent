# 620 评审包 — 问题清单 + 优化方案 + 待办分工 （已合并至终版）

> 整理：小火鸡儿 (ZS0003) | 日期：2026-06-20 02:30
> **⚠️ 本文件已合并至终版 `proposals/aim-620-final.md`（呱呱整合），后续以终版为准。**
> 本文件保留作为技术分析详版 + 评审历史记录。

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

### L1 暴露层（出问题早知道）— ✅ 三方确认，直接可做

**呱呱方案（优于原提案）**：不新增 subject，复用现有 heartbeat 通道加 `runtime_status` 字段。

```
aim-client emit_obs("degrade") 时同步更新 heartbeat KV：
  runtime_status: "degraded" | "healthy" | "stalled"

Registry _health_monitor（30s 巡检）读心跳时就能看到 Runtime 状态，
无需新增 NATS subject。
```

**阈值**：
| 事件 | 阈值 | 动作 |
|------|------|------|
| `agent_degraded` | 心跳中 runtime_status=degraded | Registry 记录 |
| `agent_stalled` | 连续 **2 次** Watchdog 自愈失败（60s） | 告警（呱呱建议 2 次而非 3 次，更灵敏，与 Registry 120s 超时拉开距离） |

**谁做**：呱呱，Registry `_health_monitor` 加字段 + KV 更新。

### L2 推送层（出问题被通知）— ✅ 三方确认 alertd 方案

> 吉量建议独立 **alertd 守护进程**（不绑 Agent）。三方同意。
> 理由：Agent 自己可能正在 degrade，让 degrade 的 Agent 推送自己的告警不可靠。

```
alertd（独立进程，不绑 Agent）
  ├── 订阅 aim.obs.alert
  ├── 阈值累计：3 次 degrade → warning, 5 次 → critical
  ├── 写入 ~/.aim/system/alerts.log（结构化 JSON）
  └── 推送到 grp_trio 群聊（大哥总归看群）
```

**谁做**：吉量。纯增量改动，不碰现有逻辑。

### L3 自修复层（自己修）— ✅ 三方确认，有重要 nuance

**呱呱关键反馈**：健康检查和恢复动作必须分层，不能混。

> `letta -p "ping"` 是恢复动作，不是健康检查。v1.7 的 memfs disk check 秒回，不能把 lazy init ping 混进去——ping 可能要等 Letta 把 agent 拉起来。

**L3 正确分层**：

| 模式 | 触发条件 | 动作 | 超时 | 谁调 |
|------|----------|------|------|------|
| `health`（不改） | Scheduler 探针 / main.py | memfs disk check | <1s | adapter.sh health |
| `recover`（新增） | Scheduler 收到 exit=4 AGENT_UNREACHABLE | `letta -p "ping" --agent` → poll 检查 → process 重试 | 30s | adapter.sh recover |
| trim（adapter 内部） | exit=1 累积 3 次（呱呱补退避后） | `letta conversations trim --keep-last 5` | 10s | adapter process 内部 |
| cron 清理 | 磁盘 > 阈值 | 清理旧 conv | - | ~/.aim/scripts/cleanup.sh |

**recover 模式入口**：Scheduler 收到 exit=4 → 调 `adapter.sh recover`，而不是 adapter 自己检测到 → 自己 recover。
> 理由：Scheduler 掌握全局状态，知道什么时候应该 recover vs 什么时候应该停止（护栏 N=3）。adapter 只负责执行恢复动作。

**护栏**（吉量提出，三方确认）：
```
自修复 N 次仍失败（N=3）
  → 触发 agent_stalled 告警
  → 发 aim.obs.alert(level=critical)
  → 停止自修复，等人工介入
```

**恢复验证**（吉量提出，三方确认）：
DEGRADE 恢复后，复用 `deploy-verify.sh` 模板发端到端 ping 测试，确认能处理消息。

**硬前置条件**：
> 呱呱：exit=1 退避（2s/4s/8s，最多 3 次）标准定了但代码还没写。L3 的「exit=1 累积 3 次清上下文」依赖这个——没有退避就没有「累积」的概念。

执行顺序：
1. 呱呱补 exit=1 退避 → 
2. 火鸡儿验证「累积 3 次后 trim」→ 
3. 火鸡儿加 `adapter.sh recover` 模式

**谁做**：
- 火鸡儿：adapter.sh recover 模式 + trim 逻辑 + cleanup cron
- 吉量：护栏规则（N=3 → agent_stalled）
- 三方：恢复验证机制

### 分工确认（三方已对齐）

| 优先级 | 模块 | 谁 | 做什么 | 估 |
|:--:|------|-----|------|:--:|
| 🔴 | **StallWatchdog 自愈 bug** | 呱呱 | dispatch_loop 修复 | 今晚 |
| 🔴 | **exit=1 退避** | 呱呱 | 2s/4s/8s 代码实现（硬前置） | 今晚 |
| 🔴 | **L1 Registry 健康追踪** | 呱呱 | heartbeat KV 加 `runtime_status` + `agent_stalled` 2 次阈值 | 小 |
| 🔴 | **L2 alertd 守护进程** | 吉量 | NATS 消费 + 阈值累计 + 日志 + 群聊推送 | 中 |
| 🟠 | **aim-watch 持久化** | 吉量 | 告警写入 `~/.aim/system/alerts.log` | 小 |
| 🟠 | **自修复护栏** | 吉量 | N=3 升级 agent_stalled + 停止自修复 | 小 |
| 🟡 | **adapter.sh recover 模式** | 火鸡儿 | `letta -p "ping"` + poll + 重试（等呱呱退避完） | 中 |
| 🟡 | **adapter trim 逻辑** | 火鸡儿 | exit=1 × 3 → trim conversation | 小 |
| 🟡 | **conv 清理 cron** | 火鸡儿 | 磁盘阈值监控 + 自动清理旧 conv | 小 |
| 🟡 | **DEGRADE 恢复验证** | 三方 | 复用 deploy-verify 模板端到端 ping | 小 |
| 🟢 | **619+ P2 遗留** | 各自 | P2-a～P2-e 清理 | 小 |

---

## 三、待评审 / 待对齐项

| 项 | 内容 | 谁 | 当前状态 |
|----|------|-----|----------|
| **P1-3 exit code** | adapter exit=2/3/4 语义三方统一 | 三方 | ✅ 全部对齐（含 Hermes v1.2 exit=3×3） |
| **620-07** | adapter health exit 3/4 是否被 main.py 健康探针路径正确解读 | 呱呱验证 | 待确认 |
| **620-08** | 单点 Runtime 故障全群静默 → L2 alertd 解决推送，L3 recover 解决自愈 | 三方 | ✅ 方案已覆盖 |
| **L3 自修复** | `letta conversations trim` 命令可用性、`letta -p "ping"` 触发注册、cron 清理策略 | 火鸡儿验证 | 待验证 |

---

## 四、执行顺序（三方确认后）

```
Phase 0（呱呱今晚独立）:
  StallWatchdog 自愈 bug 修复 (620-01/06)
  exit=1 退避实现（2s/4s/8s）—— L3 硬前置

Phase 1（三方并行，无依赖）:
  呱呱: L1 Registry heartbeat KV + stall 阈值
  吉量: L2 alertd 守护进程 + aim-watch 持久化
  火鸡儿: adapter.sh recover + trim + cron（等 Phase 0 退避完成后开始）

Phase 2（串行，依赖 Phase 0-1）:
  吉量: 自修复护栏规则（依赖 L3 recover 模式就绪）
  三方: DEGRADE 恢复后端到端验证

Phase 3（低优）:
  各自: 619+ P2 遗留项清理
```

---

## 参考文件（当前项目内所有 6.20 相关文档）

| 文件 | 作者 | 内容 | 状态 |
|------|------|------|:--:|
| `proposals/620-review-package.md` | 火鸡儿 | ⭐ 本文件 — 终版评审包 | ✅ 定稿 |
| `issues/ISSUES-620.md` | 火鸡儿 | 问题清单（8 项 + 详细日志） | ✅ 已完成 |
| `proposals/aim-620-status-and-optimization.md` | 吉量 | 状态汇总 + 三层分析 | ✅ 已评审 |
| `proposals/aim-stability-optimization-2026-06-20.md` | 呱呱 | P0-1~P1-3 修复方案 | ✅ 已部署 |
| `proposals/p1-3-exit-code-final.md` | 火鸡儿 | exit code 标准化提案 | ✅ 已对齐 |
| `proposals/degrade-model-l0-l1-l2.md` | 火鸡儿 | 降级模型设计 | 📎 参考 |
| `proposals/scheduler-state-rules.md` | 火鸡儿 | Scheduler 状态判定规则 | 📎 参考 |
| `adapters/letta/adapter.sh` | 火鸡儿 | adapter v1.7（memfs + exit code） | ✅ 已部署 |
| `issues/ISSUES-619.md` | 火鸡儿 | 619 问题清单（25 项） | ✅ 已关闭 |
| `issues/ISSUES-619-PLUS.md` | 火鸡儿 | 619+ 补充清单（10 项） | 🔓 P2 遗留 |
