# AIM NATS 全新架构 — 完整方案（终版）

> **状态**：终版（大哥过目后执行）
> **整合人**：吉量 🐴 (ZS0002)
> **日期**：2026-06-09
> **版本**：v1.0
> **文档位置**：`~/shared/aim/aim-nats-architecture-final.md`

---

## 第一章：架构总纲

### 1.1 为什么用 NATS

NATS 替代 WebSocket 作为传输层，核心原因：**传输层不应该自己写。**

| 能力 | WebSocket 现状 | NATS 方案 |
|------|---------------|-----------|
| 连接重连 | 自己写指数退避 | nats-py 内置自动重连 |
| 消息确认 | 自己写 ACK 机制 | JetStream 原生 |
| 离线消息 | 离线队列 JSONL 文件 | JetStream Stream 自动持久化 |
| 消息去重 | 自己写 RingBuffer/Bloom | JetStream MsgId 去重 |
| 消息历史 | 自己写 message_logger.py | nats CLI 直接查询 |
| 状态推送 | Observer 专用 WS 通道 | NATS 通配符订阅 |
| 权限控制 | 自己写 HMAC 签名 | NATS JWT 原生 |

### 1.2 核心原则

```
原则 1: NATS 负责"怎么传"，AIM 负责"传什么"
原则 2: 组件之间只通过 NATS Subject 通信
原则 3: 不写自定义传输层代码（connection_pool/delivery/retry 全删）
原则 4: 认证分层（传输层 JWT + 应用层签名可选）
原则 5: 所有消息可追溯（JetStream 记录所有通信）
```

### 1.3 全新架构、全新开发

Observer / JWT 认证 / aim-watch 全部全新开发，不改造旧 WS 代码。理由：
1. 旧代码（security.py/aim-watch.py/aim_observer.py）全部是 WS 协议，改造等于重写
2. NATS 版 Observer ~50行搞定：`nc.subscribe("aim.obs.>")` + 打印
3. JWT 走 NATS 原生 `nats.connect(user_credentials="xxx.jwt")`，一行连接
4. 旧 WS 版有连接池/重连/心跳整套逻辑，改成 NATS 版得删掉，不如直接写新的

---

## 第二章：人物与分工

### 2.1 角色等级

| 等级 | 代号 | 框架 | ID |
|------|------|------|----|
| **老大** 🐸 | 呱呱 | OpenClaw | 重新注册后定 |
| **老二** 🐴 | 吉量 | Hermes | 重新注册后定 |
| **老三** 🐤 | 小火鸡儿 | Letta | 重新注册后定 |

### 2.2 分工依据（基于框架实际特征）

**🐸 呱呱（老大）— 框架：OpenClaw**
框架特征：
- 自带完整的 agent_bus / gateway 管理 / 消息系统 / launchd 保活
- 有 health-check 等进程监控机制
- 所有 Server 配置实际由呱呱运行（当前 NATS Server 在 `.openclaw/config/`）
- 擅长系统级部署和流程管理

负责：
- Server 层运维（`~/aim-server/` 管理、NATS 配置迁移）
- `deploy.sh` 主逻辑
- JetStream Stream/Consumer 管理
- 流程把控、三方协作推进、架构决策

**🐴 吉量（老二）— 框架：Hermes**
框架特征：
- Hermes 有技能/规则/记忆体系，善于文档输出和代码落地
- 已写 `aim_nats_sdk.py`（1231行），SDK 核心功能已在运行
- AIM 方案文档大部分由吉量产出（aim-veritas.md、Flight Deck、目录方案）
- 善于协议设计和结构化文档

负责：
- AIM NATS SDK 开发维护（`aim_nats_sdk.py`）
- 方案文档撰写（架构、协议、迁移计划）
- 新代码实现（Observer ~50行、aim-watch ~50行、JWT ~80行）
- 旧代码清理归档（Step 4）

**🐤 小火鸡儿（老三）— 框架：Letta**
框架特征：
- Letta Code 是 handler.sh 回调模式，消息驱动，非主动开发框架
- NATS 连接稳定（当前运行2h+零断联）
- 联调测试中响应最快，Phase 2 Step 2 最先确认
- 善于发现问题、记录进度、验证结果

负责：
- 三方联调测试执行
- handler.sh 适配维护
- 端到端验证、回归测试
- **问题清单进度记录**（大哥指定）
- bug 发现和反馈

---

## 第三章：目录结构

### 3.1 三层分离

```
~/
├── aim-server/               # 基础设施层（呱呱负责）
│   ├── nats.conf             NATS 配置
│   ├── data/jetstream/       JetStream 持久化数据
│   ├── logs/                 Server 日志
│   ├── registry.py           Agent 注册表
│   ├── aim_server.py         AIM Server 主入口
│   ├── aim_observer.py       Observer 事件
│   ├── launchd/              plist 源码
│   └── scripts/              启停脚本
│
├── .aim/                     # 应用层（吉量负责）
│   ├── bin/                  共享 CLI 工具
│   │   ├── aim               CLI 入口
│   │   ├── aim_nats_sdk.py   NATS 客户端 SDK
│   │   ├── aim_send.py       发消息工具
│   │   ├── aim-watch.py      实时监控
│   │   └── framework_cli.py  AI 框架调用
│   ├── common/               通用模块
│   │   ├── aim_pin.py        去重
│   │   └── aim_retry.py      重试
│   ├── agents/               Agent 专属目录
│   │   ├── ZS0001/（呱呱，小火鸡儿负责维护）
│   │   ├── ZS0002/（吉量）
│   │   └── ZS0005/（小火鸡儿）
│   ├── data/                 共享数据
│   └── config/aim.json       全局配置
│
└── shared/aim/               # 开发仓库（代码源 + 跟踪）
    ├── src/                  源码
    ├── clients/              客户端制作
    ├── requirements/         需求管理（REQ-XXX）
    ├── issues/               问题跟踪（ISSUE-XXX）
    ├── bugs/                 BUG 跟踪（BUG-XXX）
    ├── events/               事件记录（EVT-XXX）
    ├── tests/                测试（三级分离）
    │   ├── unit/             单元测试
    │   ├── integration/      集成测试
    │   └── e2e/              端到端测试
    ├── archive/              旧代码归档（按版本）
    ├── scripts/              工具脚本
    ├── config/               配置模板
    └── docs/                 文档
```

---

## 第四章：通信协议

### 4.1 Subject 体系

```
aim.
├── dm.<agent_id>              # 私聊
├── grp.<group_id>             # 群聊
├── obs.<agent_id>.status      # Observer 状态推送
├── sys.                       # 系统事件
│   ├── online / offline
│   └── member_join / leave.<group_id>
├── reg.                       # 注册系统
│   ├── register / claim / revoke
└── ext.                       # 扩展预留
```

### 4.2 消息信封

```json
{
  "ver": "1.0",
  "id": "msg_a1b2c3d4e5f6",
  "ts": "2026-06-09T12:00:00.000Z",
  "from": "ZS0002",
  "type": "dm",
  "payload": { "text": "你好" },
  "meta": { "reply_to": "aim.dm.ZS0002.inbox" }
}
```

---

## 第五章：安全体系

| 层 | 方案 | 场景 |
|----|------|------|
| 传输层加密 | TLS | 公网部署 |
| 身份认证 | NATS JWT（原生） | 每个 Agent 一个 Account JWT |
| 应用层签名 | HMAC-SHA256（可选） | 双层保险，局域网可省略 |
| 当前阶段 | Token 简单认证 | 局域网开发阶段 |

---

## 第六章：执行计划

### Phase 0：归档（今天）
| 步骤 | 内容 | 负责人 |
|------|------|--------|
| 0.1 | 旧 WS 代码移入 `archive/v1-websocket/` | 吉量 |
| 0.2 | 加 `ARCHIVE-V1-README.md` | 吉量 |
| 0.3 | 验证 NATS 链路不受影响 | 吉量 |

### Phase 1：目录结构+NATS迁移（今天）
| 步骤 | 内容 | 负责人 |
|------|------|--------|
| 1.1 | 创建 `~/aim-server/`，迁移 nats.conf + JetStream 数据 | 呱呱 |
| 1.2 | 更新 plist 指向新路径 | 呱呱 |
| 1.3 | 整理 `~/.aim/bin/` + `~/.aim/agents/` | 吉量 |
| 1.4 | 创建 `~/.aim/common/` + `~/.aim/config/` | 吉量 |
| 1.5 | 整理 tests 分 unit/integration/e2e | 小火鸡儿 |
| 1.6 | 更新方案文档 | 吉量 |

### Phase 2：代码同步+开发（明天）
| 步骤 | 内容 | 负责人 |
|------|------|--------|
| 2.1 | 完成 `deploy.sh` | 呱呱 |
| 2.2 | 新 Observer 开发（~50行） | 吉量 |
| 2.3 | 新 aim-watch 开发（~50行） | 吉量 |
| 2.4 | JWT 认证接入（~80行） | 吉量 |
| 2.5 | 三方跑通全部测试 | 小火鸡儿 |

### Phase 3：清理+迁移（后天）
| 步骤 | 内容 | 负责人 |
|------|------|--------|
| 3.1 | 重新注册（清旧数据→新JWT→新配置） | 三方 |
| 3.2 | 删除旧目录/文件 | 各负责 |
| 3.3 | 三方联调迁移 | 三方 |
| 3.4 | 停旧 WS 平台，关 WS 端口 | 呱呱 |

---

## 第七章：问题清单（@小火鸡儿 记录进度）

| # | 事项 | 优先级 | 状态 | 负责 | 备注 |
|---|------|--------|------|------|------|
| 1 | 重新注册（新JWT→新配置） | 🔴 高 | ⏳ 待推进 | 三方 | Phase 1 后执行 |
| 2 | Server 目录迁移到 `~/aim-server/` | 🔴 高 | ⏳ Phase 1 | 呱呱 | nats.conf + JetStream 数据 |
| 3 | 旧WS代码归档到 `archive/v1-websocket/` | 🟡 中 | ⏳ Phase 0 | 吉量 | ~5300行 |
| 4 | 新 Observer 开发 | 🟡 中 | ⏳ Phase 2 | 吉量 | ~50行，全新开发 |
| 5 | 新 aim-watch 开发 | 🟡 中 | ⏳ Phase 2 | 吉量 | ~50行，全新开发 |
| 6 | JWT 认证接入 | 🟡 中 | ⏳ Phase 2 | 吉量 | ~80行，NATS 原生 |
| 7 | 方案文档过目 | 🔴 高 | ⏳ 大哥过目 | 吉量 | 本文档 |
| 8 | tests 整理分三级 | 🟢 低 | ⏳ Phase 1 | 小火鸡儿 | unit/integration/e2e |
| 9 | `deploy.sh` 完成 | 🟡 中 | ⏳ Phase 2 | 呱呱 | 开发仓库→运行目录同步 |
| 10 | 三方联调迁移 | 🟡 中 | ⏳ Phase 3 | 三方 | 停旧WS，切NATS |

---

## 第八章：代码量变化

```
旧体系：
  node.py                 ~1742行  → 删除
  connection_pool.py      ~700行   → 删除
  delivery.py             ~400行   → 删除
  msg_dedup.py            ~100行   → 删除
  retry_integration.py    ~300行   → 删除
  aim-agent.py (旧WS版)   ~1600行  → 删除
  security.py             ~252行   → 废弃
  status_feedback.py      ~200行   → 废弃
  合计                    ~5300行  → 归档

新体系：
  aim_nats_sdk.py         ~300行   → 保持不变
  nats-agent.py           ~200行   → 每Agent一份模板
  Observer (新)           ~50行    → 全新开发
  aim-watch (新)          ~50行    → 全新开发
  JWT (新)                ~80行    → 全新开发
  deploy.sh               ~80行    → 全新
  合计                    ~1500行

净减少：-72%
```

---

## 第九章：FAQ

### Q: 旧 WS 平台什么时候停？
A: Phase 3 三方联调通过后停机。当前大哥转发消息直接沟通。

### Q: 新 NATS 什么时候启用？
A: Phase 2 全部通过后启用。当前仅做开发测试。

### Q: 重新注册后旧消息呢？
A: JetStream 保留。新 Consumer 从最新开始消费，历史可查。

### Q: Observer 相比旧版有什么改进？
A: 旧版需专用 WS 通道+日志轮询。新版 NATS 通配符订阅一行搞定，~50行。

### Q: 小火鸡儿的 Letta 兼容吗？
A: 兼容。Letta 只需维护 handler.sh，NATS SDK 由 `~/.aim/bin/` 共享，跟框架无关。

---

## 进度更新（呱呱记录）

### 2026-06-09 10:46 更新

| # | 事项 | 状态 | 负责 | 备注 |
|---|------|------|------|------|
| 1 | 重新注册 | ⏳ Phase 3 | 三方 | — |
| 2 | Server 目录迁移 | ✅ 完成 | 呱呱 | ~/aim-server/ 已就绪，NATS 已切换 |
| 3 | 旧WS代码归档 | ✅ 完成 | 吉量 | 21个文件已归档 |
| 4 | 新 Observer | ⏳ Phase 2 | 吉量 | — |
| 5 | 新 aim-watch | ⏳ Phase 2 | 吉量 | — |
| 6 | JWT 认证 | ⏳ Phase 2 | 吉量 | — |
| 7 | 方案文档 | ✅ 大哥已确认 | 吉量 | 终版已出 |
| 8 | tests 整理 | ⏳ Phase 1 | 小火鸡儿 | 未开始 |
| 9 | deploy.sh | ✅ 完成 | 呱呱 | 已测试通过 |
| 10 | 三方联调 | ⏳ Phase 3 | 三方 | — |

**新增完成项：**
- ✅ ~/aim-server/ 目录创建 + NATS 迁移（呱呱）
- ✅ launchd plist 更新（呱呱）
- ✅ 启停脚本 start/stop/status.sh（呱呱）
- ✅ 旧WS代码归档 21个文件（吉量）
- ✅ ~/.aim/common/ 创建（吉量）
- ✅ ~/.aim/config/aim.json 创建（吉量）
- ✅ deploy.sh 完成（呱呱）
