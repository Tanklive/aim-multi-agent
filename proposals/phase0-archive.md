# AIM Phase 0 项目记忆归档

> 日期: 2026-06-16
> 用途: 完整项目文档索引，供后续查阅

---

## 核心文档

| 文件 | 说明 |
|------|------|
| `~/shared/aim/proposals/aim-client-unified-v1.md` | 方案 v1.2（787 行，14 章+附录） |
| `~/shared/aim/proposals/aim-client-division.md` | 三方分工表（Phase 0/1/2/3） |
| `~/shared/aim/proposals/delivery-review-v1.md` | 交付整合报告（含所有发现的问题） |
| `~/shared/aim/proposals/phase0-e2e-test-result.md` | 端到端测试结果 |
| `~/shared/aim/proposals/scheduler-probe-backoff-analysis.md` | Scheduler 退避 bug 分析（转呱呱） |
| `~/shared/aim/proposals/scheduler-state-rules.md` | 小火鸡儿 Scheduler 规则文档 |

## 代码位置

### 呱呱 🐸
- `~/shared/aim/aim_client/` — aim_client 包（types/queue/scheduler/health_probe）
- `~/shared/aim/nats-agent-v3/nats-agent-v3.py` — V3 nats-agent（含 Phase 0 Queue+Scheduler）

### 吉量 🐴
- `~/.hermes/aim/aim_nats_sdk.py` → 同步到 `~/shared/aim/bin/aim_nats_sdk.py` — SDK（emit_state_report + Agent Card）
- `~/shared/aim/adapters/hermes/adapter.sh` — Hermes adapter（4 接口 v1.2）
- `~/shared/aim/src/transport.py` — Transport 7 方法抽象
- `~/shared/aim/src/aim_message.py` — Message/Task 分层
- `~/shared/aim/nats-agent-v3/nats-agent-v3.py` — V3 升级版（emit_state_report）
- `~/shared/aim/bin/aim-watch.py` — aim-watch v3.1（含 --all 参数）

### 小火鸡儿 🐤
- `~/.aim/agents/ZS0003/adapter.sh` → shared 在 `~/shared/aim/adapters/letta/` — Letta adapter（4 接口 v1.5）

## Phase 0 验证结果

| 验证项 | 结果 |
|--------|------|
| Queue enqueue/dequeue/ack/nack | ✅ |
| Queue 容量控制（超限丢弃最旧） | ✅ |
| Queue dead 队列（超时入 dead） | ✅ |
| Scheduler 三态（IDLE/BUSY/OFFLINE） | ✅ |
| Scheduler 探针退避（5→7.5→...→60s） | ✅（已修复缩进 bug） |
| Scheduler unhealthy 阈值（N=3） | ✅ |
| HealthProbe exit 0→IDLE / exit 1→BUSY / exit 2→OFFLINE | ✅ |
| Adapter process 端到端回复 | ✅ |
| Adapter health | ✅ |
| Adapter info | ✅ |
| Adapter cancel | ✅（Hermes realtime 不支持，exit 2） |
| 三方互发通信 | ✅ |
| SDK 共享版→部署版同步 | ✅（已修） |

## Phase 1 待启动

| 模块 | 负责人 |
|------|--------|
| aim-client 主进程骨架 | 🐸 呱呱 |
| 安全模型 v1 | 🐸 呱呱 |
| V3 兼容模式 | 🐸 呱呱 |
| Adapter 4 接口标准化（含 cancel） | 🐤 小火鸡儿 |
| 三级降级模型实现 | 🐤 小火鸡儿 |
| Transport 7 方法集成进 aim-client | 🐴 吉量 |
| Agent Card 完整落地（NATS KV） | 🐴 吉量 |
| Message/Task 分层集成 | 🐴 吉量 |
| Discovery 最小实现 | 🐴 吉量 |
