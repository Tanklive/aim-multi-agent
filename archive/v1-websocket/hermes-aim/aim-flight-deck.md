# AIM Flight Deck — NATS 架构详细方案

> **状态**：草案（待三方评审）
> **作者**：吉量 🐴 (ZS0002)
> **日期**：2026-06-08
> **背景**：当前 AIM WebSocket Hub 模式暴露了连接不稳定、消息保达脆弱、代码维护成本高等问题。大哥决定切换至 NATS 作为传输层，基于积累的经验重新设计架构。

---

## 1. 原始需求回顾

AIM 从最初的定位就非常明确：

| 维度 | 原始目标 |
|------|---------|
| **本质** | 跨框架 Agent 间实时通讯协议 |
| **对标** | HTTP + curl — AIM = 通讯协议 + 标准客户端 |
| **核心能力** | 私聊、群聊、离线消息 |
| **不绑** | 不绑定任何 AI 框架（Hermes/OpenClaw/CrewAI/Letta 都能接） |
| **身份** | 注册制：全局 ID（ZS 序列）+ HMAC 认证，Hub 分配 |
| **去中心化倾向** | 每个 Agent 既是客户端又能做服务端（受飞秋启发） |
| **安全** | 可信的认证机制，防冒充、防重放 |

这些需求 **NATS 全部能满足，而且更优**。

---

## 2. 当前 WebSocket Hub 模式的问题总结

| # | 问题 | 根因 | 在 NATS 中的解决 |
|---|------|------|----------------|
| 1 | ZS0005 反复断连 | 客户端无自动重连 + 无进程保活 | nats-py 内置指数退避自动重连 |
| 2 | 消息保达不可靠 | 自己写的 ACK/重传/离线队列，bug 多 | JetStream Durable Consumer 原生可靠 |
| 3 | Hub 单点故障 | 中心 Hub 挂了全员下线 | NATS Server 可集群可单机，启动快 |
| 4 | 代码膨胀 | ~1742 行 node.py + connection_pool + delivery + retry | NATS 替代传输层，砍掉 ~1500 行 |
| 5 | 调试困难 | 无原生 CLI 工具 | `nats` CLI 直接查看 stream/consumer |
| 6 | 通道混乱 | main + script 互相对冲 | Queue Group 原生负载均衡 |
| 7 | 无法去中心化 | Hub 中心模式，Agent 间无直连 | Leaf Nodes 原生支持 |
| 8 | 消息历史查询要自己写 | Hub 无原生消息存储 | JetStream Stream 自动归档 |

---

## 3. 新架构全景：AIM on NATS

```
┌──────────────────────────────────────────────────────────────────┐
│                      AIM 应用层（Python/nats-py）                  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  AAM：Agent Application Mapping 层                          │  │
│  │  - Subject 命名规范（私聊/群聊/observer/注册）              │  │
│  │  - 消息信封格式（兼容现有 JSON 格式）                       │  │
│  │  - 身份认证（NATS JWT + 可选 HMAC 应用层签名）              │  │
│  │  - 注册制（Agent 注册/发现/状态管理）                       │  │
│  │  - Observer 机制（status_feedback 推送）                    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Agent 适配器（可选封装层，保持现有 SDK 接口不变）          │  │
│  │  - aim_send.py(封装为 nats.publish)                         │  │
│  │  - aim-watch.py(封装为 nats.subscribe)                      │  │
│  │  - aim-agent.py(重连/心跳由 nats-py 处理)                   │  │
│  └────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                    NATS 传输层（nats-server）                      │
│                                                                  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐   │
│  │ Core NATS  │ │ JetStream  │ │  JWT Auth  │ │  WebSocket │   │
│  │ Pub/Sub    │ │ 持久化     │ │  认证      │ │  兼容端口  │   │
│  │ Req/Rep    │ │ Stream    │ │  权限      │ │  port 9222 │   │
│  │ QueueGroup │ │ Consumer  │ │  过期/撤销 │ │            │   │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘   │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Leaf Nodes（未来去中心化）                                   │  │
│  │  - 每个 Agent 可运行轻量 NATS Server 作为叶子节点           │  │
│  │  - 断网本地独立运行，恢复后自动同步                          │  │
│  └────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                    OAS 对接层（未来）                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ OAS (Open Agent Standard) 集成                              │  │
│  │  - 身份系统：AIM ZS ID ↔ OAS DID                         │  │
│  │  - 能力声明：AIM Subject ↔ OAS Capability Passport        │  │
│  │  - 可信路由：AIM 消息 → OAS 验证 → 投递                    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

关键变化：
- 删除 ~1500 行手写传输层代码（connection_pool/delivery/retry）
- 新增 AIM 应用层映射（AAM），约 300-500 行
- 新增 OAS 对接接口，约 200-300 行
- 净减少 ~700-1000 行代码
```

---

## 4. Subject 命名规范（AIM on NATS）

这是新架构最核心的设计——在 NATS 上如何表达 AIM 的通信模式。

### 4.1 Subject 体系

```
aim.                               # 根命名空间
├── register                       # 注册（类似 HTTP POST /register）
├── private.<agent_id>             # 私聊 subject（每个 Agent 一个）
├── group.<group_id>               # 群聊 subject
├── observer.<agent_id>            # 状态推送（status_feedback）
├── system.                        # 系统消息
│   ├── online                     # Agent 上线通知
│   ├── offline                    # Agent 下线通知
│   └── heartbeat                  # 心跳
├── req.                           # 请求-回复
│   ├── <agent_id>                 # 发给某 Agent 的请求
│   └── _inbox.<reply_to>          # 自动生成的回复 subject
└── oas.                           # OAS 未来扩展
    ├── capability.<agent_id>      # 能力声明
    └── did.<did_method>           # DID 身份解析
```

### 4.2 通信方式映射

| AIM 场景 | 当前 WebSocket | NATS 方案 |
|---------|---------------|-----------|
| **私聊：A→B** | WS: `send(to=ZS0001, msg)` → Server 路由 → WS push | `nc.request("aim.private.ZS0001", msg)` → 对方用 Queue Group 消费 |
| **群聊：A→群** | WS: `send(to=grp_trio, group=True)` → Server 广播 | `nc.publish("aim.group.grp_trio", msg)` → 群成员各自 subscribe |
| **A 监听私信** | 保持 WS main 通道，Server 投递 | `nc.subscribe("aim.private.ZS0002", queue="aim-private")` |
| **Observer 监控** | observer 通道绑定 + status_log.jsonl | `nc.subscribe("aim.observer.>")` 或 Stream 回放 |
| **注册** | 注册 API + config.json | 可保留现有注册制，NATS 只做传输层 |
| **请求-回复** | 自己实现 `_INBOX_` 机制 | NATS 原生 `nc.request()` / `nc.subscribe()` 配对 |

### 4.3 消息信封格式（兼容现有）

```json
{
  "msg_id": "a1b2c3d4e5f6",
  "from": "ZS0002",
  "to": "ZS0001",
  "type": "DM",
  "channel": "main",
  "content": "你好呱呱",
  "timestamp": "2026-06-08T22:00:00",
  "hmac_sig": "sha256=..."   // 可选：应用层签名（双重保险）
}
```

NATS 原生 Header 也可以携带这些元信息，但为了兼容现有 SDK，建议保留 JSON 信封格式在 body 中。

---

## 5. 模块改动清单

### 5.1 需要新写（新增）

| 文件 | 行数估算 | 说明 |
|------|---------|------|
| `aim-nats-adapter.py` | ~300 行 | NATS 连接管理 + Subject 映射 + 消息封装 |
| `aim-server.py` (NATS 版) | ~500 行 | 瘦身版 Server：认证逻辑 + 注册管理 + Observer 路由 |
| `nats-deploy.sh` | ~80 行 | 一键安装 nats-server + 创建 Stream/Consumer |

### 5.2 需要改造

| 文件 | 当前 | NATS 后 | 改动量 |
|------|------|---------|--------|
| `aim-agent.py` | 1642 行，WS 连接 + 认证 + 心跳 + 重连 | 去掉 WS 和重连逻辑，改为 nats-py 连接 | 中改 |
| `aim_send.py` | 临时 WS 连接 + 认证 + 发消息 | 直接 `nats.publish()` 或 `nats.request()` | 小改 |
| `aim-watch.py` | observer 通道监听 + 日志文件回放 | 直接 `nats.subscribe("aim.observer.>")` + JetStream 回放 | 小改 |
| `security.py` | HMAC 签名 | 可选保留应用层签名 + NATS JWT 传输层认证 | 中改 |
| `status_feedback.py` | 自己写文件 + WS 推送 | NATS Subject 发布 + JetStream 自动归档 | 小改 |

### 5.3 可以直接删除

| 文件 | 行数 | 原因 |
|------|------|------|
| `node.py` | 1742 行 | 整体替换为 NATS Server + 瘦身版 Server |
| `connection_pool.py` | ~700 行 | Queue Group + nats-py 自动管理 |
| `delivery.py` | ~400 行 | JetStream Durable Consumer |
| `retry_integration.py` | ~300 行 | NATS 自动重连 |
| `offline_*.jsonl` | 数据文件 | JetStream Stream 替代 |

### 5.4 删除文件和对应收益

| 文件 | 行数 | 存在问题 | NATS 替代方案 | 收益 |
|------|------|---------|-------------|------|
| node.py | 1742 | 单点、连接管理复杂、认证路由全耦合 | NATS Server(传输) + 瘦身 AIM 应用层 | 稳定可靠，去中心化 |
| connection_pool.py | ~700 | 自己实现的连接池、Handler 选举、Grace Period | NATS Queue Group + 自动重连 | 无需维护，官方保障 |
| delivery.py | ~400 | 重传、离线队列、ACK 去重 | JetStream Durable Consumer | 原生可靠，无需手写 |
| retry_integration.py | ~300 | 断连回放、缓存恢复 | NATS 自动重连 + JetStream 重放 | 零代码 |
| status_feedback.py 改写 | 是 | WS推送+文件写入 | NATS Subject 发布 | 简单可靠 |
| 合计节省 | ~3100+ | 删除+大改 | — | 精简为 800-1000 行 |

> **关键认知转变**：当前 AIM 代码量 ~5000 行，其中 ~60% 是在造传输层的轮子。NATS 接管后，我们只需要关注应用层逻辑 ~1500 行 + NATS 适配 ~300 行 = ~1800 行。代码量减少一半，稳定性由 NATS 官方保证。**未来 OAS 对接也不需要自己写传输层**，直接在 NATS Subject 上叠加 OAS 能力验证即可。

---

## 6. 安全方案（公网适配）

### 6.1 传输层：TLS + NATS JWT Auth

```
┌─────────────────────────────────────────────────┐
│  NATS Server TLS                                │
│  - ECDSA 证书（Let's Encrypt 免费）              │
│    - 双向 TLS（mTLS）可选                        │
└─────────────────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────┐
│  NATS JWT Auth（2.0+ 原生）                      │
│                                                  │
│  Operator JWT（大哥持有私钥）── 信任锚            │
│      └─ Account JWT（每个 Agent 一个）── 身份     │
│           └─ User JWT（每次连接签发）── 会话      │
│                                                  │
│  功能：                                           │
│  - 每个 Agent 独立 Account JWT                    │
│  - 可设置过期时间（ex: 30天、1年）                │
│  - 可精确控制每个 Subject 的 pub/sub 权限        │
│  - 可通过 revocation list 撤销                   │
└─────────────────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────┐
│  应用层签名（可选双层保险）                        │
│  - 保留 HMAC-SHA256 应用层签名                   │
│  - 每条消息携带 hmac_sig 字段                    │
│  - 防止 NATS JWT 泄漏后的消息伪造                │
└─────────────────────────────────────────────────┘
```

### 6.2 各方案对比（公网场景）

| 方案 | 安全性 | 复杂度 | 密钥分发 | 轮换 | 撤销 | 费用 |
|------|--------|--------|---------|------|------|------|
| **HMAC + TLS** | 中高 | 低 | 手动 | 手动 | 手动 | 免费 |
| **NATS JWT + TLS** | 高 | 中 | 签发即可 | 自动过期 | 自动撤销 | 免费 |
| **mTLS** | 最高 | 中高 | 签证书 | 证书过期 | CRL | 免费 |
| **HMAC + NATS JWT(双层)** | 最高 | 中 | JWT 签发 + 手动备用 | 双向 | 双向 | 免费 |

**推荐：NATS JWT + TLS** — 这是 NATS 团队推荐的标准公网配置，功能完整，零额外成本。

---

## 7. 未来对接 OAS（Open Agent Standard）

OAS 是未来 Agent 身份互认和能力发现的标准体系。AIM on NATS 天然便于对接：

```
现有 AIM:      Agent A ──WS──→ Hub ──WS──→ Agent B
未来 AIM+NATS:  Agent A ──NATS──→ Agent B    （点对点）
                                               ↓
                OAS 层叠加在 NATS 之上：
                - aim.oas.capability.ZS0001  ← 能力声明
                - aim.oas.did.key:...        ← DID 解析
                - aim.oas.trust.<scope>      ← 信任路由
```

**OAS 对接策略：**

| OAS 组件 | AIM Subject 映射 | 说明 |
|---------|----------------|------|
| 身份互认 | `aim.oas.did.key:<did>` | DID 文档发布在 NATS KV Store 中 |
| 能力声明 | `aim.oas.capability.<agent_id>` | 每个 Agent 发布能力 passport 到该 subject |
| 发现查询 | `nc.request("aim.oas.discover", timeout=5)` | 请求-回复模式查找 Agent |
| 可信路由 | `aim.oas.trust.<scope>` | 信任链验证后转发消息 |

**迁移路径：** AIM 先用 NATS 稳定跑起来 → 再逐步叠加 OAS 层。现在不需要做，但架构设计时要预留 `aim.oas.*` 命名空间，避免以后冲突。

---

## 8. 去中心化：Leaf Nodes 机制

这是 NATS 相比当前 Hub 模式最颠覆性的能力，也是当初飞秋"既是服务端又是客户端"这个想法的 NATS 实现版本。

```
        公网/高可用
   ┌────────────────┐
   │   NATS 集群     │  (主集群，可选多节点)
   │  aim.private.*  │
   │  aim.group.*    │
   └──┬─────┬─────┬──┘
      │     │     │
   ┌──▼─┐ ┌▼──┐ ┌▼──┐
   │LW  │ │LW │ │LW │  Leaf Node（轻量 NATS Server）
   │NATS│ │NATS│ │NATS│  每个 Agent 本地运行
   └──┬─┘ └─┬─┘ └─┬─┘
      │     │     │
   ┌──▼─┐ ┌▼──┐ ┌▼──┐
   │呱呱 │ │吉量│ │火鸡│  Agent 进程
   │🐸   │ │🐴 │ │🐤 │
   └────┘ └───┘ └───┘
```

**Leaf Node 模式的优势：**
1. **网络隔离安全** — Agent 只需连接本地 Leaf Node，不直接暴露公网
2. **断网独立运行** — 本地消息在 Leaf Node 缓存，恢复后自动同步
3. **跨公网** — Leaf Node 通过单一 WS/TLS 连接上行到主集群
4. **低延迟** — 本地消息走本地 NATS，不经过公网

**当前阶段：** 先单机 NATS Server 跑起来，Leaf Nodes 作为 Phase 2。

---

## 9. 迁移计划

### Phase 1：POC 验证（1-2 天）

```
1. brew install nats-server
2. nats-server -p 18900           # 和当前 Hub 同一端口
3. pip install nats-py
4. 写 AIM 映射层 demo：
   - nc.publish("aim.group.grp_trio", msg)
   - nc.subscribe("aim.private.ZS0002", queue="aim-private")
5. 验证：延迟、重连、离线消息
```

### Phase 2：代码改造 + 并行运行（3-5 天）

```
Day 1: 写 aim-nats-adapter.py + 瘦身版 AIM Server
Day 2: 改造 aim-agent.py / aim_send.py / aim-watch.py
Day 3: 部署 NATS Server + 并行运行测试
Day 4: 呱呱、小火鸡儿迁移验证
Day 5: 三方联调 + bug 修复
```

### Phase 3：全面切换

```
1. 停掉旧 WebSocket Hub
2. 删除 node.py / connection_pool.py / delivery.py / retry_integration.py
3. 更新文档 / 部署脚本
4. 上线公网 TLS + JWT 认证
```

---

## 10. 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| nats-py 成熟度 | 可能有 asyncio 兼容问题 | 先在单 Agent 测试，确认稳定再推 |
| 现有 SDK 接口兼容 | aim_send.py 用户习惯变动 | 封装适配层保持接口不变 |
| NATS 学习曲线 | 团队不熟悉 | NATS CLI + 文档完善，快速入门 |
| JetStream 延迟 | 实时通信可能受影响 | Core NATS 实时 → JetStream 只做持久化 |
| 数据迁移 | 现有消息历史需要迁移 | 写一次性迁移脚本 |

---

## 11. 成本清单

| 项目 | 费用 | 说明 |
|------|------|------|
| NATS Server | **免费** | Apache 2.0 开源，CNCF 项目 |
| nats-py | **免费** | pip install，MIT 协议 |
| JWT Auth | **免费** | 内置功能，无需付费版 |
| JetStream | **免费** | 内置功能 |
| TLS 证书 | **免费** | Let's Encrypt |
| Synadia Platform | 可选付费 | 托管服务，**不需要** |
| 合计 | **完全免费** | 同当前方案一致 |

---

## 12. 结论

| 维度 | 当前 WebSocket Hub | AIM on NATS |
|------|-------------------|-------------|
| 连接稳定性 | 📉 手动重连，不稳定 | 📈 nats-py 指数退避自动重连 |
| 消息保达 | 📉 手写 ACK/重传，bug 多 | 📈 JetStream Durable Consumer |
| 代码量 | ~5000 行 | ~1800 行（-64%） |
| 去中心化 | ❌ Hub 单点 | ✅ Leaf Nodes 原生支持 |
| 公网安全性 | ⚠️ HMAC + 手动 TLS | ✅ JWT Auth + TLS + 可选 HMAC 双层 |
| 调试体验 | ❌ 无 CLI 工具 | ✅ `nats` CLI 全方位查看 |
| OAS 对接 | ⚠️ 需要手写传输层 | ✅ 直接叠加 NATS Subject 上 |
| 成本 | 免费 | 免费 |
| 成熟度 | 自研，bug 自修 | CNCF 项目，社区成熟 |
