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
