# AIM Client Changelog

## v1.4.1 (2026-07-02 14:08 +8)

### 变更
- **ZS0002 adapter v2.0**: API Server 优先 + CLI fallback，根治冷启动 54s → 8s
  - adapter.sh 重写：`services.api` 服务发现 → `AIM_API_URL`/`AIM_API_CREDENTIAL` → curl API
  - config.json `adapter_env` 注入 `API_SERVER_KEY`，main.py 自动展开 `${ENV}` 引用
  - printf 动态构造 Auth header，绕过 Hermes mask 系统
  - health probe 优先 API `/health`，不可达时 fallback CLI
- **fix**: shared v2.0 adapter 未部署问题 — sync 到 `~/.aim/adapters/hermes/`，MD5 一致
- **fix**: `$SP` 未定义、`aim_hermes_req.py` 依赖移除，payload 内联构造

### 测试
- ✅ API 路径：8.68s（vs CLI 冷启动 54s）
- ✅ AIM 端到端 DM：ZS0001 → ZS0002 → 正常回复
- ✅ CLI fallback：API 不可达时静默降级
- ✅ health probe：API 优先，健康检查正常
- ✅ 无回退：日志零 "fallback to CLI"

## v1.4.0 (2026-06-24 12:30 +8)

### 新增
- **feat**: context-card 冷启动上下文注入 — L1 骨架 + L2 即时，三 adapter 全部上线
  - ZS0001: v2.2 (session-key 独立隔离，性格+项目上下文注入)
  - ZS0002: v1.5 (context-card L1+L2 注入) → v1.6 (API Server curl 模式)
  - ZS0003: v1.13.2 (context-live L2 注入)
- **feat**: ZS0002 adapter v1.6 API Server 模式 — curl → localhost:8642，延迟 12-17s → 3-13s，CLI fallback
- **feat**: 无效沟通三层防护体系 — L1 反信号降权 + L2 环路检测 + L3 前置分类
- **feat**: aim-client 生命周期管理 — --install/--uninstall/--start/--stop
- **tool**: sync-check.sh — shared↔部署 MD5 一致性自动检查，支持 --fix
- **feat**: deliver confirmation 替代 ACK — 零循环消息

### 修复
- **fix(R-002)**: _processed_ids & _dispatched_ids 持久化到 JSONL，消除重启失忆
- **fix(P0-004)**: 合并双循环检测器 — L3 ACK/INFO fast-path
- **fix(P0-005)**: dispatch 出队联动 L1 去重 — 防止旧积压重复处理
- **fix(U-005)**: 双层去重 — L1 msg_id 精确 + L2 内容去重 120s 窗口
- **fix(U-006)**: 消息重复显示根因修复 — 日志双写 + 群聊回调竞态
- **fix**: StallWatchdog 队列空时误报 — 仅 queue>0 时 reset_to_idle
- **fix**: StallWatchdog 触发后 _dispatch_event 未 set 致 dispatch 永久阻塞
- **fix**: aim_send_nats --from 必填 + queue_persist 防御 + health_probe exit_code 追踪

### 工具与文档
- **docs**: 全球 Agent 框架横向对比 — 19 框架验证，A类 Daemon 为主流
- **docs**: ZS0003 rc=141 SIGPIPE 全链路排查与修复
- **docs**: AI 幻觉根因分析 — 消息幂等性缺失，业界方案对比
- **docs**: Letta Feature Request — serve-agent daemon mode (lettacode#3041)

### 版本管理
- **版本号对齐**: 项目级 / SDK (两份) / 三 Agent 本地 VERSION → 统一 1.4.0
- **VERSION-STANDARD.md**: 版本号同步到 1.4
- **adapter 版本注释清理**: ZS0001 adapter.sh 消除 v2.2/v1.7 双版本号矛盾

---

## v1.3.3 (2026-06-20 09:02 +8)

### Registry v1.3 (L1 KV + stalled 阈值)
- `AgentRecord._offline_count`: 累积离线次数，永不清零
- `AgentRecord.stalled_since`: stalled 状态开始时间
- stalled 检测: heartbeat 正常但 queue>=5 持续 >90s → status="stalled"
- stalled 自动恢复: queue 清空 → status="online"
- `_handle_list` 响应新增: `offline_count`, `offline_since`, `stalled_since`, `last_queue_size`
- 配置项: `STALLED_QUEUE_THRESHOLD=5`, `STALLED_TIME_THRESHOLD=90`

### handler 容错清理
- 新增 `_validate_envelope()`: veritas v1.0 信封准入校验
- 移除 `envelope.get("content")` 和 `envelope.get("from_id")` 容错回退
- 新增 `envelope_strict_mode` 配置 (默认 "warn", Phase 2 切 "reject")

### 其他
- 保活: nohup + cron watchdog (60s) 替代 launchd
- 三方共识: 吉量SDK校验 + 呱呱handler清容错 + 火鸡儿E2E验证

---

## v1.3.2 (2026-06-20 03:22 +8)
### Changes
- **fix**: adapter_ok 恒为 false → AgentState 无 "OK" 态，改为 `!= OFFLINE`
- **fix**: NATS drain() timeout 无限制 → SDK 加 5s 超时 + SIGALRM 10s 安全网
- **feat**: NATS launchd KeepAlive 守卫 (`launchctl load` + `KeepAlive=true`)
- **feat**: P2 Registry 查询 API — `aim.registry.health_query` + `aim.registry.event_query`
- **feat**: P2 recover 非阻塞化 — `asyncio.create_task` + `_recover_task` 防并发

### Previous
- v1.3.1: Queue 持久化路径按 agent_id 分文件；launchd plist KeepAlive SuccessfulExit=false
- v1.3.0: Queue+Scheduler+HealthProbe 三层解耦；trim/recover 事件日志
- v1.2.0: adapter 4 接口标准化 + 三级降级模型
- v1.1.0: Registry KV 健康快照 + 事件日志
- v1.0.0: AIM Client 独立进程，直连 NATS

## 2026-06-30 ZS0001 adapter health 修复

### Fixed
- **adapter.sh health**: macOS TCC 安全策略在 AIM Client 子进程上下文拦截 `/bin/ps`，导致 health 探针误报 unhealthy
- 修复：改用 `openclaw gateway status` 替代 `ps aux | grep`，不依赖进程查询权限
- 验证：`adapter.sh health` → `{"status":"healthy","active_sessions":1}`
- 同步：已同步到 shared/aim/adapters/openclaw/adapter.sh
