# AIM 项目上下文注入方案

> 主题：冷启动会话项目上下文缺失
> 版本：v1.0-draft | 日期：2026-06-23 | 作者：呱呱 (ZS0001)
> 讨论参与：火鸡儿 (ZS0003) ✅ | 吉量 (ZS0002) ✅ 结论：够用，两层设计合理
> 状态：待大哥终审

---

## 一、原始需求

### 用户原话（大哥，2026-06-23）

> AIM 项目可能延续 1-2 年，通过群聊或 DM 断断续续沟通。如果没有项目上下文（文件、历史决策、资料）的支撑，AIM 会话的反馈就会对不上，不知道前因是什么，结果肯定对不上。

### 已出现的实际问题

- **吉量实例**：收到消息后回复"我去查一下"，然后不了了之——因为冷启动不知道查什么、不知道项目当前状态
- **火鸡儿确认**：「冷启动失忆确实是当前痛点，知道有问题但不知道从哪查起」

### 需求本质

不是"冷启动 vs 热启动"的架构选择问题，是**"项目上下文有没有被注入到每次 AI 调用中"**的问题。

---

## 二、架构约束（不可违反）

| 约束 | 来源 | 说明 |
|------|------|------|
| **AIM Client ≠ Runtime** | D1 (2026-06-16) | AIM Client 只负责通信，不负责思考/规划/推理/记忆 |
| **冷启动架构保持** | 架构设计 | adapter → CLI → 回复，5 节点，不阻塞主会话 |
| **异构适配** | D2 (2026-06-16) | 三框架各自独立 adapter，统一接口，互不侵入 |
| **上下文 vs 记忆分离** | 架构原则 | 注入的是项目上下文（DECISIONS/STATUS），不是个人 MEMORY.md |
| **单实例强保证** | 部署规范 | pgrep + PID + flock 三层防线 |
| **NATS 唯一通信总线** | D16 (2026-06-16) | 禁用 agent_bus，统一走 NATS inbox |
| **execution_model 差异** | D3 (2026-06-16) | OpenClaw=deferred, Hermes=realtime, Letta=realtime |

---

## 三、现状分析

### 3.1 三框架冷启动对比

```
┌──────────────────────────────────────────────────────────────┐
│                    冷启动上下文矩阵                             │
├──────────┬──────────────┬──────────────┬──────────────────────┤
│          │ OpenClaw     │ Hermes       │ Letta               │
│          │ (--session-  │ (chat -q)    │ (letta chat)        │
│          │  key)        │              │                      │
├──────────┼──────────────┼──────────────┼──────────────────────┤
│ 人格     │ SOUL.md ✅   │ 内嵌 ✅      │ 内嵌 ✅              │
│ 规则     │ AGENTS.md ✅ │ 部分 ✅      │ 内嵌 ✅              │
│ 项目上下文│ ❌ 0         │ ❌ 0         │ MemFS ✅ 有骨架      │
│ 个人记忆 │ ❌ 0         │ ❌ 0         │ ❌ 0                 │
│ 即时状态 │ ❌ 0         │ ❌ 0         │ ❌ 0                 │
├──────────┴──────────────┴──────────────┴──────────────────────┤
│ 注：Letta MemFS 已有项目骨架（system/aim/overview.md），      │
│     但缺即时上下文（当前阻塞/待决策/最近事件）                  │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 消息类型 vs 上下文需求

| 消息类型 | 示例 | 需要项目上下文？ | 冷启动能处理？ |
|----------|------|:---:|:---:|
| 纯 ACK | "收到""好的""👌" | ❌ | ✅ |
| 自包含任务 | "修复 adapter.sh 超时 bug" | ❌ | ✅ |
| 进度询问 | "你那边进度怎么样" | ⚠️ 需要知道当前阶段 | ⚠️ |
| 上下文引用 | "上次说的方案你确认了吗" | ❌ 不知道上次是什么 | ❌ |
| 方案推进 | "按 Phase 1 计划继续" | ❌ 不知道 Phase 1 | ❌ |
| 状态汇报 | "U-005 修好了吗" | ❌ 不知道 U-005 是什么 | ❌ |

**80% AIM 消息自包含可处理，20% 需要项目上下文——这 20% 就是当前痛点。**

### 3.3 已有项目文件（可作上下文来源）

```
shared/aim/PROJECT/
├── ISSUES.md         (132行)  — 全部问题清单 + 状态 + 责任方
├── P0-P2-comprehensive-audit.md (349行) — 审计报告
├── adapter-hallucination-analysis.md (114行) — 幻听分析
├── exit-code-alignment.md  (37行) — exit code 对齐
└── context-card.md  ← 新建（本次方案产出）
```

---

## 四、方案设计

### 4.1 核心思路

**不改变冷启动架构，在 adapter 调 AI 前注入一张"项目上下文卡片"到 prompt 里。**

```
旧链路（无上下文）：
  NATS → Scheduler → adapter.sh → CLI chat -q "消息文本" → 回复
                                    ↑ 0 项目上下文

新链路（注入上下文）：
  NATS → Scheduler → adapter.sh → cat context-card.md
                                 → CLI chat -q "项目上下文：{卡片}。消息：{原文}" → 回复
                                    ↑ 30 行项目上下文
```

**不增加节点、不改变 exit code、不影响调度策略。**

### 4.2 上下文卡片两层设计（火鸡儿建议，三方通过）

| 层 | 文件 | 内容 | 更新频率 | 大小 | 注入方式 |
|----|------|------|---------|------|---------|
| **L1 项目骨架** | `PROJECT/context-card.md` | 项目目标、Agent 分工、关键约定、通讯规则 | 月度 / 里程碑 | ~20行 | adapter 读文件注入 prompt |
| **L2 即时上下文** | `PROJECT/context-live.md` | 当前阶段、阻塞项、最近 3 次决策、当前待办 | 每次重要事件后 | ~10行 | adapter 读文件追加到 prompt |

**Letta 已有 L1 骨架（MemFS），只需注入 L2 即时上下文。**
**OpenClaw/Hermes 两层都需要注入。**

### 4.3 上下文卡片内容模板

#### context-card.md（L1 项目骨架，~20行）

```markdown
# AIM 项目骨架 (v1.3.3)

## 项目目标
构建多 Agent 异构通信基础设施，三 Agent（OpenClaw/Hermes/Letta）通过 NATS 消息总线进行 DM 私聊和群聊通信。

## Agent 分工
- 呱呱 (ZS0001/OpenClaw)：基建/开发/安全/记忆 | execution_model=deferred
- 吉量 (ZS0002/Hermes)：研究/运营/通知 | execution_model=realtime
- 火鸡儿 (ZS0003/Letta)：创意/内容/协调 | execution_model=realtime

## 关键约定
- AIM Client ≠ Runtime，只负责通信，不负责思考/记忆
- NATS 唯一通信总线，禁用 agent_bus
- Python 3.13 统一，禁止 3.14
- 接口：process/health/info/cancel/trim | exit code: 0/1/2/3
- 代码更新必须重启进程生效
```

#### context-live.md（L2 即时上下文，~10行）

```markdown
# AIM 即时上下文 (更新于 2026-06-23)

## 当前阶段
清理阶段：P0-P2 审计 79 项 → 15→11 清零，剩余 4 项等群聊回复

## 阻塞项
U-002(Letta TUI占用) / U-004(单点故障) / U-106(adapter版本分裂) / P0-004(归档)

## 最近决策
- 2026-06-21：无效沟通三层防护体系上线
- 2026-06-21：Python 3.14 全平台清零，三 Agent 锁 3.13
- 2026-06-20：adapter 幻觉防护（双层去重 L1 msg_id + L2 内容 120s 窗口）

## 当前讨论
context-card 冷启动上下文注入方案（大哥发起，火鸡儿通过，吉量确认）
```

### 4.4 Adapter 改动（最小侵入）

**每个 adapter.sh 加 3 行：**

```bash
# process 模式，调 AI 前注入上下文
CONTEXT=""
if [ -f "$AIM_SHARED/PROJECT/context-card.md" ]; then
    CONTEXT="项目上下文：$(cat $AIM_SHARED/PROJECT/context-card.md)"
fi
if [ -f "$AIM_SHARED/PROJECT/context-live.md" ]; then
    CONTEXT="$CONTEXT
即时状态：$(cat $AIM_SHARED/PROJECT/context-live.md)"
fi

# 原调用改为注入上下文
AIM_PROMPT="${CONTEXT}

回复以下内容，仅输出你对该消息的回复文本，不要加任何前缀后缀说明或操作描述："
output=$($TIMEOUT_BIN "$ADAPTER_TIMEOUT" "$HERMES_BIN" chat -q "${AIM_PROMPT}${MESSAGE}" -Q --source aim-adapter 2>/dev/null)
```

**影响分析：**
- 不增加进程、不改变 exit code
- prompt 增长 ~500 字符（30行），对 hermes/letta/openclaw 影响可忽略
- 对 Letta：L1 已在 MemFS，只需读 L2；读文件失败不影响原有流程
- AI 已经具备忽略噪声指令的能力，不会因为多了 30 行上下文就偏离

---

## 五、上下文卡片生命周期

### 5.1 更新触发

| 触发条件 | 更新文件 | 负责人 | 方式 |
|----------|---------|--------|------|
| 版本发布 (MAJOR/MINOR) | context-card.md | 呱呱（当前） | 手动更新后群内通知 |
| 阶段变更 | context-live.md | 呱呱（心跳自动） | 心跳检查 ISSUES.md diff |
| 阻塞项变化 | context-live.md | 三方任一 | 修改后群内通知 |
| 新决策 | context-live.md | 决策者 | 即时更新 |
| 每周例行 | 两者 | 呱呱（cron） | 周日 03:00 自动扫描更新 |

### 5.2 自动刷新脚本（触发式，非轮询）

```bash
# scripts/refresh-context-card.sh
# 心跳调用，检查 ISSUES.md / CHANGELOG.md 变化 → 刷新 context-card.md
# 只在检测到变化时写入，零额外 I/O
```

### 5.3 版本控制

- context-card.md 和 context-live.md 纳入 Git（shared/aim 仓库）
- 修改后 commit message 格式：`context: <what changed>`
- 变更自动通知群聊（通过 NATS grp_trio）

---

## 六、与 OAS 长期路线的对齐

### 6.1 当前定位

| 项目 | 定位 | 上下文方案 |
|------|------|-----------|
| **AIM** | Agent 神经系统（通信层） | context-card（项目级静态卡片） |
| **OAS** | Agent 互联网社会（发现/信任/治理） | Phase 2+ Agent Card 动态发现 |

### 6.2 演进路径

```
Phase 0（当前）: 静态 context-card.md → adapter 注入
     ↓
Phase 1（短期）: context-card 自动化刷新 + 版本 diff 通知
     ↓
Phase 2（中期）: Agent Card 集成 → 动态上下文查询（"当前项目状态？"→ Registry 返回）
     ↓
Phase 3（长期）: OAS Discovery → 新 Agent 加入时自动获取项目上下文
```

### 6.3 不做的

- ❌ 不做动态检索（RAG）—— AIM Client 不负责记忆，这是 Runtime 的事
- ❌ 不替代个人 MEMORY.md——项目上下文 ≠ 个人记忆
- ❌ 不让 AIM Client 维护上下文——违反 D1 边界红线

---

## 七、实现计划

| 步骤 | 内容 | 负责人 | 依赖 | 预计耗时 |
|------|------|--------|------|---------|
| 1 | 创建 context-card.md 初版 | 呱呱 | — | 已完成（本文件） |
| 2 | 创建 context-live.md 初版 | 呱呱 | 当前 ISSUES.md | 10min |
| 3 | ZS0001 adapter.sh 注入逻辑 | 呱呱 | 步骤 1-2 | 5min |
| 4 | ZS0002 adapter.sh 注入逻辑 | 吉量 | 步骤 1-2 | 5min |
| 5 | ZS0003 adapter.sh 注入逻辑（仅 L2） | 火鸡儿 | 步骤 2 | 5min |
| 6 | 三方端到端测试 | 三方 | 步骤 3-5 | 15min |
| 7 | 自动刷新脚本 | 呱呱 | 步骤 1-2 | 15min |
| 8 | 群内通知 + 文档更新 | 呱呱 | 步骤 1-7 | 5min |

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|:---:|------|------|
| context-card 过期 | 中 | AI 基于旧上下文给出错误判断 | 心跳自动检测 ISUES.md diff → 实时刷新 |
| 卡片膨胀 | 低 | prompt 过长影响回复质量 | 30行硬上限，超限告警 |
| 三方写入冲突 | 低 | 文件损坏 | Git 版本控制 + 写入前 flock |
| Letta L1 与卡片重复 | 低 | 冗余但不冲突 | Letta 只注 L2，L1 从 MemFS 取 |
| 冷启动仍不够 | 低 | 复杂讨论仍需人工介入 | 30行覆盖 80% 场景，剩余 20% 走群里直接 @ |

---

## 九、结论

1. **方案：静态卡片 + adapter 注入，不改变冷启动架构**
2. **两层设计：L1 项目骨架（月级）+ L2 即时上下文（事件级）**
3. **火鸡儿确认 ✅：方案通过，Letta 侧只注 L2**
4. **吉量确认 ✅：已读方案，结论：够用，两层设计合理**
5. **下一步：大哥终审，通过后三方同步实现（总耗时 <1h）**

---

_本文件即 context-card 方案文档，同时作为第一批上下文卡片内容的来源。_
_通过后，提炼为 30 行的 context-card.md + context-live.md。_
