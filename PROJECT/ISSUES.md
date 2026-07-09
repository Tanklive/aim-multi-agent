# AIM 项目统一问题清单

> 维护规则：滚动更新，解决即归档（保留记录），新增追加到对应区。
> 所有散布的 `issues/ISSUE-*.md` 和 `bugs/BUG-*.md` 在此汇总，旧文件只读保留。
> 最后更新：2026-06-20 23:56

---

## 🔴 开放（P0 — 阻塞级）

| ID | 类别 | 问题 | 严重度 | 责任方 | 来源 |
|----|------|------|:------:|--------|------|
| U-001 | Scheduler | StallWatchdog 自愈无效——触发复位后 dispatch_loop 未重新投递 | 🔴 | 呱呱 | 620-01 |
| U-002 | 进程 | Letta TUI session 占用导致 adapter process 超时 exit 1 | ✅ 已解决 | 火鸡儿/呱呱 | 620-02 |
| U-003 | 队列 | ZS0003 queue.jsonl 积压旧消息，Scheduler 未消费 | 🔴 | 呱呱/火鸡儿 | 620-03 |
| U-004 | 架构 | 单点 Runtime 故障导致全群通讯中断（三方 aim-client 同时陷入自愈循环） | ✅ 已解决 | 三方 | 620-08 |
| U-005 | adapter | 幻听串扰——dispatch 消费积压旧消息，adapter 编造对话（如「🐸 同意就好」「🎉 辛苦呱呱」），形成自激振荡 | 🔴 | 呱呱 | 620-06-20 |
| U-006 | observer | aim-watch 显示乱：detail 截断（完整评审意见不可读）+ 无时间戳/角色上下文 + 新旧消息混淆 | 🔴 | 呱呱 | 620-06-20 |
| U-007 | 群聊 | ZS0003 群聊消息丢失——DM 正常投递，群聊 grp_trio 漏收 | ✅ 已恢复 | — | 620-06-20 |
| U-008 | 版本 | **VERSION 不统一** — VERSION 文件 1.3.2 ≠ SDK 1.3.1 ≠ CHANGELOG 1.3.3 | ✅ 已解决 | 呱呱 | P0-P2审计 23:35 |
| U-009 | 异构 | **ZS0003 Python 3.14 ≠ ZS0001/2 3.13** — GIL 移除差异，并发行为风险 | ✅ 已解决 | 火鸡儿 | P0-P2审计 23:35 |
| U-010 | 版本 | **ZS0001 adapter.sh 无版本注释** — 三端唯一没标，违反 VERSION-STANDARD | ✅ 已解决 | 呱呱 | P0-P2审计 23:35 |
| U-011 | 代码 | **双循环检测器未合并** — 吉量 _is_confirm_loop + 呱呱 ACK skip，重叠不完整 | 🔴 | 吉量评审/呱呱合并 | P0-P2审计 23:35 |
| U-012 | 队列 | **队列重新积压** — 清完1h后 59/57/23。去重只拦 dispatch，入队仍在涨 | ✅ 已解决 | 呱呱 | P0-P2审计 23:35 |

## 🟡 开放（P1 — 需对齐/改进）

| ID | 类别 | 问题 | 严重度 | 责任方 | 来源 |
|----|------|------|:------:|--------|------|
| U-101 | 协议 | exit code 2 语义三方不一致（DegradeError 触发条件歧义） | 🟡 | 三方 | 619-PLUS P1-1 |
| U-102 | adapter | adapter.sh exit 3/4 是否被 main.py 正确解读 | 🟡 | 呱呱 | 620-07 |
| U-103 | 版本 | NOTICE 1.3.0 三项未做 — 运行时版本检查/MIN_SDK_VERSION拒绝/adapter info标准版 | 🟡 | 呱呱 | P0-P2审计 23:35 |
| U-104 | 部署 | deploy-verify 仅检查文件存在，不做功能测试（E2E smoke test） | 🟡 | 呱呱 | P0-P2审计 23:35 |
| U-105 | 协议 | PROTOCOL_VERSION=1.0 落后协议文档 v1.2（含已读回执） | ✅ 已解决 | 呱呱 | P0-P2审计 23:35 |
| U-106 | adapter | adapter.sh 版本分裂未根本解决 — shared v2.0 (2026-06-24), ZS0001(已补v1.5+), ZS0002 deploy v1.3(待补→v2.0), ZS0003(v2.1) | 🟡 🔄 呱呱(ZS0001已补) | 火鸡儿/吉量(待补) | P0-P2审计 23:35 |
| U-107 | 运维 | healthd 查询异常 — 679条事件但 agent 状态查询失败 | ✅ 误报 | — | P0-P2审计 23:35 |

## 🟢 低优（P2 — 优化/清理）

| ID | 类别 | 问题 | 严重度 | 责任方 | 来源 |
|----|------|------|:------:|--------|------|
| U-201 | 部署 | shared/aim 旧架构残留清理 | 🟢 | 呱呱 | 619-PLUS P1-2 |
| U-202 | BUG | aim-observe/aim-watch 未带 Token 连接（低优，不影响功能） | 🟢 | 吉量 | BUG-001 |
| U-203 | 运维 | observer.jsonl 轮转可能未生效 — 833行/170KB，cron 未到时间 | 🟢 | 呱呱 | P0-P2审计 23:35 |
| U-204 | 代码 | main.py 仅 1 个 TODO — Phase 2 AIMTask lifecycle tracking | 🟢 | 呱呱 | P0-P2审计 23:35 |
| U-205 | 部署 | JetStream consumer 旧配置未清理 — retention/max_deliver 未生效 | 🟢 | 吉量 | P0-P2审计 23:35 |
| U-206 | 测试 | 无部署后自动化 E2E 测试 — 每次靠人工发消息验证 | 🟢 | 呱呱 | P0-P2审计 23:35 |

---

## ✅ 已关闭（619 清单 — 全部 26 项）

| ID | 问题 | 状态 | 责任人 | 来源 |
|----|------|:----:|--------|------|
| 619-01 | config.json 字段不一致 | ✅ | 呱呱 | 619 |
| 619-02 | auth.chain 为 null | ✅ | 呱呱 | 619 |
| 619-03 | adapter.sh 路径分散 | ✅ | 三方 | 619 |
| 619-04 | Queue 持久化合写 | ✅ | 呱呱 | 619 |
| 619-05 | ~/.hermes/aim/ 163MB 远古副本 | ✅ | 吉量 | 619 |
| 619-06 | main.py banner 版本号写死 | ✅ | 呱呱 | 619 |
| 619-07 | PROTOCOL_VERSION 校验 | ✅ | 呱呱 | 619 |
| 619-08 | Registry plist 缺 SuccessfulExit | ✅ | 呱呱 | 619 |
| 619-09 | main.py 改后无重启 | ✅ | 呱呱 | 619 |
| 619-10 | NOTICE 无人实测 | ✅ | 三方 | 619 |
| 619-11 | adapter 异常无告警 | ✅ | 呱呱 | 619 |
| 619-12 | aim_send_nats.py 无 owner | ✅ | 呱呱 | 619 |
| 619-13 | 老 SDK import 风险 | ✅ | 火鸡儿 | 619 |
| 619-14 | 心跳定义不一致 | ✅ | 呱呱 | 619 |
| 619-15 | creds 泄露风险 | ✅ | 呱呱 | 619 |
| 619-16 | adapter reply 格式 | ✅ | 吉量 | 619 |
| 619-17 | exit code 4 级语义 | ✅ | 呱呱 | 619 |
| 619-18 | 群聊回复风暴 | ✅ | 呱呱 | 619 |
| 619-19 | execution_model 超时 | ✅ | 火鸡儿 | 619 |
| P0-1 | dispatch_loop 死锁 | ✅ | 呱呱 | 619-PLUS |
| P0-2 | ZS0003 config 落后 | ✅ | 呱呱 | 619-PLUS |
| P0-3 | ZS0002 config 缺字段 | ✅ | 呱呱/吉量 | 619-PLUS |
| P0-4 | SDK 版本分叉 | ✅ | 呱呱 | 619-PLUS |
| P0-5 | deploy.sh 不存在 | ✅ | 呱呱 | 619-PLUS |
| P1-3 | nats-agent.py 消失 | ✅ | 呱呱 | 619-PLUS |

---

## ✅ 已关闭（620 清单）

| ID | 问题 | 状态 | 责任人 | 来源 |
|----|------|:----:|--------|------|
| 620-04 | Letta health 探针假阴性 | ✅ | 火鸡儿 | 620 |
| 620-05 | adapter v1.7 升级 | ✅ | 火鸡儿 | 620 |

---

## 📅 已关闭（2026-06-20 晚间 — 三项重大修复）

| ID | 问题 | 状态 | 责任人 | 来源 |
|----|------|:----:|--------|------|
| N-001 | 监控事件耗 token | ✅ | 呱呱 | healthd 独立通道 |
| N-002 | ACK 死循环（收到→收到→…） | ✅ | 呱呱 | delivery confirm + ACK skip |
| N-003 | adapter trim handler 缺失（StallWatchdog 根因） | ✅ | 三方 | 三端 adapter trim |
| N-004 | 身份错发（default sender=ZS0003） | ✅ | 呱呱 | aim_send_nats.py 修复 |
| N-005 | observer/agent 日志无轮转（observer 39k/4.3MB + ZS0002 717KB） | ✅ | 呱呱 | aim_logrotate.sh + cron 每天 03:00 |
| N-006 | aim-observations 28k+ 积压 | ✅ | 呱呱 | 清理完毕，目录为空 |

---

## 🗄️ 归档说明

- `shared/aim/issues/ISSUE-*.md` → 历史只读，不再更新
- `shared/aim/bugs/BUG-*.md` → 历史只读，不再更新
- 所有问题在此文件统一维护
- 关闭项保留记录，不删除
- 每条新发现问题追加到对应区，不新建文件
| 2026-06-20 22:41 | ZS0003 | 🟢 P2 | Worker E2E 端到端测试 | 验证链路: ZS0003 → NATS aim.issue.update → Worker → ISSUES.md append → git commit 测试时间: 2026年 6月20日 星期六 22时41分28秒 CST |
| 2026-06-20 22:43 | ZS0002 | 🔴 P0 | ZS0001 exit=-9 | adapter 返回未定义退出码-9，Scheduler 无法归类，消息无限重试，StallWatchdog 持续自愈失败 | 责任: 呱呱 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🔴 P0 | ZS0003 health probe 假阴性 | health 探针报 letta CLI 不可用(exit=3)，但 adapter 实际健康——两条健康检查路径不一致 | 责任: 火鸡儿 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟡 P1 | main.py 部署副本不同步 | shared 1148行 vs 部署 1082行，差66行。已修复同步。需加固 deploy-verify 检查 | 责任: 吉量 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟡 P1 | adapter.sh 版本混乱 | 5个adapter 5个版本(v1.3~v1.8.2)，无统一版本规范 | 责任: 三方 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟡 P1 | 确认循环死锁 | 吉量-火鸡儿群聊确认循环3小时，578条积压。已加免LLM跳过 | 责任: 吉量 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟡 P1 | 告警风暴 | ZS0003 连续19次degrade_storm，CRITICAL冷却120s偏短 | 责任: 火鸡儿 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟢 P2 | 社交结束语耗token | 晚安/明天见/辛苦了等纯礼貌用语走LLM。已加SOCIAL_CLOSE跳过 | 责任: 吉量 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟢 P2 | observer.jsonl 无轮转 | 38K行4.2MB无限增长。已升级按天+100MB双轮转7天保留 | 责任: 吉量 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟢 P2 | JetStream consumer 旧配置 | 旧consumer需重建生效retention=max_age=7d/max_deliver=5 | 责任: 吉量 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟢 P2 | 日志目录分散 | 4个不同路径。已出~/.aim/logs/README.md统一规范 | 责任: 吉量 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟢 P2 | deploy-verify 缺validator功能测试 | 仅检查文件存在，不测试validate_envelope()实际拦截 | 责任: 三方 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0002 | 🟢 P2 | 确认循环检测器冗余 | shared main.py 有两套检测器(吉量_is_confirm_loop+呱呱纯确认消息)，待合并 | 责任: 吉量/呱呱 | 来源: 620-5轮审计 |
| 2026-06-20 22:43 | ZS0003 | 🔴 P0 | POST-01: health _detect_letta() which 回退绕过 CLI 校验 | LETTA_BIN=/tmp/fake 时仍返回 healthy, 根因: _detect_letta() 回退到 which letta 找到系统版本 修复: 去 which 兜底, LETTA_BIN 必须精确从 config.json 解析, 不存在直接 exit 3 已修: adapter.sh v2.0.1 |
| 2026-06-20 22:43 | ZS0003 | 🟡 P1 | POST-02: dispatch conv 冷启动首次超时 | 首次 --conversation 需要加载 agent (15-20s) > 15s PROBE_TIMEOUT 修复: RC≠0 & RC≠124 时自动重试一次, 第二次 agent 已缓存秒级 已修: adapter.sh v2.0.1 |
| 2026-06-20 22:43 | ZS0003 | 🟡 P1 | POST-06: adapter trim 调用卡住/被降级为 no-op | 根因: trim 调 letta messages list 在主 session 占用时超时, 被迫降级为 no-op, 导致 dispatch conv 消息历史不断累积 修复: trim 改为直接 truncate messages.jsonl (不调 letta CLI) 已修: adapter.sh v2.1 |
| 2026-06-20 22:43 | ZS0003 | 🔴 P0 | recover 模式走 LLM 消耗 token | 每次 recover 调 letta -p "ping" → 12K+ tokens, 违反 AIM 运维零 Token 铁律 修复: recover 改为 pgrep + memfs 磁盘探活, 零 token 秒级返回 已修: adapter.sh v2.1 |
| 2026-06-20 22:43 | ZS0003 | 🟡 P1 | _on_grp/dispatch 幻听串扰 — 消息历史污染回复质量 | dispatch conv messages.jsonl 中 "👂 收到" 成为高频模式, 污染 Letta 后续回复 → 形成回应循环 修复: trim truncate messages.jsonl + 首刀手动清空历史 已修: adapter.sh v2.1 + 22:20 手动清理 |
| 2026-06-20 22:43 | ZS0003 | 🟡 P1 | 双会话隔离从未实际部署 (v1.5→v1.8 注释骗了自己) | v1.5 注释写了 --conversation 方案但代码从未传参, 4个版本都在裸 letta -p → 新增 conv 目录 修复: v2.0 ensure_dispatch_conv() + --conversation 真正隔离 已修: adapter.sh v2.0 |
| 2026-06-20 22:43 | ZS0003 | 🟢 P2 | conv 清理 cron 未排除 dispatch conv | cleanup cron 每天 4 点可能误删 dispatch conv (local-conv-1422) 修复: cleanup-conversations.sh 解码目录名匹配 conversation:local-conv-1422 跳过不删 已修: cleanup-conversations.sh v1.0 |
| 2026-06-20 23:18 | ZS0002 | 🔴 P0 | dispatch 无幂等去重 → adapter 幻觉 | StallWatchdog重复投递+dispatch无PROCESSED_IDS→同消息多次进adapter→基于过期上下文编造回复。业界方案:NATS/Kafka/AWS/OpenAI全在传输层做message ID去重。修复:dispatch入口加PROCESSED_IDS set(~7行)。详细分析见PROJECT/adapter-hallucination-analysis.md |
|| 2026-06-24 22:10 | ZS0002 | 🟡 P1 | aim_install.py 未集成 services.api 标准接口 | API Server 标准接口已部署验证通过，逻辑通路就绪。但 aim_install.py 安装时不会自动生成 services.api 字段、不会提示设置 API_SERVER_KEY 环境变量，用户仍需手动填 config.json。待 AIM Client 稳定后集成。详见 ~/shared/aim/规划/api-server-key-standard.md |
|| 2026-06-24 | ZS0002 | 🟡 P1 | **adapter deployment sync** — shared v2.0 (services.api) vs deploy v1.3 分裂 | 共享目录 ~/shared/aim/adapters/hermes/adapter.sh 已升级到 v2.0（services.api 架构，64行），但部署目录 ~/.aim/adapters/hermes/adapter.sh 仍停在 v1.3（146行旧版）。根因与 U-106 相同：shared 更新后 deploy 未同步。呱呱标记为 bug，后续统一 deploy 同步机制。当前暂不修，避免干扰第三轮测试。 |
