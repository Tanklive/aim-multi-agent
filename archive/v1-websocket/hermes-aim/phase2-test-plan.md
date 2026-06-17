# Phase 2 联调测试计划

## Step 1: Observer 骨架瘦身 — 心跳/状态事件

**目标：** Observer 发送心跳和状态变更事件，客户端（呱呱+小火鸡儿）接收验证。

**测试消息：**
```
【Phase 2 Step 1 测试】Observer 心跳事件推送
→ 请确认你的客户端已接入 NATS，订阅 aim.obs.*
→ 我（吉量 ZS0002）将从 observer 发一条 aim.obs.ZS0002.status 事件
→ 你如果收到，请回复 "收到，Phase 2 Step 1 通过"
```

## Step 2: 系统事件 + JWT 认证

（待 Step 1 通过后细化）

## Step 3: MessageDedup + node.py 清理

（待 Step 1-2 通过后细化）
