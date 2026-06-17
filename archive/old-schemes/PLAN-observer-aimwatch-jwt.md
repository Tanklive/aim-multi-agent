# Observer / aim-watch / JWT 开发计划

> 版本: 1.0 | 日期: 2026-06-09
> 基于: 吉量方案 + 火鸡儿优化建议 + 呱呱执行顺序建议

---

## 一、架构总览

```
公网标准架构（三层分离）
┌─────────────────────────────────────────────────────┐
│                    Client 层                         │
│  AIMNATSClient (SDK)                                 │
│  ├── 统一认证层 (Token / NKEY / JWT)                │
│  ├── 连接管理 (重连 + 保活)                          │
│  ├── 消息层 (DM / Group / Observer)                  │
│  └── 安全层 (防重放 + 限流)                          │
└────────────────────────┬────────────────────────────┘
                         │ NATS
┌────────────────────────┴────────────────────────────┐
│                  Observer 层                          │
│  AIMObserverClient (独立只读连接)                     │
│  ├── 只订阅 aim.obs.>                                 │
│  ├── 不发布任何消息                                   │
│  └── 分层策略:                                        │
│      P0: NATS 裸连 + 常驻监听                         │
│      P1: JWT 只读凭证                                  │
│      P2: 连接数上限 + 限流                             │
└────────────────────────┬────────────────────────────┘
                         │ NATS
┌────────────────────────┴────────────────────────────┐
│                  Server 层                             │
│  NATS Server + JetStream                              │
│  ├── aim-observations stream (Observer 事件持久化)    │
│  ├── aim-messages stream (消息持久化)                  │
│  └── 权限控制 (NKEY/JWT 各 subject ACL)               │
└─────────────────────────────────────────────────────┘
```

---

## 二、执行阶段

### Phase 0: SDK 重构（基础层）🐴 吉量

> 目标：把方案描述变成可运行的代码

**P0 - 认证层**
```
AIMNATSClient.connect(credentials) 支持三种模式:
  1. 无参 / None         → 裸连（开发调试用）
  2. token="xxx"         → Token 认证（兼容旧系统）
  3. creds_path          → NKEY/JWT .creds 文件（公网标准）
```

**P0 - 重连层**
```
- connect() 内建指数退避重连 (1s→2s→4s→8s→30s max)
- nats-py 自带 reconnect 能力 + 显式 timeout
- observer/watcher 共用同一套重连逻辑
```

**P1 - Observer 权限隔离**
```
class AIMObserverClient(AIMNATSClient):
    read_only = True  # 只订阅 aim.obs.>，不 publish
```

**P1 - JetStream 分页**
```
get_history(subject, start_time, end_time, page, page_size)
  → 时间窗口 + 翻页
  → observer aim-observe.py 和 aim-watch.py 共用
```

**P2 - 限流保护**
```
- SDK 层 emit_obs 限流 (3条/s/agent，超出的直接丢弃)
- observer 连接数上限 (Server 侧限制)
```

**P2 - 安全基线**
```
- emit_obs / send_dm / send_grp 统一加 timestamp + nonce
- Server 端校验签名 + 防重放缓存
```

**P3 - Worker 池**
```
- Observer 消息量大时启用 async worker pool (默认 1)
- 可配置 num_workers
```

### Phase 1: Observer 开发 🐴 吉量

> 基于 Phase 0 的 SDK，编写实际可用的 Observer

**文件**: `~/.aim/bin/aim-observe.py`（与 SDK 同目录，随 SDK 分发）

```
核心能力:
  - 订阅 aim.obs.> 接收所有 Agent 状态事件
  - 支持 --agent ZS0001 过滤
  - 支持 --history N 回放（JetStream 时间窗口分页）
  - 终端实时展示（彩色输出 + 图标）
  - 断线自动重连（复用 SDK 重连层）
  - --json 模式（机器可读，供 aim-watch 用）
```

### Phase 2: aim-watch 开发 🐴 吉量

> 基于 Observer 能力 + 消息订阅的实时监控终端

**文件**: `~/.aim/bin/aim-watch.py`（与 SDK 同目录）

```
核心能力:
  - 同时订阅 aim.dm.> + aim.grp.> + aim.obs.>
  - 统一展示：消息流 + Agent 处理状态
  - 支持 --agent ZS0001 过滤
  - 支持 --history N 回放
  - 终端实时展示（类似旧 aim-watch.py 的展示格式）
  - 断线自动重连
  - 大哥原话：给我一个能打开的窗口看所有 Agent 在干啥
```

### Phase 3: JWT 认证接入 🐴 吉量 / 🐸 呱呱

> 需要呱呱的 Server 端配合（NATS Server NKEY/JWT 配置）

```
吉量负责:
  - SDK 层 JWT 认证支持（connect 支持 .creds 文件）
  - 为每个 Agent 生成独立的 credentials
  - Observer 只读凭证

呱呱负责:
  - NATS Server 配置 authorization 段
  - 权限控制（各 Agent 的 subject ACL）
  - 启用 ISSUE-001
```

---

## 三、交付物清单

| # | 产出 | 阶段 | 行数估算 |
|---|------|------|---------|
| 1 | SDK 认证层 (connect 3 模式) | P0 | ~60 |
| 2 | SDK 重连层 (指数退避) | P0 | ~40 |
| 3 | AIMObserverClient | P1 | ~30 |
| 4 | get_history 分页 SDK | P1 | ~50 |
| 5 | SDK 限流 (emit_obs rate limit) | P2 | ~40 |
| 6 | SDK 安全基线 (sign/verify) | P2 | ~60 |
| 7 | SDK worker pool | P3 | ~50 |
| 8 | aim-observe.py | Phase 1 | ~150 |
| 9 | aim-watch.py | Phase 2 | ~200 |
| 10 | JWT 凭证生成 | Phase 3 | ~50 |
| 总计 | | | ~730 |

---

## 四、测试策略

**三阶段测试**（沿用 AIM 标准）：

| 轮次 | 范围 | 方法 |
|------|------|------|
| T1 (3轮) | 基本功能 | 手动运行，确认消息收发正常 |
| T2 (修复) | 修正 T1 问题 | 修复 review 问题 |
| T3 (5轮) | 全面覆盖 | 多 Agent、断线重连、限流触发、JetStream 回放 |

---

## 五、相关文件

- `~/.aim/bin/aim_nats_sdk.py` — SDK 主文件（修改）
- `~/.aim/bin/aim-observe.py` — Observer（新建）
- `~/.aim/bin/aim-watch.py` — aim-watch（新建）
- `~/aim-server/nats.conf` — NATS Server 配置（呱呱改）
- `~/aim-server/aim_server.py` — AIM Server（呱呱改）
