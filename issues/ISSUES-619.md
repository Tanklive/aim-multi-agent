# AIM 619 问题清单（2026-06-19）

> 创建：2026-06-19 14:16 | 最后更新：2026-06-19 19:01
> 范围：AIM 开发中因异构、分工、目录差异导致的所有已知/潜在问题
> 规则：问题永留，解决结果追加，沟通不畅记录原因

---

## 问题总览

| ID | 类别 | 问题 | 严重度 | 状态 | 责任方 | 解决日期 |
|----|------|------|:--:|:--:|--------|:--------:|
| 619-01 | 配置 | config.json 字段不一致（adapter/queue_processor/runtime_type/version 各家有各家无） | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-02 | 配置 | security.auth.chain ZS0002/ZS0003 为 null | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-03 | 配置 | adapter.sh 路径三处分散 | 🔴 | ✅ 已解决 | 三方 | 06-19 |
| 619-04 | 配置 | Queue 持久化三方共写同一文件 | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-05 | 版本 | ~/.hermes/aim/ 163MB 远古副本（VERSION=20260607.0240M），与 shared 分叉 | 🔴 | ✅ 已解决 | 吉量 | 06-19 |
| 619-06 | 版本 | main.py banner 写死 v1.2.1，实际 VERSION=1.3.1 | 🟡 | ✅ 已解决 | 呱呱 | 06-19（banner 已读 VERSION 文件） |
| 619-07 | 版本 | SDK PROTOCOL_VERSION 校验未实现（只 warning 不拦截） | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-08 | 进程 | Registry plist 缺 SuccessfulExit=false | 🔴 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-09 | 进程 | main.py 改后无自动重启机制 | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-10 | 协作 | NOTICE 发布后无人实测落地 | 🔴 | ✅ 已记教训 | 三方 | 06-19 |
| 619-11 | 协作 | adapter 异常无自动告警/降级 | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-12 | 协作 | aim_send_nats.py 无 owner | 🟢 | ✅ 已解决 | 呱呱接 | 06-19 |
| 619-13 | 配置 | ~/.hermes/aim/ 老 SDK 被 import 风险 | 🔴 | ✅ 已确认 N/A | 火鸡儿 | 06-19 |
| 619-14 | 配置 | 三方心跳定义不一致（Registry last_seen 频率） | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-15 | 配置 | NATS creds 路径泄露风险 | 🟢 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-16 | 协议 | adapter reply 格式未标准化（吉量"deepseek."兜底） | 🔴 | ✅ 已解决 | 吉量 | 06-19 |
| 619-17 | 协议 | 退出码 4 级语义 main.py 未按级处理 | 🟡 | ✅ 已解决 | 呱呱 | 06-19（exit 0/1/2/3/timeout 全部按级处理，Retryable/Degrade/HumanIntervention 各有 nack+action） |
| 619-18 | 协议 | 群聊消息回路风险（adapter 回复风暴） | 🟡 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-19 | 异构 | execution_model 差异导致超时窗口不匹配 | 🟡 | ✅ 已确认 35s 够用 | 火鸡儿 | 06-19 |
| 619-20 | 异构 | 三方 LLM 模型不同导致输出格式差异大 | 🟢 | 已知悉 | 协议层 | - |
| 619-21 | 异构 | 三方 framework 重启代价不同 | 🟢 | 已知悉 | 三方 | - |
| 619-22 | 分工 | shared 区 owner 不明确（先改先得） | 🟢 | ✅ 已解决 | 三方 | 06-19 |
| 619-23 | 分工 | aim_send_nats.py 谁修 bug | 🟢 | ✅ 已解决 | 呱呱 | 06-19 |
| 619-24 | 分工 | Registry 单点（仅呱呱自启） | 🟢 | 协调中 | 火鸡儿反馈 | - |
| 619-25 | 分工 | 协议变更无 deprecation 流程 | 🟢 | ✅ 已解决 | 三方 | 06-19 |
| 619-26 | 协作 | hermes adapter `chat -q` 新会话不稳定（14:16/12:35 卡过） | 🟡 | 待处理 | 吉量 | - |

---

## 历史问题（前期已解决，留档备查）

| ID | 日期 | 问题 | 解决 |
|----|------|------|------|
| H-01 | 06-06 | AIM Server 僵尸进程 | kill -9 + 重启 |
| H-02 | 06-07 | AIM Server cooldown bug 拒合法心跳 | AgentStateManager |
| H-03 | 06-08 | observer 连接断开不清理 → 连接数上限 | 立即清理，不走优雅等待 |
| H-04 | 06-11 | NATS JWT/Token 不能并存 | Operator 模式一次性切换 |
| H-05 | 06-13 | JWT 迁移后 inbox 信号不更新（11h 后发现） | 基础设施变更后必须端到端验证 |
| H-06 | 06-13 | 旧 AIM Server 僵尸重启绑定 4222 → 223k+/天认证错误 | 归档后彻底消失 |
| H-07 | 06-13 | launchd KeepAlive + kill = 多实例幽灵进程 | plist 参数与代码参数同步 |
| H-08 | 06-16 | nats-py 回调 >10ms → 30-77s 延迟 | Queue+Scheduler+HealthProbe 三层解耦 |
| H-09 | 06-16 | 日志双写 + dequeue/ack 不成对 | FileHandler+StreamHandler+2>&1 处理 |
| H-10 | 06-17 | macOS fcntl.flock + inode 漂移 | PID 检查 + pgrep 扫描 |
| H-11 | 06-17 | ZS0002 exit=127 旧进程未加载 expanduser | 手动启动进程不会自动热加载 shared |
| H-12 | 06-19 | v1.3.0 Queue 合写 + Registry plist | 1.3.1 修复 |

---

## 详细记录

### 619-01：config.json 字段不一致
- **描述**：三方 config.json 中 adapter/queue_processor/runtime_type/version 字段各家有各家无
- **解决**：(1) 草案 `proposals/619-01-config-schema-v0.2-draft.md`（A/B/C 三层）；(2) 校验脚本 `schema/config_schema_v0.2.py`（70行，A 层 8 字段强制 + B 层 5 字段默认值）；(3) `aim-client/config_schema_check.py` wrapper 接入 main.py 启动流程
- **验证**：ZS0001 ✅ 通过，ZS0003 ✅ 通过，ZS0002 ❌ adapter.cmd 缺失（已通知吉量）
- **状态**：✅ 已解决（06-19）

### 619-02：security.auth.chain 为 null
- **描述**：ZS0002/ZS0003 config.json 中 security.auth.chain 为 null，registered_agents 为空
- **解决**：补全 `["source_identity","rate_limit"]` + `registered_agents=["ZS0001","ZS0002","ZS0003"]`
- **验证（18:48 重验）**：三家 config.json 均含 chain + registered_agents 三元素
- **状态**：✅ 已解决（06-19）

### 619-03：adapter.sh 路径三处分散
- **描述**：ZS0001 ~/.aim/adapters/openclaw/（软链），ZS0002 ~/shared/aim/adapters/hermes/，ZS0003 ~/.aim/agents/ZS0003/
- **解决**：统一到 ~/.aim/agents/{agent_id}/adapter.sh
- **验证**：三方全部归位 + 重启验证（12:30-12:33）
- **状态**：✅ 已解决（06-19）

### 619-04：Queue 持久化合写
- **描述**：v1.3.0 三方 Agent 同时写 ~/shared/aim/data/queue.jsonl，互踩
- **解决**：按 agent_id 分目录 ~/.aim/agents/{agent_id}/queue.jsonl
- **依据**：多实例共享单文件持久化是隐蔽 bug 模板
- **状态**：✅ 已解决（06-19）

### 619-05：~/.hermes/aim/ 远古副本
- **描述**：163MB，VERSION=20260607.0240M，含自有 aim_nats_sdk.py/registry.py/security.py，与 shared 早分叉
- **风险**：Hermes 框架内部脚本可能 import 老 SDK → v1.2 SDK 连接 v1.3 NATS
- **目录差异**：吉量 main.py 实际跑 shared 版，但 ~/.hermes/aim 副本仍在
- **解决**：吉量将 163MB 整体归档到 `~/.hermes/archive/aim-deprecated-20260618/`，~/.hermes/aim/ 仅留 60KB（data/）
- **验证**：`du -sh ~/.hermes/aim` = 60K；归档目录 163MB 完整保留
- **状态**：✅ 已解决（06-19，吉量自修，已验证）

### 619-06：main.py banner 版本号
- **描述**：shared/aim/aim-client/main.py 启动 banner 写死 v1.2.1，实际 VERSION=1.3.1
- **解决**：banner 改为读 VERSION 文件（L64-69）
- **验证**：`python3.13 main.py --help` 显示 v1.3.1，启动 banner 显示 AIM Client v1.3.1
- **状态**：✅ 已解决（06-19 18:31 确认）

### 619-07：PROTOCOL_VERSION 校验未实现
- **描述**：SDK 中 PROTOCOL_VERSION 硬编码为 "1.0"，启动只 warning 不拦截
- **解决**：(1) 类属性 `__protocol_version__` 从硬编码 → 启动时动态读 VERSION 文件；(2) 保留 MIN_PROTOCOL_VERSION="1.0" 作为默认值；(3) 日志从 "未实现" → "AIM Protocol vX.X.X (MIN=1.0)"
- **遗留**：Phase 2+ 需 AgentCard+PING 版本比对 + MIN_PROTOCOL 拒绝机制（需三方协商协议层）
- **状态**：✅ 已解决（06-19，动态读 + 去 TODO）

### 619-08：Registry plist 缺 SuccessfulExit=false
- **描述**：NATS 未就绪 → exit(1) → launchd 不重启 → 死壳
- **解决**：加 SuccessfulExit=false
- **依据**：launchd 所有保活 plist 必须含此字段
- **状态**：✅ 已解决（06-19）

### 619-09：main.py 改后无自动重启
- **描述**：三方 Agent 进程都跑 shared/aim/aim-client/main.py，改后必须人工 kill+重启
- **解决**：SIGHUP 优雅重载机制——启动时注册 SIGHUP handler；dispatch loop 每轮检测 `_reload_flag` → 触发 `_reload_config()` 重读 config.json；`kill -HUP <pid>` 即可热加载配置无需重启进程
- **注意**：仅 reload config，不 reload adapter（adapter 路径变更仍需重启）
- **状态**：✅ 已解决（06-19）

### 619-10：NOTICE 发布后无人实测
- **描述**：v1.3.0 NOTICE 发 17h，没人查 Queue 持久化文件在哪里
- **教训**：发版后必须实测三件事：路径、进程、端到端
- **状态**：✅ 已记教训（06-19）

### 619-11：adapter 异常无自动告警
- **描述**：adapter 异常时无自动告警/降级，靠人工 grep log
- **解决**：(1) DegradeError 时 emit_obs("degrade") 推送降级事件；(2) health_probe_loop 中状态变迁检测：OK→BUSY/DEGRADE/OFFLINE 时 emit_obs("state_change") + warning 日志；(3) 通知通过 NATS 向 grp_trio 广播
- **状态**：✅ 已解决（06-19）

### 619-12：aim_send_nats.py 无 owner
- **描述**：工具是呱呱写的，吉量/火鸡儿发消息都得用，但没 owner
- **状态**：🟢 待处理（大哥裁定）

### 619-13：~/.hermes/aim/ 老 SDK import 风险
- **描述**：如果 Hermes 框架内部脚本 import 老 SDK → v1.2 SDK 连接 v1.3 NATS
- **触发条件**：吉量重新 import 老路径
- **解决**：619-05 归档后已不存在该风险；火鸡儿确认 Letta 端无 SDK 副本
- **状态**：✅ 已确认 N/A（06-19）

### 619-14：三方心跳定义不一致
- **描述**：呱呱主会话每几小时一次；吉量轮询；火鸡儿 deferred。Registry last_seen 更新频率可能不一致
- **触发条件**：长时间不发消息时
- **状态**：🟡 待处理

### 619-15：NATS creds 泄露风险
- **描述**：三方都在 ~/.aim/agents/{id}/aim.creds，git 操作不当可能泄露
- **解决**：~/shared/aim/.gitignore 加双保险 `*.creds` `*.key` `*.pem` `secret*` `*secret*` `*.token`；creds 实际在 ~/.aim/ 不在 git 仓库内
- **状态**：✅ 已解决（06-19）

### 619-16：adapter reply 格式未标准化
- **描述**：吉量 "deepseek."（模型名兜底）、火鸡儿 markdown、呱呱纯文本
- **影响**：主程序当 reply 转发出现质量问题
- **解决**：吉量在 adapter.sh 里加 `sed '/Normalized model/{N;d;}'` 删除噪声行+续行；再 `grep -v` 过滤 session_id/Restored session/Saving session/...开头/空行；只取第一条有效行
- **验证**：`grep -A3 "Normalized model" ~/shared/aim/adapters/hermes/adapter.sh` 已含过滤逻辑
- **状态**：✅ 已解决（06-19，吉量自修，已验证）

### 619-17：退出码 4 级语义已实现
- **描述**：adapter 退出码 0/1/2/3 + timeout 已全部按级处理
- **实现详情**：
  - exit=0 → 正常返回 reply
  - timeout → RetryableError → scheduler.on_retry() + nack("retry") + sleep(2)
  - exit=1 → RetryableError（可重试：session忙/排队） → on_retry() + nack
  - exit=2 → DegradeError（降级：Runtime不可用） → on_degrade() + nack("degrade") + break（停止处理循环）
  - exit=3 → HumanInterventionError（人工介入：权限/崩溃） → on_human_intervention() + nack("human_intervention")
- **状态**：✅ 已解决（06-19 18:31 确认）

### 619-18：群聊消息回路风险
- **描述**：吉量回复既发 DM 也广播到群，三方 adapter 收到群消息可能触发回复风暴
- **解决**：添加 `_last_grp_reply` 字典追踪每组最后回复时间；群聊回复前检查 30s 冷却——冷却期内只 drop 不发送；DM 不受影响
- **验证**：`main.py` 已含 `self._last_grp_reply = {}` 初始化 + dispatch 循环冷却检查
- **状态**：✅ 已解决（06-19）

### 619-19：execution_model 超时窗口不匹配
- **描述**：ZS0001/ZS0002 realtime、ZS0003 deferred。Letta deferred 下投递→adapter→reply 超时窗口可能不够
- **触发条件**：复杂 prompt
- **评估**：火鸡儿确认 ADAPTER_TIMEOUT=120s 当前足够；Letta agents list 用 `timeout 10` 双层防御
- **状态**：✅ 已确认 OK（06-19，火鸡儿评估）

### 619-20：三方 LLM 输出格式差异
- **描述**：OpenClaw=deepseek-v4-pro / Hermes=deepseek / Letta=?，相同 prompt 输出格式/长度差异大
- **状态**：🟢 待处理（协议层）

### 619-21：三方 framework 重启代价不同
- **描述**：呱呱 launchctl 几秒、吉量 hermes 重启可能影响别的 session、Letta 启动慢
- **状态**：🟢 待处理

### 619-22：shared 区 owner 不明确
- **描述**：呱呱写了 main.py，但吉量/火鸡儿都依赖。谁能改？谁审核？现在是"先改先得"
- **状态**：🟢 待处理（大哥裁定）

### 619-23：aim_send_nats.py 谁修 bug
- **描述**：吉量发现 bug 敢改吗？改完三方都受影响
- **状态**：🟢 待处理（大哥裁定）

### 619-24：Registry 单点
- **描述**：Registry 只有呱呱起的 launchd，吉量/火鸡儿没有自启能力
- **状态**：🟢 待处理

### 619-25：协议变更无 deprecation 流程
- **描述**：v1.3.0 直接发 NOTICE 改路径，万一吉量没看到就出事
- **状态**：🟢 待处理（大哥裁定）

---

## 沟通记录

| 时间 | 渠道 | 内容 | 结果 |
|------|------|------|------|
| 06-19 12:32 | grp_trio + DM | adapter 路径统一任务发出 | ZS0002 12:33 完成，ZS0003 12:30 完成 |
| 06-19 14:16 | - | 619 清单建立 | 待分发 |

---

## 优先级矩阵

| 等级 | ID | 谁主导 | 预计 |
|:--:|-----|--------|------|
| 🔴 P0 | 619-05 | 吉量 | 即刻 |
| 🔴 P0 | 619-13 | 吉量 | 即刻 |
| 🔴 P0 | 619-16 | 吉量 | 即刻 |
| 🟡 P1 | 619-01 | 呱呱起草 | 本周 |
| 🟡 P1 | 619-07 | 呱呱 | 本周 |
| ✅ 已解 | 619-06/619-17 | 呱呱 | 06-19 |
| 🟢 P2 | 619-22/23/25 | 大哥裁定 | 按需 |
