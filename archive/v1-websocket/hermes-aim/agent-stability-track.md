# AIM Agent 进程稳定性跟踪

> 自动跟踪周期: 2026-06-07 至 2026-06-14
> 每日检查: 10:00, 18:00

## 2026-06-07 10:00
- ZS0002 (吉量): PID=26116, state=running, exit_code=0 (未退出过), last_signal=none, runs=1, 已运行 07:31:06
- ZS0001 (呱呱): PID=35653, state=running, exit_code=-15 (上次被 SIGTERM 终止), last_signal=Terminated:15, runs=4
- Server (node.py): PID=25609, running, 已运行 07:35:40
- 日志分析:
  - stdout 最后20行正常，包含正常消息收发和心跳
  - stderr 正常（心跳 PING/PONG 调试日志）
  - 发现 `delivery_failed` 事件多次（向 ZS0001 投递失败，max_retries_exceeded）—— 属于通信问题而非进程崩溃
  - stdout 存在多条 "Timeout — denying command" 记录（对 ZS0001 的指令超时拒绝）
  - stderr 有1条旧ERROR: "WebSocket未连接，无法发送"（非今日，属历史遗留）
- 结论: 三个进程均在运行，无异常崩溃

## 2026-06-08 10:00
- ZS0002 (吉量): PID=70095, state=running, exit_code=0 (当前正常), last_signal=Terminated:15, runs=6 (上次runs=1, +5次重启), 已运行 ~0:01
- ZS0001 (呱呱): PID=70225, state=running, exit_code=-15 (SIGTERM 终止), last_signal=Terminated:15, runs=10 (上次runs=4, +6次重启)
- Server (node.py): PID=70077, running, 已运行 ~0:02 (上次PID=25609)
- 日志分析:
  - 三个进程均在 10:15~10:16 同时重启（PID全部变更）
  - ZS0002 stderr 最后20行正常: 包含正常消息收发、心跳、状态反馈、ACK
  - ZS0001 stderr 最后20行正常: 包含正常心跳、PING/PONG、消息接收
  - stdout 最后20行正常: 包含心跳、presence（呱呱上线/下线）、status_update 等
  - 系统自 2026-06-06 11:26 启动后未重启（uptime 1day 22h）
  - DiagnosticReports 中无 aim/python crash 记录
  - 所有进程均为 SIGTERM 正常退出后重启（非 crash）
  - 推测原因: 可能为 launchd keepalive 策略触发重启，或人为手动重启
- 结论: 进程当前均在运行，无异常崩溃。但三进程在检查间隔内（约24h）同时重启，需持续关注

## 2026-06-08 18:00
- ZS0002 (吉量): PID=19841, state=running, exit_code=0 (未退出过), runs=1, 已运行 02:41
- ZS0001 (呱呱): PID=19801, state=running, exit_code=0 (未退出过), runs=1, 已运行 02:42
- Server (node.py): PID=48643, running, 已运行 0:17
- ZS0003 (额外): PID=41148, running, 框架=qwenpaw, 已运行 0:58
- 日志分析:
  - stdout 最后20行: 正常心跳消息（heartbeat/heartbeat_ack），无异常
  - stderr 最后30行: 正常心跳 PING/PONG + heartbeat 收发，无 ERROR/WARNING
  - 无 crash report 记录
- PID 变更分析 (与 10:00 对比):
  - ZS0002: 70095 → 19841 (变更，15:22 重启)
  - ZS0001: 70225 → 19801 (变更，15:21 重启)
  - Server: 70077 → 48643 (变更，17:46 重启)
  - 三进程再次在检查间隔内全部重启（第二次出现集体重启）
  - 启动时间分析: ZS0001(15:21) → ZS0002(15:22) 几乎同时；Server(17:46) 晚约2.5h，可能为独立重启
  - 均非 crash（无 diagnostic report，无 SIGABRT/SIGSEGV）
- 结论: 进程当前均在运行，无异常崩溃。但 ZS0001/ZS0002 在 10:00~15:22 之间再次集体重启（第二次观察到此模式），Server 在 17:46 单独重启。持续关注重启模式是否与 launchd keepalive 或系统资源回收相关。

## 2026-06-09 10:00
- ZS0002 (吉量): PID=90547, state=running, exit_code=-15 (SIGTERM), last_signal=Terminated:15, runs=29 (上次runs=1 -> 激增28次重启), 已运行 ~41分
- ZS0001 (呱呱): PID=44981, state=running, exit_code=-15 (SIGTERM), last_signal=Terminated:15, runs=36 (上次runs=1 -> 激增35次重启), 已运行 ~7分
- Server (node.py): PID=49188, state=running, exit_code=0 (未退出过), runs=1, 已运行 ~4分
- 日志分析:
  - ZS0002 stderr 最后30行: 包含正常群聊消息收发，多条 "NATS 未连接，跳过心跳" WARNING，多条 "AI 调用未返回内容: 超时 (120秒)" WARNING，1条 Handler error (JSON解析失败)，以及 ConnectionResetError (NATS 连接被对端重置)
  - ZS0001 stderr 最后30行: 正常 WebSocket 连接握手，有 keepalive ping 超时被关闭的记录
  - Server stderr 最后30行: ZS0002:main / ZS0005:main / ZS0001:main 连接均在 10:29:58 断开（grace_period 触发清理），有一条 websocket handshake failed (ConnectionClosedError)
  - stdout 最后20行: 包含正常心跳收发，最后一条显示 "服务端关闭: server_restart — 即将自动重连"
- PID 变更分析 (与 2026-06-08 18:00 对比):
  - ZS0002: 19841 -> 90547 (变更，09:47 启动)
  - ZS0001: 19801 -> 44981 (变更，10:22 启动)
  - Server: 48643 -> 49188 (变更，10:25 启动)
  - 三进程再次在12h内全部重启（2026-06-08 15:22 -> 2026-06-09 09:47，约18.5h间隔）
  - ZS0002 runs=29, ZS0001 runs=36 -- 说明在过去18.5h内各自被重启了数十次，每次都是 SIGTERM 正常终止
  - 高重启频率可能与 keepalive 策略有关（post-crash 或 post-SIGTERM 立即重新启动）
  - 系统 uptime=2d23h，未整机重启
  - DiagnosticReports 中无 aim/python crash 记录
- 结论: 进程当前均在运行，无 crash。但 ZS0001 和 ZS0002 出现极高频率的 SIGTERM 重启（runs 从个位数暴涨至 29/36），且三进程再次集体 PID 变更。建议排查 launchd keepalive 策略或 NATS 连接稳定性问题。
