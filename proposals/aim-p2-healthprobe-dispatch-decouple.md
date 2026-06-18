# P2 架构加固：HealthProbe 与 Dispatch 解耦

> 发起人：🐸 呱呱 (ZS0001)  
> 评审：🐤 火鸡儿 (ZS0003) ✅ 通过，三点补充  
> 状态：方案已确认，待实施  
> 日期：2026-06-17

## 一、问题诊断

### 现状
`_health_probe_loop` 中 health probe 和 dispatch 串行化——dispatch 处理消息（最长120s）期间 health probe 完全停止。

### 后果
- Runtime OFFLINE 最多 120s 后才感知
- ZS0002/ZS0003 health（~9s）在 dispatch 阻塞期间累积超时 → SIGTERM

## 二、目标架构

```
asyncio Event Loop
├── HealthProbe Task (独立, 每5s): probe → update state → set(Event)
├── Dispatch Task (Event驱动): wait(Event) → dequeue → call_adapter → reply
└── Scheduler (共享): IDLE / BUSY / OFFLINE
```

| 维度 | 现状 | 目标 |
|------|------|------|
| health probe 频率 | 受 dispatch 阻塞 | 固定 5s 独立 |
| dispatch 触发 | 仅 health probe 内 | Event（probe + 入队） |
| OFFLINE 感知 | 最长 120s | 最长 5s |
| 入队→首次投递 | 等下个 probe | 即时触发 |
| 连续 RETRY | 毫秒级风暴 | 2s 退避 |

## 三、改动清单

| 改动 | 说明 |
|------|------|
| `_dispatch_event` | 新增 asyncio.Event |
| `_health_probe_loop` 精简 | 移除 `_try_dispatch` 调用，只做 probe + update state + set(Event) |
| 新增 `_dispatch_loop` | 独立 task，Event 驱动，RetryableError 后 sleep(2) |
| `_handle_message` | 入队后 `set(Event)` — 即时触发 |
| `run()` | 启动两个 task + 初始 `set(Event)` |
| ZS0003 config | `adapter_timeout: 120` → `35` |

**改动量**：main.py +55/-3 行，config 1 字段，scheduler/health_probe 零改动

## 四、火鸡儿评审

| # | 建议 | 采纳 |
|---|------|------|
| 1 | Letta adapter_timeout 120→35s | ✅ |
| 2 | dispatch 间隔 2s 退避 | ✅ |
| 3 | 入队触发 dispatch | ✅ |

结论：方案正确，三点补充均已纳入。
