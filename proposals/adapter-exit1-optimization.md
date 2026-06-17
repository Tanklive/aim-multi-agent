# adapter exit 1 → dead 队列浪费 — 优化方案

> 小火鸡儿 🐤 | 2026-06-17 | Letta 单线程架构下的重试策略优化

---

## 一、问题

Letta 单 session 互斥架构下，`adapter.sh process` 在大哥对话中时阻塞 30s → exit 1（session忙）。当前 RETRY 策略对所有 exit 1 一视同仁：重试 3 次（每次 30s 内部超时）后进 dead 队列丢弃。

**实际数据**（2026-06-17 5轮联调）：exit 1 共 10 次，8 次进 dead。session 空闲时 dispatch 成功率 26/37 = 70%。

**核心矛盾**：session 忙是暂时的（对话结束即恢复），但 3×30s=90s 的重试窗口太短，对话还没结束消息就丢弃了。

---

## 二、方案：区分"session忙"和"真故障"

### 2.1 adapter.sh 增强 stderr 输出（最轻量）

当前 adapter exit 1 只有一个通用消息：

```
[letta-adapter] 处理超时 (30s)，session 可能忙，可重试
```

**改动**：在 stderr 中加一个可机器解析的标记：

```
[letta-adapter] 处理超时 (30s)，session 可能忙，可重试
DEADLINE_HINT: retry_after_idle
```

- `retry_after_idle` = "等到 Runtime 空闲后再重试"，不是限次重试
- 老的 exit 1（非超时场景）不加此标记，走原有限次重试逻辑

### 2.2 aim-client `_call_adapter` 解析 stderr

`RetryableError` 异常中附加 `retry_strategy` 字段：

```python
class RetryableError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        self.retry_strategy = "limited"  # 默认有限次重试
        if "retry_after_idle" in detail:
            self.retry_strategy = "after_idle"  # 等空闲后重试
```

### 2.3 `_try_dispatch` 中分支重试策略

```python
except RetryableError as e:
    if e.retry_strategy == "after_idle":
        # session 忙：消息回到 pending 队尾，不消耗重试次数
        # 等 health_probe 下次确认空闲后自动 dispatch
        self.queue.enqueue(msg)
        self.logger.debug(f"⏸ [{msg.msg_id[:8]}] session忙，回pending等空闲")
    else:
        # 真故障：有限次 RETRY
        if msg.retry_count < 3:
            msg.retry_count += 1
            self.queue.enqueue(msg)
            self.logger.info(f"🔄 RETRY #{msg.retry_count}/3")
        else:
            self._to_dead_queue(msg, "retryable")
    self.scheduler.on_retry()
```

### 2.4 配置参数

```json
{
  "adapter": {
    "idle_retry_max_wait": 300,
    "idle_retry_probe_interval": 10
  }
}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `idle_retry_max_wait` | 300s | session 忙消息最长等待时间（超过通知大哥） |
| `idle_retry_probe_interval` | 10s | 空闲重试探针间隔 |

---

## 三、改动范围

| 文件 | 改动 | 行数 |
|------|------|------|
| `adapter.sh` | exit 1 stderr 加 `DEADLINE_HINT` | +2 |
| `main.py` `_call_adapter` | 解析 stderr → retry_strategy | +6 |
| `main.py` `_try_dispatch` | 分支重试逻辑 | +8 |
| `config.json` | 加 idle_retry 配置 | +4 |

**总计 ~20 行，不改架构。**

---

## 四、预期效果

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| session 忙 → dispatch | RETRY ×3 (90s) → dead | 回到 pending 队尾 → 空闲后自动 dispatch |
| adapter 真故障 exit 1 | RETRY ×3 → dead | 保持不变 |
| 大哥对话 10 分钟 | 所有 AIM 消息进 dead | 消息排队等待，对话结束后全部消费 |
| session 空闲 | 秒回 ✅ | 不变 |

---

## 五、风险

| 风险 | 缓解 |
|------|------|
| pending 队列无限增长 | `idle_retry_max_wait` 上限 + 通知大哥 |
| 消息在 pending 中永远不被消费 | health_probe 每 5s 触发 dispatch，空闲即消费 |
| DEADLINE_HINT 字符串匹配脆弱 | 固定前缀 `DEADLINE_HINT:` 机器解析，不会被翻译 |

---

*小火鸡儿 🐤 | 2026-06-17 | 等呱呱确认后落地*
