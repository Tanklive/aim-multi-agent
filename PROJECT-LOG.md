# AIM 项目记录 — 版本·功能·需求·问题

> 创建：2026-07-02 18:21 (大哥要求) | 维护者：呱呱 🐸
> 用途：全项目回溯 — 版本演替、功能矩阵、BUG 修复、需求溯源、决策记录
> 更新频率：每次变更后立即更新

---

## 一、版本演替

| 版本 | 日期 | 里程碑 | 主要变更 |
|------|------|--------|---------|
| v1.0.0 | 2026-06-08 | NATS 替代 WebSocket | NATS Server + JetStream 持久化、三 Agent 迁移 |
| v1.0.1 | 2026-06-09 | JWT 认证 | Operator 模式 JWT、NSC 管理、权限 scope |
| v1.1.0 | 2026-06-11 | Registry KV | Agent 注册/发现、KV 健康快照、事件日志 |
| v1.2.0 | 2026-06-13 | OAS 扩展层 Phase 0 | Capability Registry 设计、Trust Routing、DID |
| v1.2.1 | 2026-06-13 | 基础设施修复 | JWT 迁移验证、launchd KeepAlive、TOKEN 优化 |
| v1.3.0 | 2026-06-19 | Queue+Scheduler+HealthProbe 三层解耦 | 消息入库→调度分发→adapter执行 三层独立 |
| v1.3.1 | 2026-06-19 | 修复马拉松 | 42项 P0-P2 清零、持久化隔离、StallWatchdog |
| v1.3.2 | 2026-06-20 | 适配器幻觉防护 | L1+L2 双层去重、无效沟通三层防护 |
| v1.3.3 | 2026-06-21 | Python 13统一 + 79项审计 | 三方锁定 3.13、P0-P2 审计清零 |
| v1.4.0 | 2026-06-24 | context-card + 清理闭环 | L1骨架+L2即时、四座大山收尾、统一版本管理 |
| v1.5.0-alpha | 2026-07-02 | **L1 Adapter Protocol 标准化** | JSON stdin/stdout、SessionManager、ContextManager |

### 详细 CHANGELOG

见 `CHANGELOG.md`（每次发布同步更新）。

---

## 二、功能矩阵

### 已完成

| ID | 功能 | 版本 | 负责人 | 状态 |
|----|------|------|--------|------|
| F-001 | NATS 消息总线 (pub/sub + JetStream) | v1.0.0 | 呱呱 | ✅ |
| F-002 | AIM Client Core (三 Agent) | v1.0.0 | 三方 | ✅ |
| F-003 | JWT 认证 + Operator 管理 | v1.0.1 | 呱呱 | ✅ |
| F-004 | Registry KV (Agent 注册/发现) | v1.1.0 | 呱呱 | ✅ |
| F-005 | GroupAdmission (群组管理) | v1.1.0 | 吉量 | ✅ |
| F-006 | Queue 持久化 (queue.jsonl) | v1.3.0 | 呱呱 | ✅ |
| F-007 | Scheduler 调度分发 | v1.3.0 | 呱呱 | ✅ |
| F-008 | HealthProbe 三级探针 | v1.3.0 | 呱呱 | ✅ |
| F-009 | StallWatchdog 自愈 | v1.3.1 | 呱呱 | ✅ |
| F-010 | 双层去重 (msg_id + content) | v1.3.2 | 呱呱 | ✅ |
| F-011 | context-card (L1骨架+L2即时) | v1.4.0 | 呱呱 | ✅ |
| F-012 | **L1 Adapter Protocol v1.0** | v1.5.0 | 呱呱 | ✅ |
| F-013 | SessionManager (session复用≤5) | v1.5.0 | 呱呱 | ✅ |
| F-014 | ContextManager (SOUL + mtime热刷新) | v1.5.0 | 呱呱 | ✅ |
| F-015 | 消息类型四分类 (TASK/DISC/INFO/ACK) | v1.3.2 | 火鸡儿 | ✅ |
| F-016 | 无效沟通三层防护 (反信号/环路/前置) | v1.3.2 | 三方 | ✅ |
| F-017 | Python 3.13 全平台锁版本 | v1.3.3 | 呱呱 | ✅ |

### 进行中

| ID | 功能 | 版本 | 负责人 | 状态 |
|----|------|------|--------|------|
| F-101 | MCP Bridge (tools/list, tools/call) | v1.6.0 | 火鸡儿 ZS0003 | 🔄 PoC |
| F-102 | A2A Bridge (tasks/send, Agent Card) | v1.6.0 | 吉量 ZS0002 | 🔄 规范研究 |
| F-103 | T024 P0-P2 综合测试 Round2 | v1.5.0 | 呱呱 | 🔄 |

### 规划

| ID | 功能 | 版本 | 优先级 | 依赖 |
|----|------|------|--------|------|
| F-201 | OAS Capability Registry (P1.1) | v1.7.0 | 🥇 | — |
| F-202 | OAS Trust Routing (P1.2) | v1.7.0 | 🥈 | F-201 |
| F-203 | OAS Capability Discovery | v1.7.0 | 🥈 | F-201 |
| F-204 | REST/Webhook Bridge | v1.8.0 | 🥉 | — |
| F-205 | OAS DID Resolver (P2) | v1.8.0 | 🥉 | — |
| F-206 | Adapter trim/recover/cancel 全实现 | v1.6.0 | 🥈 | F-012 |

---

## 三、BUG 修复记录

| ID | 发现日期 | 版本 | 严重度 | 描述 | 根因 | 修复 | 引入版本 |
|----|---------|------|--------|------|------|------|---------|
| B-001 | 06-08 | v1.0.0 | P0 | AIM Server 僵尸进城 | launchd KeepAlive | kill -9 + 归档旧平台 | v1.0.0 |
| B-002 | 06-08 | v1.0.0 | P0 | observer 连接泄漏 | 断开不清理 | 立即清理,不走优雅等待 | v1.0.0 |
| B-003 | 06-11 | v1.0.1 | P0 | NATS JWT/Token 不能并存 | 配置冲突 | Operator 一次性切换 | v1.0.1 |
| B-004 | 06-11 | v1.0.1 | P0 | 基础设施变更不验证 | JWT迁移11h后发现 | 端到端验证铁律 | v1.0.0 |
| B-005 | 06-13 | v1.2.0 | P1 | 纯重试=自DDoS | 无退避 | 加退避+抖动+熔断 | v1.1.0 |
| B-006 | 06-13 | v1.2.0 | P1 | adapter 自激振荡 | 响应回环 | 过滤错误消息 | v1.1.0 |
| B-007 | 06-16 | v1.2.0 | P0 | nats-py 回调<10ms 串行化 | await 同步 | Queue+Scheduler 解耦 | v1.2.0 |
| B-008 | 06-16 | v1.2.0 | P1 | 日志双写 + dequeue不成对 | FileHandler+StreamHandler | 去 StreamHandler, ack pairs | v1.2.0 |
| B-009 | 06-17 | v1.2.0 | P1 | macOS fcntl.flock + inode漂移 | APFS | PID+pgrep > flock | v1.2.0 |
| B-010 | 06-17 | v1.2.0 | P1 | 旧代码不重启=没改 | 手动start不走hotload | 统一走launchd | v1.0.0 |
| B-011 | 06-19 | v1.3.0 | P0 | 多实例共享 queue.jsonl | 路径不含agent_id | 路径含进程身份 | v1.3.0 |
| B-012 | 06-19 | v1.3.0 | P0 | NOTICE 发布不实测 | 发了17h没人查 | 三验证:路径→进程→E2E | v1.3.0 |
| B-013 | 06-19 | v1.3.0 | P0 | launchd plist 缺 SuccessfulExit | exit1不算crashed | 加 SuccessfulExit=false | v1.2.0 |
| B-014 | 06-19 | v1.3.0 | P1 | Scheduler 五出口不全闭环 | exit1/2/3不恢复IDLE | on_retry/error 加transition | v1.3.0 |
| B-015 | 06-20 | v1.3.1 | P1 | adapter 处理旧消息自激 | StallWatchdog重投→无上下文 | 双层去重+独立去重集合 | v1.3.1 |
| B-016 | 06-21 | v1.3.2 | P1 | Python 3.14 混入 (ZS0003) | brew install 3.14 | 三Agent锁3.13路径 | v1.3.2 |
| B-017 | 06-23 | v1.4.0 | P1 | context-card 注入冲突 | 三方adapter不同步 | --session-key 隔离 | v1.4.0 |
| B-018 | 06-30 | v1.4.0 | P0 | python3 symlink→3.14 复发 | auto-recovery spawn | 硬编码路径,修复别名 | v1.3.3 |
| B-019 | 06-30 | v1.4.0 | P1 | MCP 僵尸繁殖 | 心跳poll频率过高 | poll 2min→15min | v1.4.0 |
| B-020 | 06-30 | v1.4.0 | P2 | nats-guard 误杀活 NATS | 不检查存活 | 先检查再决定 | v1.4.0 |
| **B-021** | **07-02** | **v1.5.0** | **P1** | **ZS0001 adapter 路径未同步** | **config 指向旧文件** | **同步 MD5 + 重启** | **v1.5.0** |

---

## 四、需求溯源

| ID | 需求 | 来源 | 日期 | 状态 |
|----|------|------|------|------|
| R-001 | NATS 替代 WebSocket | 呱呱提案 | 06-04 | ✅ v1.0.0 |
| R-002 | "先兼容天下，再形成标准，最后兼并" | 大哥定调 | 06-04 | 🔄 持续 |
| R-003 | OAS 扩展层 (Capability/Trust/DID) | 大哥构想 | 06-04 | 🔄 Phase 0 compelete |
| R-004 | Token 优化 (1000k→140k/天) | 三方共识 | 06-13 | ✅ 省86% |
| R-005 | Adapter 协议标准化 | 呱呱调研 | 06-29 | ✅ v1.5.0 |
| R-006 | L2 MCP 优先 (大哥裁决) | 大哥 | 06-29 | 🔄 PoC |
| R-007 | 项目版本/功能/需求完整记录 | 大哥 | 07-02 | ✅ 本文 |
| R-008 | 5轮会话模式测试 | 大哥 | 07-02 | ✅ 通过 |

---

## 五、关键决策记录

| ID | 日期 | 决策 | 原因 | 影响 |
|----|------|------|------|------|
| D-001 | 06-04 | NATS 做地基,非 WS | WS=传输层,NATS=消息中间件 | 架构根基 |
| D-002 | 06-04 | OAS 不替代 NATS Subject | OAS 管payload,NATS管路由 | 职责分离 |
| D-003 | 06-07 | 5分钟自动开干规则 | 防等待死循环 | 任务推进机制 |
| D-004 | 06-13 | 开发/测试/联调直接推进 | 大哥授权,不等指令 | 效率提升 |
| D-005 | 06-16 | 跨Agent通信走AIM(NATS),禁agent_bus | agent_bus仅OpenClaw内部 | 通信标准化 |
| D-006 | 06-19 | NOTICE发布三验证铁律 | 多次假完成教训 | 质量保障 |
| D-007 | 06-19 | 一个turn干到底 | Markdown停=假完成 | 执行模式 |
| D-008 | 06-20 | Python 3.13 全平台锁定 | 3.14 GIL已移除,兼容风险 | 版本稳定 |
| D-009 | 06-23 | context-card L1骨架+L2即时 | 冷启动有上下文 | Session隔离 |
| D-010 | 06-24 | aim-watch 临时独立版本 2.1.0 | 下次MAJOR纳入 | 版本管理 |
| D-011 | 06-29 | MCP优先 > A2A (大哥裁决) | MCP生态更成熟 | L2方向 |
| D-012 | 07-02 | 三Agent不可同时切换协议 | 一个挂了全部断 | 切换顺序:吉→火→呱 |

---

## 六、文件清单

| 文件 | 版本 | 最后更新 | 说明 |
|------|------|---------|------|
| `main.py` | v1.5.0-alpha | 07-02 | AIM Client Core (134KB) |
| `aim_nats_sdk.py` | v1.4.0 | 06-24 | NATS SDK (86KB) |
| `session.py` | v1.5.0-alpha | 07-02 | SessionManager |
| `context.py` | v1.5.0-alpha | 07-02 | ContextManager |
| `registry.py` | v1.3.0 | 06-20 | Registry Client |
| `security.py` | v1.0.0 | 06-20 | Security (白名单/限流) |
| `adapter.sh` | v2.2 | 07-02 | ZS0001 OpenClaw adapter |
| `ADAPTER-PROTOCOL.md` | v1.0-draft | 07-02 | L1 协议规范 |
| `ADAPTER-STANDARDIZATION.md` | v1.2 | 07-02 | L1+L2 标准化方案 |
| `AIM-NATS-ARCHITECTURE.md` | v4 | 06-09 | NATS 方案 (评审中) |
| `AIM-NATS-PROTOCOL.md` | v1.2 | 06-17 | Subject + 消息格式 |
| `AIM-SYSTEM-ARCHITECTURE.md` | v2.0 | 07-02 | **当前架构权威文档** |
| `OAS-DESIGN.md` | v1.2 | 06-13 | OAS 扩展层设计 |
| `CHANGELOG.md` | — | 07-02 | 版本变更日志 |
| `VERSION-STANDARD.md` | v1.4 | 06-24 | 版本管理标准 |

---

## 七、当前状态摘要 (2026-07-02)

```
L0 NATS           ████████████ ✅ 生产稳定
L1 Adapter v1.0   ████████████ ✅ 全量切换 (三 Agent)
L2 MCP Bridge     ████░░░░░░░░ 🔄 PoC (火鸡儿)
L2 A2A Bridge     ██░░░░░░░░░░ 🔄 规范研究 (吉量)
OAS Phase 0       ████████░░░░ ✅ 设计完成
OAS Phase 1       ░░░░░░░░░░░░ ⏳ 待启动
T024 综合测试 R2  ████████░░░░ 🔄 进行中
```
