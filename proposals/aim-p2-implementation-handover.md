# P2 落地脚本 — 老三（火鸡儿）请接手

## 目标
修改 `~/shared/aim/aim-client/main.py`，落地方案见 `~/shared/aim/proposals/aim-p2-healthprobe-dispatch-decouple.md`

## 现状
- main.py 当前是干净的 P0 备份（timeout=25，无 P2 改动）
- v1/v2/v3 均因缩进/语法问题失败，已恢复备份
- 备份：main.py.p2-backup（P0 干净版）

## 5 处精确改动

### 1. `__init__`：加 `_dispatch_event`
在 `self._drain_task = None` 之后加一行同缩进：
```python
self._dispatch_event = asyncio.Event()
```

### 2. `_health_probe_loop`：移除 `_try_dispatch`，加 Event.set
原始（在 `try` 块内）：
```python
self.scheduler.update_state(report)
await self._try_dispatch()
```
改为：
```python
prev_can = self.scheduler.should_dispatch()
self.scheduler.update_state(report)
if not prev_can and self.scheduler.should_dispatch():
    self._dispatch_event.set()
```

### 3. 插入 `_dispatch_loop` 方法
在 `async def run(self):` 之前插入（注意缩进与 `run()` 同级，即 4 空格）：

```python
async def _dispatch_loop(self):
    """独立消息投递：Event驱动 + scheduler控制 + 2s退避"""
    while self.running:
        try:
            await self._dispatch_event.wait()
            self._dispatch_event.clear()
            while self.scheduler.should_dispatch() and self.queue.size() > 0:
                msg = self.queue.dequeue()
                if not msg:
                    break
                self.scheduler.on_dispatch_started()
                self.logger.info(f"投递: {msg.msg_id[:8]} from={msg.from_id}")
                try:
                    await self.transport.send_ack(msg.from_id, msg.msg_id)
                except Exception:
                    pass
                try:
                    reply = await self._call_adapter(msg)
                    if reply:
                        if msg.grp_id:
                            await self.transport.send_grp(msg.grp_id, reply)
                        else:
                            await self.transport.send_dm(msg.from_id, reply)
                    self.scheduler.on_processing_done()
                    self.queue.ack(msg.msg_id)
                except DegradeError:
                    self.scheduler.on_degrade()
                    self.queue.nack(msg.msg_id, "degrade")
                    break
                except RetryableError:
                    self.scheduler.on_retry()
                    self.queue.nack(msg.msg_id, "retry")
                    await asyncio.sleep(2)
                except HumanInterventionError:
                    self.scheduler.on_human_intervention()
                    self.queue.nack(msg.msg_id, "human_intervention")
                except Exception as e:
                    self._handle_exception(msg, e)
        except Exception as e:
            self.logger.error(f"投递循环异常: {e}")
            await asyncio.sleep(5)
```

### 4. `_handle_message`：入队后触发 dispatch
在 `self.queue.enqueue(msg)` 之后加一行同缩进：
```python
self._dispatch_event.set()
```

### 5. `run()`：启动两个 task
原始：
```python
asyncio.create_task(self._health_probe_loop())
```
改为：
```python
asyncio.create_task(self._health_probe_loop())
asyncio.create_task(self._dispatch_loop())
self._dispatch_event.set()
```

## ZS0003 配置
`~/.aim/agents/ZS0003/config.json`：`"adapter_timeout": 120` → `35`

## 部署
```bash
for id in ZS0001 ZS0002 ZS0003; do
  launchctl unload ~/Library/LaunchAgents/com.aim.agent.$id.plist
  launchctl load ~/Library/LaunchAgents/com.aim.agent.$id.plist
done
pgrep -fl 'main.py.*--agent-id'
```

## 验证
- 三个进程语法过 Python import（无 IndentationError）
- pgrep 三个 PID
- ZS0002/ZS0003 agent.err.log 无新增 HealthProbe 超时
