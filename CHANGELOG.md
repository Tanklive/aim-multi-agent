# AIM Client Changelog

## v1.3.2 (2026-06-20 03:22 +8)
### Changes
- **fix**: adapter_ok 恒为 false → AgentState 无 "OK" 态，改为 `!= OFFLINE`
- **fix**: NATS drain() timeout 无限制 → SDK 加 5s 超时 + SIGALRM 10s 安全网
- **feat**: NATS launchd KeepAlive 守卫 (`launchctl load` + `KeepAlive=true`)
- **feat**: P2 Registry 查询 API — `aim.registry.health_query` + `aim.registry.event_query`
- **feat**: P2 recover 非阻塞化 — `asyncio.create_task` + `_recover_task` 防并发

### Files
- `main.py`: _shutdown alarm, adapter_ok fix, query methods, non-blocking recover
- `registry.py`: health_query + event_query handlers
- `aim_nats_sdk.py`: drain timeout (5s)
- `nats-guard.sh`: launchd-friendly wrapper (no `exec`, log output)

### Previous
- v1.3.1: Queue 持久化路径按 agent_id 分文件；launchd plist KeepAlive SuccessfulExit=false
- v1.3.0: Queue+Scheduler+HealthProbe 三层解耦；trim/recover 事件日志
- v1.2.0: adapter 4 接口标准化 + 三级降级模型
- v1.1.0: Registry KV 健康快照 + 事件日志
- v1.0.0: AIM Client 独立进程，直连 NATS

## v1.3.3 — 2026-06-20 09:02

### Registry v1.3 (L1 KV + stalled 阈值)
- `AgentRecord._offline_count`: 累积离线次数，永不清零
- `AgentRecord.stalled_since`: stalled 状态开始时间
- `AgentRecord.last_queue_size/at`: 最近健康快照中的 queue 信息
- stalled 检测: heartbeat 正常但 queue>=5 持续 >90s → status="stalled"
- stalled 自动恢复: queue 清空 → status="online"
- `_handle_list` 响应新增: `offline_count`, `offline_since`, `stalled_since`, `last_queue_size`
- 配置项: `STALLED_QUEUE_THRESHOLD=5`, `STALLED_TIME_THRESHOLD=90`

### 保活
- plist launchctl bootstrap 在 OpenClaw sandbox 下不可用 (error 5)
- 改用 nohup + cron watchdog (60s) 替代 launchd 自动保活
- Registry 服务恢复运行 (python3.13 PID 60081)

### 责任人
- 呱呱 (ZS0001)
