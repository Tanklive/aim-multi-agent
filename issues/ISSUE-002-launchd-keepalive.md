# ISSUE-002: NATS Server launchd 保活

> 状态: ✅ 已完成 | 负责人: 呱呱 | 优先级: 🔴 高

## 问题描述
NATS Server 需要 launchd 保活，系统重启后自动启动。

## 解决状态
⚠️ 部分完成（2026-06-09 13:30 核实）

- ✅ `com.aim.server.plist` — 已加载，管理 aim_server.py（PID 97202）
- ⚠️ `com.aim.nats-server.plist` — 文件仅存在于备份目录（~/aim-server/launchd/），未加载
- 当前 nats-server（PID 4282, ppid=1）被 init 收养，不是 launchd 管理
- ❌ 系统重启后 nats-server 不会自动拉起

## 待办
- [ ] 将 `~/aim-server/launchd/com.aim.nats-server.plist` 复制到 `~/Library/LaunchAgents/` 并加载
- [ ] 确认 nats-server 重启后能从 launchd 自动拉起

## 已配置服务
- `com.aim.nats-server` → NATS Server（端口 4222）
- `com.aim.server` → AIM Server（注册/心跳/消息）
- `com.aim.log-rotation` → 日志轮转（每天 3:00）

## plist 位置
- ~/Library/LaunchAgents/com.aim.nats-server.plist
- ~/Library/LaunchAgents/com.aim.server.plist
- ~/Library/LaunchAgents/com.aim.log-rotation.plist
- 备份：~/aim-server/launchd/

---
创建时间: 2026-06-09
完成时间: 2026-06-09 11:30