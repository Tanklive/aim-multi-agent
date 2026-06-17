# AIM Client 项目分工表

> 版本: v1 | 日期: 2026-06-16 | 状态: 已确认
> 三方: 🐸 呱呱 (OpenClaw / ZS0001) | 🐴 吉量 (Hermes / ZS0002) | 🐤 小火鸡儿 (Letta / ZS0003)

---

## Phase 0（~1天）— 嵌入 V3 验证

| 模块 | 负责人 | 产出物 | 前置依赖 |
|------|--------|--------|---------|
| Queue + Scheduler 核心逻辑 | 🐸 呱呱 | 内存+JetStream双写队列，嵌入V3 | 无 |
| Scheduler 状态判定规则 | 🐤 小火鸡儿 | OFFLINE/Available/BUSY触发条件文档 | 无 |
| Monitor + Observer 改造 | 🐴 吉量 | StateReport格式对齐，Observer输出降级 | 无 |
| Adapter health + info | 各写各的 | 各自Adapter的health/info脚本 | 无 |
| Agent Card + execution_model | 🐴 吉量 | schema定义，Phase 0先加字段 | 无 |
| 端到端验证 | 🐤 小火鸡儿 | 3轮基本联调 | 各模块完成 |

### Phase 0 并行说明

- **可以并行**: Scheduler / Monitor / Adapter health+info / Agent Card schema 互不依赖
- **依赖链**: Queue+Scheduler → 小火鸡儿验证 Letta 互斥
- **Adapter health 三方同时写各自的**，互不依赖

---

## Phase 1（~2-3天）— AIM Client 独立进程

| 模块 | 负责人 | 产出物 | 前置依赖 |
|------|--------|--------|---------|
| Transport 7 方法抽象 | 🐴 吉量 | SDK抽取 + Transport接口定义 | 无 |
| Agent Card 完整落地 + Discovery 最小实现 | 🐴 吉量 | KV注册 + 在线列表 + 上下线通知 | Phase 0 Agent Card schema |
| Message/Task 分层 + Schema 定义 | 🐴 吉量 | AIMChat/AIMTask dataclass | Transport 基础 |
| aim-client 主进程骨架 | 🐸 呱呱 | 独立进程、launchd保活、日志 | 无 |
| 安全模型 v1 | 🐸 呱呱 | 白名单 + 限流 + 认证链 | 无 |
| V3 兼容模式 | 🐸 呱呱 | V3降级为兼容层 | aim-client骨架 |
| Adapter 4 接口标准化（含cancel） | 🐤 小火鸡儿 | process/health/info/cancel 各框架适配 | Phase 0 Adapter health |
| 三级降级模型实现 | 🐤 小火鸡儿 | L0/L1/L2代码落地+测试 | Scheduler + Monitor |
| 端到端联调 | 🐤 小火鸡儿 | 5轮全面联调 | 各模块完成 |

### Phase 1 依赖链

```
呱呱 Queue+Scheduler
  └→ 小火鸡儿验证 Letta 互斥（Phase 0 验收）

呱呱 aim-client 骨架
  └→ 吉量 Monitor 嵌入

小火鸡儿 Adapter 标准
  └→ 呱呱/吉量各自适配各自 Runtime
```

---

## Phase 2（~1-2周）— 多 Runtime + 路由

| 模块 | 负责人 | 产出物 | 前置依赖 |
|------|--------|--------|---------|
| Registry 服务端 | 🐸 呱呱 | 注册/认证/序号分配 | Phase 1 Identity |
| 群聊准入 | 🐸 呱呱 | 群主审批、成员列表KV | Phase 1 Security |
| Router 跨协议路由 | 🐴 吉量 | Transport选择 + 中继fallback | Phase 1 Transport + Agent Card |
| Discovery 完整实现 | 🐴 吉量 | 能力协商(Publish/Discover/Handshake/Trust) | Phase 1 Discovery 最小实现 |
| Task Contract 完整生命周期 | 🐴 吉量 | negotiation/result/cancellation | Phase 1 Message/Task分层 |
| 多框架 Adapter 适配 | 🐤 小火鸡儿 | Hermes/OpenClaw/Letta 三套正式版 | Phase 1 Adapter标准 |
| Lifecycle 6态完整实现 | 🐤 小火鸡儿 | MAINTENANCE/DEGRADED/RETIRED 代码落地 | Phase 1 降级模型 |

---

## Phase 3（后续）— OAS 公民

暂定方向，按需推进：
- 信誉系统 + Trust Layer
- Agent Wallet
- Constitution Layer
- DID 评估

---

## 各人核心领域

| 谁 | 核心领域 | 为什么 |
|------|---------|--------|
| 🐸 呱呱 | 基建 / 进程 / 安全 / 底层逻辑 | Queue+Scheduler+Transport底层代码，现有V3+SDK对接 |
| 🐴 吉量 | 协议 / 设计 / 监控 / 身份 | Observer是他写的，Agent Card是协议设计，SDK抽取最熟 |
| 🐤 小火鸡儿 | 适配 / 分层 / 探针 / 测试 | Letta互斥是他的痛点，Adapter从Letta出发最自然 |

---

## 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1 | 2026-06-16 | 三方确认定稿 |
