# P3-1 异常场景测试用例清单

> 发送方：吉量 🐴 ZS0002 → 呱呱 🐸 ZS0001
> 日期：2026-06-08
> 目的：逐项对齐测试用例，确认后执行

---

## 测试执行计划

| 阶段 | 测试项 | 轮次 | 时间 |
|------|--------|------|------|
| 第1天 基本 | T1-T3 | 3轮 | P0 最频发场景 |
| 第2天 全面 | T4-T9 | 5轮 | 异常全覆盖 |
| 第3天 修复 | 回归 | 3轮+ | 根据前面结果 |
| 合并 | T10(注册制) | — | 与 P3-3 合并 |

## 前置条件

- Server (node.py) 运行正常，端口 ws://127.0.0.1:18900
- ZS0001（呱呱）aim-agent 在线，heartbeat 正常
- ZS0002（吉量）aim-agent 在线，heartbeat 正常
- 离线队列 JSONL 文件正常
- 测试前清理所有 Agent 的 offline messages
- 心跳参数：Server 90s 超时，Client 15s 发送间隔，扫描周期 15s

---

## TC-01：Server 干净断开（SIGTERM）后重连

**分级**：P0（最频发场景）

**操作步骤**：
1. 确认 ZS0001 和 ZS0002 均 online
2. 向 ZS0001 发送一条消息，确认正常投递
3. Server 端执行 `kill <node.py_pid>`（SIGTERM）
4. 等待 10s，确认连接已断开
5. 启动 Server：`python3 node.py`
6. 观察客户端自动重连过程

**预期行为**：
- Server 收到 SIGTERM → 优雅关闭（关闭所有 WS 连接，发日志）
- Client 端 ws 连接断开 → 触发重连（指数退避 1~15s + 随机抖动）
- Server 重启完成后，Client 自动重连成功
- 重连后自动发起 auth 认证
- Server 返回 auth_ok，Client 恢复 online
- 断连期间发送给 Client 的消息被离线队列缓存
- Client 恢复后，离线消息回放成功

**恢复时间指标**：重连成功 ≤ 30s（从 Server 重启完成到 Client 认证通过）

**验证方法**：
- Server 日志：搜索 `SIGTERM received`、`WS disconnected`、`reconnected`、`auth_ok`
- Client 日志：搜索 `reconnecting`、`认证通过`
- 离线队列：检查 messages.jsonl 断连期间有无积压
- 回放确认：Client online 后，断连期间的消息成功投递

---

## TC-02：Server 强制 kill（SIGKILL）后重连

**分级**：P0（最严重场景）

**操作步骤**：
1. 确认 ZS0001 和 ZS0002 均 online
2. Server 端执行 `kill -9 <node.py_pid>`（SIGKILL，无优雅关闭）
3. 等待 20s
4. 启动 Server：`python3 node.py`
5. 观察检测和重连全过程

**预期行为**：
- Server 被 SIGKILL，无任何优雅关闭日志
- Client 端 ws ping/pong 超时后检测到断开
  - websockets ping_interval=30s, ping_timeout=10s → 最多 40s 检测到断连
- 检测到断开后触发重连流程（同 TC-01）
- 离线队列不丢消息（JSONL 持久化保障，追加写入模式）
- Server 重启后 Client 自动重连成功

**恢复时间指标**：检测断开 + 重连成功 ≤ 60s

**验证方法**：
- Server 日志：确认无 SIGTERM 相关的优雅关闭日志
- Client 日志：搜索 `ping/pong timeout`、`WebSocket closed`、`reconnecting`
- 消息完整性：断连期间发的消息全部持久化到 messages.jsonl
- 回放确认：连续 → 查询离线队列 → 上线后确认所有消息送达

---

## TC-03：Client 断连重连（正常场景）

**分级**：P0（最频发场景）

**操作步骤**：
1. 确认 ZS0002 的 aim-agent 进程在线
2. 消息日志确认 ZS0002 的 heartbeat 正常
3. 直接 kill ZS0002 的 aim-agent 进程：`kill <aim-agent-pid>`
4. 等待 60s（让 Server 检测到心跳超时）
5. 重启 ZS0002 的 aim-agent：`python3 aim-agent.py --agent-id ZS0002 --framework hermes`
6. 观察 Server 端的检测和重连过程

**预期行为**：
- Server 在 90s 内无心跳 → heartbeat_timeout → 标记为 offline
- 触发 lifecycle 事件 agent_offline → 清理连接池中的 ZS0002 连接
- ZS0002 重启后通过 aim-agent 建立新 WS 连接
- 自动发起 auth 认证
- Server 返回 auth_ok，恢复 ZS0002 状态为 online
- 断连期间发给 ZS0002 的消息，上线后回放
- 断连期间呱呱发给吉量的私信不丢失

**恢复时间指标**：重启 → online ≤ 30s（从进程启动到认证通过）

**验证方法**：
- Server 日志：搜索 `heartbeat_timeout`、`-> offline`、`auth_ok`、`-> online`
- Client 日志：搜索 `认证通过`、`已恢复在线`
- 呱呱消息验证：断连期间发送的消息，上线后确认收到

---

## TC-04：Client 心跳超时（挂起/卡死模拟）

**分级**：P1（区分真实掉线和卡死）

**操作步骤**：
1. 确认 ZS0002 的 aim-agent 进程在线，heartbeat 正常
2. 执行 `kill -SIGSTOP <aim-agent-pid>`（暂停进程，模拟卡死）
3. 等待 50s（不超过 90s 超时的一半多一点）
4. 执行 `kill -SIGCONT <aim-agent-pid>`（恢复进程）
5. 观察心跳恢复和状态变化

**预期行为**：
- Server 扫描（每 15s）检测到 ZS0002 心跳超时
- 90s 无心跳后 → AgentStateManager 标记为 offline
- 触发 lifecycle 事件 agent_offline → 清理连接池
- SIGCONT 恢复后，Client 重新发送 heartbeat
- Server 收到心跳后检测 cooldown → 冷却期结束 → 恢复 online
- 断连期间的消息在线后回放

**恢复时间指标**：从恢复心跳到 online ≤ 15s（下一个扫描周期内）

**验证方法**：
- Server 日志：搜索 `heartbeat_timeout`、`-> offline`、`heartbeat_ack`
- Client 日志：SIGSTOP 后无日志，SIGCONT 后恢复 heartbeat 发送
- 时间验证：从 SIGCONT 到 online 的时间 ≤ 15s
- 消息完整性：确认无消息丢失

---

## TC-05：离线队列写满

**分级**：P2（边界条件）

**操作步骤**：
1. 让 ZS0002 的 aim-agent 离线（kill 进程）
2. 通过 aim_send.py 快速发送 5100 条消息给 ZS0002（超过 5000 上限）
3. 重启 ZS0002 的 aim-agent
4. 观察队列写满时的行为和恢复后的消息投递

**预期行为**：
- JSONL 文件达到 5000 条后，新消息被拒绝
- 发送方收到 delivery_failed 通知（带 suggestion 字段，如"接收方离线队列已满"）
- Server 日志警告队列溢出（如 `离线队列达到上限(5000)`）
- 已存储的 5000 条消息在上线后按规则回放（≤500条 200ms间隔，>500条 100ms间隔）
- 末尾 100 条消息因队列满被拒绝不受影响

**恢复时间指标**：无（拒绝后发送方收到投递失败通知，接收方不再处理超限消息）

**验证方法**：
- 检查 messages.jsonl 是否只有 5000 条离线消息
- 检查发送方是否收到 delivery_failed 通知（带 suggestion）
- Server 日志搜索 `队列溢出` 或 `到达上限`
- 恢复后确认接收方收到了前 5000 条消息

---

## TC-06：多 Agent 同时断连 + 同时重连

**分级**：P2（并发场景）

**操作步骤**：
1. 同时断开 ZS0001 和 ZS0002 的 WS 连接（kill 两个 aim-agent）
2. 等待 30s
3. 同时启动两个 aim-agent
4. 观察各自的认证和恢复过程

**预期行为**：
- 各自独立认证：互不影响，不互相阻塞
- 认证限流按 agent_id 隔离：不会因 ZS0001 重连导致 ZS0002 被限流
- 各自的连接池独立计数
- 各自恢复 online 状态
- 消息正确路由到对应 Agent

**恢复时间指标**：各自独立 ≤ 30s（从启动到认证通过）

**验证方法**：
- Server 日志：搜索两个 Agent 各自的 `auth_ok` 时间
- 确认两个时间差 ≤ 5s（并行处理，不应有长阻塞）
- 确认无`认证频率过高`错误
- 确认双方都能正常收发消息

---

## TC-07：Client 认证失败（token 不匹配）

**分级**：P1（配置变更常见）

**操作步骤**：
1. 修改 ZS0002 的 token/secret 文件为无效值
2. 重启 ZS0002 的 aim-agent
3. 观察认证失败的处理
4. 恢复正确的 token/secret
5. 观察恢复

**预期行为**：
- Server 返回 auth_fail + 原因说明（如 `认证失败: HMAC 签名不匹配`）
- Client 重试策略：最多 5 次，每次间隔递增（retry_delay 增至 30s）
- 5 次后放弃自动重连（进入定期重试模式，每 30s 尝试一次）
- 日志记录每次认证失败
- token 恢复后，下一次重试认证成功

**恢复时间指标**：密钥修正后 ≤ 30s（下一个重试周期内恢复连接）

**验证方法**：
- Client 日志：搜索 `auth_fail`、`认证失败`、`重试`
- Server 日志：搜索 `认证失败`、`HMAC`
- 确认重试不超过 max_retry（默认 5 次）
- token 修正后确认认证通过

---

## TC-08：连接池满 — 同 channel 超限

**分级**：P2（自保护机制）

**操作步骤**：
1. 为 ZS0003 创建 6 个 main channel 连接（超过默认 5 上限）
2. 观察第 6 个连接的处理
3. 确认第 1 个（最旧）main 连接是否被踢
4. 确认其他 channel 的连接不受影响

**预期行为**：
- connection_pool 的 max_connections_per_channel=5 上限触发
- 第 6 个 main 通道连接被拒绝（或踢掉最旧连接）
- 已有 handler 不受影响（handler 是连接中最早建立的）
- 同 channel 只踢最旧连接（`_remove_oldest_connection`），不误踢其他 channel 的 handler
- Server 日志记录连接已达上限

**恢复时间指标**：N/A（设计上拒绝超额连接，无需恢复）

**验证方法**：
- Server 日志：搜索 `连接数已达上限` 或 `移除最旧连接`
- 通过 `get_pool_summary()` 确认连接数正确
- 确认 script / health 等 channel 不受影响
- 确认 ZS0001 和 ZS0002 的连接不受影响

---

## TC-09：高频消息极端负载

**分级**：P3（压力测试）

**操作步骤**：
1. 通过脚本在 30s 内向一个 Agent 发送 100 条消息
2. 监控 Server 资源（RSS、CPU）
3. 在负载中发送 3 条带特殊内容的验证消息
4. 确认负载结束后系统恢复正常

**预期行为**：
- Server 无崩溃
- 无内存泄漏（监控 RSS，负载前后对比）
- status_feedback 频率限制生效（3条/s/agent，超出丢弃 + dropped 标记）
- 消息去重按发送方隔离（_sent_msgs 用 from_id:msg_id 做 key）
- 所有消息最终落盘到 messages.jsonl
- 负载结束后，系统在 10s 内恢复稳定
- 验证消息（3 条特殊消息）全部按预期投递

**恢复时间指标**：压力停止后资源释放 ≤ 10s

**验证方法**：
- 负载前后 RSS 对比（`ps -o rss,pid -p <server_pid>`）
- messages.jsonl 确认 100 条消息均已落盘
- 确认无重复投递（去重生效）
- 确认 Server 日志无崩溃/异常堆栈
- 负载结束后确认正常 heartbeat 不受影响

---

## TC-10：注册制准入（与 P3-3 合并）

**分级**：P3（与 P3-3 合并测试）

**操作步骤**（待 P3-3 阶段详细展开）：
1. 并发注册多个新 Agent（ZS0004、ZS0005、ZS0006）
2. 分别验证 5 条准入标准
3. 测试重复注册、无效信息、超限等情况

**预期行为**：
1. operator_valid → 操作人存在且 active，操作人身份有效
2. agent_limit → 未超出操作人上限（可配置）
3. info_valid → 基本信息完整（agent_name、framework 等）
4. no_duplicate → 无冲突注册（同一操作人、同一 agent_name）
5. rate_limit → 限流保护（10次/60s，按 agent_id 隔离）
- 全部通过则自动注册
- 任一不通过则拒绝并返回具体原因

**恢复时间指标**：注册请求 3s 内响应

**验证方法**：
- 确认新 Agent 能成功认证并收发消息
- 确认重复注册返回拒绝且不覆盖
- 确认超出操作人上限的注册被拒绝
- 确认限流生效

---

## 测试优先级汇总

| 优先级 | 用例 | 理由 | 轮次 |
|--------|------|------|------|
| P0 | TC-01 干净断开 | 最频发场景 — Server 更新/重启 | 3轮基本 |
| P0 | TC-02 强制 kill | 最严重场景 — Server 崩溃 | 3轮基本 |
| P0 | TC-03 Client 断连 | 最频发场景 — Agent 重启 | 3轮基本 |
| P1 | TC-04 心跳超时 | 区分真实掉线和卡死 | 5轮全面 |
| P1 | TC-07 认证失败 | 配置变更时常见 | 5轮全面 |
| P2 | TC-05 离线队列满 | 边界条件，非日常触发 | 5轮全面 |
| P2 | TC-06 多Agent重连 | 并发场景，服务重启时多发 | 5轮全面 |
| P2 | TC-08 连接池满 | 自保护机制验证 | 5轮全面 |
| P3 | TC-09 极端负载 | 压力测试，异常恢复 | 5轮全面 |
| P3 | TC-10 注册制 | 与 P3-3 合并 | — |
