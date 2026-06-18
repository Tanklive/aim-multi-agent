# main.py 代码审查报告

> 审查人：🐤 小火鸡儿 | 日期：2026-06-17 | 版本：AIM Client v1.0.0

---

## 🔴 问题 1：`_try_dispatch` 死代码（53 行）

**位置**：L361-413

**问题**：P2 解耦后 `_health_probe_loop` 已不再调用 `_try_dispatch`（改为 `_dispatch_event.set()`），但方法完整保留。内部逻辑与新的 `_dispatch_loop` 行为不一致：
- `_dispatch_loop`：RetryableError → `nack("retry")` + 2s sleep
- `_try_dispatch`：RetryableError → `nack("retryable")` + enqueue

L410-413 中 `retry_count` 判断写了两次，缩进错乱。

**影响**：死代码。53 行永不执行。

**建议**：删除整个 `_try_dispatch` 方法（L361-413）。

---

## 🔴 问题 2：`_handle_exception` 方法未定义

**位置**：`_dispatch_loop` L302

```python
except Exception as e:
    self._handle_exception(msg, e)
```

**问题**：`AIMClient` 类里没有 `_handle_exception` 方法定义。

**影响**：通用异常 → `_handle_exception` → `AttributeError` → 被外层 `except Exception` 吞掉 → 异常信息丢失。当前不会崩溃（外层兜底），但异常原因不可追踪。

**建议**：添加方法，或直接改为 `self.logger.error(f"投递异常 [{msg.msg_id[:8]}]: {e}")`。

---

## 🟡 问题 3：`WORKSPACE` 硬编码为 OpenClaw 路径

**位置**：L50

```python
WORKSPACE = Path.home() / ".openclaw" / "workspace"
```

**问题**：这是 OpenClaw 专属工作目录，不应硬编码在全局 `main.py`。Letta / Hermes Agent 启动时会注入此无意义路径到 `sys.path`。

**影响**：运行时无功能影响（路径不存在时 `sys.path.insert` 跳过），但语义上越界——全局入口不应包含框架特定路径。

**建议**：移到 ZS0001 的 `config.json` 的 `paths.workspace` 字段，或直接删除（`SHARED_AIM` 已兜底 `aim_client` 导入）。

---

## 🟡 问题 4：HealthProbe timeout 硬编码 10s

**位置**：L257

```python
self.health_probe = HealthProbe(
    health_cmd=f"bash {self.adapter_cmd} health",
    timeout=10.0,
)
```

**问题**：10s 按 OpenClaw 的 `curl` 探针（~2.7s）设定。Letta adapter health 耗时 ~4.8s 尚可，但 Hermes adapter 在某些场景可能超 10s。此前呱呱 P0 已临时改到 25s 但此代码未同步。

**影响**：Hermes adapter health 可能被误判超时 → `OFLINE` → 停投。

**建议**：从 `config.json` 读取 `health_probe_timeout`，默认值 25s。

---

## 🟡 问题 5：NATS `connect()` 失败直接退出

**位置**：L316-318

```python
if not await self.transport.connect():
    self.logger.error("NATS 连接失败，退出")
    sys.exit(1)
```

**问题**：无重试。NATS 瞬断或启动时序偏差 → 进程直接死 → 等 launchd 拉起 → 最少 10s 中断。

**影响**：可靠性和自愈能力不足。

**建议**：加 3 次重试，间隔 3s：

```python
for attempt in range(3):
    if await self.transport.connect():
        break
    self.logger.warning(f"NATS 连接失败 (attempt {attempt+1}/3)")
    await asyncio.sleep(3)
else:
    self.logger.error("NATS 连接失败，退出")
    sys.exit(1)
```

---

## 🟡 问题 6：`close()` 不等待协程 task 退出

**位置**：L533-535

```python
async def close(self):
    await self.transport.disconnect()
    self.lock.release()
```

**问题**：`_health_probe_loop` / `_dispatch_loop` 两个 `asyncio.create_task` 仍在运行，`close()` 不等待它们退出。`SIGTERM` → `_shutdown()` 设 `self.running=False` → task 在下一次 `while self.running` 检查时退出，但 `close()` 和 task 之间有竞态窗口。

**影响**：进程退出时可能挂在 event loop 上，锁释放但 task 未完全退出。

**建议**：在 `close()` 中加短暂等待：

```python
async def close(self):
    self.running = False
    await asyncio.sleep(0.5)  # 等 task 退出
    await self.transport.disconnect()
    self.lock.release()
```

---

## 总结

| # | 严重度 | 问题 | 改动量 |
|---|--------|------|--------|
| 1 | 🔴 | `_try_dispatch` 死代码 | 删 53 行 |
| 2 | 🔴 | `_handle_exception` 未定义 | +3 行 |
| 3 | 🟡 | `WORKSPACE` OpenClaw 硬编码 | 删 1 行 |
| 4 | 🟡 | HealthProbe timeout 硬编码 | +5 行 |
| 5 | 🟡 | NATS connect 无重试 | +5 行 |
| 6 | 🟡 | `close()` 不等待 task | +2 行 |

**6 项均为代码规范问题，不涉及框架特定逻辑，谁改都一样。按分工：main.py → 🐸 呱呱。**
