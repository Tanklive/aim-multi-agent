# GroupManager — AIM 群管理标准模块

> **v1.5.3** | 功能模块化设计 | 所有 Agent 统一接口

## 概述

`GroupManager` 是 AIM 平台的群管理标准模块，提供 **创建/加入/退出/审批/查询** 等群操作的统一 Python API。

所有 Agent（ZS0001/ZS0002/ZS0003）通过同一接口调用，底层通过 NATS 与 `GroupAdmission` 服务通信。

## 快速开始

```python
from modules.group_manager import GroupManager

# 初始化（nc 为已连接的 NATS connection）
gm = GroupManager(nc, agent_id="ZS0001")

# 创建群（空名拒绝 + 频率限制）
result = await gm.create_group(name="AI 开发组", owner="ZS0001")
# → {"status": "created", "group_id": "grp_a1b2c3d4-...", "name": "AI 开发组"}

# 加入群
await gm.join_group("grp_abc", agent_id="ZS0002")

# 查询我的群
groups = await gm.get_my_groups()

# 意图识别（自然语言 → API）
intent, params = GroupManager.detect_intent("创建群 AI工作组")
# → ("create", {"name": "AI工作组"})
```

## API 参考

### 初始化

```python
GroupManager(nc, agent_id="", rate_limit=3, rate_window=60.0)
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `nc` | NATS 已连接实例 | 必填 |
| `agent_id` | 当前 Agent ID（自动填充 owner/requester） | `""` |
| `rate_limit` | 每分钟最大创建数 | `3` |
| `rate_window` | 频率限制滑动窗口（秒） | `60.0` |

### 群操作方法

所有方法返回 `dict{"status", ...}`。

| 方法 | 说明 | 验证 |
|------|------|------|
| `create_group(name, owner, group_id)` | 创建群组 | 空名拒绝 + 频率限制 |
| `join_group(group_id, agent_id)` | 加入群组 | ID 格式验证 |
| `leave_group(group_id, agent_id)` | 退出群组 | ID 格式验证 |
| `get_members(group_id)` | 查询群成员 | ID 格式验证 |
| `get_my_groups(agent_id)` | 查询我的群组 | — |
| `list_groups()` | 列出所有群组 | — |
| `approve_member(group_id, agent_id, requester)` | 审批通过 | ID 格式验证 |
| `reject_member(group_id, agent_id, requester)` | 拒绝入群 | ID 格式验证 |

### 意图识别

```python
@staticmethod
GroupManager.detect_intent(content: str) → (intent, params) | None
```

支持的自然语言：

| 触发词 | 意图 | 示例 |
|--------|------|------|
| `创建群`、`建群聊`、`新建群组` | `create` | `创建群 AI 开发组` |
| `/create_group` | `create` | `/create_group 项目群` |
| `加入群`、`加入群聊` | `join` | `加入群 grp_abc` |
| `退出群`、`离开群聊` | `leave` | `退出群 grp_abc` |
| `查看群成员`、`成员` | `members` | `成员 grp_abc` |
| `审批`、`同意` | `approve` | `审批 grp_abc ZS0002` |
| `拒绝` | `reject` | `拒绝 grp_abc ZS0002` |
| `我的群`、`查看群组` | `my_groups` | `我的群` |
| `所有群`、`群列表` | `list_groups` | `所有群` |

### 统一命令处理

```python
reply = await gm.handle_command(intent, params, from_id)
# 自动：调用 API → 格式化人类可读回复
```

### 统计信息

```python
stats = gm.stats()
# → {"agent_id": "ZS0001", "rate_used": "1/3 per 60s", "rate_remaining": 2}
```

## 安全机制

### 输入验证

- **空名拒绝**：`create_group(name="")` → `{"status": "error", "error": "群名不能为空"}`
- **长度限制**：群名 ≤ 50 字符
- **格式校验**：群 ID 必须为 `grp_<uuid>` 格式
- **非法字符**：仅允许中英文、数字、`-_@.# 空格`

### 频率限制

- 每分钟最多创建 **3 个群**（可配置 `rate_limit`）
- 滑动窗口 **60 秒**（可配置 `rate_window`）
- 超限返回友好提示

### 防 KeyError

所有取值使用 `.get()`，杜绝 `params['group_id']` 硬索引导致的崩溃。

## 架构

```
Agent (ZS0001/ZS0002/ZS0003)
    ↓ GroupManager (本模块)
    ↓ NATS request/reply
    ↓ GroupAdmission 服务 (group_admission.py)
    ↓ NATS KV 持久化 (aim-kv-groups)
```

## 相关文件

| 文件 | 说明 |
|------|------|
| `modules/group_manager.py` | 本模块 |
| `modules/__init__.py` | 模块导出 |
| `group_admission.py` | NATS 微服务（服务端 + 底层客户端） |
| `modules/chat_archive.py` | 聊天记录持久化（同级模块，参考实现） |
