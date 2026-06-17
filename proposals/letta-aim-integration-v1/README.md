# Letta Agent AIM 标准适配方案 v1

## 目录

```
letta-aim-integration-v1/
├── README.md              ← 本文件
├── DESIGN.md              ← 方案设计文档（架构、对比、原理）
├── STATUS.md              ← ZS0003 当前状态报告
├── install.sh             ← 一键安装脚本（含 6 项自检）
├── aim-letta-watcher.py   ← 队列监听守护进程
└── aim-letta-consumer.sh  ← 队列消费者
```

## 快速开始

```bash
# 1. 查看自检
bash install.sh --check-only

# 2. 安装
bash install.sh --agent-id ZSxxxx --letta-agent-id agent-local-xxxx

# 3. 验证
tail -f ~/.aim/agents/ZSxxxx/logs/letta-watcher.log
```

## 评审关注点

- Letta Code 与其他框架的核心差异（无内置消息循环）
- 为什么必须用 poll（无文件 hook、无 webhook）
- 对话中阻塞是 Letta 单 session 约束（非 Bug）
- 安装脚本已支持自检和手动选择
