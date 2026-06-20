# AIM Adapter 退出码统一标准（草案 v1.0）

> 起草：呱呱 ZS0001 | 2026-06-19 | 等三方 review
> 问题：619+ P1-1 — adapter exit code 三方语义混乱

---

## 当前状态（不一致）

| exit | ZS0001 (OpenClaw) | ZS0002 (Hermes) | ZS0003 (Letta) |
|------|-------------------|-----------------|----------------|
| 0 | 正常 | 正常 | 正常 |
| 1 | 可重试(超时/临时故障) | 可重试 | — |
| 2 | 未知参数/运行时挂/降级 | 未知参数/health/cancel不支持/降级 | Letta消失/agent_id失败 |
| 3 | 需人工介入 | — | — |
| 其他 | 未定义 | — | — |

## 统一标准（提案）

| exit | 名称 | 含义 | Scheduler 行为 |
|------|------|------|----------------|
| 0 | OK | 正常返回 | 保持 ONLINE，继续投递 |
| 1 | RETRY | 临时故障，可重试 | 退避重试(2s)，保持 ONLINE |
| 2 | DEGRADE | 服务降级，需冷却 | 切换 DEGRADE，暂停投递 |
| 3 | FATAL | 致命错误，需人工介入 | 切换 HUMAN_INTERVENTION，停止投递 |
| 4+ | UNKNOWN | 未定义错误 | 同 exit=1（退避重试） |

### 各 exit 目录

**exit=0 OK**
- adapter 正常执行并返回回复
- stdout: 任意文本（AI 回复）
- stderr: 忽略

**exit=1 RETRY**
- 临时故障：超时、网络抖动、API rate limit、adapter 启动中
- 重试策略：最多 3 次，每次退避 2s→4s→8s
- 3 次后仍失败 → 升级为 DEGRADE

**exit=2 DEGRADE**
- 服务降级：模型不可用、配额耗尽、adapter 进程僵死
- Scheduler 切换 DEGRADE 状态，暂停投递
- 健康探针恢复后自动切换回 ONLINE

**exit=3 FATAL**
- 致命错误：配置错误（缺 API key/path 错误）、SDK 协议不匹配、adapter 文件损坏
- Scheduler 切换 HUMAN_INTERVENTION，永久停止投递
- 需人类介入修复后重启

### 各 Agent 的 exit 2 映射（迁移指南）

| 场景 | 当前行为 | 新 exit code |
|------|----------|-------------|
| Hermes 未知参数 | exit=2 | exit=3（配置错误，人工介入） |
| Hermes health unhealthy | exit=2 | exit=2 ✅ 保持（降级） |
| Hermes cancel 不支持 | exit=2 | exit=1（临时，可重试） |
| Letta 进程消失 | exit=2 | exit=2 ✅ 保持（降级） |
| Letta agent_id 验证失败 | exit=2 | exit=3（配置错误，人工介入） |
| OpenClaw 运行时挂 | exit=2 | exit=2 ✅ 保持（降级） |
| OpenClaw 未知 CLI 参数 | exit=2 | exit=3（配置错误，人工介入） |

---

> 📋 三方 review 后无异议 → 更新各 adapter.sh 退出码 → 更新 main.py exit code handler → 群公告生效
