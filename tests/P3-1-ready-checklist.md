# P3-1 测试准备就绪清单

> 2026-06-08 吉量整理 | 呱呱建议先准备好再等大哥确认

---

## 准备状态总览

| 准备项 | 状态 | 说明 |
|--------|------|------|
| 测试计划 | ✅ 已定稿 | `P3-1-test-plan.md`，呱呱评审通过 v2 |
| WebSocket 测试客户端 | ✅ 已就绪 | `ws_test_client.py` 直连 Server |
| pf 防火墙脚本 | ✅ 已就绪 | `pf-isolate.sh` 阻断/恢复端口 18900 |
| 清理脚本 | ✅ 已就绪 | `cleanup-test-data.sh` 清空离线队列+恢复 pf |
| delivery.py 逻辑确认 | ✅ 已验证 | `_persist()` 先写 messages.jsonl，再走 delivery_guarantee |
| 环境依赖 | ✅ 已验证 | `websockets==16.0` 已在 Python 3.13 中安装 |
| 测试 Agent 认证 | ⏳ 待确认 | 需确认 ZS0003 的 AGENT_SECRET (测试客户端默认用 ZS0003) |

---

## 各测试项所需工具清单

| 测试 | 工具 | 命令示例 |
|------|------|---------|
| **T1** 干净断开 | Server SIGTERM + `aim-server.sh restart` | — |
| **T2** 强制 kill | Server SIGKILL | — |
| **T3** Client 断连 | `kill <aim-agent-pid>` + 重启 | — |
| **T3.5** 持久化验证 | `ws_test_client.py send --to ZS0002 --count 5` | 直接发 5 条 |
| **T4** 心跳超时 | `pf-isolate.sh start 18900` + 等待 90s | 备选: 注释心跳代码 |
| **T5** 离线队列满 | `ws_test_client.py send --to ZS0002 --count 5100` | 触发 5000 条上限 |
| **T6** 多 Agent 同时重连 | 同时杀 ZS0001 + ZS0002 的 Client | — |
| **T7** 认证失败 | 修改 ZS0002 的 secret/token | — |
| **T8** 连接池满 | `ws_test_client.py multi-connect --agent ZS0003 --count 6` | 第 6 个应被拒 |
| **T9** 极端负载 | `ws_test_client.py send --to ZS0002 --count 100 --interval 0.3` | 30s 内 100 条 |
| **T10** 注册制 | 与 P3-3 合并 | 暂不单独执行 |

---

## 执行前准备工作

- [x] ws_test_client.py 已保存到 `~/.hermes/aim/tests/`
- [x] pf-isolate.sh 已保存并 chmod +x
- [x] cleanup-test-data.sh 已保存并 chmod +x
- [x] 测试计划已同步到 `~/shared/aim/tests/P3-1-test-plan.md`
- [ ] 确认 ZS0003 AGENT_SECRET（用于 ws_test_client.py 认证）
- [ ] 确认 Server 和 3 个 Client 均在运行
- [ ] 通知呱呱/小火鸡儿测试即将开始

---

## Day 1 执行顺序

```
T1 (Server 干净断开) → T3 (Client 断连) → T3.5 (持久化验证) → T2 (Server 强制 kill)
```

## Day 2 执行顺序

```
T4 (心跳超时) → T7 (认证失败) → T6 (多Agent重连) → T5 (离线队列满) → T8 (连接池满) → T9 (极端负载)
```

## Day 3

```
T10 (注册制，合并 P3-3) + 修复回归
```
