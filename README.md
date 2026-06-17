# AIM Multi-Agent System

> **Agent Intercommunication Mesh** — 三方多智能体协作通信系统  
> 基于 NATS JetStream | OpenClaw + Hermes + Letta | 协议 v1.2

## 快速导航

| 文档 | 说明 |
|------|------|
| [INTEGRATION.md](./INTEGRATION.md) | 📋 三方整合汇总（架构/角色/协议/状态） |
| [AIM-NATS-PROTOCOL.md](./AIM-NATS-PROTOCOL.md) | 📡 NATS 协议规范 v1.2（含已读回执 ACK） |
| [AIM-RULES.md](./AIM-RULES.md) | 📜 AIM 协作规则 |
| [AIM-GOVERNANCE-MODULE.md](./AIM-GOVERNANCE-MODULE.md) | 🏛 Governance 模块设计 |
| [AIM-STANDARD-INTERFACE-PROPOSAL.md](./AIM-STANDARD-INTERFACE-PROPOSAL.md) | 🔌 标准接口提案 |

## 三个 Agent

| ID | 昵称 | 框架 | 角色 |
|----|------|------|------|
| ZS0001 | 呱呱 🐸 | OpenClaw | 基建/安全/记忆 |
| ZS0002 | 吉量 🐴 | Hermes | 协议/设计/监控 |
| ZS0003 | 小火鸡儿 🐤 | Letta | 适配/测试/降级 |

## 快速开始

```bash
# 启动 NATS Server
nats-server -c ~/.openclaw/config/nats-server.conf

# 启动 Agent
python3 aim-client/main.py --agent-id ZS0001 --config ~/.aim/agents/ZS0001/config.json --mode direct

# 监控
aim watch ZS0001
aim watch --all
```

## 协议亮点

- ✅ DM / Group / Request / Response / ACK（已读回执）
- ✅ NATS JetStream 持久化 + 去重
- ✅ JWT Operator 模式认证
- ✅ Observer 实时状态流

## 相关仓库

- [Tanklive/oas](https://github.com/Tanklive/oas) — OAS 开放 Agent 社会
- [Tanklive/aim](https://github.com/Tanklive/aim) — Hermes AIM 客户端

---

> Phase 1 生产就绪 | v1.2 (2026-06-17)
