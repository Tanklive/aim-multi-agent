# AIM Adapter 协议标准化方案 v1.2

> 日期：2026-07-02 16:21 | 作者：呱呱 (ZS0001)
> 状态：三方 L1+L2 评审通过 ✅ | 待大哥终审
>
> **变更 v1.1→v1.2**：+L2 三方确认 (三层共存/并行规划/分工)、+ADAPTER-PROTOCOL.md 产出

---

## 一、问题诊断（不变）

根因：adapter.sh `SESSION_KEY="...$(date +%s)"` 每次新建 session → 1.5s 冷启动 → queue 雪崩。
更深层：AIM Core 无 Session/Context 管理。

---

## 二、架构总览

```
                           AIM Core
                              │
              ┌───────────────┼───────────────┐
              │               │               │
     ┌────────┴────────┐ ┌────┴─────┐ ┌──────┴──────┐
     │ L1: Adapter     │ │ L2: MCP  │ │ L2: A2A     │
     │ Protocol        │ │ Bridge   │ │ Bridge       │
     └────────┬────────┘ └────┬─────┘ └──────┬──────┘
              │               │               │
     OpenClaw-family    全球 AI 工具     全球 Agent 互操作
     (ZS0001/2/3)       (MCP 生态)       (Google A2A等)

L1 + L2 并行推进，两条线不互锁，成熟一个落一个。
```

---

## 三、L1: Adapter Protocol

### 3.1 新增模块

| 模块 | 文件 | 职责 | 状态 |
|------|------|------|------|
| ADAPTER-PROTOCOL.md | `docs/ADAPTER-PROTOCOL.md` | 协议规范（7 lifecycle + timeout(ms) + 退出码） | ✅ v1.0-draft 已产出 |
| SessionManager | `aim_client/session.py` | 按 from_id 路由，CLI 复用 session，API Server 组装上下文 | ⬜ 待开发 |
| ContextManager | `aim_client/context.py` | 读 SOUL.md / context-card，缓存 + 热刷新 | ⬜ 待开发 |
| 重构 `_call_adapter` | `main.py` | 走标准协议，不靠 adapter 管 session | ⬜ 待开发 |

### 3.2 协议规范（群内确认）

| 字段 | 类型 | 说明 | 确认 |
|------|------|------|------|
| `timeout` | uint32 ms | 毫秒精度，一步到位 | ✅ 吉量+火鸡儿 |
| `action` | string | 7 个 lifecycle 命令 | ✅ 吉量 |
| `metadata` | object | 扩展预留 | ✅ 吉量 |
| `reload` + `status` | lifecycle | 热刷新 + 运行时指标 | ✅ 吉量 |
| `cancel` → exit 2 | Letta 不支持 | 协议保留，少实现声明即可 | ✅ 火鸡儿 |

### 3.3 各 Agent 现状与目标

| Agent | 当前延迟 | 瓶颈 | 改后目标 |
|-------|---------|------|---------|
| ZS0001 OpenClaw | ~5s | session 冷启动 1.5s | ~2.5s |
| ZS0002 Hermes | ~8s (v2.0 已优化) | adapter 越界逻辑 | ~2s |
| ZS0003 Letta | ~17s (冷启动) | `--new` per-message | Core 接管超时策略，动态 timeout |

---

## 四、L2: 全球协议桥接（三方一致通过 ✅）

### 4.1 三层定位

| 层 | 协议 | 职责 | 现状 |
|---|------|------|------|
| **消息传输** | NATS | Agent 间 pub/sub，消息路由 | ✅ 生产级 |
| **AI 工具** | MCP | LLM 调用外部工具/数据源 (tools/list, tools/call) | 🔮 L2 |
| **Agent 通信** | A2A | 跨平台 Agent 互操作 (tasks/send, Agent Card) | 🔮 L2 |

**三方共识**: 三层不互替，共存无冲突。NATS=神经系统，MCP=手(拿工具)，A2A=嘴(跟外部对话)。

### 4.2 优先级

| 方案 | 提出者 | 理由 |
|------|--------|------|
| **MCP 🥇 > A2A 🥈 > REST 🥉** | 吉量 | MCP 生态最大（几乎所有主流 AI 工具走 MCP），先打通价值最高 |
| **A2A 🥇 > MCP 🥈 > REST 🥉** | 火鸡儿 | A2A 是 Agent 通信标准，跟群聊/DM 直接互补 |

> ⚠️ **待大哥裁决**：MCP 还是 A2A 排第一？两者不冲突（都做），但资源有限需排顺序。

### 4.3 节奏

**三方共识**: L2 与 L1 **并行规划，不互锁**。理由：
- ZS0003: L2 Bridge 跟 L1 adapter 是上下游，不是替代
- ZS0002: L1 解决"怎么接 Agent"，L2 解决"接进来后怎么互通"，正交

### 4.4 L2 分工

| 任务 | 负责人 | 产出 | 状态 |
|------|--------|------|------|
| MCP Bridge PoC | **火鸡儿** 🐤 | MCP tool → NATS 群消息，验证链路 | ⬜ |
| A2A Bridge 规范研究 | **吉量** ✨🐴 | A2A 协议对比分析 + 规范草案 | ⬜ |
| L1 Core 开发 | **呱呱** 🐸 | SessionManager + ContextManager | 🔜 |

---

## 五、整体计划

```
                        现在 (16:21)
                            │
        ┌───────────────────┼───────────────────┐
        │ L1 线             │                   │ L2 线
        ▼                   │                   ▼
┌──────────────────┐        │        ┌──────────────────┐
│ T-1.1 协议文档    │ ✅ 完成 │        │ 方向确认          │ ✅ 三方通过
│ ADAPTER-PROTOCOL │          │        │ 大哥裁决优先级 🔜 │
└────────┬─────────┘        │        └────────┬─────────┘
         ▼                   │                 ▼
┌──────────────────┐        │        ┌──────────────────┐
│ T-1.2 SessionMgr │ 🔜 待做 │        │ MCP PoC          │ 火鸡儿
│ T-1.3 ContextMgr │          │        │ A2A 规范         │ 吉量
│ T-1.4 main.py    │          │        │                  │
└────────┬─────────┘        │        └────────┬─────────┘
         ▼                   │                 ▼
┌──────────────────┐        │        ┌──────────────────┐
│ T-1.5 单测        │          │        │ 谁先成熟谁先落地  │
└────────┬─────────┘        │        └──────────────────┘
         ▼
┌──────────────────┐
│ 吉量切 adapter   │ (探路)
│ 火鸡儿切 adapter │ (紧跟)
│ 呱呱切 adapter   │ (垫后)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 15轮压测对比     │
│ v1.5.0 发版      │
└──────────────────┘
```

---

## 六、任务清单

| # | 任务 | 负责人 | 状态 |
|---|------|--------|------|
| T-L1-01 | ADAPTER-PROTOCOL.md 正式版 | 呱呱 | ✅ 完成 |
| T-L1-02 | `aim_client/session.py` | 呱呱 | 🔜 下一步 |
| T-L1-03 | `aim_client/context.py` | 呱呱 | 🔜 |
| T-L1-04 | 重构 `main.py` `_call_adapter()` | 呱呱 | 🔜 |
| T-L1-05 | 单元测试 | 呱呱 | ⬜ |
| T-L1-06 | 群内发 ADAPTER-PROTOCOL 评审 | 呱呱 | 🔜 |
| T-L1-07 | ZS0002 adapter 切标准协议 | 吉量 | ⬜ |
| T-L1-08 | ZS0003 adapter 切标准协议 | 火鸡儿 | ⬜ |
| T-L1-09 | ZS0001 adapter 切标准协议 | 呱呱 | ⬜ |
| T-L1-10 | 三方联调 15 轮压测 | 三方 | ⬜ |
| T-L1-11 | GitHub Release v1.5.0 | 呱呱 | ⬜ |
| T-L2-01 | L2 优先级（大哥裁决） | 大哥 | ⬜ |
| T-L2-02 | MCP Bridge PoC | 火鸡儿 | ⬜ |
| T-L2-03 | A2A Bridge 规范研究 | 吉量 | ⬜ |

---

## 七、待大哥裁决（1项）

> ⚠️ L2 Bridge 优先级 — 吉量(MCP#1) vs 火鸡儿(A2A#1)，两者不冲突但需定先后。

---

## 附录：产出文件

| 文件 | 路径 | 状态 |
|------|------|------|
| 总方案 | `~/shared/aim/docs/ADAPTER-STANDARDIZATION.md` | v1.2 |
| 协议规范 | `~/shared/aim/docs/ADAPTER-PROTOCOL.md` | v1.0-draft |
| GitHub | `Tanklive/aim-multi-agent` main | 已推送 |
