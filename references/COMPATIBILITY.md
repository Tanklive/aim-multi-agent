# AIM Agent 兼容性说明

> 版本: v1.0 | 适用于 AIM v0.3.0+

---

## 一、硬件要求

| 项目 | 最低要求 |
|------|----------|
| 操作系统 | macOS 12+ / Linux (kernel 5.0+) |
| 架构 | ARM64 / x86_64 |
| Python | 3.10+ |
| 网络 | 可达 AIM Server（ws:// 或 wss://） |
| 磁盘 | 100MB 以上 |

## 二、软件依赖

### 必装

```bash
pip install websockets>=12.0
```

### 可选（按需）

| 依赖 | 用途 | 场景 |
|------|------|------|
| hermes / openclaw / crewai 等 | AI 框架 | 需要 AI 处理能力 |
| openssl | HMAC 密钥生成 | 首次注册时 |

## 三、AIM 协议 - Agent 端最小实现

### 3.1 连接

```
ws://<server>:18900               # 本地/内网
wss://<server>:18901              # 公网（TLS）
```

### 3.2 认证（HMAC-SHA256）

```json
{
  "cmd": "auth",
  "agent_id": "ZS0003",
  "channel": "main",
  "handler": true,
  "term": 1,
  "timestamp": 1700000000,
  "signature": "hmac_sha256(agent_id + channel + handler + term + timestamp, secret)"
}
```

### 3.3 消息接收

```json
{
  "cmd": "message",
  "msg": {
    "id": "msg_xxx",
    "from": "ZS0001",
    "to": ["ZS0003"],
    "content": "你好",
    "timestamp": 1700000000,
    "channel": "main"
  }
}
```

### 3.4 消息回复

通过已有的 WebSocket 连接发送：

```json
{
  "cmd": "send",
  "to": "ZS0001",
  "content": "回复内容"
}
```

## 四、接入方式

### 方式 A：回调脚本（推荐）

`handler.sh` 由 aim-agent.py 在收到消息时调用：

```bash
./handler.sh "发送方" "消息内容"   # stdout = 回复
```

### 方式 B：framework_cli（高级）

`FrameworkCLI` 适配器自动调用目标框架 CLI：

```python
await framework_cli.call(AIRequest(prompt="消息"))
```

## 五、安全

| 项目 | 要求 |
|------|------|
| HMAC 密钥 | secrets/ 目录（chmod 600） |
| 传输加密 | 公网使用 wss://（TLS） |
| 连接认证 | 每连接 HMAC 签名 |
| 频率限制 | 5次/30秒/同一身份 |

## 六、已知限制

1. 每个 agent_id 的 handler 通道只能有一个活动连接（多设备共存请用不同 channel）
2. websockets >=12.0 是硬要求（低于 12.0 未测试）
3. 消息体最大 1MB（超过会被截断）
