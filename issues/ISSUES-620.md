# AIM 620 问题清单（2026-06-20）

> 创建：2026-06-20 02:20 | 最后更新：2026-06-20 02:20
> 范围：6 月 20 日凌晨调试发现的全部问题
> 规则同 ISSUES-619.md：问题永留，解决结果追加

---

## 问题总览

| ID | 类别 | 问题 | 严重度 | 状态 | 责任方 | 解决日期 |
|----|------|------|:--:|:--:|--------|:--------:|
| 620-01 | Scheduler | StallWatchdog 自愈无效——触发复位后 dispatch_loop 未重新投递 | 🔴 | 未处理 | 呱呱 | - |
| 620-02 | 进程 | Letta TUI session 占用导致 adapter process 超时 exit 1 | 🔴 | 已知 (P0-1 修复但复发) | 火鸡儿适配 / 呱呱调度 | - |
| 620-03 | 队列 | ZS0003 queue.jsonl 积压 88 条旧消息（部分跨天），Scheduler 未消费 | 🔴 | 未处理 | 呱呱 (Scheduler) / 火鸡儿 (手动清理) | - |
| 620-04 | adapter | Letta adapter health 探针 memfs 检查产生假阴性——`letta agents list` 不含活跃 session agent | 🟡 | ✅ 已解决 | 火鸡儿 | 06-20 |
| 620-05 | adapter | Letta adapter v1.7 升级完成（memfs 磁盘探针 + exit code 对齐 P1-3） | 🟡 | ✅ 已解决 | 火鸡儿 | 06-20 |
| 620-06 | 部署 | ZS0001 同样陷入 StallWatchdog 自愈循环（28 条积压，OpenClaw 无回复） | 🔴 | 未处理 | 呱呱 | - |
| 620-07 | adapter | adapter.sh health exit 3 (CLI 不可用) + exit 4 (数据不存在) 是否被 main.py 正确解读 | 🟡 | 待验证 | 呱呱 | - |
| 620-08 | 发现 | 三方 aim-client 全部因 TUI/Runtime 占用同时陷入自愈循环——单点 Runtime 故障导致全群通讯中断 | 🔴 | 待讨论 | 三方 | - |

---

## 统计

| 指标 | 数量 |
|------|:--:|
| 总发现 | 8 |
| 🔴 P0 | 5 |
| 🟡 P1 | 3 |
| ✅ 已解决 | 2 |
| 待处理 | 5 |
| 待验证 | 1 |

---

## 详细记录

### 620-01：StallWatchdog 自愈无效
- **时间**：06-20 01:53 起，持续至今
- **现象**：ZS0003 aim-client 重启后第一条消息超时 exit 1，之后 StallWatchdog 每 30s 触发一次自愈（共 6+ 轮），队列从未真正消费
- **日志证据**：
  ```
  01:53:25 [ERROR] 投递循环异常: [letta-adapter] 处理超时 (15s)，可重试
  01:54:00 [WARNING] ⚠️ StallWatchdog: 30.0s 无投递, queue=8, 触发自愈 (#1)
    Scheduler: 强制复位 busy → IDLE (StallWatchdog)
  01:54:30 [WARNING] ⚠️ StallWatchdog: 30.0s 无投递, queue=8, 触发自愈 (#2)
  ... (持续 10+ 分钟)
  ```
- **分析**：StallWatchdog 复位 busy→IDLE 后，dispatch_loop 没有真正 re-dispatch。可能是 dispatch_event 没被 set 或者 dispatch_loop 在另一个分支卡住
- **状态**：🔴 未处理（呱呱 Scheduler 核心逻辑）

### 620-02：Letta TUI session 占用
- **时间**：06-20 01:53
- **现象**：aim-client 重启时 Letta TUI 正在活跃对话中，adapter process `letta -p` 阻塞 15s 超时
- **根因**：Letta 单 session 架构，TUI 打开时 subprocess 排队等待
- **现有机制**：adapter exit 1 → Scheduler RETRY → 重新入队
- **问题**：620-01 导致重试从未执行
- **状态**：🔴 已知陷阱，依赖 620-01 修复

### 620-03：queue.jsonl 积压
- **文件**：~/.aim/agents/ZS0003/queue.jsonl
- **行数**：88 行（25KB）
- **内容**：大部分是 06-19 的旧消息，最新的是 06-20 01:53 的
- **风险**：如果 Scheduler 恢复后逐条消费 88 条旧消息，adapter 会逐条调用 Letta 处理——可能导致大量无意义回复
- **建议**：Scheduler 修复后，先清理 queue.jsonl（备份后），避免洪水回复
- **状态**：🔴 未处理

### 620-04：health 探针 memfs 假阴性 ✅
- **时间**：06-20 02:00 确认
- **现象**：`letta agents list` 列出 3 个 agent，但均不是 `agent-local-f763730a`
- **根因**：`letta agents list` 只列出持久化注册的 agent（如 reflection subagent），不含仅存在于活跃 session 的 agent
- **修复**：v1.7 已切换到 memfs 磁盘检查（`~/.letta/lc-local-backend/memfs/$AGENT_ID/memory` 目录存在性）
- **限制**：只检查目录存在，不验证 agent 能否加载
- **状态**：✅ 已解决（06-20 adapter v1.7）

### 620-05：adapter v1.7 升级 ✅
- **改动**：
  1. `_verify_agent_id`: agents list → memfs 磁盘检查
  2. health exit code: 2/2 → 3/4（对齐 P1-3）
  3. process exit code: exit 3 → exit 4（AGENT_UNREACHABLE）
  4. 版本号：1.6.1 → 1.7
- **部署**：shared 仓库已 commit（1a6884a），local adapter 已同步
- **验证**：`adapter.sh health` → exit 0, `adapter.sh process` → exit 0
- **状态**：✅ 已解决（06-20 小火鸡儿）

### 620-06：ZS0001 StallWatchdog 自愈循环
- **时间**：06-20 01:52 起，持续至今
- **现象**：与 ZS0003 完全相同的模式——重启后第一条投递失败 → StallWatchdog 触发自愈 → 无效循环
- **日志**：
  ```
  01:52:59 [ERROR] 投递循环异常: OpenClaw 无回复
  01:53:34 [WARNING] ⚠️ StallWatchdog: 30.0s 无投递, queue=28, 触发自愈
  ```
- **根因**：与 620-01 相同，Scheduler dispatch_loop 自愈 bug
- **状态**：🔴 未处理（呱呱）

### 620-07：exit 3/4 被 main.py 正确解读
- **问题**：adapter health 改为 exit 3（CLI 不可用）/ exit 4（数据不存在）后
- main.py P1-3 定义的 exit code 处理：
  - exit=3 → FATAL → HumanInterventionError → 永久停止
  - exit=4 → AGENT_UNREACHABLE → DEGRADE + 可恢复
- **疑问**：health probe 调用 adapter health 时，exit 3 会触发永久停止吗？还是 health probe 有独立的 exit code 映射？
- **状态**：🟡 待验证

### 620-08：单点 Runtime 故障 → 全群通讯中断
- **发现**：三个 Agent 同时因各自的 Runtime（OpenClaw/Hermes/Letta）不可用或 TUI 占用，全部陷入自愈循环
- **后果**：NATS 消息正常收发，但三个 Agent 都无法消费队列 → 全群静默
- **设计问题**：当前架构下 Runtime 单点 = Agent 单点 = 群通讯单点
- **讨论方向**：
  - 是否需要在 Agent 无回复时自动发降级通知到群聊？
  - 是否需要「我挂了」自爆机制？（health probe 检测到 unhealthy 后发群消息）
- **状态**：🔴 待三方讨论

---

## 优先级矩阵

| 等级 | ID | 谁主导 | 预计 |
|:--:|-----|--------|------|
| 🔴 P0 | 620-01 | 呱呱 | 即刻 |
| 🔴 P0 | 620-06 | 呱呱 | 即刻（同根因） |
| 🔴 P0 | 620-03 | 呱呱修 Scheduler / 火鸡儿清队列 | 呱呱修完后 |
| 🟡 P1 | 620-07 | 呱呱验证 | 本周 |
| 🔴 P0 | 620-08 | 三方讨论 | 本周 |
| ✅ 已解 | 620-04/05 | 火鸡儿 | 06-20 |

---

## 大哥三层优化提案（2026-06-20 02:25）

> 大哥在排查完 blind spot 后提出三层进化方案。这是对现有 stabilization plan (proposals/aim-stability-optimization-2026-06-20.md) 的补充——呱呱方案只到 P1-3 exit code 标准化，**L3 自修复层完全缺失**。

### 现有问题盲区
1. **Registry 只管心跳**：不追踪 Runtime 是否存活
2. **告警不主动推送**：degrade 只在 aim-watch 终端打印，无人收到通知
3. **无人知晓的 degrade 风暴**：620-01 StallWatchdog 自愈循环持续 10+ 分钟，大哥完全不知情

### 三层方案

| 层 | 目标 | 改动 |
|----|------|------|
| **L1 暴露层** | 出问题早知道 | Registry + `aim.obs.registry` 新 subject：`agent_degraded` / `agent_stalled`；aim-watch 加告警持久化到 `~/.aim/system/alerts.log` |
| **L2 推送层** | 出问题被通知 | 3 次 degrade → warning 告警、5 次 → critical 告警；agent offline > 5min 同上；通过 NATS `aim.obs.alert` 广播 → 各 Agent 框架推送（OpenClaw→Slack、Hermes→系统通知、Letta→`osascript` 系统通知） |
| **L3 自修复层** | 自己修 | Letta agent 离线 → `letta -p "ping" --agent` 触发 lazy init；adapter timeout 累积 3 次 → `letta conversations trim` 清上下文；conversation 膨胀 → Cron 定期清理 |

### 分工推荐

| 模块 | 谁 | 做什么 |
|------|-----|------|
| **Registry 健康追踪** | 呱呱 | `_health_monitor` 加 Runtime degrade 追踪、`agent_degraded`/`agent_stalled` 告警 |
| **Observer 推送 + aim-watch 持久化** | 吉量 | degrade 阈值累计 → `aim.obs.alert`；aim-watch 加 `alerts.log` + 推送通知 |
| **adapter 自修复** | 我 🔧 | Letta agent 离线自动 ping 触发注册；conv 膨胀清理 cron；adapter timeout 自愈 |

### 与现有方案的关系

现有 `proposals/aim-stability-optimization-2026-06-20.md` 覆盖 P0-1 ~ P1-3（adapter 验证路径修复、Queue 隔离、env 注入、DEGRADE 容错、exit code 标准化），对应 **修现有逻辑**。
大哥的三层方案覆盖 **暴露 + 推送 + 自修复**，属于 **稳定性增强**——不是替代，是叠加。

### 我的 L3 项详细对照

| 场景 | 现有陷阱/gotchas 记录 | 自修复策略 | 风险 |
|------|----------------------|-----------|------|
| Letta agent 不在 agents list | gotchas.md: `letta -p TTY 问题`、`agents list 假阴性` | adapter health exit=4 后触发 `letta -p "ping" --agent` 触发 Letta lazy init 重新注册 | 已在 v1.7 memfs 探针中减轻，但 `letta -p` 可能耗时 >8s |
| adapter timeout 累积 3 次 | gotchas.md: `adapter.sh v1.7 set -e + timeout 124`、`conversation 复用导致历史回复` | 自动 `letta conversations trim --keep-last 5` 清上下文，降低后续 prompt 长度 | trim 本身也可能 timeout；需要验证 `letta conversations trim` 命令可用性 |
| conversation 膨胀 | gotchas.md: `Session 历史堆积 → Context Overflow 死循环` | Cron 定期检查 memfs conversation 目录大小，超过阈值自动清理旧 conv | Letta conversation 清理机制需要验证；不能误清活跃会话 |

### 待验证
- [ ] `letta conversations trim` 命令可用性（当前 Letta 版本是否支持）
- [ ] `letta -p "ping" --agent $ID` 能否触发 lazy init 重新注册
- [ ] macOS `osascript` 发送系统通知的权限和格式
- [ ] Cron 清理 conv 时如何排除活跃会话

---

## 不在此清单的问题（已知陷阱，已有记录）
- `letta agents list` 假阴性 → gotchas.md 已记录，v1.7 已修复
- Letta 单 session 并发限制 → 已知架构限制，adapter timeout + retry 兜底
- conversation 复用导致历史回复 → adapter v1.5.1 prompt 前缀约束已覆盖
