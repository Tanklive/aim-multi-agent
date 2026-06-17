# AIM Governance Module — 核心规则平台化 v1.0

> 大哥问：核心规则能不能嵌入 AIM 平台？  
> 答：能。作为 AIM Governance Module。  
> 日期: 2026-06-14

---

## 一、核心规则 → 可执行策略

核心规则的每一条都可以转化成 AIM 平台的**自动化执行逻辑**：

| 规则编号 | 核心规则原文 | AIM Governance 实现 |
|---------|------------|-------------------|
| **一.1** | 5分钟自动开干 | `idle_detector`: 检测 Agent 5 分钟无事 → 自动推送待办提醒 |
| **一.2** | 30分钟空闲主动推进 | `learning_trigger`: 30 分钟空闲 → 推送学习/升级任务 |
| **一.3** | 沟通升级：15m/30m/60m | `escalation_engine`: 消息未回复 → 倒计时 → 逐级升级通知 |
| **一.4** | 等回复超 5 分钟开干 | 同 idle_detector，加 `waiting_reply` 状态标记 |
| **一.5** | 有明确时长任务到期检查 | `deadline_watchdog`: 检测 task.deadline 到期 → 升级 |
| **二.1** | 团队事项先沟通再行动 | `collab_gate`: 检测 `@team` 操作 → 自动发起群聊确认 |
| **二.3** | 任务完成先验证再反馈 | `completion_gate`: 检测 task completed → 强制发验证消息 |
| **二.6** | 讨论 ≤9 轮 | `round_limiter`: 同一 thread_id 计数 → 第 9 轮截断 + 提醒 |
| **二.7** | ACK ≠ 处理 | `ack_detector`: 检测 "收到/ok" → 自动追问 "确认已完成？" |
| **四.2** | BUG 修复授权 | `bug_fix_auth`: 项目负责人标记 → 跳过大哥审批 |
| **六** | 什么情况联系大哥 | `qq_escalation_rules`: 匹配 8 种触发条件 → 自动 QQ 上报 |

---

## 二、架构

```
AIM Platform
    │
    ├── AIM Core (NATS + Envelope + Adapter)
    │       │
    │       └── 消息路由 + AI 处理
    │
    ├── AIM Governance Module (新增)
    │       │
    │       ├── Policy Engine         — 规则解析引擎（读 YAML）
    │       ├── State Tracker         — 全局状态追踪（idle/waiting/working）
    │       ├── Escalation Engine     — 根据状态触发规则动作
    │       └── Rules Store           — 规则库（YAML，可热更新）
    │
    └── AIM Observer (已有)
```

## 三、规则 YAML 示例

```yaml
# ~/shared/aim/governance-rules.yaml
rules:
  - id: "1.1"
    name: "idle_auto_start"
    trigger:
      event: "agent_idle"
      condition: "duration >= 300"  # 5 分钟
      filter: "pending_tasks > 0"
    action:
      type: "nats.publish"
      subject: "aim.notify.{agent_id}"
      payload:
        status: "reminder"
        text: "有 {pending_tasks} 个待办任务，自动开干。"

  - id: "1.3"
    name: "escalation_engine"
    trigger:
      event: "message_unreplied"
      thresholds:
        - after: 900    # 15 分钟
          action: "dm_remind"
        - after: 1800   # 30 分钟
          action: "grp_push"
        - after: 3600   # 60 分钟
          action: "qq_report"

  - id: "2.6"
    name: "round_limiter"
    trigger:
      event: "thread_reply"
      condition: "thread.round >= 9"
    action:
      type: "thread_freeze"
      qq_report: "thread {thread_id} 超过 9 轮讨论，需要大哥决策"
```

## 四、与通知闭环的关系

```
Governance Module
    │ 检测到规则触发（如：30 分钟无回复）
    │
    ▼ 生成 AIM Notification
    ├── → nats-agent (Agent 本地通知)
    └── → aim.notify.{agent_id} (主会话通知)
               │
               ▼
         Agent 主会话收到提醒
```

通知闭环 + Governance Module = **规则自动执行**：
- 不需要大哥手动盯着 aim-watch
- 超时自动催办
- 9 轮自动截断
- 关键决策自动 QQ 上报

## 五、实施分级

| Phase | 内容 | 依赖 |
|-------|------|------|
| P0 | State Tracker (idle/waiting/working 状态追踪) | Observer 已有数据 |
| P0 | Escalation Engine (一.3 沟通升级) | State Tracker |
| P1 | Round Limiter (二.6 讨论回合限制) | Escalation Engine |
| P1 | Idle Detector (一.1 自动开干) | State Tracker |
| P2 | ACK Detector (二.7) | NLP 检测 |
| P3 | 全规则覆盖 | P0-P2 基础设施 |

---

*此模块作为 AIM 平台内置功能，不依赖外部系统。*
