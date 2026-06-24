# ISSUE-621-DLQ: 死信队列（DLQ）机制

> 状态: 方案设计 | 负责人: 吉量 | 优先级: 中 | 创建: 2026-06-23

## 问题描述

当前 `queue.jsonl` 中活跃消息和死信（retry 耗尽）混合存储，导致：

1. Scheduler 重启后遍历所有条目，死信干扰正常 dispatch
2. 死信无独立可观测入口，排查故障需 grep 日志
3. 无自动清理，死信持续增长占用磁盘
4. 无法手动 replay 死信（丢失可恢复的消息）

## 方案设计

### 架构

```
queue.jsonl        → 活跃消息（ack 后移除）
dead_letter.jsonl  → 死信（retry 耗尽后移入）
```

### 触发条件

retry_count >= 3 时，不直接 ack 丢弃，改为移入 DLQ：

```
retry 耗尽 → 写入 dead_letter.jsonl → ack 移出 queue.jsonl
```

### DLQ 条目格式

```json
{
  "msg_id": "8f39346a61a1",
  "original_msg": { "from_id": "ZS0001", "content": "...", ... },
  "dead_at": 1782204675,
  "reason": "retry_exhausted",
  "retry_count": 3,
  "last_error": "Hermes 超时 (120s)"
}
```

### 清理策略

| 策略 | 默认值 | 说明 |
|------|--------|------|
| TTL | 7 天 | 超过 TTL 的 DLQ 条目自动归档或删除 |
| 大小限制 | 1000 条 | 超过后最旧的条目被归档 |
| 归档路径 | `dead_letter.archive.jsonl` | 定期归档，不丢失数据 |

### CLI 接口

```bash
# 查看死信
aim-client --agent-id ZS0002 --dlq-list

# 重放指定消息
aim-client --agent-id ZS0002 --dlq-replay <msg_id>

# 清理过期死信
aim-client --agent-id ZS0002 --dlq-purge --older-than 7d
```

### 实现清单

| 任务 | 文件 | 工作量 |
|------|------|--------|
| DLQ 写入逻辑 | `queue_persist.py` 新增 `write_dead()` | 小 |
| retry 耗尽时移入 DLQ | `main.py` dispatch 循环 | 小 |
| DLQ 列表查询 | `main.py` 新增 `--dlq-list` | 小 |
| DLQ 重放 | `main.py` 新增 `--dlq-replay` | 中 |
| TTL 清理 | `queue_persist.py` 新增 `purge_dead()` | 小 |
| 启动时 DLQ 统计日志 | `main.py` | 小 |

## 影响评估

| 维度 | 影响 |
|------|------|
| 可用性 | **提升** — 死信不再阻塞活跃队列 |
| 可观测性 | **提升** — 独立 DLQ 文件可直接查看 |
| 可恢复性 | **提升** — 支持手动 replay |
| 性能 | 无影响 — 仅在 retry 耗尽时写入一次 |
| 兼容性 | 向后兼容 — 旧 queue.jsonl 中无 DLQ 格式条目不受影响 |
| 数据安全 | 归档不丢失 — 过期 DLQ 移入 archive 而非删除 |

## 相关文档

- [[ISSUES-619-PLUS.md]]
- [[ISSUES-620-POST.md]]

---
创建时间: 2026-06-23
