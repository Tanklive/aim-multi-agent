# AIM 配置说明

## 配置文件

**位置：** `~/.aim/config/aim.json`

```json
{
  "ver": "1.0",
  "description": "AIM 全局配置",
  "nats_server": "nats://127.0.0.1:4222",
  "nats_token": "your-token-here",
  "default_group": "grp_trio",
  "agents": {
    "ZS0001": {"name": "呱呱", "framework": "openclaw"},
    "ZS0002": {"name": "吉量", "framework": "hermes"},
    "ZS0003": {"name": "小火鸡儿", "framework": "letta"}
  },
  "streams": {
    "aim-messages": {"max_age": "7d", "subjects": ["aim.dm.>", "aim.grp.>"]},
    "aim-observations": {"max_age": "24h", "subjects": ["aim.obs.>"]},
    "aim-system": {"max_age": "30d", "subjects": ["aim.sys.>"]}
  }
}
```

## 环境变量

环境变量优先级高于配置文件：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AIM_NATS_URL` | NATS Server 地址 | `nats://127.0.0.1:4222` |
| `AIM_NATS_TOKEN` | NATS 认证 Token | 空 |

## 优先级

```
命令行参数 > 环境变量 > 配置文件 > 硬编码默认值
```

## Agent 配置

每个 Agent 有独立配置：`~/.aim/agents/{agent_id}/config.json`

```json
{
  "agent_id": "ZS0001",
  "agent_name": "呱呱",
  "framework": "openclaw",
  "nats_server": "nats://127.0.0.1:4222"
}
```

## 密钥文件

| 文件 | 说明 | 权限 |
|------|------|------|
| `~/aim-server/.nats-token` | NATS Token | 600 |
| `~/.aim/agents/{id}/secrets/nkey.seed` | NKEY Seed | 600 |
