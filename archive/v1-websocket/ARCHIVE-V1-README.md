# v1 WebSocket 归档说明

归档时间：2026-06-09
归档人：吉量 🐴 (ZS0002)

## 背景

这些文件是 AIM WebSocket 体系（v1）的代码，已被 NATS 架构（v2）替代。
保留在 archive/v1-websocket/ 供历史参考，不参与运行。

## 归档清单

### 从 ~/shared/aim/ 移入

| 文件 | 原行数 | 说明 | 替代方案 |
|------|--------|------|---------|
| (这些文件在 shared/aim/ 根目录，属于新架构中的代码，不归档) |

### 从 ~/.hermes/aim/ 移入（旧WS体系）

| 文件 | 原行数 | 说明 | 替代方案 |
|------|--------|------|---------|
| node.py | ~1742 | WS Server | NATS Server |
| connection_pool.py | ~700 | 连接池 | nats-py |
| delivery.py | ~400 | 投递保达 | JetStream |
| retry_integration.py | ~300 | 重试 | NATS 自动重连 |
| aim-agent.py | ~1600 | WS Agent | nats-agent.py |
| aim-light-agent.py | ~300 | 轻量Agent | nats-agent.py |
| lifecycle.py | ~500 | 生命周期 | NATS sys events |
| status_feedback.py | ~200 | 状态反馈 | aim.obs.* |
| security.py | ~252 | HMAC | NATS JWT |
| msg_dedup.py | ~100 | 去重 | JetStream dedup |
| aim_observer.py | ~120 | WS Observer | NATS subscribe |

## 注意事项

1. 归档不删除实际运行代码，只移出共享开发目录
2. 先归档，等迁移完成后删除旧运行目录中的文件
