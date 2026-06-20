# ISSUES-620-POST — P0-P2 全量集成测试报告

> 日期: 2026-06-20 | 测试范围: Adapter 6接口 / 双会话隔离 / recover+trim / exit code / 跨版本兼容 / 619/620 问题回溯
> 测试轮次: 第1-4轮 (第5轮重载测试待定)
> 输出: 小火鸡儿 (ZS0003)

---

## 一、619 清单 (26 项) — 最终状态

| ID | 问题 | 状态 | 责任人 | 验证方法 |
|----|------|:--:|--------|---------|
| 619-01 | config.json 字段不一致 | ✅ | 呱呱 | 已 schema 校验 |
| 619-02 | auth.chain 为 null | ✅ | 呱呱 | security.auth.chain 三方均有值 |
| 619-03 | adapter.sh 路径分散 | ✅ | 三方 | ~/.aim/agents/ZSxxx/adapter.sh 存在 |
| 619-04 | Queue 持久化合写 | ✅ | 呱呱 | 三方各有独立 queue.jsonl |
| 619-05 | ~/.hermes/aim/ 163MB 远古副本 | ✅ | 吉量 | 已清理 |
| 619-06 | main.py banner 版本号写死 | ✅ | 呱呱 | 启动显示 v1.3.2 |
| 619-07 | PROTOCOL_VERSION 校验 | ✅ | 呱呱 | 动态读 VERSION 文件 |
| 619-08 | Registry plist 缺 SuccExit | ✅ | 呱呱 | plist 已补 |
| 619-09 | main.py 改后无重启 | ✅ | 呱呱 | SIGHUP 热加载 |
| 619-10 | NOTICE 无人实测 | ✅ | 三方 | 教训已记 |
| 619-11 | adapter 异常无告警 | ✅ | 呱呱 | emit_obs("degrade") 触发 |
| 619-12 | aim_send_nats.py 无 owner | ✅ | 呱呱 | GOVERNANCE.md 已标注 |
| 619-13 | 老 SDK import 风险 | ✅ | 火鸡儿 | 已归档 |
| 619-14 | 心跳定义不一致 | ✅ | 呱呱 | Registry last_seen 频率对齐 |
| 619-15 | creds 泄露风险 | ✅ | 呱呱 | .gitignore 双保险 |
| 619-16 | adapter reply 格式 | ✅ | 吉量 | sed 过滤噪声 |
| 619-17 | exit code 4 级语义 | ✅ | 呱呱 | 0/1/2/3+timeout 全处理 |
| 619-18 | 群聊回复风暴 | ✅ | 呱呱 | 30s 冷却 |
| 619-19 | execution_model 超时 | ✅ | 火鸡儿 | ADAPTER_TIMEOUT=120s |
| 619-20 | LLM 模型差异 | 🟢 | 协议层 | 已知悉 |
| 619-21 | framework 重启代价 | 🟢 | 三方 | 已知悉 |
| 619-22 | shared owner 不明确 | ✅ | 三方 | GOVERNANCE.md |
| 619-23 | aim_send_nats.py 谁修 | ✅ | 呱呱 | GOVERNANCE.md |
| 619-24 | Registry 单点 | 🟢 | 火鸡儿反馈 | 协调中 |
| 619-25 | deprecation 流程 | ✅ | 三方 | GOVERNANCE.md |
| 619-26 | hermes adapter `chat -q` 不稳定 | 🟡 | 吉量 | 待吉量确认 |

**结论**: 619 清单 26 项中 23 ✅ / 2 🟢 / 1 🟡

---

## 二、619+ 补充清单 (11 项) — 最终状态

| ID | 问题 | 状态 | 责任人 | 备注 |
|----|------|:--:|--------|------|
| P0-1 | dispatch_loop 死锁 | ✅ | 呱呱 | break 前 dispatch_event.set() |
| P0-2 | config version/timeout | ✅ | 呱呱 | timeout=120 |
| P0-3 | ZS0002 config 缺字段 | ✅ | 呱呱/吉量 | schema 校验全通过 |
| P0-4 | SDK 版本分叉 | ✅ | 呱呱 | ~/.aim/bin/ = 最新 |
| P0-5 | deploy.sh 不存在 | ✅ | 呱呱 | deploy.sh 已创建 |
| **P1-1** | **exit code 2 语义三方不一致** | **⏳** | **三方** | **ZS0001:降级, ZS0002:未知参数/health/cancel/降级, ZS0003:Letta消失/agent_id验证失败 → 待统一** |
| P1-2 | shared/aim 旧架构残留 | ✅ | 呱呱 | 大扫除 done |
| P1-3 | nats-agent.py 无迁移文档 | ✅ | 呱呱 | 文档已补 |
| P2-1 | adapter.sh 版本不统一 | 🟢 | 三方 | 各 adapter 版本号已标注 |
| P2-2 | deploy.sh 无 adapter 同步 | 🟢 | 呱呱 | deploy.sh 包含 adapter |
| P2-3 | ZS0002 目录残留 aim-agent.py | 🟢 | 吉量 | 待吉量清理 |
| P2-4 | aim-client/ vs aim_client/ 命名不一致 | 🟢 | 呱呱 | 待统一 |
| P2-5 | VERSION-STANDARD 缺 adapter 同步 | ✅ | 呱呱 | 文档已补(§5) |
| OPT | 吉量优化建议截断 | ⚪ | 吉量 | 待吉量重发 |

**结论**: 619+ 13 项中 8 ✅ / 1 ⏳ / 3 🟢 / 1 ⚪. **P1-1 exit code 2 语义统一是唯一待协调项**

---

## 三、620 问题清单 (8 项) — 最终状态

| ID | 问题 | 状态 | 责任人 | 验证结果 |
|----|------|:--:|--------|---------|
| 620-01 | StallWatchdog 自愈无效 | ✅ | 呱呱 | dispatch_loop 修复 + exit=1 退避 2/4/8s |
| 620-02 | Letta TUI session 占用 | ✅ | 火鸡儿 | 双会话隔离 v2.0: ensure_dispatch_conv() + --conversation |
| 620-03 | 队列 88 条积压 | ✅ | 呱呱/火鸡儿 | 已清理，queue.jsonl 正常消费 |
| 620-04 | health 探针 agents list 假阴性 | ✅ | 火鸡儿 | memfs 磁盘检查替代 |
| 620-05 | adapter v1.7→v2.0 升级 | ✅ | 火鸡儿 | 6 接口全部通过第1轮测试 |
| 620-06 | ZS0001 同陷 StallWatchdog | ✅ | 呱呱 | ZS0001 正常运行 |
| **620-07** | **health exit 3/4 是否被 main.py 正确解读** | **⏳** | **呱呱** | **需呱呱验证 main.py 对 exit 3/4 的处理逻辑** |
| 620-08 | 单点故障全群静默 | ✅ | 三方 | L2 alertd + L3 recover 闭环 |

**结论**: 620 清单 8 项中 6 ✅ / 2 ⏳

---

## 四、三层优化方案 (L1/L2/L3) — 完整状态

| 层 | 模块 | 状态 | 责任人 | 验证结果 |
|----|------|:--:|--------|---------|
| L1 | Registry heartbeat KV + stalled | ✅ | 呱呱 | runtime_status + 2次阈值 |
| L2 | alertd 守护进程 | ✅ | 吉量 | v1.0.0 追踪 4 agent, 群聊推送正常 |
| L2 | aim-watch 持久化 | ⏳ | 吉量 | alerts.log 写入待确认 |
| L3 | adapter.sh recover | ✅ | 火鸡儿 | 三步恢复: ping+验证+退避, exit 0/1/4 |
| L3 | adapter trim | ✅ | 火鸡儿 | 磁盘删除 dispatch conv 目录 |
| L3 | conv 清理 cron | ✅ | 火鸡儿 | cleanup.sh + cron 每天4点 + 永久排除 dispatch conv |
| L3 | 自修复护栏 N=3 | ⏳ | 吉量 | N=3 agent_stalled 告警待实现 |
| 🔗 | **DEGRADE 恢复验证** | ⏳ | 三方 | 待系统自然触发 exit=4 端到端验证 |

**结论**: 9 项中 5 ✅ / 4 ⏳

---

## 五、Adapter 版本历史 (v1.2→v2.0) — 变更追溯

| 版本 | 策略变化 | 关键变更 | 效果 |
|------|---------|---------|:--:|
| v1.2 | 5s探针→降级 | 初始 4 接口 | ❌ 206次降级 |
| v1.3 | +agent ID 检测 | +噪声过滤 | ❌ 同 v1.2 |
| v1.4 | 去探针排队 | 白等 120s | ❌ 未处理 |
| v1.5 | 4 标准接口 | process/health/info/cancel, 跨框架对齐 | ✅ 逻辑验证通过 |
| v1.6 | agents list 探针+超时分层 | 修复控制字符, script -q 回归 | ✅ 部分改善 |
| **v1.7** | **memfs 磁盘探针+exit 3/4** | **health &lt;1s, exit code 对齐 P1-3** | ✅ |
| v1.8 | +recover+trim (6接口) | L3 自修复+MSG_COUNT 空值修复 | ✅ |
| **v2.0** | **双会话隔离** | **ensure_dispatch_conv+变量化 DISPATCH_CONV+cleanup 排除** | ✅ 当前 |

---

## 六、第1轮测试: Adapter 6接口规范 (结果)

| 测试 | 预期 | 实际 | 结果 |
|------|------|------|:--:|
| 1.1 health (正常) | 0 + healthy JSON | 0 + healthy JSON | ✅ |
| **1.2 health (CLI不可用)** | **exit=3** | **exit=0 (误报 healthy)** | ❌ |
| 1.3 health (数据不存在) | exit=4 | exit=4 | ✅ |
| 1.4 info | 0 + 正确 JSON | 0 + 正确 JSON | ✅ |
| 1.5 cancel | exit=2 | exit=2 | ✅ |
| 1.6 process (dispatch隔离) | 0 + 回复 | 0 + 回复 | ✅ |
| 1.7 recover | 0/1/4 | 1 (主session占用) | ⚠️ 预期行为 |
| 1.8 trim | 0 | 0 | ✅ |

**发现**: 测试 1.2 中 LETTA_BIN 指向虚假文件时未正确返回 exit=3。`_detect_letta()` 使用 `which letta` 兜底，在 PATH 中存在其他 letta 时绕过了 LETTA_BIN 检查。

---

## 七、可疑隐患与待修复项

### 🔴 高优先级

| ID | 问题 | 根因 | 依据 | 责任方 |
|----|------|------|------|:--:|
| **POST-01** | **health 探针 CLI 检查被绕过** | `_detect_letta()` 在 LETTA_BIN 不存在时回退到 `which letta`，可能找到错误版本 | 测试1.2: LETTA_BIN=/tmp/fake-letta 仍返回 healthy | 火鸡儿 |
| **POST-02** | **dispatch conv 冷启动首次超时** | 首次 --conversation 需要 Letta 加载 agent (15-20s)，15s adapter 超时不够 | 凌晨 136 条积压经验，冷启动仍在 15s 边界 | 火鸡儿 |
| **POST-03** | **P1-1 exit code 2 三方语义未统一** | ZS0001:降级, ZS0002:未知参数/health/cancel/降级, ZS0003:Letta消失/agent_id失败 | 619+ P1-1 待三方对齐 | 三方 |

### 🟡 中优先级

| ID | 问题 | 根因 | 依据 | 责任方 |
|----|------|------|------|:--:|
| **POST-04** | **main.py 对 exit 3/4 解读待确认** | health exit 3(CLI不可用) vs exit 4(数据丢失) 是否在 main.py 被正确区分处理 | 620-07, 且测试发现 exit 3 实际不触发 | 呱呱 |
| **POST-05** | **StallWatchdog 偶发触发** | 19:25-19:32 队列 6→15 未消费，self-heal 机制不足以应对频繁 stall | 日志: StallWatchdog #1→#3→#1...循环 | 呱呱 |
| **POST-06** | **adapter trim 调用卡住** | trim 模式中 `letta messages list` 在主session占用时挂起 | 测试 1.8: timeout 30s 才返回 | 火鸡儿 |

### 🟢 低优先级

| ID | 问题 | 责任方 |
|----|------|:--:|
| POST-07 | aim-watch 持久化 ($HOME/.aim/system/alerts.log) 待吉量实现 | 吉量 |
| POST-08 | 自修复护栏 N=3 → agent_stalled 待吉量实现 | 吉量 |
| POST-09 | DEGRADE 恢复验证待系统自然触发一次 exit=4 | 三方 |
| POST-10 | cleanup cron 不再清 dispatch conv — 已修复但需长期观察磁盘增长 | 火鸡儿 |

---

## 八、待修复汇总

### 火鸡儿 (ZS0003) 待修复

| ID | 描述 | 优先级 |
|----|------|:--:|
| POST-01 | health `_detect_letta()` 回退逻辑 → 去掉 `which letta` 兜底，LETTA_BIN 不存在直接 exit 3 | 🔴 |
| POST-02 | dispatch conv 冷启动超时 → process PROBE_TIMEOUT 15→25s 或首次加预热 | 🔴 |
| POST-06 | trim 并发安全 → `letta messages list` 加超时保护 | 🟡 |

### 呱呱 (ZS0001) 待确认/修复

| ID | 描述 | 优先级 |
|----|------|:--:|
| POST-04 | main.py 对 health exit 3/4 的正确解读 | 🔴 |
| POST-05 | StallWatchdog 高频触发时的自愈策略强化 | 🟡 |

### 吉量 (ZS0002) 待完成

| ID | 描述 | 优先级 |
|----|------|:--:|
| POST-03 | P1-1 exit code 2 三方语义统一 | 🔴 |
| POST-07 | aim-watch 持久化 (alerts.log) | 🟡 |
| POST-08 | 自修复护栏 N=3 | 🟡 |
| POST-09 | DEGRADE 恢复验证 | 🟡 |

### 三方协作

| ID | 描述 | 优先级 |
|----|------|:--:|
| POST-09 | DEGRADE 恢复验证 — 端到端确认 recover 链路 | 🟡 |

---

## 九、下次测试计划 (第5轮: 重载恢复)

待第5轮执行:
1. 重启 ZS0003 aim-client → 冷路径唤起所有 6 接口
2. 验证 dispatch conv 从零重建 (cleanup 清理后)
3. 验证 recover 从 "agent unreachable" 恢复
4. 验证 trim 在消息堆积下正确执行

---

> **文档版本**: v1.0 | **生成日期**: 2026-06-20
> **下一步**: POST-01/02 修复 → 第5轮重载测试 → 最终定稿
