# AIM 自愈机制设计（Resilience v1）

> 背景：619+ 排查发现 dispatch_loop 死锁 5 小时无人知，queue 积压 135 条 0 ack，旧进程跑僵尸代码
> 目标：平台级自愈，公网部署后此类问题自动发现+恢复+通知

---

## 一、问题归类

| # | 故障模式 | 公网风险 | 根因 |
|---|---------|---------|------|
| 1 | dispatch_loop 死锁 | 群消息全丢 | DegradeError break 未 reset event |
| 2 | 代码更新未重启 | 修复不生效 | 无人/无机制触发重启 |
| 3 | 健康探针 crash | 状态判定失效 | dataclass 当 dict 调 .get() |
| 4 | queue 积压无感知 | 消息丢失 | 无积压告警 |
| 5 | adapter 超时不恢复 | 单点阻塞 | 超时后无退避/冷却机制 |

---

## 二、自愈架构

```
┌─────────────────────────────────────────────────────┐
│                 AIM Client (main.py)                │
│                                                     │
│  ┌─────────────┐   ┌──────────────┐                 │
│  │ Watchdog    │   │ HealthProbe  │                 │
│  │ (L1 进程内)  │   │ (L2 服务级)   │                 │
│  │             │   │              │                 │
│  │ last_drain  │   │ last_ack_ts  │                 │
│  │ stall 检测   │   │ queue_depth  │                 │
│  │ ≥30s→自重启  │   │ ≥50→告警     │                 │
│  └──────┬──────┘   └──────┬───────┘                 │
│         │                 │                          │
│         ▼                 ▼                          │
│  ┌─────────────────────────────────┐                │
│  │        SelfHealManager          │                │
│  │  判定 → 动作 → 通知              │                │
│  │  RESTART | DEGRADE | ALERT     │                │
│  └─────────────────────────────────┘                │
│                                                     │
│  ┌──────────────┐   ┌────────────────┐              │
│  │ Version Check│   │ IntegrityGuard │              │
│  │ (L3 部署级)   │   │ (L4 一致性)     │              │
│  │              │   │                │              │
│  │ shared mtime │   │ config hash    │              │
│  │ vs process   │   │ SDK checksum   │              │
│  │ 不一致→告警   │   │ 不一致→拒绝启动  │              │
│  └──────────────┘   └────────────────┘              │
└─────────────────────────────────────────────────────┘
```

---

## 三、四级自愈

### L1 — 进程内自愈（Watchdog）

**触发条件**：
- `last_dispatch_success` > `stall_timeout`（默认 30s）
- dispatch_loop task 异常退出
- health_probe task 连续 3 次异常

**动作**：
1. 记录 stall 事件到持久化日志
2. `self.running = False` → 清理资源
3. 退出码 `exit=4`（SELF_HEAL_RESTART）
4. 外部进程管理器（launchd/systemd/supervisor）自动拉起新进程

**实现**：
```python
# aim-client/main.py
class StallWatchdog:
    def __init__(self, stall_timeout: float = 30.0):
        self._last_drain_ts = time.time()
        self._stall_timeout = stall_timeout
    
    def heartbeat(self):
        self._last_drain_ts = time.time()
    
    def is_stalled(self) -> bool:
        return (time.time() - self._last_drain_ts) > self._stall_timeout

# 在 _dispatch_loop 每次成功 ack 后
self._watchdog.heartbeat()

# 独立 watchdog 循环
async def _watchdog_loop(self):
    while self.running:
        if self._watchdog.is_stalled():
            self.logger.critical(f"⛔ dispatch_loop stall {self._watchdog.stall_seconds}s → 自重启")
            self.running = False
            sys.exit(4)  # SELF_HEAL_RESTART
        await asyncio.sleep(5)
```

### L2 — Queue 积压自愈

**触发条件**：
- `enqueue - ack >= stall_threshold`（默认 50 条）
- 且 `last_ack_ts` > 60s 前
- 或 `ack_rate` < 1/min 持续 5 分钟

**动作**：
1. emit_obs("queue_stall", f"积压={n} 速率={rate}/min")
2. 尝试通过 NATS 通知群内其他 Agent
3. 降级→自重启

**实现**：
```python
# queue_persist.py 或 scheduler.py
class QueueHealthMonitor:
    def check(self) -> str:
        depth = self.enqueued - self.acked
        if depth > 50 and self._seconds_since_last_ack() > 60:
            return "STALL"
        if self._ack_rate_per_minute() < 1 and self._observation_minutes > 5:
            return "SLOW"
        return "OK"
```

### L3 — 版本一致性检测

**触发条件**：
- `shared/aim/` mtime > process start time + 60s
- 或 checksum 不匹配

**动作**：
- **开发模式**：自重启
- **生产模式**：告警 + 等待人工
- 生产模式不自动重启（避免因部署中断导致服务抖动）

**实现**：
```python
# integrity_guard.py (新增)
class VersionGuard:
    def __init__(self, shared_dir, mode="dev"):
        self.shared_dir = shared_dir
        self.mode = mode
        self._start_mtime = self._scan_shared()
    
    def check(self) -> tuple[bool, str]:
        current = self._scan_shared()
        if current > self._start_mtime:
            if self.mode == "dev":
                return True, "RESTART"  # 自动重启加载新代码
            else:
                return False, "NEW_VERSION_AVAILABLE"  # 告警不重启
        return False, "OK"
```

### L4 — 启动完整性校验

**触发条件**：每次进程启动时

**动作**：
1. 校验 config.json schema（已有）
2. 校验 SDK checksum vs shared
3. 校验 VERSION 一致性
4. 校验 adapter 可执行
5. 任一项失败 → exit=3 (FATAL)，不启动

---

## 四、退出码扩展

```
exit=0  OK
exit=1  RETRY         临时故障
exit=2  DEGRADE       降级
exit=3  FATAL         致命错误（人工介入）
exit=4  SELF_HEAL     自愈重启（进程管理器自动拉起）★ 新增
```

launchd/systemd 配置：
```xml
<!-- 对 exit=4 自动重启 -->
<key>KeepAlive</key>
<dict>
    <key>SuccessfulExit</key>
    <false/>
    <key>ExitCodes</key>
    <array>
        <integer>4</integer>  <!-- SELF_HEAL -->
    </array>
</dict>
```

---

## 五、observer 事件体系（可观测性）

| 事件 | 触发 | 消费者 |
|------|------|--------|
| `queue_stall` | 积压>50 且 60s 无 ack | 监控面板/告警 |
| `self_heal` | 自愈重启 | 群通知/日志 |
| `version_mismatch` | 代码过期 | 群通知 |
| `integrity_fail` | 启动校验失败 | 群通知+拒绝启动 |
| `health_probe_crash` | 健康探针连续异常 | 自愈判定 |

---

## 六、实施计划

| 序号 | 功能 | 复杂度 | 依赖 |
|------|------|:--:|------|
| 1 | StallWatchdog (L1) | 低 | — |
| 2 | QueueHealthMonitor (L2) | 低 | scheduler |
| 3 | launchd exit=4 配置 | 低 | L1 |
| 4 | VersionGuard (L3) | 中 | shared 监控 |
| 5 | IntegrityGuard (L4) | 中 | SDK checksum |
| 6 | observer 事件扩展 | 低 | emit_obs |

**优先级**：L1+L2 → 今天修完的两大问题直接防住。L3+L4 → Phase 2

---

## 七、公网部署差异

| 维度 | 本地开发 | 公网生产 |
|------|---------|---------|
| VersionGuard | 自动重启 | 告警不重启 |
| IntegrityGuard | 告警启动 | 拒绝启动 |
| 进程管理 | launchd | systemd/k8s liveness probe |
| watchdog stall | 30s | 15s（更快） |
| queue stall | 50条 | 20条/10s |
| 告警通道 | 群聊 | 群聊 + webhook + PagerDuty |
