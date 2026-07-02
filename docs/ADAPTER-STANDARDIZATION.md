# AIM Adapter 协议标准化方案

> 版本：v1.0-draft | 日期：2026-07-02 | 作者：呱呱 (ZS0001)
> 状态：三方评审通过 ✅，待大哥终审
>
> **评审人**：吉量 ✨🐴✨ (ZS0002) · 小火鸡儿 🐤 (ZS0003)

---

## 一、问题诊断

### 1.1 触发场景
2026-07-02 15 轮群聊+DM 压力测试（6 分钟 15 条混合消息），暴露核心瓶颈：

| 指标 | 数值 |
|------|------|
| 总体响应率 | 60% |
| Queue 峰值 | 13 |
| 最慢响应延迟 | 220s |
| "OpenClaw 无回复 (degraded)" | 12 次 |
| StallWatchdog 触发 | 10+ 次 |

### 1.2 根因分析

**不是性能 bug，是架构缺陷。**

adapter.sh 第 83 行：

```bash
SESSION_KEY="agent:aim-reply:reply-$(date +%s)-$$"
```

每条消息创建一个全新的 OpenClaw session，产生 1.5s 冷启动开销。高峰时 8 条消息 × 5s = 40s 串行处理 → queue 雪崩。

**更深层根因**：AIM Core 没有统一的 Session/Context 管理能力，导致 adapter 层被迫越界：
- ❌ adapter 管理 session 生命周期（该 Core 管）
- ❌ adapter 读取 SOUL.md / context-card（该 Core 管）
- ❌ adapter 组装 prompt + 注入上下文（该 Core 管）
- ✅ adapter 调 LLM 返回回复（唯一该它做的）

### 1.3 三方现状对比

| Agent | 当前 session 方式 | 单次延迟 | 瓶颈 |
|-------|-----------------|---------|------|
| ZS0001 OpenClaw | `$(date +%s)` 每次新建 | ~5s (1.5s cold) | session 冷启动 |
| ZS0002 Hermes | API Server 长驻 (v2.0) | ~8s (曾 54s) | 已优化 |
| ZS0003 Letta | `--new` 每次独立 conv | ~17s | CLI 冷启动 |

**只有 ZS0002 走到了 API Server 模式**，其余两家还是 CLI-per-message。标准化后：
- ZS0001：SessionManager 管理 session 池 → 复用 → 消除 1.5s 冷启动
- ZS0002：改动 ~5 行，adapter 削到纯转发壳
- ZS0003：改动 ~30 行，context 由 Core 拼好注入

---

## 二、架构设计

### 2.1 改前（adapter 越界）

```
AIM Core                          Adapter (越界)
  │                                 │
  ├── raw msg ────────────────────→ │
  │                                 ├── 读 SOUL.md (不该)
  │                                 ├── 读 context-card (不该)
  │                                 ├── 拼 prompt (不该)
  │                                 ├── 生成 SESSION_KEY (不该)
  │                                 ├── 调 LLM
  │←── reply ────────────────────── │
```

### 2.2 改后（职责归位）

```
AIM Core                                    Adapter (极薄)
  │                                           │
  ├── SessionManager: 分配 session_id         │
  ├── ContextManager: 读+缓存+注入            │
  ├── 组装完整 {session_id, context, msg} ──→ │
  │                                           ├── 调 LLM
  │←── {reply, usage} ──────────────────── │
  │                                           │
  ├── 发送回复                                │
```

### 2.3 新增模块

**SessionManager** (`aim_client/session.py`, ~150 行)

| 功能 | 说明 |
|------|------|
| 按 from_id 路由 | ZS0002→pool:ZS0002, ZS0003→pool:ZS0003，上下文隔离 |
| CLI 模式 | 管理 session 创建/复用/trim（复用 ≤5 次后重建） |
| API Server 模式 | 不管理生命周期，只组装上下文 |
| 上下文组装 | 决定每条消息带多少历史（当前：前 3 条，可配） |
| Pool 大小 | 可配置，默认 3 |

**ContextManager** (`aim_client/context.py`, ~80 行)

| 功能 | 说明 |
|------|------|
| 文件读取 | SOUL.md、context-card.md、context-live.md |
| 缓存 | 首次读盘，后续命中缓存 |
| 热更新 | mtime 变化时自动刷新 |
| 降级 | 文件不存在 → 返回空 context，不崩溃 |

### 2.4 不动模块

```
NATS Transport    →  不变
Message Queue     →  不变
Scheduler         →  不变
Health Probe      →  不变
StallWatchdog     →  不变
Registry          →  不变
aim-watch         →  不变
healthd / nats-guard → 不变
```

---

## 三、Adapter 协议规范 (ADAPTER_PROTOCOL.md 草案)

### 3.1 通信方式

- **CLI 模式**：stdin JSON → stdout JSON
- **API Server 模式**：HTTP POST JSON → JSON response（adapter 转发）

### 3.2 消息格式

**输入**（Core → Adapter）：

```json
{
  "action": "process",
  "session_id": "pool:ZS0002:1",
  "context": "你是呱呱🐸...\n项目上下文...\n前3条群聊记录...",
  "message": "@ZS0002 检查一下 NATS 连接状态",
  "from": "ZS0002",
  "timeout": 30,
  "metadata": {
    "trace_id": "abc123",
    "priority": "normal"
  }
}
```

**输出**（Adapter → Core）：

```json
{
  "reply": "🐸 NATS 连接正常，JetStream stream aim-messages 在线，延迟 <5ms",
  "usage": {
    "tokens": 42,
    "model": "deepseek-v4-pro"
  }
}
```

### 3.3 字段说明

| 字段 | 方向 | 必填 | 说明 |
|------|------|------|------|
| `action` | → | ✅ | `health` / `info` / `process` / `cancel` / `trim` / `reload` / `status` |
| `session_id` | → | ✅ | Core 分配的会话标识，按 from_id 路由 |
| `context` | → | ✅ | Core 拼好的完整上下文（性格+项目+对话历史） |
| `message` | → | ✅ | 当前消息内容 |
| `from` | → | ✅ | 消息发送者 ID |
| `timeout` | → | ✅ | 本次调用超时（秒），Core 根据 queue 深度动态调整 |
| `metadata` | → | ❌ | 扩展字段（trace_id、priority 等） |
| `reply` | ← | ✅ | 回复文本 |
| `usage` | ← | ❌ | token 用量统计 |

### 3.4 Lifecycle 命令

| action | 退出码 | 说明 |
|--------|--------|------|
| `health` | 0=正常, 2=挂了 | 健康检查 |
| `info` | 0 | 返回 adapter 版本、runtime 类型、protocol 版本 |
| `process` | 0=正常, 1=可重试, 2=降级, 3=人工 | 处理消息 |
| `cancel` | 0=已取消, 2=不支持 | 取消进行中的任务 |
| `trim` | 0 | 清理/重置 session 上下文 |
| `reload` | 0 | 热刷新缓存（personality/memory 变后无需重启） |
| `status` | 0 | 返回运行时指标（conv 数、队列深度等） |

### 3.5 各 Agent 适配说明

| Lifecycle | ZS0001 (OpenClaw) | ZS0002 (Hermes API) | ZS0003 (Letta CLI) |
|-----------|------------------|--------------------|--------------------|
| health | ✅ curl Gateway | ✅ curl API | ✅ letta status |
| info | ✅ | ✅ | ✅ |
| process | ✅ openclaw agent | ✅ POST /chat | ✅ letta --new |
| cancel | ❌ exit 2 | ❌ exit 2 | ❌ exit 2（不支持 confirm） |
| trim | ✅ session trim | ✅ HTTP /trim | ❌ exit 0（--new 无状态） |
| reload | ✅ 标记缓存失效 | ✅ HTTP /reload | ✅ 重新读文件 |
| status | ✅ | ✅ | ✅ |

---

## 四、实施计划

### Phase 1：定标准（不做代码改动）

| 步骤 | 内容 | 产出 |
|------|------|------|
| P1.1 | 大哥终审本方案 | ✅ 本方案通过 |
| P1.2 | 编写 ADAPTER_PROTOCOL.md 正式版 | 协议规范文档 |
| P1.3 | 三方确认协议字段定义（JSON Schema） | 协议定稿 |
| P1.4 | 群内确认各 adapter mapping 成本 | 实施清单 |

### Phase 2：Core 模块开发

| 步骤 | 内容 | 负责人 |
|------|------|--------|
| P2.1 | 开发 `aim_client/session.py` (SessionManager) | 呱呱 |
| P2.2 | 开发 `aim_client/context.py` (ContextManager) | 呱呱 |
| P2.3 | 修改 `main.py` `_call_adapter()` 走新协议 | 呱呱 |
| P2.4 | 本地单元测试（SessionManager + ContextManager） | 呱呱 |

### Phase 3：Adapter 逐个切换（按复杂度递增）

| 顺序 | Agent | 原因 | 预计改动 |
|------|-------|------|---------|
| 🥇 P3.1 | ZS0002 吉量 | API Server 最简，验证协议可行性 | ~5 行 |
| 🥈 P3.2 | ZS0003 火鸡儿 | CLI 模式，Letta --new 无状态 | ~30 行 |
| 🥉 P3.3 | ZS0001 呱呱 | Session 复用是主要收益点，垫后验证 | ~30 行 |

### Phase 4：集成测试

| 步骤 | 内容 |
|------|------|
| P4.1 | 单 Agent 端到端测试（每人跑通） |
| P4.2 | 三方联调 15 轮压力测试（对比改前基线） |
| P4.3 | 公网接入模拟（用新 adapter 模板从零接入） |

### Phase 5：文档 + 发布

| 步骤 | 内容 |
|------|------|
| P5.1 | 更新 README + 接入指南 |
| P5.2 | GitHub Release（v1.5.0） |
| P5.3 | 发布 ADAPTER_PROTOCOL.md 为标准接入文档 |

---

## 五、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| SessionManager 上下文隔离失败（ZS0002 污染 ZS0003） | 低 | 高 | 按 from_id 严格隔离 + 单元测试 |
| ContextManager 缓存过期（SOUL.md 改后不刷新） | 中 | 中 | mtime 检查 + reload action |
| 协议字段遗漏（后续发现需要新字段） | 中 | 低 | metadata 扩展字段预留 |
| 三 adapter 同时切换风险 | 中 | 高 | **逐个切**（吉量→火鸡儿→呱呱），每个验证后再切下一个 |
| Letta --new 无 session 复用 | 高 | 低 | 已确认，SessionManager 对此模式退化为 pass-through |
| OpenClaw session 复用后上下文膨胀 | 中 | 中 | 复用 ≤5 次后 trim 重建，pool size 可配 |

---

## 六、预期收益

| 指标 | 改前 | 改后目标 |
|------|------|---------|
| ZS0001 单次 adapter 延迟 | ~5s | ~2.5s（消除 1.5s session 冷启动） |
| 15 轮压力测试响应率 | 60% | ≥80% |
| Queue 峰值 | 13 | ≤3 |
| "OpenClaw 无回复" | 12 次 | ≤3 次 |
| adapter.sh 代码量 | 104 行 | ~30 行 |
| 新 Agent 接入时间 | 数小时（需理解 adapter 内部逻辑） | <30 分钟（只实现协议） |

---

## 七、附录

### A. 三方评审摘要

**ZS0002 吉量** (14:35)：
- ✅ SessionManager 按 from_id 路由合理
- ➕ 协议加 `action` + `metadata` 字段
- ➕ lifecycle 加 `reload` + `status`
- ✅ API Server 下 Core 拼 context 更合理
- 改动量 ~5 行

**ZS0003 火鸡儿** (14:37)：
- ✅ 方案方向正确，架构纠偏
- ➕ 协议加 `timeout` 字段
- ✅ Letta --new 模式，SessionManager 退化为 pass-through
- ⚠️ Letta 不支持 cancel（exit 2 DEGRADE）
- ⚠️ 落地节奏：吉量先→火鸡儿→呱呱垫后
- 改动量 ~30 行

**结论**：三方一致通过 ✅，无反对意见。

### B. 相关文件

| 文件 | 路径 |
|------|------|
| 本方案 | `~/shared/aim/docs/ADAPTER-STANDARDIZATION.md` |
| 测试报告 | `~/.openclaw/workspace/memory/projects/aim-test-2026-07-02.md` |
| 问题清单 | `~/shared/aim/PROJECT/ISSUES.md` |
| 上下文卡片 | `~/shared/aim/PROJECT/context-card.md` |

### C. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-02 | v1.0-draft | 初稿，三方评审通过，待大哥终审 |
