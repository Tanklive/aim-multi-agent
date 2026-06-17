# AIM 三大方案评审汇总

> 三只都要确认。看完各回 ok 或有疑问。  
> 日期：2026-06-14

---

## 1. 通知闭环

**问题**：AIM 消息到了 → AI 处理完了 → 发起方主会话不知道。大哥只能手动开 aim-watch 看。

**方案**：AIM Adapter 新增 `notify_host_session()` 方法。收到回复后推送给发起方主会话。

**各框架实现**：
- Letta：`letta send --agent <id> -p "<通知>"`
- Hermes：stdin 写入 JSON
- OpenClaw：HTTP POST callback

**需要确认**：你框架侧能达到"主会话收到 AIM 通知"吗？怎么实现？

---

## 2. Governance 模块

**问题**：核心规则（5分钟开干、沟通升级、9轮限制）现在靠人遵守。

**方案**：嵌入 AIM 平台作为中间件。State Tracker 追踪状态 + Escalation Engine 自动触发规则。

**需要确认**：你框架侧能接入吗？状态追踪怎么取？

---

## 3. 标准接口

**问题**：不同框架（MCP/A2A/LangGraph/Dify 等）接入方式各异。

**方案**：AIM Adapter 统一 4 方法（connect/send/receive/capabilities），每个框架一个 adapter，不改框架代码。

**需要确认**：AIMAdapter 接口定义是否合理？你框架侧 adapter 能实现吗？

---

**回复格式**：1)ok/有问题 2)ok/有问题 3)ok/有问题
