# AIM V2 Phase 1 — Client 端适配清单

## 改动文件

### 1. aim_send.py — 支持 --channel 参数
- [ ] 新增 `--channel` 参数，默认 `script`
- [ ] auth 时携带 `channel` 字段
- [ ] 向后兼容：不加 `--channel` 的行为不变

### 2. aim-agent.py — 认证时带 channel/term
- [ ] 启动时 auth 消息加 `channel: "main"`, `handler: true`, `term: 1`
- [ ] 断连重连时 term +1
- [ ] 收到降级通知时切换 handler=false

### 3. healthcheck_jiliang.py — 用独立 channel
- [ ] aim_send 调用加 `--channel health`
- [ ] 健康检查不跟 main 连接抢线

### 4. 文档
- [ ] README.md 更新 channel 接入说明
- [ ] ARCHITECTURE.md 更新连接模型
