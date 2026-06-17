# 三方交付整合报告

> 日期: 2026-06-16 | 整合人: 吉量 🐴

---

## 一、各人交付物总览

### 🐸 呱呱（已审核）

| 交付物 | 路径 | 功能 | 验证结果 |
|--------|------|------|---------|
| aim_client/types.py | `~/shared/aim/aim_client/types.py` | AgentState/StateReport/Message/AgentCard/AdapterInfo | ✅ 通过 |
| aim_client/queue.py | `~/shared/aim/aim_client/queue.py` | MessageQueue(enqueue/dequeue/ack/nack/dead) | ✅ 通过 |
| aim_client/scheduler.py | `~/shared/aim/aim_client/scheduler.py` | Scheduler(IDLE/BUSY/OFFLINE 三态 + 探针退避) | ✅ 通过（含 1 个 bug 已修复） |
| aim_client/health_probe.py | `~/shared/aim/aim_client/health_probe.py` | HealthProbe(adapter.sh health → StateReport) | ✅ 通过 |
| aim_client/__init__.py | `~/shared/aim/aim_client/__init__.py` | 包导出 | ✅ 通过 |

### 🐴 吉量（已自测 + 小火鸡儿审核已修）

| 交付物 | 路径 | 功能 | 验证结果 |
|--------|------|------|---------|
| Hermes adapter.sh | `~/shared/aim/adapters/hermes/adapter.sh` | process/health/info/cancel 4 接口 | ✅ 通过（4 个优化项已修） |
| SDK emit_state_report | `~/.hermes/aim/aim_nats_sdk.py` | StateReport 格式事件发射 | ✅ 通过 |
| SDK Agent Card | `~/.hermes/aim/aim_nats_sdk.py` | publish/fetch/list/card bucket | ✅ 通过 |
| V3 nats-agent 升级 | `~/shared/aim/nats-agent-v3/nats-agent-v3.py` | emit_state_report 替换 emit_obs | ✅ 通过 |
| aim-watch 增强 | `~/shared/aim/bin/aim-watch.py` | StateReport 显示 + 新事件类型 | ✅ 通过 |
| Transport 抽象 | `~/shared/aim/src/transport.py` | ABC 7 方法 + NATSTransport | ✅ 通过 |
| Message/Task 分层 | `~/shared/aim/src/aim_message.py` | AIMChat/AIMTask dataclass | ✅ 通过 |

### 🐤 小火鸡儿（已审核）

| 交付物 | 路径 | 功能 | 验证结果 |
|--------|------|------|---------|
| Letta adapter.sh | `~/.aim/agents/ZS0003/adapter.sh` | process/health/info/cancel 4 接口 | ✅ 通过 |
| Scheduler 状态判定规则 | `~/shared/aim/proposals/scheduler-state-rules.md` | OFFLINE/AVAILABLE/BUSY 规则文档 | ✅ 通过（含 4 个建议项已转呱呱） |

---

## 二、发现并已修复的问题

### 🐸 呱呱 side — 1 个 bug 已修

| 问题 | 文件 | 位置 | 修复 |
|------|------|------|------|
| 探针退避只在第一次进入 OFFLINE 时触发，后续 unhealthy 不再退避 | `scheduler.py` | L91-96 | 将退避逻辑移到 transition 判断外，每次收到 unhealthy 都退避 → `[5, 7.5, 11.2, 16.9, 25.3, 38, 57, 60, 60...]` |

### 🐴 吉量 side — 4 个优化已修（小火鸡儿建议）

| 问题 | 文件 | 原始 | 修复后 |
|------|------|------|--------|
| subscribe id 用 `id()` 跨进程不稳定 | `transport.py:167` | `f"sub_{id(subject)}_{id(callback)}"` | `f"sub_{uuid.uuid4().hex[:12]}"` |
| is_task_message 只匹配前缀 | `aim_message.py:171` | `content.startswith(t)` | `t in content`（子串匹配） |
| pgrep 匹配太宽 | `adapter.sh:83` | `pgrep -f "hermes"` | `pgrep -f "hermes-agent"` |
| cancel exit 0 但内容说 not_supported | `adapter.sh:130` | `exit 0` | `exit 2` |
| info 版本号含多行 | `adapter.sh:102` | `hermes --version \| head -1` | `\| grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+'` 输出纯版本号 |

### 🐤 小火鸡儿 side — 4 个建议已转（呱呱写 Scheduler 时会用到）

| 问题 | 文件 | 建议 |
|------|------|------|
| "AVAILABLE 但 pending 不入队"自相矛盾 | `scheduler-state-rules.md:143` | 改为 degraded 保持当前状态不变，不引入中间态 |
| exit 1 重新入队无重试上限 | `scheduler-state-rules.md:124` | 加"重试上限 3 次，超限→dead" |
| 连续 N 次 != healthy 歧义 | `scheduler-state-rules.md:78 vs 144` | 明确 exit=1 只计数不触发 OFFLINE，exit=2 立即 OFFLINE |
| exit 3 通知大哥的机制缺失 | `scheduler-state-rules.md:125` | 明确走 Observer 事件还是 AIM 消息 |

---

## 三、依赖关系

```
Phase 0 依赖链（已全部完成验证）:

呱呱 aim_client 包 (Queue + Scheduler)
  └→ 小火鸡儿验证 Letta 互斥（小火鸡儿处理）
  └→ 吉量 HealthProbe 集成（Phase 1）

吉量 SDK (emit_state_report + Agent Card)
  └→ 呱呱 Scheduler StateReport 对接（Phase 1）

小火鸡儿 Adapter 标准 (4 接口)
  └→ 呱呱 OpenClaw adapter（已完成）
  └→ 吉量 Hermes adapter（已完成）

Phase 0 所有交付物功能正确、无阻塞依赖。
```

---

## 四、Phase 1 待启动项

| 模块 | 负责人 | 前置 |
|------|--------|------|
| aim-client 主进程骨架 | 🐸 呱呱 | aim_client 包已完成 |
| 安全模型 v1（白名单 + 限流） | 🐸 呱呱 | 无 |
| V3 兼容模式 | 🐸 呱呱 | aim_client 包 |
| Adapter 4 接口标准化（含 cancel） | 🐤 小火鸡儿 | Letta adapter 已完成 |
| 三级降级模型实现 | 🐤 小火鸡儿 | Scheduler + Monitor |
| Transport 7 方法集成进 aim-client | 🐴 吉量 | transport.py 已完成 |
| Agent Card 完整落地（NATS KV） | 🐴 吉量 | SDK publish_agent_card 已完成 |
| Message/Task 分层集成 | 🐴 吉量 | aim_message.py 已完成 |
| Discovery 最小实现 | 🐴 吉量 | SDK list/fetch 已完成 |
