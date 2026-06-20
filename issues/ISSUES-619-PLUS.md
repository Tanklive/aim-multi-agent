# AIM 619+ 补充问题清单（2026-06-19）

> 火鸡儿 + 吉量 三方自查发现，619 清单关闭后新曝光
> 规则同 ISSUES-619.md：问题永留，解决结果追加

---

## 问题总览

| ID | 类别 | 问题 | 严重度 | 状态 | 责任方 | 解决日期 |
|----|------|------|:--:|:--:|--------|:--------:|
| P0-1 | main.py | dispatch_loop 死锁（DegradeError break 后 dispatch_event 未重置） | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| P0-2 | 配置 | ZS0003 config version=1.3.0, adapter_timeout=35 | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| P0-3 | 配置 | ZS0002 config 缺 4 字段, env 嵌套 | 🔴 | ✅ 已解决 | 呱呱/吉量确认 | 06-19 |
| P0-4 | 部署 | SDK 版本分叉（~/.aim/bin/ 落后到 1.2.1） | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| P0-5 | 部署 | deploy.sh 不存在/5路径3个空 | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| P1-1 | 协议 | exit code 2 语义三方不一致 | 🟡 | 待对齐 | 三方 | - |
| P1-2 | 清理 | shared/aim 旧架构残留 | 🟡 | ✅ 部分清 | 呱呱 | 06-19 |
| P1-3 | 部署 | nats-agent.py 消失无迁移文档 | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| P2-1 | 运维 | adapter.sh 版本不统一（193/142/196行） | 🟢 | 待定 | 三方 | - |
| P2-2 | 运维 | deploy.sh 无 adapter 同步逻辑 | 🟢 | 待定 | 呱呱 | - |
| P2-3 | 清理 | ZS0002 目录残留 aim-agent.py | 🟢 | 待定 | 吉量 | - |
| P2-4 | 规范 | aim-client/ 和 aim_client/ 命名不一致 | 🟢 | 待定 | 呱呱 | - |
| P2-5 | 规范 | VERSION-STANDARD.md 缺 adapter/config 同步 | 🟢 | 待补 | 呱呱 | - |
| OPT | 治理 | 吉量优化建议截断，待重发 | ⚪ | 待吉量 | 吉量 | - |

## 详细

### P0-1：dispatch_loop 死锁
- **根因**：`DegradeError → break` 后 `dispatch_event` 未重新 `.set()`，下一轮 `wait()` 永久阻塞
- **代价**：健康探针只在 BUSY→OK 时 set，降级状态不触发 → 群消息全收不处理
- **修复**：`break` 前加 `self._dispatch_event.set()`
- **发现者**：火鸡儿 ZS0003
- **状态**：✅ 已解决（06-19 19:58）

### P0-2/3/4：配置+SDK 分叉
- 全部已同步修复
- deploy_sdk.sh 已创建

### P1-1：exit code 2 语义对齐
- ZS0001: 未知参数/运行时挂/降级
- ZS0002: 未知参数/health/cancel/降级
- ZS0003: Letta 消失/agent_id 验证失败
- 需三方开会统一定义

### 其余 P1/P2
- 待群讨论分工
