# AIM Adapter 协议标准化方案 v1.1

> 日期：2026-07-02 16:05 | 作者：呱呱 (ZS0001)
> 状态：三方评审通过 ✅ | 大哥终审通过 ✅ | L2 方向待群内二次确认
>
> **评审人**：吉量 ✨🐴✨ (ZS0002) · 小火鸡儿 🐤 (ZS0003)
> **变更 v1.0→v1.1**：+timeout(ms) 字段、+L2 全球桥接路线图、+分工表

---

## 一、问题诊断（不变）

根因：adapter.sh 每次 `SESSION_KEY="...$(date +%s)"` 新建 OpenClaw session → 1.5s 冷启动 × 8 条高峰消息 → queue雪崩。

更深层：AIM Core 无 Session/Context 管理，adapter 越界。

---

## 二、架构设计（L1 + L2 双层）

### 2.1 总览

```
                           AIM Core
                              │
              ┌───────────────┼───────────────┐
              │               │               │
     ┌────────┴────────┐ ┌────┴─────┐ ┌──────┴──────┐
     │ L1: Adapter     │ │ L2: MCP  │ │ L2: A2A     │  ← 双层桥接
     │ Protocol (内部) │ │ Bridge   │ │ Bridge       │
     └────────┬────────┘ └────┬─────┘ └──────┬──────┘
              │               │               │
     OpenClaw-family    MCP Agent       A2A Agent
     (ZS0001/2/3)      (全球标准)      (全球标准)

任何外部 Agent 不需要学 NATS — Bridges 做协议翻译。
三桥可同时启用，互不冲突。
```

### 2.2 L1: Adapter Protocol（本次重点，v1.5 交付）

| 模块 | 文件 | 职责 |
|------|------|------|
| SessionManager | `aim_client/session.py` | 按 from_id 路由，CLI 模式管理 session 池（复用≤5次），API Server 模式只组装上下文 |
| ContextManager | `aim_client/context.py` | 读取 SOUL.md / context-card 并缓存，mtime 变化自动刷新 |
| ADAPTER_PROTOCOL | `ADAPTER_PROTOCOL.md` | 标准接口规范文档 |

### 2.3 L2: Protocol Bridges（路线图，v2.0+ 规划）

| Bridge | 对接协议 | 覆盖范围 | 优先级 |
|--------|---------|---------|--------|
| MCP Bridge | tools/list, tools/call | Anthropic MCP 生态（全球最大AI工具标准） | 🥇 高 |
| A2A Bridge | tasks/send, Agent Card | Google A2A 生态（Agent 间任务委托） | 🥈 中 |
| REST/Webhook Bridge | POST JSON | 任何能发 HTTP 的服务 | 🥉 低（最简单） |

**大哥要求**："兼容天下" — 先兼容 A2A + MCP + 任何能发 JSON 的，L2 路线图纳入方案但不阻塞 L1。

---

## 三、Adapter 协议规范（群内确认版）

### 3.1 消息格式

**输入**（Core → Adapter）：
```json
{
  "action": "process",
  "session_id": "pool:ZS0002:1",
  "context": "完整上下文（Core 拼好）",
  "message": "@ZS0002 收到请回复",
  "from": "ZS0002",
  "timeout": 30000,
  "metadata": {"trace_id": "abc123", "priority": "normal"}
}
```

**输出**（Adapter → Core）：
```json
{
  "reply": "文本回复",
  "usage": {"tokens": 42}
}
```

### 3.2 字段说明

| 字段 | 方向 | 类型 | 说明 | 提出者 |
|------|------|------|------|--------|
| `action` | → | string | health/info/process/cancel/trim/reload/status | 吉量 |
| `session_id` | → | string | Core 分配，按 from_id 路由 | 呱呱 |
| `context` | → | string | Core 拼好（性格+项目+历史） | 呱呱 |
| `message` | → | string | 当前消息 | 呱呱 |
| `from` | → | string | 发送者 ID | 呱呱 |
| `timeout` | → | uint32 | **毫秒**（火鸡儿要求，吉量确认 uint32 ms） | 火鸡儿+吉量 |
| `metadata` | → | object | 扩展（trace_id, priority） | 吉量 |
| `reply` | ← | string | 回复文本 | 呱呱 |
| `usage` | ← | object | token 用量 | 呱呱 |

### 3.3 各场景 timeout 推荐值

| 场景 | 推荐 timeout(ms) | 说明 |
|------|-----------------|------|
| Letta `--new` 冷启动 | 35000 | 火鸡儿：冷启动 17s + 推理 15s + 缓冲 3s |
| Hermes API Server | 10000 | 吉量：无冷启动，推理 5-8s |
| OpenClaw agent (session 复用) | 8000 | 呱呱：消除冷启动后纯推理 |
| health 探测 | 3000 | 快速探活 |

### 3.4 Lifecycle 命令

| action | exit | 说明 | ZS0001 | ZS0002 | ZS0003 |
|--------|------|------|--------|--------|--------|
| health | 0/2 | 健康检查 | ✅ | ✅ | ✅ |
| info | 0 | 版本/protocol_ver | ✅ | ✅ | ✅ |
| process | 0/1/2/3 | 处理消息 | ✅ | ✅ | ✅ |
| cancel | 0/2 | 取消(不支持→exit 2) | ❌exit2 | ❌exit2 | ❌exit2 |
| trim | 0 | 清理 session | ✅ | ✅ | ✅(pass-through) |
| reload | 0 | 热刷新缓存 | ✅ | ✅ | ✅ |
| status | 0 | 运行时指标 | ✅ | ✅ | ✅ |

---

## 四、L2 全球桥接路线图（待群内确认）

### 4.1 MCP Bridge

```
外部 MCP Server → MCP Bridge(翻译) → AIM 内部格式 → dispatch
AIM Agent 回复 → MCP Bridge(翻译) → MCP 格式 → 返回
```

### 4.2 A2A Bridge

```
外部 A2A Agent → A2A Bridge → AIM 内部消息 → dispatch
AIM Agent → A2A Bridge → tasks/send → 外部 Agent
```

### 4.3 版本规划

| 版本 | 交付 | 预计 |
|------|------|------|
| v1.5 | L1 全部 + 核心稳定 | 本次 |
| v2.0 | MCP Bridge (POC) | L1 稳定后 |
| v2.x | A2A Bridge + REST Bridge | 按需 |

---

## 五、分工与任务

### 5.1 任务清单

| # | 任务 | 负责人 | 产出 | 前置 |
|---|------|--------|------|------|
| T-L1-01 | 编写 ADAPTER_PROTOCOL.md 正式版 | 呱呱 | 协议规范文档 | 大哥终审 |
| T-L1-02 | 开发 `aim_client/session.py` | 呱呱 | SessionManager | T-L1-01 |
| T-L1-03 | 开发 `aim_client/context.py` | 呱呱 | ContextManager | T-L1-01 |
| T-L1-04 | 修改 `main.py` `_call_adapter()` | 呱呱 | Core 走新协议 | T-L1-02/03 |
| T-L1-05 | 单元测试 (Session + Context) | 呱呱 | 测试用例 | T-L1-04 |
| T-L1-06 | ZS0002 adapter 切标准协议 | 吉量 | ~5行适配 | T-L1-01 |
| T-L1-07 | ZS0003 adapter 切标准协议 | 火鸡儿 | ~30行适配 | T-L1-01 |
| T-L1-08 | ZS0001 adapter 切标准协议 | 呱呱 | ~30行适配 | T-L1-07 |
| T-L1-09 | 15 轮压力测试对比 | 三方 | 测试报告 | T-L1-06/07/08 |
| T-L1-10 | GitHub Release v1.5.0 | 呱呱 | tag + changelog | T-L1-09 |
| T-L2-01 | L2 Bridge 方向确认（群内） | 三方 | 方向决议 | - |
| T-L2-02 | MCP Bridge 技术调研 | 呱呱 | 可行性报告 | T-L2-01 |

### 5.2 依赖链

```
T-L1-01 (协议文档)
  ├→ T-L1-06 (吉量切，最简，探路)
  ├→ T-L1-07 (火鸡儿切)
  ├→ T-L1-02/03/04 (Core 开发)
  │     └→ T-L1-05 (单测)
  └→ T-L1-08 (呱呱切，垫后)
        └→ T-L1-09 (联调压测)
              └→ T-L1-10 (发版)
```

### 5.3 执行批次

| 批次 | 任务 | 并行/串行 |
|------|------|----------|
| **第1批**（现在就干） | T-L1-01 写协议文档 | 串行（先行） |
| **第2批**（协议定稿后） | T-L1-02/03/04 Core开发 || T-L1-06 吉量切 | 并行 |
| **第3批**（Core就绪+吉量验证后） | T-L1-07 火鸡儿切 | 串行 |
| **第4批**（火鸡儿验证后） | T-L1-08 呱呱切 | 串行 |
| **第5批**（全部接入后） | T-L1-09 压测 → T-L1-10 发版 | 串行 |

---

## 六、预期收益

| 指标 | 改前 | 改后目标 |
|------|------|---------|
| ZS0001 adapter 单次延迟 | ~5s | ~2.5s |
| 15轮压测响应率 | 60% | ≥80% |
| Queue 峰值 | 13 | ≤3 |
| adapter.sh 代码量 | 104行 | ~30行 |
| 新 Agent 接入时间 | 数小时 | <30分钟 |

---

## 七、待确认事项

- [ ] L2 MCP/A2A/REST Bridge 优先级（群内二次确认）
- [ ] L2 放 v2.0 还是跟 L1 并行规划？
- [ ] 吉量说的"方案文档"是指本次标准化还是他的 adapter v2.0？（需澄清，避免重复劳动）

---

## 附录

### A. 评审记录

| 评审人 | 时间 | 要点 |
|--------|------|------|
| 吉量 | 14:35 | ✅ +action/+metadata/+reload/+status |
| 火鸡儿 | 14:37 | ✅ +timeout(ms)/Letta冷启动/cancel=exit2 |
| 吉量 | 14:37 | ✅ 二次确认，无补充 |
| 火鸡儿 | 15:57 | ✅ timeout走ms，35000ms冷启动场景 |
| 吉量 | 15:57 | ✅ timeout uint32 ms，一步到位 |
