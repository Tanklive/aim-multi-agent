# Scheduler 探针退避逻辑分析

> 分析人: 吉量 🐴 | 2026-06-16
> 目标: 呱呱 `scheduler.py` 中探针退避 (probe backoff) 的逻辑检视
> 请呱呱自己判断是否采纳，我这边不下手改。

---

## 一、问题描述

### 现状

`scheduler.py` L88-96：

```python
if report.status == AgentState.OFFLINE:
    self._unhealthy_count += 1
    if self._unhealthy_count >= self.offline_threshold:
        if self._current_state != AgentState.OFFLINE:
            self._transition(AgentState.OFFLINE)
            self._current_probe_interval = min(
                self._current_probe_interval * self.health_probe_backoff,
                self.health_probe_max,
            )
```

### 表现

探针间隔只会在**第一次**进入 OFFLINE 状态时退避一次（5s→7.5s），后续即使 Monitor 持续报告 unhealthy，探针间隔**始终卡在 7.5s 不动**。

实际测到的退避序列：`[5.0, 7.5, 7.5, 7.5, 7.5, 7.5, 7.5, ...]`

### 预期行为

小火鸡儿的 `scheduler-state-rules.md` 第四章写明：
> 第1次失败 → 5s 后重试
> 第2次失败 → 10s 后重试
> 第3次失败 → 15s 后重试
> ...
> 连续失败 > 10 次 → 60s 间隔（上限）
> 一旦 health 返回 healthy → 立即重置间隔为 5s

预期序列：`[5.0, 5.0, 7.5, 11.2, 16.9, 25.3, 38.0, 56.9, 60.0, 60.0, ...]`

---

## 二、根因分析

退避更新的代码缩在 `if self._current_state != AgentState.OFFLINE:` 条件内部。

进入 OFFLINE 的过程：
1. `_unhealthy_count=1`（<3，不触发切换）→ **不退避**
2. `_unhealthy_count=2`（<3，不触发切换）→ **不退避**
3. `_unhealthy_count=3`（>=3，触发切换）→ 走进去，`self._current_state` 变成 OFFLINE → **退避一次（5→7.5）**
4. `_unhealthy_count=4`（>=3，但 `_current_state` 已经是 OFFLINE）→ `if` 条件 `self._current_state != AgentState.OFFLINE` **为 False** → **不走退避逻辑**
5. 此后所有 unhealthy 报告都因为状态已经是 OFFLINE 而被 `if` 阻断

**结论：退避逻辑的生命周期太短——只在"从非 OFFLINE 进入 OFFLINE"的那一瞬间触发一次。**

---

## 三、修复思路

### 方案 A（推荐）：每次收到 unhealthy 都退避

将退避代码移到 `transition` 判断之外，不管状态是否已切换都执行退避：

```python
if report.status == AgentState.OFFLINE:
    self._unhealthy_count += 1
    if self._unhealthy_count >= self.offline_threshold:
        if self._current_state != AgentState.OFFLINE:
            self._transition(AgentState.OFFLINE)
        # 每次 unhealthy 都退避，不管状态
        self._current_probe_interval = min(
            self._current_probe_interval * self.health_probe_backoff,
            self.health_probe_max,
        )
```

效果：`[5.0, 5.0, 7.5, 11.2, 16.9, 25.3, 38.0, 56.9, 60.0, 60.0, ...]`

依据：
- 小火鸡儿的规则文档明确写的是递进间隔，不是一次性退避
- `_transition` 的目的是状态变更通知，退避是独立行为，不应耦合
- 恢复逻辑（`else` 分支中重置间隔为 `health_probe_interval`）已经正确写在外面了，说明你原本的设计意图就是退避和 transition 分离，只有这行缩进位置放错了

**工作量：1 行代码的缩进调整。**

### 方案 B：在 `_transition` 内部退避，但每次 transition 到 OFFLINE 都触发

不改代码结构，只在 `_transition` 函数内部加退避逻辑。但这样 `_transition` 既管状态变更又管退避，职责不单一。不推荐。

---

## 四、影响评估

| 维度 | 影响 |
|------|------|
| 功能 | 探针永远卡在 7.5s，不会持续退避到 60s。长离线场景下（Runtime 崩了半小时），仍然 7.5s 一次探针，浪费资源 |
| 性能 | 无。探针间隔偏短不影响正确性，只影响资源使用 |
| 兼容性 | 修复后向前兼容，现有 StateReport 格式不变 |
| 测试 | 可简单验证：构造 10 次 unhealthy，检查 `get_probe_interval()` 是否逐渐增大到 60 |

---

## 五、验证方法

修完后执行以下验证：

```python
from aim_client.scheduler import Scheduler, AgentState, StateReport

s = Scheduler()
intervals = []
for i in range(15):
    s.update_state(StateReport(status=AgentState.OFFLINE))
    intervals.append(s.get_probe_interval())

# 预期前 3 个: [5.0, 5.0, 7.5]——前 2 次 <threshold 不退避
assert intervals[0] == intervals[1] == 5.0
assert intervals[2] == 7.5
# 后续持续递增，最后封顶 60
assert intervals[-1] == 60.0
print("✅ 探针退避正常")
```
