# AIM Agent NATS 连接问题报告

> 报告时间：2026-06-11 14:30
> 分析工具：MiMo Code Agent
> 位置：桌面，仅本地存储

---

## 一、问题摘要

| 问题 | 严重程度 | 影响范围 |
|------|---------|---------|
| NATS 认证失败 | 🔴 严重 | 所有 Agent |
| JetStream 超时 | 🟠 中等 | 所有 Agent |
| Agent 频繁重启 | 🟠 中等 | ZS0001/ZS0002 |
| 注册失败 | 🟡 轻微 | 新启动的 Agent |

---

## 二、详细问题分析

### 问题1：NATS 认证失败（最严重）

**错误日志**：
```
[ERR] 127.0.0.1:64072 - cid:1439 - authentication error
[ERR] 127.0.0.1:64075 - cid:1441 - authentication error
...（每分钟数十次）
```

**根本原因**：
- NATS 服务器使用 **JWT 认证**（Operator + Account + User）
- Agent 配置使用 **简单 Token 认证**
- 认证方式不匹配，导致连接失败

**证据**：
1. NATS 配置文件 `nats-jwt.conf` 使用 JWT 认证
2. Agent 有正确的 `aim.creds` 文件（JWT 凭证）
3. 但 Agent 的 `config.json` 没有配置 `creds_path`
4. SDK 默认使用 `nats_token`（简单 Token），而非 JWT

**解决方案**：
```bash
# 修改 Agent 配置，添加 creds_path
# 示例：ZS0001
{
    "agent_id": "ZS0001",
    "nats_server": "nats://127.0.0.1:4222",
    "nats_token": "MeUz84HdDc4nlTX_uoWUE_64phYwWi30jmonQz1eZCw",
    "framework": "openclaw",
    "creds_path": "~/.aim/agents/ZS0001/aim.creds"
}
```

---

### 问题2：JetStream 超时

**错误日志**：
```
[DEBUG] emit_obs(heartbeat) JS failed: nats: timeout
[DEBUG] emit_obs(heartbeat) JS failed: nats: timeout
...（持续出现）
```

**根本原因**：
- JetStream 操作需要有效的认证
- 认证失败导致 JetStream 操作超时
- 这是问题1的连锁反应

**解决方案**：
- 修复问题1（认证失败）后，JetStream 超时应自动消失

---

### 问题3：Agent 频繁重启

**现象**：
- ZS0001 和 ZS0002 多次集体重启
- runs 数从个位数暴涨至 29/36
- 三进程在检查间隔内全部 PID 变更

**根本原因**：
1. NATS 连接失败，Agent 退出
2. launchd `KeepAlive` 配置导致立即重启
3. `ThrottleInterval` 仅 5秒，重启过于频繁

**证据**：
```
ZS0002: runs=29（18.5小时内重启28次）
ZS0001: runs=36（18.5小时内重启35次）
```

**解决方案**：
1. 增加 `ThrottleInterval` 到 30秒
2. 在 Agent 代码中添加重试逻辑，避免立即退出
3. 修复认证问题后，Agent 不再因连接失败退出

---

### 问题4：注册失败

**错误日志**：
```
[WARNING] ⚠️ 注册失败 (nats: no responders available for request)，降级跳过
```

**根本原因**：
- Agent 连接到 NATS 后，尝试注册到 AIM Registry
- Registry 服务未启动或未响应
- 导致注册请求超时

**证据**：
- ZS0004 和 ZS0005 启动时出现此错误
- Registry 服务可能未运行

**解决方案**：
1. 检查 AIM Registry 服务状态
2. 确保 Registry 服务先于 Agent 启动
3. 在 Agent 代码中添加注册重试逻辑

---

## 三、影响评估

### 直接影响
1. **Agent 间通讯失败**：无法正常发送/接收消息
2. **AIM Watch 监控异常**：无法获取实时状态
3. **任务协作中断**：多 Agent 协作任务无法执行

### 间接影响
1. **系统资源浪费**：频繁重启消耗 CPU/内存
2. **日志污染**：大量错误日志影响问题排查
3. **用户体验下降**：Agent 响应不稳定

---

## 四、修复优先级

| 优先级 | 问题 | 修复难度 | 预计耗时 |
|--------|------|---------|---------|
| P0 | NATS 认证失败 | 简单 | 10分钟 |
| P1 | Agent 频繁重启 | 中等 | 20分钟 |
| P2 | JetStream 超时 | 依赖P0 | - |
| P3 | 注册失败 | 中等 | 30分钟 |

---

## 五、修复步骤

### 步骤1：修复 NATS 认证（P0）

```bash
# 1. 修改 ZS0001 配置
cat > ~/.aim/agents/ZS0001/config.json << 'EOF'
{
    "agent_id": "ZS0001",
    "nats_server": "nats://127.0.0.1:4222",
    "nats_token": "MeUz84HdDc4nlTX_uoWUE_64phYwWi30jmonQz1eZCw",
    "framework": "openclaw",
    "creds_path": "~/.aim/agents/ZS0001/aim.creds"
}
EOF

# 2. 修改 ZS0002 配置
cat > ~/.aim/agents/ZS0002/config.json << 'EOF'
{
    "agent_id": "ZS0002",
    "nats_server": "nats://127.0.0.1:4222",
    "nats_token": "MeUz84HdDc4nlTX_uoWUE_64phYwWi30jmonQz1eZCw",
    "framework": "auto",
    "creds_path": "~/.aim/agents/ZS0002/aim.creds"
}
EOF

# 3. 修改 ZS0003 配置
cat > ~/.aim/agents/ZS0003/config.json << 'EOF'
{
    "agent_id": "ZS0003",
    "agent_name": "小火鸡儿",
    "framework": "letta",
    "nats_server": "nats://127.0.0.1:4222",
    "operator_id": "OP0001",
    "nats_token": "MeUz84HdDc4nlTX_uoWUE_64phYwWi30jmonQz1eZCw",
    "creds_path": "~/.aim/agents/ZS0003/aim.creds"
}
EOF

# 4. 重启所有 Agent
launchctl stop com.aim.nats-agent.ZS0001
launchctl stop com.aim.nats-agent.ZS0002
launchctl stop com.aim.nats-agent.ZS0003
launchctl start com.aim.nats-agent.ZS0001
launchctl start com.aim.nats-agent.ZS0002
launchctl start com.aim.nats-agent.ZS0003
```

### 步骤2：验证修复

```bash
# 检查 Agent 日志
tail -f ~/.aim/agents/ZS0001/logs/agent.err.log

# 预期输出：
# ✅ [ZS0001] NATS connected: nats://127.0.0.1:4222
# 📬 已订阅私聊: aim.dm.ZS0001
# ✅ ZS0001 启动完成，等待消息...

# 不应该看到：
# ❌ [ZS0001] NATS 连接失败: authentication error
```

### 步骤3：修复 launchd 配置（P1）

```bash
# 修改 ZS0001 的 launchd 配置
# 增加 ThrottleInterval 到 30秒
cat > ~/Library/LaunchAgents/com.aim.nats-agent.ZS0001.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aim.nats-agent.ZS0001</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/yangzs/.aim/agents/ZS0001/nats-agent.py</string>
        <string>--agent-id</string>
        <string>ZS0001</string>
        <string>--server</string>
        <string>nats://127.0.0.1:4222</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>ThrottleInterval</key>
        <integer>30</integer>
    </dict>
    <key>WorkingDirectory</key>
    <string>/Users/yangzs/.aim/agents/ZS0001</string>
    <key>StandardOutPath</key>
    <string>/Users/yangzs/.aim/agents/ZS0001/logs/agent.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/yangzs/.aim/agents/ZS0001/logs/agent.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>AIM_AGENT_ID</key>
        <string>ZS0001</string>
        <key>AIM_NATS_SERVER</key>
        <string>nats://127.0.0.1:4222</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
EOF

# 重新加载 launchd 配置
launchctl unload ~/Library/LaunchAgents/com.aim.nats-agent.ZS0001.plist
launchctl load ~/Library/LaunchAgents/com.aim.nats-agent.ZS0001.plist
```

---

## 六、预防措施

### 1. 配置管理
- 使用脚本批量更新 Agent 配置
- 建立配置模板，避免手动修改错误

### 2. 监控告警
- 设置认证错误告警（每分钟 > 10次）
- 监控 Agent 重启次数（每小时 > 5次）

### 3. 定期检查
- 每周检查 Agent 进程状态
- 每月检查 NATS 服务器性能

### 4. 文档更新
- 更新 Agent 部署文档
- 明确认证方式和配置要求

---

## 七、总结

**核心问题**：NATS 认证方式不匹配（JWT vs Token）

**影响**：Agent 间通讯失败，系统不稳定

**解决方案**：修改 Agent 配置，添加 `creds_path`

**预计修复时间**：30分钟

**预防措施**：统一配置管理，建立监控告警

---

*报告完成时间：2026-06-11 14:30*
*报告人：MiMo Code Agent*
*位置：桌面，仅本地存储*
