# AIM 项目变更日志

## [1.2.1] — 2026-06-17

### 新增
- 版本号统一：SDK/aim_client/项目级全部 1.2.1（aim-watch 保持 2.1.0 独立）
- aim_client VERSION = "1.2.1"（呱呱）
- SDK PROTOCOL_VERSION = "1.0" + MIN_PROTOCOL_VERSION = "1.0"（吉量）
- Scheduler 集成 DegradeLevel L0/L1/L2 三级降级模型（小火鸡儿）
- aim_send_nats.py 恢复 + JWT creds 自动注入（小火鸡儿）
- aim-watch 显示名称映射 ZS→呱呱/吉量/小火鸡儿（呱呱修复）

### 变更
- 记忆管理：金字塔分层（热431行/温1635行/冷归档）
- gotchas 冷热分层（21活跃 + 废弃归档）
- aim_client/__init__.py：去 Phase 标记，改用 v1.2（呱呱）

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

### 变更
- aim-watch 临时独立版本（2.1.0），下次项目 MAJOR→2.0 时纳入统一版本管理（大哥决策）

### 修复
- SDK 版本同步到共享目录（呱呱）
- Observer 事件通道无数据问题排查（呱呱）
- 架构 review 文档修正代码示例（呱呱）
