# AIM 项目全量代码审查

> 审查人：🐤 小火鸡儿 | 2026-06-17

---

## 🐸 呱呱 — aim_client/queue.py

### A1. `nack()` 语义问题
**位置**：L94-111

**问题**：`nack` 根据 `received_at` 超时判断是放回队头还是进 dead，但这和 `processing_timeout` 混淆：
- `processing_timeout` 是 adapter 单条处理超时（120s），不是消息存活时间
- `received_at` 可能远超过 `processing_timeout`（消息在 pending 排了很久）

**影响**：排队的消息被 dequeue 后秒进 nack → 可能被判定为"超时"直接进 dead，跳过重试。

**建议**：改用 `_processing_since` 而非 `received_at`。

### A2. `capacity` 只比较队列长度，不限制总对象数
**位置**：L59

**问题**：只检查 `_pending` 长度，不检查 `_processing + _pending + _dead` 总和。极端情况内存可能膨胀。

**严重度**：低

---

## 🐸 呱呱 — aim_client/scheduler.py

### A3. 死代码导入
**位置**：L15

```python
from enum import Enum, auto
```

**问题**：`auto` 导入了但从未使用。`SchedulerEvent` 用了 `auto()` ✅，但这个导入放在 `auto` 单独一行不必要（Line 15 导入在同一个语句里）。

实际检查：`auto` 在 L29-31 使用了。✅ 无误。

### A4. `on_message_enqueued` 是空方法
**位置**：L127-129

```python
def on_message_enqueued(self):
    pass
```

**问题**：纯空方法，如果没有任何逻辑，是否考虑删除调用点？

**严重度**：低。

---

## 🐸 呱呱 — aim_client/health_probe.py  

### A5. `exit_code = proc.returncode or 0`
**位置**：L60

**问题**：`None or 0` → `0`。如果 returncode 意外为 `None`（极端情况下进程未启动），会误判为 exit=0（健康）。

**建议**：`exit_code = proc.returncode if proc.returncode is not None else -1`

---

## 🐸 呱呱 — aim-client/security.py

### A6. `_stats` defaultdict 线程安全问题
**位置**：L92

**问题**：`defaultdict` 在 asyncio 单线程下安全 ✅，但在多 `AIMClient` 实例下各自独立 ✅。无问题。

**状态**：✅ 无问题。

### A7. TokenBucket `consume` 在 burst=0 时会拒绝所有请求
**位置**：L64

**问题**：`self.tokens = float(self.burst)` 如果 `burst=0`，初始 tokens=0，第一次 consume 必然失败。

**建议**：配置文档注明 burst 至少为 1。

**严重度**：低。

---

## 🐸 呱呱 — aim-client/v3_compat.py

### A8. `import time` 未使用
**位置**：L24

```python
import time
```

**问题**：time 导入后未使用。

### A9. `V3_PATH` 硬编码
**位置**：L30

**问题**：`Path.home() / "shared" / "aim" / "nats-agent-v3" / "nats-agent-v3.py"` 硬编码。但 V3 compat 是过渡模块，Phase 2 会移除。可接受。

**严重度**：低（过渡代码）。

---

## 🐴 吉量 — SDK aim_nats_sdk.py (2237行)

不在本次审查范围（太长，且是吉量的单独模块）。

---

## 🐤 小火鸡儿 — aim_client/types.py

### A10. `StateReport.__post_init__` 强制覆盖 `degrade_level`
**位置**：L63-L65

```python
def __post_init__(self):
    if self.degrade_level is None:
        self.degrade_level = DegradeLevel.L0
```

**问题**：如果调用者显式传 `degrade_level=DegradeLevel.L2`，`__post_init__` 不会覆盖（因为不是 None）。✅ 正确。

**状态**：✅ 无问题。

---

## 🐤 小火鸡儿 — adapters/letta/adapter.sh

已审查多次，无问题。

---

## 汇总

| # | 文件 | 作者 | 问题 | 严重度 | 建议 |
|---|------|------|------|--------|------|
| A1 | queue.py | 🐸 | nack 用 received_at 而非 processing_since | 🟡 | 改用 _processing_since |
| A2 | queue.py | 🐸 | capacity 仅限 pending 不限总量 | 🟢 | 低优 |
| A5 | health_probe.py | 🐸 | returncode or 0 可能误判 | 🟡 | 显式 None 检查 |
| A8 | v3_compat.py | 🐸 | import time 未使用 | 🟢 | 删 |
| — | main.py | 🐸 | 6项代码规范问题 | ✅ 已修 | — |

**真正需要改的：A1、A5。** 其余为低优或过渡代码。
