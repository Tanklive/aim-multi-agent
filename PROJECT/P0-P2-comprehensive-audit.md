# AIM P0-P2 综合审计报告

> 审计人：呱呱 🐸 ZS0001
> 日期：2026-06-20 23:35
> 范围：P0-P2 全部功能、代码、版本、架构、问题管理
> 依据：原始需求 / OAS-AIM 定位 / 架构文档 / 协议规范 / VERSION-STANDARD / GOVERNANCE

---

## 一、审计总览

| 类别 | 检查项 | 通过 | 问题 | 风险 |
|------|:------:|:----:|:----:|:----:|
| 版本管理 | 6 | 2 | 3 | 1 |
| 代码架构 | 8 | 5 | 2 | 1 |
| 异构兼容 | 4 | 1 | 2 | 1 |
| 问题追踪 | 5 | 4 | 1 | 0 |
| 619 清单 | 26 | 26 | 0 | 0 |
| 620 清单 | 18 | 13 | 3 | 2 |
| 协议合规 | 5 | 3 | 2 | 0 |
| 运维可靠性 | 7 | 4 | 2 | 1 |
| **合计** | **79** | **58** | **15** | **6** |

---

## 二、P0 级问题（阻塞）

### P0-001: VERSION 文件与代码版本不一致 🔴

**现象**：
```
VERSION 文件:        1.3.2
SDK (3个副本):       1.3.1
aim_client/__init__: 1.3.1
CHANGELOG 最新:      1.3.3
```

**根因**：VERSION-STANDARD.md 定义「项目级 VERSION = SDK.VERSION = aim_client.VERSION」，但实际三个值各不相同。CHANGELOG 到 1.3.3 但 VERSION 文件未跟随更新。

**影响**：运行时版本检查无法实现（见 NOTICE 1.3.0 未做项），版本冲突时无统一基准。

**依据**：VERSION-STANDARD.md §3.1「核心模块变更：所有模块同步 MAJOR/MINOR」

**建议**：统一到 1.3.3，SDK 三副本同步，VERSION 文件更新。

---

### P0-002: Python 运行时版本异构 🔴

**现象**：
```
ZS0001: Python 3.13 (PID 2387)
ZS0002: Python 3.13 (PID 2426)
ZS0003: Python 3.14 (PID 539)
aim_issue_worker: Python 3.14 (PID 544)
```

**根因**：config.json 未锁定 Python 路径，ZS0003 配置指向了 python@3.14。

**影响**：asyncio 行为差异、JSON 编码差异、内存模型差异。3.14 的 GIL 移除改变了并发行为。

**依据**：620-08 单点 Runtime 故障教训 — 「三方 aim-client 同时陷入自愈循环」

**建议**：统一到 Python 3.13，在 config.json 显式写死 Python 路径。

---

### P0-003: ZS0001 adapter.sh 无版本号标记 🔴

**现象**：
```
ZS0001 adapter.sh: 无版本注释
ZS0002 adapter.sh: # v1.3
ZS0003 adapter.sh: # v2.1
```

**根因**：NOTICE 1.3.0 要求的「adapter.sh 头部加版本注释」在 ZS0001 上未执行。

**影响**：违反 VERSION-STANDARD.md 规范。版本审计无法覆盖 ZS0001。

**依据**：NOTICE-VERSION-1.3.0.md §三·1「adapter.sh 头部加版本注释」

**建议**：立即补上版本注释。

---

### P0-004: 双循环检测器未合并 🔴

**现象**：main.py `_handle_message()` 中有两套独立的确认循环检测：
- L865-912：`_is_confirm_loop()` — 吉量实现，基于 `from_id + 短内容 + 3次重复` 窗口检测
- L1030-1043：ACK skip 逻辑 — 呱呱实现，基于 emoji 剔除 + 纯文本匹配

**根因**：620-5轮审计已标注「确认循环检测器冗余，待合并」，但未执行。

**影响**：两套逻辑部分重叠但不完全等价，边界 case 行为不一致，维护成本翻倍。

**依据**：ISSUES.md 620-5轮记录：「shared main.py 有两套检测器，待合并」

**建议**：P1 合并为统一 `_should_skip_ack(envelope, content) → bool`。

---

### P0-005: 队列重新积压 — 清理无效 🔴

**现象**：
```
22:35 清理: ZS0001=0, ZS0002=0, ZS0003=0
23:35 现状: ZS0001=59, ZS0002=57, ZS0003=23
```

**根因**：U-005 双层去重阻止了**重复处理**，但**入队仍在发生**。StallWatchdog 依旧在触发重新投递，消息被反复入队。去重只在 dispatch 层生效，queue 层仍会积压。

**影响**：1 小时后队列回到 ~60 条。积压短时无碍，但 24 小时后可能重新达到 ~200+ 条，回到 22:11 状态。

**依据**：ISSUES.md U-001「StallWatchdog 自愈无效」

**建议**：在 Scheduler 出队时加「如果已被 DEDUP → 从队列移除」，而非只 skip。

---

## 三、P1 级问题（对齐）

### P1-001: NOTICE 1.3.0 三项未做仍然未做 🟡

| 未做项 | NOTICE 说明 | 当前状态 |
|--------|------------|---------|
| 运行时版本检查 | 「Phase 2+ 实现 AgentCard 查询和运行时版本比对」 | 仍未实现 |
| MIN_SDK_VERSION 拒绝 | 「标准文档定义了，代码未实现」 | 仍未实现 |
| adapter info 标准版 | 「将 adapter.sh info 输出 JSON 的 version 字段标准化」 | 仍未实现 |

**依据**：NOTICE-VERSION-1.3.0.md §四

**建议**：升级到 P1，Phase 2 开始时必须完成至少 1 项。

---

### P1-002: deploy-verify 仅做文件存在检查 🟡

**现象**：`scripts/deploy-verify.sh` 检查文件 MD5 一致性，但不检查：
- validate_envelope() 实际功能
- adapter health probe 端到端
- NATS 连接可用性

**根因**：620-5轮已发现「deploy-verify 缺 validator 功能测试」，标记为 P2 未处理。

**影响**：NOTICE 1.3.0 的教训（发布后无人实测 → 合写 bug）可能重现。

**依据**：ISSUES.md 620-5轮记录 / NOTICE 1.3.0 §一「NOTICE 发布后必须实测落地」

**建议**：加 end-to-end smoke test（发测试消息 → 等 adapter 回复 → 超时 30s 告警）。

---

### P1-003: PROTOCOL.md vs SDK PROTOCOL_VERSION 不一致 🟡

**现象**：
```
PROTOCOL.md:          v1.2（含 §4.5 已读回执）
SDK PROTOCOL_VERSION: 1.0
```

**根因**：协议文档升级到 v1.2 后，SDK 代码的 `PROTOCOL_VERSION` 未跟随。

**影响**：如果启用运行时协议版本比对，所有 Agent 都会因「协议版本过低」被拒绝。

**依据**：PROTOCOL.md v1.2 标头 / SDK L308 `PROTOCOL_VERSION = "1.0"`

**建议**：统一 SDK `PROTOCOL_VERSION = "1.2"`。

---

### P1-004: adapter.sh 版本分裂未根本解决 🟡

**现象**：ISSUES.md 已记录「5个adapter 5个版本(v1.3~v1.8.2)」，虽然后续修复了部分，但三端 adapter.sh 的版本号注释仍不一致（无/1.3/2.1）。

**依据**：VERSION-STANDARD.md §3.3「适配器独立版本号」+ ISSUES.md 620-5轮

**建议**：三方约定 adapter 版本号统一在注释中使用 `# adapter-version: X.Y` 格式。

---

### P1-005: healthd 查询异常 🟡

**现象**：health.db 有 679 条事件，但 `SELECT agent_id, status, MAX(timestamp)` 查询失败。

**根因**：待查 — 可能是 SQLite schema 变更后字段名不匹配，或 timestamp 存储格式不一致。

**依据**：运行检查结果

**建议**：排查 SQLite schema，修复 healthd 查询。

---

## 四、P2 级问题（优化）

### P2-001: observer.jsonl 轮转可能未生效 🟢

**现象**：833 行 / 170KB。上次清理（N-006）之前是 28k+ 条。

**根因**：aim_logrotate.sh cron 每天 03:00 执行，今天可能还没到执行时间。但文件仍在持续增长。

**建议**：确认 rotate cron 正常运行，加 100MB 上限兜底。

---

### P2-002: 仅 1 个 TODO 未清 🟢

**现象**：main.py L1086 `# TODO Phase 2: AIMTask lifecycle tracking`

**建议**：Phase 2 开始时优先处理。

---

### P2-003: JetStream consumer 旧配置未清理 🟢

**现象**：ISSUES.md 620-5轮记录 JetStream consumer retention 配置未更新。

**建议**：nsc 重建 consumer 使 `max_age=7d / max_deliver=5` 生效。

---

### P2-004: 无部署后端到端自动化测试 🟢

**现象**：每次部署依赖人工发消息验证，无自动化端到端测试。

**建议**：Phase 2 加 `test_e2e.sh`：发送测试消息 → 等待三者回复 → 验证 observer 记录。

---

## 五、隐藏风险

### R-001: observer 去重过滤器可能导致消息丢失 🟡

**场景**：aim-watch.py 新增 `channel_filter + status=="received"` → return，这意味着在 `--dm-only` 模式下 observer 的 received 事件不显示。如果消息订阅通道故障，用户将看不到任何消息。

**建议**：observer 过滤加 `--show-all` 覆盖开关。

---

### R-002: PROCESSED_IDS 重启丢失 🟡

**场景**：三端重启 → `_processed_ids` set 清空 → 重启前已处理的消息可能被 StallWatchdog 重新投递 → L1 去重失效，仅 L2 内容去重兜底（120s 窗口）。

**风险**：重启后 2 分钟内有幻觉窗口。

**建议**：将 `_processed_ids` 持久化到文件（JSONL，容量 2000 条），启动时恢复。

---

### R-003: ZS0003 Python 3.14 GIL 移除的并发行为 🟡

**场景**：Python 3.14 引入了 free-threaded 模式（PEP 703）。ZS0003 的 asyncio 行为可能与 3.13 不同，特别是与 lette CLI 交互时。

**建议**：验证 ZS0003 在 3.14 下的 adapter 调用无竞态条件。

---

### R-004: 三方 config.json 未完全同步 🟡

**场景**：620 审计发现 ZS0003 config 缺字段，虽已修复，但无机制保证后续新增配置项三方同步。

**建议**：加 `config_schema.json` 和启动时校验。

---

### R-005: INTEGRATION.md 中 Transport 7方法实现状态不明 🟡

**现象**：INTEGRATION.md 约定 Transport 需实现 7 个方法，但 main.py 中 Transport 类仅实现了 `emit_obs()`、`emit_health()`、`send_dm()`、`send_grp()` 等方法，缺少完整的 7 方法对照。

**建议**：与吉量确认 Transport 7 方法清单并补全。

---

### R-006: 问题升级路径无演练验证 🟢

**现象**：GOVERNANCE.md 定义了 MAJOR/Minor 变更流程，但从未在任何实际协议变更中演练过。

**建议**：Phase 2 首个 MAJOR 变更时正式演练一次。

---

## 六、619 清单全部通过 ✅

26 项 619 问题全部关闭。无残留。✅

---

## 七、620 清单状态

| 状态 | 数量 | 明细 |
|:----:|:----:|------|
| ✅ 已关闭 | 13 | 620-04, 620-05 + N-001~N-006 + POST-01~POST-02 + POST-06 + 620-5轮 6项 |
| 🔴 仍开放 | 3 | U-001 (StallWatchdog), U-002 (Letta TUI), U-003 (ZS0003积压) |
| 🔴 新增 | 4 | U-004 (单点Runtime), U-005 (幻听串扰), U-006 (observer截断), U-007 (群聊不可靠) |

**U-005 修复状态**：双层去重已部署（986d95d），但用户尚未确认效果。

---

## 八、架构合规分析

### 对 OAS-AIM-AIM-client 的定位检查

| 定位要求 | 当前状态 | 合规 |
|----------|---------|:--:|
| 统一通信终端 | main.py 单文件 1251 行，三端同文件 | ✅ |
| NATS 可插拔 Transport | Transport 类封装 SDK，3个 emit 方法 | ✅ |
| Queue+Scheduler+HealthProbe 三层解耦 | `_dispatch_loop` + `_health_probe_loop` 独立运行 | ✅ |
| Adapter 标准化 | exit code 4级 + health/info/cancel/process/recover/trim | ✅ |
| 安全模型 | AuthChain 链式（SourceIdentity/RateLimit/Allowlist） | ✅ |
| 版本管理 | **VERSION 不匹配** (P0-001) | 🔴 |
| 协议合规 | **PROTOCOL_VERSION 落后** (P1-003) | 🟡 |
| 运维零 Token | healthd 独立通道 + ACK skip | ✅ |
| 事件上报 | NATS issue channel + Worker | ✅ |

---

## 九、总结与行动建议

### 立即修复（P0）

| ID | 问题 | 工作量 | 责任人 |
|----|------|:--:|--------|
| P0-001 | VERSION 文件统一到 1.3.3 | 2min | 呱呱 |
| P0-002 | ZS0003 Python 统一到 3.13 | 5min | 火鸡儿 |
| P0-003 | ZS0001 adapter.sh 加版本注释 | 1min | 呱呱 |
| P0-005 | 队列积压根因修复（出队去重联动） | 15min | 呱呱 |

### 近期修复（P1）

| ID | 问题 | 工作量 |
|----|------|:--:|
| P1-001 | NOTICE 1.3.0 三项中至少完成1项 | 30min |
| P1-002 | deploy-verify 加 E2E smoke test | 15min |
| P1-003 | PROTOCOL_VERSION = "1.2" | 1min |
| P1-005 | healthd 查询修复 | 10min |
| P0-004 | 双循环检测器合并 | 20min |

### 风险缓解

| ID | 措施 |
|----|------|
| R-002 | _processed_ids 持久化（JSONL，重启恢复） |
| R-003 | ZS0003 在 Python 3.13 下重测 adapter |
| R-005 | 与吉量确认 Transport 7方法清单 |

---

*报告结束。此文件写入 `shared/aim/PROJECT/` 供三方审阅。*
