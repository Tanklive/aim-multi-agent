# AIM (NATS) 架构完整问题分析报告

> 分析时间：2026-06-11
> 分析工具：MiMo Code Agent + Explore Subagent
> 位置：桌面，仅本地存储

---

## 一、系统架构总览

```
NATS Server (JWT Operator 模式)
├── AIM Server (aim_server.py) — Agent 注册/心跳/消息路由
├── ZS0001 呱呱 (openclaw) — nats-agent.py
├── ZS0002 吉量 (hermes) — nats-agent.py
├── ZS0003 小火鸡儿 (letta) — nats-agent.py
└── Observer Daemon (aim-observer.py) — 状态监控
```

**核心组件**：
- **NATS Server**：消息总线，使用 JWT Operator 认证
- **AIM Server**：业务层，处理注册、心跳、消息路由
- **Agent**：各 AI 助手的 NATS 接入端
- **Observer**：状态监控和事件收集

---

## 二、发现的问题清单

### 🔴 严重问题（P0）

#### 问题1：认证方式不匹配

**现象**：
```
[ERR] 127.0.0.1:64072 - cid:1439 - authentication error
[ERR] 127.0.0.1:64075 - cid:1441 - authentication error
...（1497+ 条）
```

**根本原因**：
| 组件 | 配置的认证方式 | 实际期望的认证方式 |
|------|---------------|-------------------|
| NATS Server | JWT Operator | - |
| Agent config.json | 简单 Token | - |
| Agent aim.creds | JWT | - |

**详细分析**：
1. NATS Server 使用 JWT Operator 模式（`nats.conf` / `nats-jwt.conf`）
2. Agent 的 `config.json` 只配置了 `nats_token`（简单 Token）
3. Agent 有正确的 `aim.creds` 文件（JWT 凭证）
4. 但 `config.json` 没有配置 `creds_path`
5. SDK 默认使用 `nats_token`，而非 JWT
6. 导致所有连接认证失败

**影响**：所有 Agent 无法正常连接 NATS，通讯完全中断

---

#### 问题2：NATS 端口冲突

**现象**：
```
[FTL] Error listening on port: 0.0.0.0:4222, "listen tcp 0.0.0.0:4222: bind: address already in use"
```

**根本原因**：
- `com.aim.nats-server.plist` 和 `com.nats.server.plist` 都尝试启动 nats-server
- 两个实例竞争同一个端口（4222）
- 实际运行的实例可能使用了 4223 端口

**影响**：
- Agent 连接的端口可能与实际运行的 NATS Server 不一致
- 导致连接失败或连接到错误的实例

---

### 🟠 中等问题（P1）

#### 问题3：两套 NATS 配置并存

**发现的配置文件**：
| 文件 | 模式 | 状态 |
|------|------|------|
| `~/aim-server/nats.conf` | JWT Operator | 当前活跃 |
| `~/aim-server/nats-jwt.conf` | JWT Operator | 启动脚本使用 |
| `~/aim-server/nats.conf.bak` | 简单 Token | 旧版备份 |
| `~/.aim/server/nats.conf` | 无认证 | 旧版配置 |
| `~/.aim/config/nats.conf.template` | 模板 | 未使用 |

**问题**：
- 配置文件分散在多个位置
- 新旧版本混用，容易混淆
- 启动脚本 `start-aim-server.sh` 使用 `nats-jwt.conf`，而非 `nats.conf`

---

#### 问题4：Agent 代码版本不一致

**发现的 SDK 版本**：
| 文件 | 行数 | 功能 |
|------|------|------|
| `~/.aim/common/aim_nats_sdk.py` | 1231 行 | 早期简化版 |
| `~/.aim/bin/aim_nats_sdk.py` | 1500+ 行 | 完整版 |

**差异**：
- `bin` 版本多了：`_resolve_credentials()`, `ObsEventType`, `RateLimiter`, `SecureMessage`, `MessageValidator`, `from_config()`
- `common` 版本缺少安全组件
- Agent 实际使用 `bin` 版本

**问题**：
- 两套代码并存，容易维护混乱
- 如果误用 `common` 版本，会缺少安全功能

---

#### 问题5：两个注册表并存

**发现的注册表**：
| 文件 | 内容 |
|------|------|
| `~/.aim/agents/_registry.json` | 3 个 Agent（ZS0001-03） |
| `~/.aim/config/registry.json` | 9 个 Agent（ZS0001-09，含测试 Agent） |

**问题**：
- 两个文件格式和内容不一致
- 不确定哪个是权威来源
- 容易导致注册信息混乱

---

#### 问题6：ZS0003 缺少 launchd plist

**现象**：
- ZS0001 和 ZS0002 都有 `com.aim.nats-agent.plist`
- ZS0003 没有

**影响**：
- ZS0003 无法通过 launchd 自动启动
- 需要手动启动或使用其他方式
- 进程稳定性无法保证

---

### 🟡 轻微问题（P2）

#### 问题7：Agent 实现不一致

| Agent | AI 调用方式 | 特点 |
|-------|-----------|------|
| ZS0001 | handler.sh（echo 回复） | 未真正调用 OpenClaw |
| ZS0002 | handler.sh（Hermes CLI） | 完整实现，含防循环逻辑 |
| ZS0003 | FrameworkCLI（Letta CLI） | 串行处理，MAX_CONCURRENT=1 |

**问题**：
- ZS0001 的 handler.sh 只是 echo 回复，没有实际 AI 处理
- 三个 Agent 的实现方式不统一
- 维护成本高

---

#### 问题8：AIM Server 使用旧 subject 命名

**现象**：
- AIM Server 订阅：`agent.*.msg` / `group.*.msg`
- Agent 使用：`aim.dm.*` / `aim.grp.*`

**问题**：
- 两套命名不兼容
- 消息可能无法正确路由
- 需要确认实际使用哪套命名

---

#### 问题9：deploy.sh 引用不存在的 Agent ID

**现象**：
- `deploy.sh` 中同步 `ZS0005`
- 实际 Agent 是 ZS0003

**问题**：
- 部署脚本过时
- 可能导致部署错误

---

### 🔒 安全问题

#### 问题10：Token 明文存储

**发现的敏感文件**：
| 文件 | 内容 |
|------|------|
| `~/aim-server/.nats-token` | 明文 Token |
| `~/aim-server/nkeys.json` | NKEY Seed（私钥） |
| `~/.aim/agents/*/config.json` | 明文 Token |
| `~/.aim/agents/*/aim.creds` | JWT 凭证 |

**问题**：
- 敏感信息明文存储
- 文件权限可能过于宽松
- 存在泄露风险

---

## 三、架构设计问题

### 1. 配置管理混乱

**问题**：
- 配置文件分散在多个目录
- 新旧版本混用
- 缺乏统一的配置管理机制

**建议**：
- 建立统一的配置目录
- 使用版本控制管理配置
- 建立配置模板和校验机制

---

### 2. 代码重复

**问题**：
- `~/.aim/common/` 和 `~/.aim/bin/` 存在重复代码
- 不同 Agent 的实现方式不统一
- 缺乏代码复用机制

**建议**：
- 统一使用 `bin` 版本的 SDK
- 建立共享模块库
- 制定 Agent 实现规范

---

### 3. 监控和告警缺失

**问题**：
- 没有认证错误告警
- 没有进程重启监控
- 没有性能指标收集

**建议**：
- 建立监控告警机制
- 收集关键指标（连接数、消息量、错误率）
- 建立健康检查机制

---

## 四、修复优先级

| 优先级 | 问题 | 修复难度 | 预计耗时 |
|--------|------|---------|---------|
| P0 | 认证方式不匹配 | 简单 | 10分钟 |
| P0 | NATS 端口冲突 | 中等 | 20分钟 |
| P1 | 两套 NATS 配置并存 | 中等 | 30分钟 |
| P1 | Agent 代码版本不一致 | 简单 | 10分钟 |
| P1 | 两个注册表并存 | 简单 | 10分钟 |
| P1 | ZS0003 缺少 launchd plist | 简单 | 10分钟 |
| P2 | Agent 实现不一致 | 复杂 | 2小时 |
| P2 | AIM Server 使用旧 subject 命名 | 中等 | 30分钟 |
| P2 | deploy.sh 引用不存在的 Agent ID | 简单 | 5分钟 |
| P2 | Token 明文存储 | 中等 | 30分钟 |

---

## 五、总结

### 核心问题
1. **认证断裂**：NATS Server 已升级到 JWT，但 Agent 仍在使用旧 Token
2. **配置混乱**：多套配置并存，缺乏统一管理
3. **代码重复**：SDK 存在两个版本，Agent 实现不统一

### 影响
- Agent 间通讯完全中断
- 系统稳定性差
- 维护成本高

### 建议
1. **立即修复**：P0 问题（认证、端口冲突）
2. **短期优化**：P1 问题（配置整理、代码统一）
3. **长期规划**：P2 问题（架构优化、安全加固）

---

*报告完成时间：2026-06-11*
*报告人：MiMo Code Agent*
*位置：桌面，仅本地存储*
