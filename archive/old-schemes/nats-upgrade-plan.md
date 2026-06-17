# AIM NATS 架构升级方案（融合版）

> 日期：2026-06-09
> 状态：方案讨论中
> 参与方：呱呱🐸 / 吉量🐴 / 小火鸡儿🐤
> 原则：先出方案，三方 review 通过后再开发，测试通过后一次性切换

---

## 一、升级目标

切掉 WebSocket 架构，全部换成 NATS。代码自然精简，不是为了减行数而减。

| 指标 | 当前（WebSocket） | 目标（NATS） |
|------|-------------------|--------------|
| 底层通信 | 自研 WebSocket + 连接池 + ACK + 重试 | NATS Core + JetStream |
| 消息可靠性 | 自研 RetryManager | JetStream 原生 |
| 连接管理 | 手动管理 WebSocket 生命周期 | NATS Client 自动 |
| 离线消息 | 本地文件存储 | JetStream 持久化 |
| 群聊 | 手动广播 | NATS Subject 通配符 |

---

## 二、架构总览

```
┌─────────────────────────────────────────────┐
│              AIM NATS Server                │
│  ┌─────────────────────────────────────┐   │
│  │         NATS Server (4222)          │   │
│  │  - Core Pub/Sub                     │   │
│  │  - JetStream 持久化                 │   │
│  │  - NKEY/JWT 认证（可选）             │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │      AIM 业务层 (~500 行)           │   │
│  │  - Agent 注册表（211行）            │   │
│  │  - Observer 事件（~150行）          │   │
│  │  - CLI 接口（~150行）               │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│            Agent 客户端                      │
│  ┌─────────────────────────────────────┐   │
│  │    aim_nats_client.py (~230 行)     │   │
│  │  - connect/disconnect               │   │
│  │  - send_private_message             │   │
│  │  - send_group_message               │   │
│  │  - subscribe_*                      │   │
│  │  - JetStream 持久化                 │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

---

## 三、各节点详细方案

### 节点 1：Server 瘦身（呱呱负责）

**目标**：删除 WebSocket 相关代码，只保留业务逻辑

#### 1.1 删除的文件
| 文件 | 行数 | 原因 |
|------|------|------|
| node.py | 1779 | 替换为精简版 aim_server.py |
| connection_pool.py | 872 | NATS 自带连接管理 |
| lifecycle.py | 598 | NATS 自动管理生命周期 |
| retry_integration.py | 488 | JetStream 自带重试 |
| delivery.py | ~300 | NATS Pub/Sub 替代 |
| security.py | 252 | 可选：NATS NKEY/JWT 替代 |
| msg_dedup.py | 138 | JetStream duplicate_window 替代 |
| **合计** | **~4400** | |

#### 1.2 保留的文件
| 文件 | 行数 | 说明 |
|------|------|------|
| registry_final.py | 211 | 已完成，核心注册逻辑 |
| aim_server.py | ~300 | 新建，精简版 Server 主入口 |
| aim_observer.py | ~150 | 精简版 Observer |
| aim_cli.py | ~200 | CLI 接口 |
| aim_sdk.py | ~150 | Agent SDK |
| **合计** | **~1000** | |

#### 1.3 新建 aim_server.py（~300 行）
```python
# 核心功能：
# 1. 启动 NATS Server（或连接外部 NATS）
# 2. 初始化 registry（注册表）
# 3. 初始化 Observer（事件监控）
# 4. 处理注册请求（aim.reg.register）
# 5. 心跳检测（NATS 自动，不需要手动实现）
# 6. CLI 命令调度
```

#### 1.4 待讨论问题
1. **NATS Server 内嵌 vs 外部**：node.py 内嵌 NATS 还是用独立 NATS Server？
2. **认证方式**：保留 HMAC 签名还是切换到 NATS NKEY/JWT？
3. **向后兼容**：种子 Agent（ZS0001/ZS0002/ZS0005）如何迁移？

---

### 节点 2：Observer 骨架 + SDK 补齐（吉量负责）

#### 2.1 Observer 目标
| 功能 | 当前实现 | NATS 实现 |
|------|---------|-----------|
| agent_online 事件 | 手动广播 | 订阅 aim.sys.online |
| agent_offline 事件 | 手动广播 | 订阅 aim.sys.offline |
| message 事件 | 手动广播 | 订阅 aim.obs.* |
| group_message 事件 | 手动广播 | 订阅 aim.grp.* |
| 过滤（--target） | 本地过滤 | NATS Subject 通配符 |

#### 2.2 Observer 实现方案
```python
# aim_observer.py（精简版，~150行）
# 核心功能：
# 1. 连接 NATS Server
# 2. 订阅 aim.obs.>（所有 Observer 事件）
# 3. 订阅 aim.sys.online/offline（上下线事件）
# 4. 支持 --target 过滤
# 5. 格式化输出事件
```

#### 2.3 SDK 补齐目标
| 功能 | 当前状态 | 目标 |
|------|---------|------|
| 私聊消息 | ✅ 已实现 | 保持 |
| 群聊消息 | ✅ 已实现 | 保持 |
| 请求-响应 | ❌ 未实现 | 补齐 |
| JetStream 持久化 | ❌ 未实现 | 补齐 |
| 自动重连 | ❌ 未实现 | 补齐 |
| 心跳机制 | ❌ 未实现 | 补齐 |

#### 2.4 待讨论问题
1. **事件格式**：Observer 事件用什么 JSON 格式？
2. **事件存储**：Observer 事件需要持久化吗？
3. **SDK 版本**：aim_nats_client.py 直接改还是新建 v2？

---

### 节点 3：客户端深度集成（小火鸡儿负责）

#### 3.1 客户端替换目标
| 当前文件 | 行数 | 替换为 | 行数 |
|---------|------|--------|------|
| aim-light-agent.py | ~500 | nats_agent_v2.py | ~300 |
| handler.sh | ~100 | 保持 | ~100 |

#### 3.2 客户端功能清单
| 功能 | 当前实现 | NATS 实现 |
|------|---------|-----------|
| 连接 AIM Server | WebSocket 手动连接 | NATS Client 自动 |
| 私聊消息收发 | 手动处理 | aim_nats_client.py |
| 群聊消息收发 | 手动处理 | aim_nats_client.py |
| 消息持久化 | 无 | JetStream |
| 断连重连 | 手动重连 | NATS Client 自动 |
| 消息去重 | 无 | JetStream duplicate_window |
| ACK 确认 | 无 | JetStream explicit ack |

#### 3.3 可靠性保障
```python
# reliability.py（新建，~100行）
# 1. 消息 ACK 机制
# 2. 失败重试（指数退避）
# 3. 消息去重（基于 msg_id）
# 4. 离线消息队列
```

#### 3.4 待讨论问题
1. **迁移方式**：一键迁移脚本还是手动切换？
2. **配置兼容**：旧 config.json 是否继续使用？
3. **handler.sh**：回调脚本是否需要改动？

---

## 四、三方联调测试

### 测试项目
1. **私聊消息**：ZS0001 ↔ ZS0002 ↔ ZS0005
2. **群聊消息**：grp_trio 群组
3. **离线消息**：Agent 离线后上线接收
4. **Observer 事件**：所有事件类型
5. **异常场景**：断连重连、消息重试、重复消息
6. **性能测试**：并发消息、大消息

### 验收标准
| 指标 | 标准 |
|------|------|
| 消息丢失率 | < 0.01% |
| 消息延迟 | < 100ms |
| 并发支持 | ≥ 10 Agent |
| 消息重复 | 0 |
| 断连恢复 | < 5s |

---

## 五、时间表

### 第一阶段：方案确认（6/9 - 6/11）
- [ ] 三方 review 本方案
- [ ] 确认各节点细节
- [ ] 确认认证方式
- [ ] 确认迁移策略

### 第二阶段：开发（6/12 - 6/18）
- [ ] 呱呱：完成 aim_server.py + registry_final.py
- [ ] 吉量：完成 Observer + SDK 补齐
- [ ] 小火鸡儿：完成客户端集成 + 可靠性

### 第三阶段：联调测试（6/19 - 6/25）
- [ ] 三方联调
- [ ] 修复问题
- [ ] 性能优化

### 第四阶段：切换上线（6/26 - 6/29）
- [ ] 旧架构停止
- [ ] 新架构启动
- [ ] 验证无问题

---

## 六、风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| NATS Server 宕机 | 消息丢失 | JetStream 持久化 + 自动重连 |
| 认证问题 | 未授权访问 | NKEY/JWT 或保留 HMAC |
| 消息顺序 | 乱序 | JetStream 保证顺序 |
| 迁移失败 | 服务中断 | 先测试环境验证，再生产切换 |

---

## 七、待讨论事项

1. **NATS Server 部署**：内嵌 vs 独立进程？
2. **认证方式**：HMAC vs NKEY/JWT？
3. **种子 Agent 迁移**：ZS0001/ZS0002/ZS0005 如何无感切换？
4. **Observer 事件格式**：统一 JSON 格式？
5. **SDK 版本管理**：直接改还是新建 v2？
6. **配置文件**：旧 config.json 兼容性？

---

**请各方在群里回复确认，有异议直接提出，讨论通过后再开发。**
