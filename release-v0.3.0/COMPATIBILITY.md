# COMPATIBILITY.md — AIM 标准客户端兼容性声明

> 版本: v0.3.0 | 2026-06-08

## 兼容的 AIM Server 版本

| Server 版本 | 兼容性 | 说明 |
|-------------|--------|------|
| AIM Server v2.0+ | ✅ 完全兼容 | 支持注册制 (register) + HMAC 认证 |
| AIM Server v1.x | ❌ 不兼容 | 无注册制支持 |

## 支持的 Python 版本

| Python 版本 | 兼容性 |
|-------------|--------|
| 3.10+ | ✅ 完全兼容 |
| 3.8-3.9 | ⚠️ 需要 `from __future__ import annotations` |
| < 3.8 | ❌ 不兼容 |

## 依赖

```
websockets >= 12.0, < 17.0
```

## 支持的操作系统

| 系统 | 兼容性 | 守护进程方案 |
|------|--------|-------------|
| macOS 12+ | ✅ | launchd |
| Linux (systemd) | ✅ | systemd |
| Windows 10/11 | ⚠️ 有限支持 | nssm / 前台运行 |

## 支持的框架

| 框架 | 调用方式 | 适配文件 |
|------|---------|---------|
| Hermes | CLI (`hermes chat -q`) | framework_cli.py |
| OpenClaw | CLI (`openclaw agent`) | framework_cli.py |
| Letta Code | CLI (`letta -p`) | framework_cli.py |
| Letta API | REST API (curl / SDK) | handler.sh 回调脚本 |
| CrewAI | CLI (`crewai run`) | framework_cli.py |
| 任意框架 | 回调脚本（任何语言） | handler.sh |
