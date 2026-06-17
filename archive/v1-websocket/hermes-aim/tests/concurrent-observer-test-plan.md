# Observer 并发测试方案

> 测试目标：验证 Server 端 `_observer_bindings`（target → list of observers）在多个 observer 同时 watch 同一 Agent 时的正确性和稳定性。

## 测试架构

```
吉量 ── aim_send ──→ Server ──→ 呱呱（处理中）
                        │
               ┌────────┼────────┐
               │        │        │
           🐴 observer1  │    🐸 observer2
        (吉量起，ZS0002)  │  (呱呱起，ZS0001)
                         │
                    status_feedback
                    双向推送
```

- **目标 Agent**：呱呱（ZS0001）
- **Observer A**：吉量起（`--agent-id observer_a`）
- **Observer B**：呱呱起（`--agent-id observer_b`）
- **数据源**：吉量向呱呱发 N 条消息 → Server 广播 status_feedback 给两个 observer

## 第一轮：基础并发测试（3 轮）

### 场景 1：双 Observer 同时注册
- 吉量和呱呱各起一个 observer，都用 `--target ZS0001`
- 验证点：两个 observer 都收到 `auth_ok`
- Server 日志确认 `_observer_bindings[ZS0001]` 中有两个连接

### 场景 2：单条消息双路送达
- 吉量发 1 条消息给呱呱
- 验证 point：两个 observer 都在 5 秒内收到 status_feedback 序列
- 检查点：msg_id 一致、两条 observer 输出的进度同步（无遗漏）

### 场景 3：注册隔离
- Observer A 断开
- Observer B 继续收到 status_feedback（不受影响）
- 然后 Observer A 重连，仍然能收到后续消息

## 第二轮：压力并发测试（5 轮）

### 场景 4：10 条消息快速连续
- 吉量用脚本连续发 10 条消息给呱呱（间隔 500ms）
- 验证点：两个 observer 均收到全部 10 条消息的 status_feedback
- 检查点：无丢失、无重复、顺序一致

### 场景 5：20 条消息批量爆发
- 吉量连续发 20 条（间隔 200ms）
- 验证点：Server 广播层吞吐、无明显延迟堆积
- 检查点：两个 observer 消息总数一致、顺序一致

### 场景 6：双 Observer 同时回调 Server
- 两个 observer 同时用 aim_send 给 Server 发消息（script channel）
- Server 端是否正确处理（不丢、不重复）

## 测试检查清单

| # | 检查项 | 通过标准 |
|---|--------|---------|
| 1 | 两个 observer 都 auth_ok | 各自打印 `✅ 已连接为 observer` |
| 2 | status_feedback 双路送达 | 两条路径的 step/status/progress 一致 |
| 3 | msg_id 一致性 | 两条路径的 session_id 相同 |
| 4 | 注册隔离 | Observer A 断连不影响 B |
| 5 | 无丢失 | 累计 msg count 两 observer 一致 |
| 6 | 无重复 | 无重复 seq 或 session_id |
| 7 | 顺序一致 | seq 单调递增，两 observer 相同 |
| 8 | Server 无异常 | 无 `ERROR`、无 `ConnectionClosed`、无内存暴涨 |
| 9 | 回调并发 | 两个 observer 同时回调 Server 正常处理 |

## 中止条件

任一测试 3 次失败则中止并汇报诊断。连续 2 次失败且根因一致，标记已知 bug。

## 执行计划

1. 今天先跑基础并发（场景 1-3），确认 Server 广播层和注册隔离正常
2. 基础跑通了再跑压力测试（场景 4-6）
3. 所有轮次通过后存档测试报告，标记 Observer 并发能力已验证
