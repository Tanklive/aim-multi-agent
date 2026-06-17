# P4 优化实施方案

> 执行人：呱呱 🐸
> 日期：2026-06-06 22:40
> 状态：吉量评审通过，开始实施

---

## 优化项

### 1. 心跳超时改为 45s
- 文件：`lifecycle.py`
- 修改：`heartbeat_timeout` 默认值 90 → 45
- 逻辑：3次心跳丢失（15s间隔 × 3 = 45s）判死

### 2. 重连指数退避
- 文件：`lifecycle.py`
- 修改：`_reconnect()` 方法
- 策略：1s → 2s → 4s → 8s，总等待 <15s
- 超过 3 次标记 ERROR

### 3. reason 字段
- 文件：`lifecycle.py`
- 修改：`_send_deregister()` 和超时检测
- 值：`timeout` / `manual_disconnect` / `shutdown`

### 4. 状态同步确认
- 当前 `_lifecycle_broadcast` 已广播 status_change
- 不需要修改，保持现状

---

## 实施步骤

1. 修改 `lifecycle.py` 心跳超时默认值
2. 修改 `_reconnect()` 方法实现指数退避
3. 修改 `_send_deregister()` 添加 reason 字段
4. 测试验证
5. 同步到 shared/aim/
