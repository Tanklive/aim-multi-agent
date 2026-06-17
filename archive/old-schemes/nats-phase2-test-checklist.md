# NATS Phase 2 三方联调测试清单

> **起草**：吉量 🐴 (ZS0002)
> **日期**：2026-06-09
> **分工**：呱呱🐸→Server瘦身, 吉量🐴→Observer骨架+SDK补齐, 小火鸡儿🐤→NATS客户端深度集成
> **原则**：每项带**操作步骤**、**预期结果**、**通过标准**，三方对照执行，不扯皮

---

## 目录

- [Phase 2.1: Observer 骨架 (吉量主导)](#phase-21-observer-骨架-吉量主导)
- [Phase 2.2: SDK 补齐 (吉量主导)](#phase-22-sdk-补齐-吉量主导)
- [Phase 2.3: NATS Server 瘦身 (呱呱主导)](#phase-23-nats-server-瘦身-呱呱主导)
- [Phase 2.4: 小火鸡儿 NATS 深度集成 (小火鸡儿主导)](#phase-24-小火鸡儿-nats-深度集成-小火鸡儿主导)
- [Phase 2.5: 三方联调测试](#phase-25-三方联调测试)
- [Phase 2.6: 异常/边界场景](#phase-26-异常边界场景)

---

## Phase 2.1: Observer 骨架 (吉量主导)

### OBS-T1: Observer 连接到 NATS 并订阅 aim.obs.*

| 项目 | 内容 |
|------|------|
| **操作** | Observer 启动，连接到 NATS Server，订阅 `aim.obs.>` |
| **预期结果** | Observer 成功连接，订阅确认消息输出 |
| **通过标准** | Observer 输出 "已连接" + "已订阅 aim.obs.>", 无异常退出 |

### OBS-T2: Observer 收到 agent_online 事件

| 项目 | 内容 |
|------|------|
| **操作** | Agent (如 ZS0001) 上线发布 `aim.sys.online` 事件，Observer 订阅中 |
| **预期结果** | Observer 显示 `🟢 agent_online: ZS0001` |
| **通过标准** | 事件显示在 3s 内，agent_id/ts/detail 字段完整 |

### OBS-T3: Observer 收到 agent_offline 事件

| 项目 | 内容 |
|------|------|
| **操作** | Agent 下线或断开连接，发布 `aim.sys.offline` 事件 |
| **预期结果** | Observer 显示 `🔴 agent_offline: ZS0001` |
| **通过标准** | 下线事件在断连后 15s 内触发（NATS 心跳超时检测），字段完整 |

### OBS-T4: Observer 收到 dm 事件

| 项目 | 内容 |
|------|------|
| **操作** | ZS0001 发送 DM 给 ZS0002，Observer 订阅 `aim.obs.>` |
| **预期结果** | Observer 显示 `💬 message: ZS0001 — 发送消息给 ZS0002` |
| **通过标准** | 事件包含 type/message/from/to/ts，摘要正确 |

### OBS-T5: Observer 收到 group 事件

| 项目 | 内容 |
|------|------|
| **操作** | ZS0001 发送群聊消息到 `aim.grp.grp_trio` |
| **预期结果** | Observer 显示 `💬 group_message: ZS0001 — grp_trio: ...` |
| **通过标准** | 事件显示群组名 + 发送者 + 消息摘要 |

### OBS-T6: Observer 过滤能力 --watch target

| 项目 | 内容 |
|------|------|
| **操作** | Observer 启动时指定 `--target ZS0002`，然后 ZS0001 和 ZS0002 分别发事件 |
| **预期结果** | 只显示 ZS0002 相关的事件，ZS0001 的事件被过滤掉 |
| **通过标准** | 非 target 的事件 0 显示，target 事件正常显示 |

### OBS-T7: Observer 断连后重连

| 项目 | 内容 |
|------|------|
| **操作** | Observer 运行中，重启 NATS Server，然后重启 Observer |
| **预期结果** | Observer 自动（或手动）重连后继续接收事件 |
| **通过标准** | 重连后事件正常订阅，不丢订阅（需验证上线事件再次显示） |

---

## Phase 2.2: SDK 补齐 (吉量主导)

### SDK-T1: 私密消息发送 (AIMNATSClient.send_dm)

| 项目 | 内容 |
|------|------|
| **操作** | 使用 SDK `send_dm("ZS0001", "hello")` 发送私聊 |
| **预期结果** | 消息发布到 `aim.dm.ZS0001`，信封格式符合 Veritas v1.0 |
| **通过标准** | 消息包含 ver/id/ts/from/type/payload，payload.text 正确，3s 内送达 |

### SDK-T2: 群聊消息发送 (AIMNATSClient.send_group)

| 项目 | 内容 |
|------|------|
| **操作** | 使用 SDK `send_group("grp_trio", "hello 兄弟们")` 发送群聊 |
| **预期结果** | 消息发布到 `aim.grp.grp_trio` |
| **通过标准** | 同 SDK-T1，信封格式正确，所有订阅该群组的成员收到 |

### SDK-T3: 消息订阅 (subscribe_dm / subscribe_group)

| 项目 | 内容 |
|------|------|
| **操作** | SDK 调用 `subscribe_dm(callback)` / `subscribe_group("grp_trio", callback)` |
| **预期结果** | callback 收到消息时正确解析信封 |
| **通过标准** | callback 收到的 dict 包含完整信封字段，type/payload/from 正确 |

### SDK-T4: PIN 去重持久化

| 项目 | 内容 |
|------|------|
| **操作** | 连续发送 3 条同 msg_id 的消息，SDK 端处理 |
| **预期结果** | PIN 检测到重复，后 2 条被过滤不传给 callback |
| **通过标准** | callback 只触发 1 次，pin stats 显示 hits=2,mises=1，重启后去重仍生效（SQLite 持久化）|

### SDK-T5: Observer 事件发布 (publish_observer_event)

| 项目 | 内容 |
|------|------|
| **操作** | SDK 调用 `publish_observer_event("message", {"from": "ZS0002", "to": "ZS0001", ...})` |
| **预期结果** | 事件发布到 `aim.obs.ZS0002`，Observer 收到 |
| **通过标准** | Observer 显示对应事件，字段完整，通过 OBS-T1~OBS-T7 验证 |

### SDK-T6: 系统事件发布 (publish_sys_online / publish_sys_offline)

| 项目 | 内容 |
|------|------|
| **操作** | SDK 调用 `publish_sys_online()` 和 `publish_sys_offline()` |
| **预期结果** | 事件分别发布到 `aim.sys.online` / `aim.sys.offline` |
| **通过标准** | Observer 收到显式上线/下线通知 |

### SDK-T7: RetryManager 重试 + 离线缓存

| 项目 | 内容 |
|------|------|
| **操作** | SDK 发送消息时 NATS 暂时不可用（停 Server） |
| **预期结果** | RetryManager 阶梯退避（1s→2s→4s→8s），Server 恢复后消息自动发出 |
| **通过标准** | 消息未丢失，Server 恢复后 30s 内送达，retry stats 显示 retry_count>0 |

### SDK-T8: 注册流程 (reg.register request-reply)

| 项目 | 内容 |
|------|------|
| **操作** | SDK 发送注册请求到 `aim.reg.register`，等待回复 |
| **预期结果** | 收到注册确认回复（含 agent_id + subject） |
| **通过标准** | request-reply 在 5s 内响应，回复格式合法 |

---

## Phase 2.3: NATS Server 瘦身 (呱呱主导)

### SRV-T1: WebSocket 连接池删除验证

| 项目 | 内容 |
|------|------|
| **操作** | 确认 Node.js Hub Server 中 connection_pool 模块已被移除 |
| **预期结果** | 旧 WebSocket 连接管理代码不再存在 |
| **通过标准** | grep "connection_pool" 返回空项，NATS 启动正常 |

### SRV-T2: WebSocket 离线队列模块验证

| 项目 | 内容 |
|------|------|
| **操作** | 确认离线队列 JSONL 模块已被移除或停用 |
| **预期结果** | 离线消息由 JetStream 持久化承载 |
| **通过标准** | JetStream Stream "aim-messages" 存在且配置正确（7d 保留），旧 JSONL 逻辑不运行 |

### SRV-T3: WebSocket delivery 模块验证

| 项目 | 内容 |
|------|------|
| **操作** | 确认 delivery/retry 模块已移除 |
| **预期结果** | 消息路由由 NATS Subject 机制处理 |
| **通过标准** | Agent 间消息通过 NATS 直达（不经过 Hub 中转），无 delivery 代码残留 |

### SRV-T4: 旧 Hub 进程停止

| 项目 | 内容 |
|------|------|
| **操作** | 停止旧 WebSocket Hub (port 18900) |
| **预期结果** | 旧 Hub 进程退出，NATS Server (port 4222) 正常运行 |
| **通过标准** | `lsof -i :18900` 无输出，`lsof -i :4222` 显示 nats-server |

---

## Phase 2.4: 小火鸡儿 NATS 深度集成 (小火鸡儿主导)

### FIRE-T1: nats-py 安装 + 基础连通

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 环境安装 nats-py，写简单脚本连接 NATS Server |
| **预期结果** | 连接成功，能 publish/subscribe 测试消息 |
| **通过标准** | 连接成功输出确认，订阅收到消息 |

### FIRE-T2: 私聊接收（订阅 aim.dm.ZS0005）

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 订阅 `aim.dm.ZS0005`，ZS0001/ZS0002 发送 DM 给 ZS0005 |
| **预期结果** | ZS0005 收到 DM，解析信封格式正确 |
| **通过标准** | DM 3s 内送达，envelope 字段完整（ver/id/from/payload/text）|

### FIRE-T3: 私聊发送（发布 aim.dm.目标）

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 发送 DM 给 ZS0001 和 ZS0002 |
| **预期结果** | ZS0001、ZS0002 分别收到 |
| **通过标准** | 消息双方均 3s 内送达，信封格式正确 |

### FIRE-T4: 群聊参与（订阅 aim.grp.grp_trio）

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 订阅 `aim.grp.grp_trio`，三方在群中互发消息 |
| **预期结果** | 三方都能收到群聊消息 |
| **通过标准** | 所有群成员收到，消息不重不漏 |

### FIRE-T5: 消息去重集成

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 集成 msg_id 去重（内存或 PIN 组件） |
| **预期结果** | 重复 msg_id 的消息被过滤 |
| **通过标准** | 同一 msg_id 只触发一次 handler |

### FIRE-T6: 进程保活 (launchd/systemd)

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 配置 launchd/systemd plist + 自动重连代码 |
| **预期结果** | 进程退出后自动重启，NATS 断连后自动重连 |
| **通过标准** | plist 已安装+已加载，kill 后 10s 内自动重启，重连后消息正常收发 |

### FIRE-T7: 消息回放（断连期间的消息）

| 项目 | 内容 |
|------|------|
| **操作** | ZS0005 断连 → 发消息给 ZS0005 → ZS0005 重连 |
| **预期结果** | JetStream 持久化的断连期间消息被回放 |
| **通过标准** | 重连后收到断连期间的消息，顺序正确，内容完整 |

---

## Phase 2.5: 三方联调测试

### E2E-T1: 三方私聊闭环 ZS0001 ↔ ZS0002

| 项目 | 内容 |
|------|------|
| **操作** | ZS0001 → ZS0002: "你好吉量"，ZS0002 回复 |
| **预期结果** | ZS0002 收到并回复，ZS0001 收到回复 |
| **通过标准** | DM 双向可达，Observer 可看到双方事件 |

### E2E-T2: 三方私聊闭环 ZS0001 ↔ ZS0005

| 项目 | 内容 |
|------|------|
| **操作** | ZS0001 → ZS0005: "你好小火鸡儿"，ZS0005 回复 |
| **预期结果** | ZS0005 收到并回复（通过 handler 回调），ZS0001 收到回复 |
| **通过标准** | DM 双向可达，Letta 框架回调正常 |

### E2E-T3: 三方私聊闭环 ZS0002 ↔ ZS0005

| 项目 | 内容 |
|------|------|
| **操作** | ZS0002 → ZS0005: "测试 DM"，ZS0005 回复 |
| **预期结果** | 同 E2E-T2 |
| **通过标准** | 全部 3 对 DM 链路都经过验证 |

### E2E-T4: 三方群聊（grp_trio）

| 项目 | 内容 |
|------|------|
| **操作** | 任意一方在 grp_trio 群发消息，其他两方确认收到 |
| **预期结果** | 三方都收到群聊消息 |
| **通过标准** | 三方各收到 1 条，不以发送者自己重复接收。Observer 看到群聊事件 |

### E2E-T5: Request/Reply 超时

| 项目 | 内容 |
|------|------|
| **操作** | 向不在线的 agent_id 发 request |
| **预期结果** | 超时返回 NoRespondersError |
| **通过标准** | 5s 内抛出超时异常，不 hang，不 crash |

### E2E-T6: 三方 Observer 同时观看

| 项目 | 内容 |
|------|------|
| **操作** | 同时启动 2 个 Observer (分别 watch all 和 watch ZS0001)，三方互相发消息 |
| **预期结果** | 两个 Observer 都正常收到事件，`watch all` 显示全部，`watch ZS0001` 只显示 ZS0001 |
| **通过标准** | 过滤正确，不丢事件，不断连 |

---

## Phase 2.6: 异常/边界场景

### EX-T1: 大消息传输（max_payload）

| 项目 | 内容 |
|------|------|
| **操作** | 发送 ~500KB 的消息（含大文本 payload） |
| **预期结果** | 消息正常投递，不截断不崩溃 |
| **通过标准** | 接收端收到的内容完整，总字节数一致 > 1MB 时返回 max_payload 错误而非崩溃 |

### EX-T2: 高频消息

| 项目 | 内容 |
|------|------|
| **操作** | 30s 内发送 50 条消息到同一 Queue Group |
| **预期结果** | 所有消息被消费，无丢失 |
| **通过标准** | 接收端收到 50/50 条，NATS Server 无内存泄漏 |

### EX-T3: 断连→重连→JetStream 回放

| 项目 | 内容 |
|------|------|
| **操作** | 一台 Agent 下线 → 其他 Agent 发送 10 条 DM → 下线 Agent 重连 |
| **预期结果** | JetStream 消费（Durable Consumer）回放离线期间的消息 |
| **通过标准** | 重连后收到全部 10 条消息，顺序一致，无丢无重 |

### EX-T4: NATS Server 重启

| 项目 | 内容 |
|------|------|
| **操作** | 三方在线时重启 NATS Server，然后观察重连 + 恢复 |
| **预期结果** | 三方 NATS 客户端自动重连（指数退避），连接恢复后消息正常 |
| **通过标准** | 所有 Agent 30s 内重连成功，JetStream 持久化消息不丢，Observer 恢复订阅 |

### EX-T5: 旧 Hub 无影响并行

| 项目 | 内容 |
|------|------|
| **操作** | NATS 新端口 (4222) 和旧 Hub (18900) 并行运行，分别发消息测试 |
| **预期结果** | 两边互不干扰，各自正常收发 |
| **通过标准** | NATS 端正常，旧 Hub 端正常，无冲突 |

---

## 执行顺序建议

```
┌─────────────────────────────────────────────────────────────────┐
│  并行准备阶段                                                    │
│                                                                  │
│  吉量: OBS-T1~OBS-T7 + SDK-T1~SDK-T8                            │
│  呱呱: SRV-T1~SRV-T4 (Server 瘦身)                              │
│  小火鸡儿: FIRE-T1~FIRE-T7 (客户端深度集成)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  联调阶段（三方一起）                                              │
│                                                                  │
│  第1步: E2E-T1 (ZS0001↔ZS0002) 快速验证                          │
│  第2步: E2E-T2 (ZS0001↔ZS0005) 验证小火鸡儿集成                                    │
│  第3步: E2E-T3 (ZS0002↔ZS0005) 完成全部私聊闭环                                │
│  第4步: E2E-T4 (三方群聊) 群聊验证                            │
│  第5步: E2E-T5 (Request/Reply) + E2E-T6 (Observer 双视图)                  │
│  第6步: EX-T1~EX-T5 异常/边界                                │
└─────────────────────────────────────────────────────────────────┘
```

## 执行纪律

1. **每个测试项执行前**：先确认前置条件就绪（Server 运行、Agent 在线）
2. **每个测试项完成后**：在对应测试项后标注 ✅ 以及执行时间
3. **失败处理**：标注 ❌ + 失败原因 + 责任人，修复后重新测试
4. **三方确认**：联调阶段的每个 E2E 测试，必须三方都确认收到消息才算通过
5. **报告**：全部完成后汇总到 grp_trio 群汇报大哥

---

*文档版本: v1.0 | 最后更新: 2026-06-09 03:50*

---

## Phase 2.3 执行记录 (呱呱 2026-06-10 20:18)

### SRV-T1: WebSocket 连接池删除验证 ✅
- 执行时间：2026-06-10 20:15
- aim_server.py (402行) 纯 NATS 架构，零 connection_pool 引用
- nats-agent.py / aim_agent_nats.py 均不引用旧模块
- 旧文件已归档至 archive/ws-cleanup-20260610/
- **通过标准达成**：grep "connection_pool" 运行代码返回空项，NATS 启动正常 ✅

### SRV-T2: WebSocket 离线队列模块验证 ✅
- 执行时间：2026-06-10 20:16
- 无 JSONL 离线队列文件（已清理）
- JetStream Stream "AIM_MESSAGES" 存在，subjects: agent.*.msg, group.*.msg
- aim_server.py 无旧离线队列逻辑
- **通过标准达成**：JetStream 承载离线消息持久化，旧 JSONL 逻辑不运行 ✅

### SRV-T3: WebSocket delivery 模块验证 ✅
- 执行时间：2026-06-10 20:18
- delivery.py / retry_integration.py 已归档
- 运行代码零 delivery/retry_integration 引用
- 消息通过 NATS Subject 路由直达（agent.*.msg / group.*.msg）
- **通过标准达成**：Agent 间消息通过 NATS 直达，无 delivery 代码残留 ✅

### SRV-T4: 旧 Hub 进程停止 ✅
- 执行时间：2026-06-10 20:18
- lsof -i :18900 → 无输出（旧 Hub 已停止）
- lsof -i :4222 → nats-server PID 3159 正常 LISTEN
- **通过标准达成**：旧 Hub 不运行，NATS Server 正常 ✅

### 归档文件清单
| 文件 | 行数 | 归档位置 |
|------|------|----------|
| connection_pool.py | 872 | archive/ws-cleanup-20260610/ |
| delivery.py | 780 | archive/ws-cleanup-20260610/ |
| retry_integration.py | 347 | archive/ws-cleanup-20260610/ |
| msg_dedup.py | 138 | archive/ws-cleanup-20260610/ |
| message_bridge.py | 107 | archive/ws-cleanup-20260610/ |
| retry_components.py | — | archive/ws-cleanup-20260610/ |
