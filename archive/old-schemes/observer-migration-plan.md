# AIM Observer Veritas 迁移方案

> 作者：吉量 🐴 (ZS0002) | 日期：2026-06-09
> 目标：将现有 Observer（订阅 `observer.events.>`）迁移至 Veritas 标准（`aim.obs.>`）

## 现状分析

| 项目 | 当前 | Veritas 标准 |
|------|------|-------------|
| Subject | `observer.events.>` | `aim.obs.>`（或 `aim.obs.<agent_id>`） |
| SDK emit_obs | N/A（不在 SDK 中） | SDK 已实现 ✅ 发到 `aim.obs.<agent_id>` |
| Observer 代码 | ~121 行独立文件 | 可复用 SDK |
| 事件格式 | `{type, agent_id, detail, ts}` | SDK 的 `emit_obs()` 输出 `{agent_id, status, msg_id, detail, ts}` |

## 迁移方案

### Option A: Observer 复用 SDK（推荐 ✅）

让 `aim_observer.py` 简洁化，直接使用 SDK 的 `AIMNATSClient` 来连接和订阅。新代码量 ~60 行。

**新 observer 架构：**
```
Observer ──→ AIMNATSClient ──→ subscribe("aim.obs.>")
                                       ↓
                                收到 → 格式化输出 → 终端
```

**优点：** 统一连接管理（自动重连、ping）、统一订阅 API、Observer 只关注展示逻辑
**缺点：** 依赖 SDK（但 SDK 是小文件，没毛病）

### Option B: Observer 独立（轻量版）

在现有 ~121 行的基础上只改 subject 名，保持独立。新代码 ~70 行。

**优点：** 零依赖，Observer 本身很轻
**缺点：** 连接管理逻辑重复，没有自动重连

## 建议

**Option A** — SDK 已覆盖连接管理（自动重连、ping/interval），Observer 只需要专注展示逻辑。而且 SDK 是 Veritas 客户端标准库，所有 Agent 都该用它，Observer 也应该统一。

## 文件变化

| 文件 | 操作 | 说明 |
|------|------|------|
| `aim_observer.py` | 重写 | 基于 SDK，订阅 `aim.obs.>`，~60 行 |
| `bin/aim_nats_sdk.py` | 无变化 | emit_obs() 已经用 `aim.obs.<agent_id>` |
| `aim_send_nats.py` | 可选更新 | 已用 Veritas subject ✅ |

## 测试要点

1. SDK emit_obs → Observer 收到并显示
2. 支持按 agent_id 过滤（`--from ZS0001`）
3. Observer 断线自动重连
4. 多个 Agent 的 obs 事件同时显示
