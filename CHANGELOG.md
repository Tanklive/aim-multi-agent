# AIM 项目变更日志

## [1.3.0] — 2026-06-18

### 新增
- Queue 持久化：JSONL 异步追加写入 + 启动恢复 + 自动压缩（呱呱）
  - `aim_client/queue_persist.py`：独立持久化层
  - enqueue/ack/nack 异步写入 JSONL
  - 文件 > 50KB 自动压缩，压缩后仅保留 pending 消息

- 认证链 v1.1：AuthStep 链式认证 + 来源身份验证（呱呱）
  - `aim-client/security.py`：重构为 AuthStep 链式架构
  - Step 1: SourceIdentityCheck — from_id 必须在注册 Agent 列表中
  - Step 2: RateLimitCheck — 令牌桶每 Agent 独立限流
  - Step 3 (可选): AllowlistCheck — sender 白名单
  - main.py：集成 authenticate() 到消息处理器
  - 支持动态注册（Registry 回调 register_agent）
  - 配置：config.json security.auth.chain 可显式指定链步骤

- Registry 独立运行：作为 NATS 微服务 + launchd 持久化（呱呱）
  - 创建 `com.aim.registry.plist` launchd 配置
  - 三方 Agent 启动时自动向 Registry 注册
  - Registry PID 92552，KeepAlive Crashed+NetworkState

### 修复
- A1：queue nack() 改用 dequeued_at 计算超时，避免队列积压误判 dead（呱呱）
  - Message 新增 dequeued_at 字段
  - dequeue() 时打时间戳，nack() 用 dequeued_at 替代 received_at
  - queue_persist 序列化同步更新

- launchd zombie 修复：三方 plist KeepAlive SuccessfulExit→Crashed+NetworkState（呱呱）
  - ZS0001/ZS0002/ZS0003 plist 全部统一
  - ThrottleInterval 10s→30s

- 版本管理标准化 v1.1：修正 VERSION-STANDARD.md 路径 + 下发三方（呱呱）
  - SDK 路径修正：aim_nats_sdk.py → src/aim_nats_sdk.py
  - aim-watch 路径修正：aim-watch.py → src/aim-watch.py
  - 版本号 1.2.1→1.3.0（3 个新功能 + 2 个修复）

## [1.2.1] — 2026-06-17
- gotchas 冷热分层（21活跃 + 废弃归档）
- aim_client/__init__.py：去 Phase 标记，改用 v1.2（呱呱）

### 新增
- 认证链 v1.1：AuthStep 链式认证 + 来源身份验证（呱呱）
  - `aim-client/security.py`：重构为 AuthStep 链式架构
  - Step 1: SourceIdentityCheck — from_id 必须在注册 Agent 列表中
  - Step 2: RateLimitCheck — 令牌桶每 Agent 独立限流
  - Step 3 (可选): AllowlistCheck — sender 白名单
  - main.py：集成 authenticate() 到消息处理器
  - 支持动态注册（Registry 回调 register_agent）
  - 配置：config.json security.auth.chain 可显式指定链步骤

- Queue 持久化：JSONL 追加写入 + 启动恢复 + 自动压缩（呱呱）
  - `aim_client/queue_persist.py`：独立持久化层
  - enqueue/ack/nack 异步写入 JSONL
  - 启动时恢复未 ack 消息到内存队列
  - 文件 > 50KB 自动压缩，压缩后仅保留 pending 消息

### 变更
- aim-watch 临时独立版本（2.1.0），下次项目 MAJOR→2.0 时纳入统一版本管理（大哥决策）

### 修复
- NATS Server 恢复（重启后挂掉）
- 禁止裸连 NATS publish，统一走标准接口
- aim-watch：修复 agent_name_map 引用问题（呱呱）
- aim_nats_sdk.py：修复 send_grp 缺少 meta.group 字段（呱呱）

## [1.0.0] — 2026-06-17

### 新增
- 版本管理规范正式建立（SemVer 2.0.0）
- SDK (aim_nats_sdk.py) 添加 VERSION="1.0.0"、PROTOCOL_VERSION="1.0"、MIN_PROTOCOL_VERSION="1.0"
- aim-watch（2.1.0）
- 创建 VERSION 项目级别版本文件
- 创建本 CHANGELOG.md

## [1.2.2] — 2026-06-18

### 新增
- 认证链 v1.1：AuthStep 链式认证 + 来源身份验证（呱呱）
  - `aim-client/security.py`：重构为 AuthStep 链式架构
  - Step 1: SourceIdentityCheck — from_id 必须在注册 Agent 列表中
  - Step 2: RateLimitCheck — 令牌桶每 Agent 独立限流
  - Step 3 (可选): AllowlistCheck — sender 白名单
  - main.py：集成 authenticate() 到消息处理器
  - 支持动态注册（Registry 回调 register_agent）
  - 配置：config.json security.auth.chain 可显式指定链步骤

- Queue 持久化：JSONL 追加写入 + 启动恢复 + 自动压缩（呱呱）
  - `aim_client/queue_persist.py`：独立持久化层
  - enqueue/ack/nack 异步写入 JSONL
  - 启动时恢复未 ack 消息到内存队列
  - 文件 > 50KB 自动压缩，压缩后仅保留 pending 消息

### 变更
- aim-watch 临时独立版本（2.1.0），下次项目 MAJOR→2.0 时纳入统一版本管理（大哥决策）

### 修复
- SDK 版本同步到共享目录（呱呱）
- Observer 事件通道无数据问题排查（呱呱）
- 架构 review 文档修正代码示例（呱呱）
