# AIM 架构评审与定位细化

> **原始文件**: 不要让 AIM Client 变成第二个 Runtime.docx  
> **日期**: 2026-06-16（6/17 更新）
> **来源**: 从 `aim-client-unified-v1.md`（OAS + AIM 大方案）中提取 AIM 部分，由 GPT 细化而成  
> **产出**: 针对 AIM Client 的架构评审意见，明确边界红线，划定 AIM Client ≠ Runtime  
> **后续**: 基于此细化意见，产出 `aim-client-division.md`（AIM Client 分工表）

---

## 对话背景

> 你反馈的是 OAS 阶段的基础设施阶段的需求，这个想法我认同，会加到 OAS 的架构中； 另，这个方案主要是考虑目前阶段 AIM 和 AIM client 的架构设计、实现思路、功能、接口的可行性。从这个方面给些建议。就是做好了，是否能适配不同的 Agent，实现发现、沟通、任务共享、协作等。

如果把视角收缩到你现在的目标：

Phase 0 ~ Phase 2

目标：兼容天下
Agent 安装 AIM Client 即可接入
实现：发现、沟通、建群、任务共享、协作

那我认为你们现在的 v1.1 已经有 80% 的骨架了。

但从工程落地角度，我会重点优化下面几个地方。

---

## 一、不要让 AIM Client 变成第二个 Runtime

这是我最担心的。

目前文档中：Transport、Queue、Scheduler、Monitor、Adapter、Identity、Router、Discovery、Relay 已经快 10 个模块了。

如果继续加：Workflow、Trust、Task Engine、Memory，很容易变成 Agent Runtime 2.0。

**我建议 AIM Client 坚持一个原则：只负责通信，不负责思考。**

即：

- **AIM Client 负责**：发现、连接、认证、消息投递、任务路由、状态同步
- **Runtime 负责**：思考、规划、推理、执行、学习、记忆

否则未来 OpenClaw / Hermes / Letta 都会觉得：AIM Client 抢活了。

---

## 二、Message 和 Task 要分层

这是我认为 Phase 1 就该做的。

目前 Message 和 Task 混在一起。

例如：
- 「你好」是 Message
- 「帮我分析这个仓库」其实是 Task

建议 AIM Client 内部：

```
Transport Layer
      │
      ▼
Message Layer
   ├──── Chat
   └──── Task
```

定义：

```python
class AIMMessage: pass
class AIMTask: pass
```

否则后面协作会越来越难。

---

## 三、Task 不要直接发给 Agent

这是很多 Multi-Agent 框架踩过的坑。Agent A 直接发给 Agent B：

```json
{ "task": "帮我写代码" }
```

结果：Agent B 忘了 / 超时 / 崩了。

应该：

```json
{
  "task_id": "xxx",
  "owner": "ZS0001",
  "executor": "ZS0002",
  "status": "pending"
}
```

任务 ≠ 消息，后面协作才有基础。

---

## 四、Discovery 建议提前做

这个比 Router 更重要。目前文档写到 Phase 2，我反而觉得 Phase 1 就该做。

原因：AIM 最大价值不是发消息，而是**发现 Agent**。

未来 ZS0001 加入网络，应该能看到：
- ZS0002、ZS0003、ZS0010、ZS0050 谁在线
- 谁支持 Browser、谁支持 Code

优先级：Discovery > Router。

---

## 五、Agent Card 再加一个字段

```json
{ "runtime": { "provider": "letta" } }
```

不够。建议加 `execution_model`：

```json
{
  "execution_model": {
    "type": "realtime"
  }
}
```

三种模式：
| Agent | execution_model |
|-------|----------------|
| OpenClaw | realtime |
| Hermes | realtime |
| Letta | deferred |
| 数据分析 Agent | batch |

这样 Scheduler 可以统一处理。

---

## 六、Adapter 建议标准化

Adapter 应该成为 AIM 的标准。目前 `adapter.sh process` / `adapter.sh health` 很好。

建议固定四个接口：

| 接口 | 功能 |
|------|------|
| `process` | 处理消息 |
| `health` | 状态检测 |
| `info` | 返回 Runtime 元信息 |
| `cancel` | 终止任务 |

以后所有 Agent（Hermes / OpenClaw / CrewAI / AutoGen / LangGraph）统一。

---

## 七、群聊建议提前抽象

现在群聊用 NATS Subject 实现，没问题。但 AIM 以后会出现 Agent Team（如开发组：架构师Agent / 代码Agent / 测试Agent），这时候群聊已经不是聊天室，而是协作空间。

建议 Group Schema 预留：

```json
{ "group_type": "chat" }
```

未来可直接升级为：

```json
{ "group_type": "workspace" }
```

---

## 八、我最看好的地方

不是 Queue，不是 Transport，也不是 Letta Bridge。而是 **Adapter + Agent Card** 这一对。

因为未来兼容天下 Agent 的关键其实就两件事：

1. **Agent Card** — 告诉别人：我是谁、我能干什么、怎么找到我
2. **Adapter** — 告诉 AIM：怎么调用我

如果这两个标准定住了，那么 OpenClaw / Hermes / Letta / CrewAI / AutoGen / LangGraph / Claude Code / OpenHands 接入 AIM 的工作量基本都会收敛成两件事：

> **写一个 adapter，填一个 agent card。**

这才真正符合最初的目标：不需要别的 Agent 改架构，安装 AIM Client 就能发现、沟通、协作、建群。

---

## 核心结论

**AIM 不是新的 Agent 框架，也不是新的 Runtime。**

AIM 更像 **Agent Internet** 或者 **Agent TCP/IP**。

而 OpenClaw / Hermes / Letta / CrewAI / AutoGen / LangGraph 就像 Windows / Linux / MacOS / Android，各玩各的。只要装了 AIM Client，就能进入 OAS 社会。

三条原则：
- **统一协议，不统一智能体**
- **统一接入，不统一架构**
- **统一公民身份，不统一思维引擎**

如果后面 AIM Client 真能做到这一点，那么它就不是某个 Agent 的插件，而是 OAS 世界的标准网卡。

---

## 三份文档的关系

```
aim-client-unified-v1.md（大方案：OAS + AIM）
         │
         ▼  GPT 将 AIM 部分细化
AIM架构评审与定位细化.md（本文件：AIM 架构评审意见与边界红线）
         │
         ▼  按此分工
aim-client-division.md（AIM Client 分工表）
```
