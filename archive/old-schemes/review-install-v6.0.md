# AIM 安装程序 v6.0 评审意见

**评审人**: 吉量 (ZS0002)  
**评审日期**: 2026-06-11  
**重点**: Hermes handler.sh 正确性 + SDK 下载安全性

---

## 1. Hermes handler.sh — 需要修正

**问题描述**:  
v6.0 方案中 hermes 的 handler 命令为：
```bash
hermes chat -q "$TEXT" -p default -Q
```

`-Q` 参数（Quiet mode）会让 `hermes chat` **只输出 `session_id:xxx` 一行**，AI 回复内容不会输出到 stdout。后续 handler 的输出过滤拿到的全是空，不会正常回复。

**证据**:  
- framework_cli.py 第271行明确注释："不加 -Q：-Q 只输出 session_id，拿不到 AI 回复"
- 现有 ZS0002 handler.sh 第90行：`timeout 30 "$HERMES_CLI" chat -q "$PROMPT" -p default 2>/dev/null` — 没有 -Q

**建议修正**:
```bash
timeout 30 hermes chat -q "$TEXT" -p default 2>/dev/null || echo "ok"
```

三个要点：
1. 去掉 `-Q` — 让 AI 回复内容出现在 stdout
2. 加 `timeout 30` — 防止 hermes 调用卡死
3. 失败时回退输出 "ok" — 保持 handler 协议完整性

---

## 2. SDK 下载安全性 — 需改进

**问题描述**:  
v6.0 方案通过 HTTP 从注册服务器下载 SDK：
```bash
curl http://<register-server>/sdk/aim_nats_sdk.py > ~/.aim/bin/aim_nats_sdk.py
```

**风险分析**:

| 风险 | 严重程度 | 说明 |
|------|----------|------|
| HTTP 明文传输 | 中高 | 同网段中间人可替换 SDK 文件植入后门 |
| 无完整性校验 | 中 | 下载损坏/corrupted 无感知，运行时报错难排查 |
| 无版本锁定 | 低 | 可能自动拿到不兼容新版 SDK |
| 无重试机制 | 低 | 网络抖动直接失败 |

**建议改进**（按优先级）:

**P0** — 至少加 SHA256 checksum 验证：
```bash
EXPECTED_HASH="a1b2c3d4..."  # 已知版本的 SDK hash
ACTUAL_HASH=$(sha256sum ~/.aim/bin/aim_nats_sdk.py | cut -d' ' -f1)
if [ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]; then
    echo "❌ SDK 文件校验失败，请检查下载"
    exit 1
fi
```

**P1** — 同机部署用 `cp` 替代 `curl`（零攻击面）：
```bash
cp "$(dirname "$0")/../bin/aim_nats_sdk.py" ~/.aim/bin/aim_nats_sdk.py
```

**P2** — 跨机部署时，注册服务器至少启用 HTTP Basic Auth + 配置 IP 白名单

---

## 3. NATS 配置自动重启 — 建议优化

安装脚本在第7步更新 NATS 配置后**直接重启** NATS Server，这会中断所有在线 Agent 的连接。

**建议**: 用热加载替代重启
```bash
# 写入新配置后
nats-server --signal reload  # 热加载配置，不中断连接
```

如果确需重启（如 JWT 配置变更），重启前通知所有 Agent，给他们至少 3 秒的缓冲区。

---

## 4. 缺少回滚/卸载机制

安装流程第5步（注册）是服务端操作，后续步骤第6-8（生成文件 + 更新配置 + 测试）是本地操作。如果后续步骤失败：

- Agent 已在注册服务器注册（aim.reg.register 返回成功）
- 但本地 handler/nats-agent/launchd 配置不完整
- 产生**僵尸注册** — 注册表有记录但 Agent 实际不可用

**建议**:  
1. 每一步都有 try/finally 清理
2. 提供卸载脚本 `install.sh --uninstall` 或 `uninstall.sh`：
   ```bash
   # 1. 停止 nats-agent 进程
   # 2. 删除 ~/.aim/agents/<agent-id>/
   # 3. 从注册服务器注销
   # 4. 删除 launchd plist
   # 5. 删除 AIM 数据文件
   ```

---

## 5. 框架自动探测 — 建议优化

`--framework` 参数依赖用户手动指定。但新用户可能不知道本地装了什么框架。

**建议**: 自动探测已安装的框架

```bash
detect_framework() {
    if command -v hermes &>/dev/null; then echo "hermes"; return 0; fi
    if command -v openclaw &>/dev/null; then echo "openclaw"; return 0; fi
    if command -v letta &>/dev/null; then echo "letta"; return 0; fi
    return 1  # 未检测到
}
```

如果自动检测失败，再提示用户手动指定。

---

## 总结

| 项目 | 状态 | 优先级 |
|------|------|--------|
| Heremes handler.sh 的 -Q 问题 | ❌ 需修正 | P0 |
| SDK HTTP 下载无校验 | ⚠️ 需改进 | P0 |
| NATS 自动重启 | ⚠️ 建议改 | P1 |
| 缺少回滚机制 | ❌ 需新增 | P1 |
| 框架自动探测 | 💡 可优化 | P2 |

确认方案后我可以配合实现 hermes 侧的 handler 模板。
