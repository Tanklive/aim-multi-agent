# Process Health Degrade States — 自守护层状态机

> 吉量 (ZS0002) · 小火鸡儿 (ZS0003) | 2026-07-22

---

## 一、两层状态机关系

```
┌─────────────────────────────────────────┐
│  Scheduler 层（三态，不动）               │
│  OFFLINE / AVAILABLE / BUSY              │
│  只关心 adapter.sh health 的 exit code   │
│  不穿透到内部                             │
├─────────────────────────────────────────┤
│  Main 进程自守护层（五态，新增）           │
│  HEALTHY / WARNED / PROTECTED / DEAD     │
│  决策粒度更细，在 main 内部跑             │
└─────────────────────────────────────────┘
```

- Scheduler 层：只关心 `adapter.sh health` 的 exit code，不感知自守护层内部状态
- 自守护层：在 main 进程内部，不穿透到 Scheduler

---

## 二、/probe → adapter.sh health，exit code 映射

| health exit | 含义 | 自守护层行为 |
|---|---|---|
| 0 (OK) | 健康 | 维持当前状态，走正常恢复路径 |
| 1 (WARN) | 警告 | 不触发状态变更，记录 metric |
| 2 (ERROR) | 错误 | 触发 WARNED |
| 3 (FATAL) | 致命 | 触发 DEAD（直接拉起自愈） |
| 4 (DEGRADE) | 降级 | 触发 PROTECTED |

---

## 三、五态自守护状态机

```
                    ┌──────────┐
          ┌─────────│ HEALTHY  │◄──────────┐
          │         └────┬─────┘           │
          │ exit=2       │ exit=3          │ 自愈成功
          ▼              ▼                 │
    ┌──────────┐   ┌──────────┐           │
    │  WARNED  │   │   DEAD   │───────────┘
    └────┬─────┘   └──────────┘
         │           自愈 = main 进程内部重新拉起
         │ 连续3次     worker/协程，作用域限定进程内
         │ 超时升级
         ▼
    ┌──────────┐
    │PROTECTED │ ◄── exit=4 (DEGRADE)
    └──────────┘
```

### 3.1 HEALTHY — 健康态

- 所有指标正常
- exit=0 维持此状态

### 3.2 WARNED — 警告态

- 触发：exit=2 (ERROR)，如超时
- 行为：不自动降级，记录 metric
- 升级条件：连续 3 次超时 → PROTECTED（防止误杀瞬态网络抖动）

### 3.3 PROTECTED — 保护态

- 触发：exit=4 (DEGRADE)，如内存超标；或 WARNED 连续3次升级
- 行为：降级保护，不自杀
- 恢复：外部监控兜底，或资源恢复后自动回到 HEALTHY
- 设计意图：mem 是硬资源，超了就是超了，外部监控能兜底恢复，不需要自愈只需降级

### 3.4 DEAD — 致命态

- 触发：exit=3 (FATAL)
- 行为：main 进程内部重新拉起 worker/协程
- 作用域：限定在进程内，不是重启 Runtime

---

## 四、WARNED vs PROTECTED 不对称（设计意图）

| | WARNED | PROTECTED |
|---|---|---|
| 触发 | exit=2 超时 | exit=4 内存超标 |
| 原因 | 可能是瞬态网络抖动 | 硬资源，超了就是超了 |
| 升级 | 连续3次 → PROTECTED | 直接触发 |
| 自愈 | 不触发 | 不触发（外部监控兜底） |
| 恢复 | 单次 ok → HEALTHY | 资源恢复 → HEALTHY |

不对称是刻意的：超时可能瞬态（防止误杀），内存是硬伤（不需要自愈）。

---

## 五、DEAD 自愈范围

- 是 main 进程内部重新拉起 worker/协程
- **不是**重启 Runtime
- **不是**重启 main 进程
- **不是**触发 Scheduler 层 OFFLINE
