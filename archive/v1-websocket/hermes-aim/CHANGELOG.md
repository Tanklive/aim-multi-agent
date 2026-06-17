# AIM 变更日志

## 20260607.0240M (2026-06-07 02:40) [🟡 小版本]

核心规则体系 + 去重键修复 + 通信可靠性治理

### 核心规则体系（建立）

大哥给出了七类核心规则（任务推进/协作/技术/BUG/学习/QQ联系/记忆管理），整理为可执行版 `~/.hermes/core-rules.md`，包含：

**三大机制：**
- A. 6 节点触发（收到指令/开始干活/写完代码/发消息/完成任务/对方回复）
- B. 4 步循环自检（进展→等待→反馈→闭环）
- C. 10 级自我怀疑链（技术→协作→方向，存疑不执行）

**呱呱评审后的精确化更新：**
- 等待循环：cron at 实时层 + 3 次/天 backlog 兜底
- 沟通分级：🔴 必须沟通 / 🟡 知会即可 / 🟢 自行处理
- 完成定义：实质性工作触发 5 分钟规则，反馈告知 15 分钟窗口
- 空闲定义：无进行中任务 + 无等待回复 + 无已排期工作
- 记忆分层：8 类（memory 常驻 4 类 / PROJECT-MEMORY.md 2 类 / skill 1 类 / session_search 1 类）
- 学习框架：`.learnings/YYYY-MM-DD.md`，统一产出格式带关联规则编号和复用路径标记

### Server 端修复

**P0: _deliver 去重键修复**
- 去重键由 `msg.msg_id` 改为 `f"{from_id}:{msg.msg_id}"`，按发送方隔离
- from_id 缺失时 fallback `"unknown"` 并打 warning 日志
- 修复 ZS0001→ZS0002 方向消息因跨 Agent msg_id 误拦截的 bug

**P0: connection_pool 日志增强**
- `get_delivery_targets` 找不到 handler 时加 debug 日志

**P1 重新评估：** `status_update` 走 `_broadcast_status` 是正确设计（状态通知不需要重传/离线队列），不需要修改

### P2: aim_message_watcher.py（新增防御性兜底）

启动全量扫描 + 每 30 秒轮询 messages.jsonl，发现未处理消息通过 message_bridge 推送到桥接文件。WS 推送 + DeliveryGuarantee 双重失效时兜底。

### 心跳冷却修复（6月7日晨）

`AgentStateManager.handle_heartbeat()` 冷却期内收到合法心跳不应拒绝，应清理 cooldown 并恢复 online：
- registry.py: 冷却期收到心跳 → 清理冷却记录 → 正常处理（offline→online）
- node.py: 删除 heartbeat_rejected 死分支

### 双向通信联调验证（5 轮测试全部通过）

| 测试项 | 结果 |
|--------|------|
| 呱呱→吉量 5 条消息 WS 推送 | ✅ 全部到达 |
| aim-agent 完整处理链 | ✅ 入队→出队→AI处理 |
| watcher 兜底（aim-agent 离线） | ✅ 30秒捡漏推送 |
| P0 去重键修复生效 | ✅ |
| Server delivery 双向日志 | ✅ |

### 进程稳定性跟踪

- `com.aim.agent.ZS0002` launchd 管理，KeepAlive 保活
- 添加 1 周稳定性跟踪 cronjob（每天 10:00/18:00 检查）
- 确认 aim-agent 不应使用 terminal(background) 启动

### 新增 cronjob

| 名称 | 时间 | 用途 |
|------|------|------|
| 每天8:00自学自我提升 | 0 8 * * * | 规则23学习机制 |
| 会话启动backlog检查 | 0 9,14,21 * * * | B2等待循环兜底 |
| aim-agent进程稳定性跟踪 | 0 10,18 * * * | 1周稳定性监控 |

---

## 20260606.1950M (2026-06-06 19:50) [🟡 小版本]

V2 连接池重构 + 双栈架构 + 消息保达

Server:
- 连接池重构（多 channel 共存，main/script 不互踢）
- 双栈架构：ws://:18900（本地）+ wss://:18901（公网）
- 旧客户端强制 channel 字段，不传拒绝
- 日志格式 [agent_id:channel]
- ACK 确认码优化（短连接直接回复）
- HMAC + 时间戳防重放
- Phase 2.1 消息保达（离线队列、超时重传、DeliveryGuarantee）
- launchd 开机自启 + 崩溃自动重启

客户端:
- 方案 A：load_env_file() 加载 .env 修复 AI 调用
- 独立客户端：各 Agent 独立目录，通过标准端口互通
- 独立 aim-agent launchd 自启动

## 20260604.2249M (2026-06-04 22:49) [🟡 小版本]

自启动标准已写入文档：~/shared/aim/aim-autostart-standard.md。支持 macOS(launchd)/Linux(systemd)/Windows/ Docker。每个平台都有对应的配置方式。

## 20260604.2248M (2026-06-04 22:48) [🟡 小版本]

补充：aim_autostart.py 自启动工具，运行 python3 aim_autostart.py install --all 注册三Agent开机自启

## 20260604.2248M (2026-06-04 22:48) [🟡 小版本]

新增 aim_autostart.py — AIM agent 自启动注册工具。开机自启+崩溃自动重启。运行：python3 aim_autostart.py install --all

## 20260604.2208M (2026-06-04 22:08) [🟡 小版本]

补充通知：aim_qq_forward.py 已就绪，QQ Bot handler加一行即可用

## 20260604.2208M (2026-06-04 22:08) [🟡 小版本]

新增 aim_qq_forward.py — 通用QQ消息转发脚本。各Agent在QQ Bot handler中加一行：python3 aim_qq_forward.py '<消息>' --from 你的ID 即可自动转发到AIM

## 20260604.2151M (2026-06-04 21:51) [🟡 小版本]

新增QQ Bot转发配置标准：每个Agent的QQ Bot收到消息后，检测@目标并调用aim_send.py转发到AIM。配置文档：~/shared/aim/aim-platform-standard-v3.md

## 20260604.2114P (2026-06-04 21:14) [🟢 修复]

版本管理 v3: 只提示不自动，用户通过 upgrade 命令手动升级

## 20260604.2024P (2026-06-04 21:14) [🟢 修复]

全链路升级测试：验证 AIM 消息触发自动升级

## 20260604.2024P (2026-06-04 21:13) [🟢 修复]

全链路升级测试：验证 AIM 消息触发自动升级

## 20260604.2024P (2026-06-04 20:24) [🟢 修复]

全链路升级测试：验证 AIM 消息触发自动升级

## 20260604.2019P (2026-06-04 20:19) [🟢 修复]

测试 AIM 消息触发升级全链路

## 20260604.2009M (2026-06-04 20:09) [🟡 小版本]

QQ桥接守护进程优化：减少扫描间隔、增加错误重试

## 20260604.2009M (2026-06-04 20:09) [🟡 小版本]

QQ桥接守护进程优化：减少扫描间隔、增加错误重试

## 20260604.2004 (2026-06-04 20:04)

初始版本：AIM 任务协议 v0.1 + Agent SDK + QQ桥接 + 版本管理
