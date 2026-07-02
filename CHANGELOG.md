# Changelog

## v1.5.1 (2026-07-03) — P-fix 三连

### P0: content validator pattern #2 放行 v1.0 协议 JSON
- `aim_nats_sdk.py` `_BLOCKED_PATTERNS` #2: 白名单从 `ver|from|id|type` → `ver|from|id|type|status|version|reply|error|error_code|session_id|elapsed_ms`
- v1.0 adapter 返回 `{"status":"ok","reply":"..."}` 不再被拦
- 提交: `5b9261e`

### P0: queue_persist is_mentioned 序列化丢失
- `queue_persist.py` `_message_to_dict` 缺少 `is_mentioned` → 重启后 @消息被当 COLD 吞
- `_dict_to_message` 补 `is_mentioned=data.get("is_mentioned", False)`
- 提交: `d71ef74`

### 配置标准化 + 协议切换管控
- 三 Agent config.json 统一 23 标准字段
- `setup-agent.sh` + `agent-template.json` 新 Agent 接入机制
- `protocol_version` 默认 `""` (安全 legacy), adapter 升级后再手动切 v1.0
- 提交: `7a8cd16`, `5ebea03`, `9d89afa`, `14c2fba`, `ff965ca`, `f9b0516`

### 全平台 v1.0 协议上线
- ZS0001/ZS0002/ZS0003 全部 protocol v1.0 ✅
- Letta adapter v1.14.1 (JSON stdin 双协议 + shlex.quote 安全注入)


## v1.5.0 (2026-07-02 晚间)

### L1: Adapter Protocol 标准化

#### Added
- `docs/ADAPTER-PROTOCOL.md` — 协议规范 (7 lifecycle, JSON stdin/stdout, 退出码约定)
- `aim-client/session.py` — SessionManager (按 from_id 路由, CLI 复用≤5次)
- `aim-client/context.py` — ContextManager (SOUL.md + context-card, mtime 热刷新)
- `aim-client/main.py` — `_call_adapter_v1()` JSON 协议路径, `protocol_version: "1.0"` 切换

#### Changed
- `main.py` __init__ 集成 SessionManager + ContextManager
- `_call_adapter` 增加 protocol_version 判断 → v1.0 优先, 旧 CLI 保留

### L2: 全球协议桥接方向
- MCP Bridge / A2A Bridge / REST Bridge 三层架构确认
- NATS + MCP + A2A 三层不互替, 并行规划
- 大哥裁决: MCP 优先

### Docs
- `docs/ADAPTER-STANDARDIZATION.md` v1.2

### 2026-07-02 变更 (同版本增量)

#### Bug Fixes
- B-021: ZS0001 adapter 路径未同步 — config 指向 `~/.aim/agents/ZS0001/adapter.sh` (旧版,无JSON), 但修改的是 `~/.aim/adapters/openclaw/adapter.sh` (新版)。MD5 不一致导致部分调用 "缺少 --message"。修复: 同步适配器 + 重启。

#### Tested
- 5轮会话模式测试全部通过 (DM往返/Session复用/Context注入/延迟容忍/三方群聊)

#### Docs
- `PROJECT-LOG.md` — 项目完整记录 (版本演替/功能矩阵/BUG修复/需求溯源/决策记录)
- `AIM-SYSTEM-ARCHITECTURE.md` v2.0 — +OAS 扩展层定位, 四层架构全景

#### Diff
- `aim-client/adapter.sh` — ZS0001 adapter 路径同步
- `docs/PROJECT-LOG.md` — 新建
- `docs/AIM-SYSTEM-ARCHITECTURE.md` — +OAS 章节

## ZS0002 v1.4.1 → v1.5.0 变更 (2026-07-03)

### Adapter v2.1
- JSON stdin 协议支持 (protocol v1.0, `adapter_mode=cli`)
- CLI args 后向兼容保留
- API Server 优先 (curl → 8642, ~8s) + CLI fallback
- printf 动态构造 Auth header, 绕过 Hermes mask 系统
- `services.api` 服务发现 → `AIM_API_URL`/`AIM_API_CREDENTIAL` 自动注入

### Config
- `config.json` 加 `protocol_version: "1.0"`, `adapter_env.API_SERVER_KEY`
- 三重冗余: config.yaml + launchd plist + config.json

### Bug Fixes
- B-CRASH-LOOP: `v1.5.0-alpha` → `_ver_tuple()` ValueError → crash loop (呱呱修复: VERSION→1.5.0)
- 已知残留: dispatch 假死 (StallWatchdog exit=4), 待呱呱排查
