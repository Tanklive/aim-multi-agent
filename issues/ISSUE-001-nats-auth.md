# ISSUE-001: NATS 认证

> 状态: ✅ 已完成 | 负责人: 呱呱 | 优先级: 🔴 高

## 问题描述
NATS Server 需要启用认证，防止未授权访问。

## 解决状态
✅ 已完成（2026-06-09 13:45）

## 实现方式
- Token 认证（NATS 原生 authorization.token）
- Token 存储：~/aim-server/.nats-token（600 权限）
- 配置同步：~/.aim/config/aim.json（nats_token 字段）

## 已配置
- NATS Server：nats.conf authorization.token
- AIM Server：从 ~/.aim/config/aim.json 读取 token
- SDK：AIMNATSClient 支持 token 参数

## 验证
- ✅ 无 Token 连接被拒（Authorization Violation）
- ✅ 带 Token 连接成功
- ✅ 注册 + DM 全链路通过

---
创建时间: 2026-06-09
更新时间: 2026-06-09