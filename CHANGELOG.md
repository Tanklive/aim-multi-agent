# Changelog

## v1.5.0-alpha (2026-07-02)

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
