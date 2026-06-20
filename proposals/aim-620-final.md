# AIM 6/20 终版 — 问题清单 + 根因分析 + 三层优化 + 分工

> 整合：呱呱 ZS0001 | 三方评审完成 | 2026-06-20 02:50
> 源文档：火鸡儿 `620-review-package.md` + 吉量 `aim-620-status-and-optimization.md`
> 大哥已阅三层方案

---

## 一、6.20 凌晨发现的问题（8 项）

| ID | 类别 | 问题 | 严重度 | 责任方 | 状态 |
|----|------|------|:--:|--------|:--:|
| 620-01 | Scheduler | **StallWatchdog 自愈无效** | 🔴 | 呱呱 | 根因已找到 |
| 620-02 | 进程 | Letta TUI session 占用 → adapter 超时 exit=1 | 🔴 | 火鸡儿 | 待适配 |
| 620-03 | 队列 | ZS0003 queue.jsonl 积压 88 条 | 🔴 | 呱呱修/火鸡儿清 | 待清理 |
| 620-04 | adapter | health 探针假阴性 → memfs | ✅ | 火鸡儿 | 已解决 |
| 620-05 | adapter | adapter v1.7 升级 | ✅ | 火鸡儿 | 已解决 |
| 620-06 | 部署 | ZS0001 同样 StallWatchdog 自愈 | 🔴 | 呱呱 | 同 620-01 |
| 620-07 | 协议 | exit 3/4 健康探针路径解读 | 🟡 | 呱呱 | 待验证 |
| 620-08 | 架构 | 单点 Runtime 故障全群静默 | 🔴 | 三方 | 三层方案覆盖 |

### 🎯 核心问题：620-01/06 StallWatchdog 自愈无效 — 根因已找到

**现象**：
```
01:53:25 [ERROR] 投递循环异常: [letta-adapter] 处理超时 (15s)
01:54:00 [WARNING] StallWatchdog: 30s 无投递, queue=8, 自愈(#1)
01:54:30 [WARNING] StallWatchdog: 30s 无投递, queue=8, 自愈(#2)
... (持续 10+ 分钟，从未真正 dispatch)
```

**根因**（呱呱 02:15 代码审查发现）：

`main.py` 第 387 行：
```python
self._stall_recovery_count = 0  # 成功投递 → 清零自愈计数
```

问题：这个清零在 **dispatch 开始时**执行（`on_dispatch_started()` 之前），不是在 dispatch **成功时**。后果：

```
StallWatchdog 触发 (count=1)
→ reset_to_idle()
→ dequeue → _stall_recovery_count = 0  ← BUG: 刚开始就清零
→ _call_adapter 超时(15s) → RetryableError → nack → sleep(2)
→ 下轮 dequeue → count 又清零
→ 永远达不到 3 → 永远不丢弃卡死消息 → 无限循环
```

**修复**：清零移到 exit=0 成功路径（1 行改动）。

---

## 二、稳定性修复（7 项）— 全部完成 ✅

| # | 问题 | 状态 |
|---|------|:--:|
| P0-1 | adapter 验证路径（memfs 替代 grep） | ✅ |
| P0-2 | Queue 多实例共享单文件 → per-agent | ✅ |
| P0-3 | 部署后 0 验证 → deploy-verify 8/8 | ✅ |
| P1-1 | env 注入（os.environ + config） | ✅ |
| P1-2 | DEGRADE 滑动窗口（30s/2 次） | ✅ |
| P1-3 | exit code 标准化（0/1/2/3/4/5+） | ✅ |
| P2-1 | ZS0002 旧 StallWatchdog 重启 | ✅ |

---

## 三、三层优化方案（大哥方案，三方共识版）

> 现有 P0-1~P1-3 修复了「现有逻辑」。三层方案是「暴露 + 推送 + 自修复」增强，不是替代，是叠加。

### 盲区
1. Registry 只管心跳，不管 Runtime 健康
2. degrade 只终端打印，不持久化不推送
3. degrade 风暴 → Watchdog 自愈循环 → 无人告警

### L1：暴露层 — 呱呱

**方案**：不新增 subject，复用心跳 KV 加 `runtime_status` 字段。

```
aim-client emit_obs("degrade") 时同步更新 heartbeat KV：
  runtime_status: "degraded" | "healthy" | "stalled"
Registry _health_monitor（30s 巡检）读心跳即可见 Runtime 状态。
```

| 事件 | 阈值 | 动作 |
|------|------|------|
| `agent_degraded` | runtime_status=degraded | Registry 记录 |
| `agent_stalled` | 连续 2 次 Watchdog 自愈失败（60s） | 告警 |

### L2：推送层 — 吉量 alertd

独立 `alertd` 守护进程（不绑 Agent，Agent 可能自己在 degrade）：

```
alertd
├── 订阅 aim.obs.alert
├── 阈值：3 次 emit_obs("degrade") → warning / 5 次 → critical
├── agent offline > 5min → critical
├── 写入 ~/.aim/system/alerts.log（结构化 JSON）
├── 推送到 grp_trio 群聊
└── 保障：
      launchd + KeepAlive + SuccessfulExit=false
      alertd --test 自检（deploy-verify 加一项）
      启动发 [alertd online] 到群
```

**阈值说明**：按 `emit_obs("degrade")` 事件次数算（跟 aim-watch 一致）。

### L3：自修复层 — 火鸡儿

**关键设计**：健康检查（秒回）≠ 恢复动作（可能慢），必须分层：

| 模式 | 触发 | 动作 | 超时 |
|------|------|------|:--:|
| `health`（不改） | Scheduler 探针 | memfs disk check | <1s |
| `recover`（新增） | exit=4 AGENT_UNREACHABLE | `letta -p "ping"` + poll + 重试 | 30s |
| trim | exit=1 × 3（等退避代码） | `letta conversations trim --keep-last 5` | 10s |
| cron | 磁盘 > 阈值 | 清理旧 conv | - |

**recover 入口**：Scheduler 收到 exit=4 → 调 `adapter.sh recover`，不是 adapter 自己检测。

**护栏**（吉量）：
```
自修复 N 次仍失败（N=3）
→ agent_stalled 告警
→ aim.obs.alert(level=critical)
→ 停止自修复，等人工介入
```

**恢复验证**（吉量）：复用 `deploy-verify.sh` 模板端到端 ping。

**硬前置依赖**：exit=1 退避代码（呱呱今晚补）—— 没有退避就没有「累积 3 次」触发点。

### 分工

| 优先级 | 模块 | 谁 | 估 |
|:--:|------|-----|:--:|
| 🔴 | StallWatchdog 修复 | 呱呱 | 今晚 |
| 🔴 | exit=1 退避（2s/4s/8s） | 呱呱 | 今晚 |
| 🔴 | L1 Registry KV + stalled | 呱呱 | 小 |
| 🔴 | L2 alertd 守护进程 | 吉量 | 中 |
| 🟠 | aim-watch 持久化 | 吉量 | 小 |
| 🟠 | 自修复护栏 | 吉量 | 小 |
| 🟡 | adapter.sh recover 模式 | 火鸡儿 | 中 |
| 🟡 | adapter trim 逻辑 | 火鸡儿 | 小 |
| 🟡 | conv 清理 cron | 火鸡儿 | 小 |
| 🟡 | 恢复端到端验证 | 三方 | 小 |
| 🟢 | 619+ 遗留 P2-a~e | 各自 | 小 |

---

## 四、执行顺序

```
Phase 0（呱呱今晚）：
  StallWatchdog 修复（L387 清零移位置）
  exit=1 退避实现（2s/4s/8s × 3）—— L3 硬前置

Phase 1（三方并行，无依赖）：
  呱呱: L1 Registry heartbeat KV + stalled 阈值
  吉量: L2 alertd + aim-watch 持久化 + L3 护栏规则
  火鸡儿: adapter.sh recover + trim + cron（等 Phase 0 退避完成）

Phase 2（串行）：
  三方: DEGRADE 恢复端到端验证

Phase 3（低优）：
  各自: 619+ P2 遗留项
```

---

## 五、三方共识确认

| # | 事项 | 呱呱 | 火鸡儿 | 吉量 |
|---|------|:--:|:--:|:--:|
| 1 | alertd 独立守护（含三条保障） | ✅ | ✅ | ✅ |
| 2 | 自修复 N=3 护栏 | ✅ | ✅ | ✅ |
| 3 | L3 放 adapter，Scheduler 不介入 | ✅ | ✅ | ✅ |
| 4 | DEGRADE 恢复端到端验证 | ✅ | ✅ | ✅ |
| 5 | 阈值按 emit_obs 次数 | ✅ | ✅ | ✅ |
| 6 | L1 用 heartbeat KV 不新增 subject | ✅ | ✅ | ✅ |
| 7 | 健康检查/恢复动作分层（health≠recover） | ✅ | ✅ | ✅ |
| 8 | 619+ 遗留各领各的 | ✅ | ✅ | ✅ |

---

## 参考文件

| 文件 | 作者 | 内容 |
|------|------|------|
| ⭐ `proposals/aim-620-final.md` | 呱呱整合 | 本文件 — 终版 |
| `proposals/620-review-package.md` | 火鸡儿 | 评审包（含详细技术分析） |
| `proposals/aim-620-status-and-optimization.md` | 吉量 | 状态汇总 + 三层分析 |
| `issues/ISSUES-620.md` | 火鸡儿 | 问题清单（8 项 + 日志） |
| `proposals/aim-stability-optimization-2026-06-20.md` | 呱呱 | P0-1~P1-3 修复方案 |
| `adapters/letta/adapter.sh` | 火鸡儿 | adapter v1.7（memfs + exit code） |

---

> 🐸🐴🤖 三方共识，按 Phase 0→1→2→3 推进。
