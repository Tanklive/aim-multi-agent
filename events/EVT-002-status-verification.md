# EVT-002: NATS 问题状态核实

> 类型: 状态确认 | 日期: 2026-06-09 13:30

## 核实结果

### ISSUE-001: NATS 认证未启用 🔴 高
- **实际状态**: ❌ 未完成
- **核实时间**: 2026-06-09 13:30
- **详情**: nats.conf 中认证配置依然被注释，任何客户端可直接连接
- **结论**: 与记录一致，确认为待处理

### ISSUE-002: NATS Server launchd 保活 🔴 高
- **记录状态**: ✅ 已完成（文件中写已完成）
- **核实结果**: ⚠️ **部分完成，需修正**
- **详情**:
  - ✅ `com.aim.server.plist` — 已加载，管理 aim_server.py（PID 97202）
  - ⚠️ `com.aim.nats-server.plist` — 文件仅存在于备份目录（~/aim-server/launchd/），未复制到 ~/Library/LaunchAgents/，未加载
  - 当前 nats-server（PID 4282, ppid=1）只是被 init 收养，不是 launchd 管理
  - ❌ 系统重启后 nats-server 不会自动拉起
- **结论**: 需要补充 nats-server 的 launchd 配置

### 其他运行中进程
| 进程 | PID | launchd 管理 | 重启后存活 |
|------|-----|-------------|-----------|
| nats-server | 4282 | ❌ | ❌ |
| aim_server.py | 97202 | ✅ com.aim.server | ✅ |
| aim_agent_nats.py (ZS0002) | 4270 | ✅ com.aim.agent.ZS0002 | ✅ |
| nats-agent.py (ZS0005) | 4298 | ❌ | ❌ |
| nats-agent.py (ZS0001) | 4543 | ❌ | ❌ |

## 建议
1. 将备份目录的 `com.aim.nats-server.plist` 复制到 LaunchAgents/ 并加载
2. ZS0001/ZS0005 的 NATS agent 也需要 launchd 保活

