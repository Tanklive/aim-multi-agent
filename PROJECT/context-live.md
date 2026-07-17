# AIM 即时上下文 2026-07-17
## 当前阶段
v1.5.2 稳定运行（已连续 **14 天零事故** 🔥）。池会话契约已固化。版本管理五条规则三方闭环。零事故基线：从 7/3 起算，破线 = 🚨红牌。

## 阻塞
— 无硬阻塞。

## 近期修复 (7/3~7/10)
- 7/3 L1 熔断器：三态 + 指数退避 (91ab110)
- 7/3 dispatch 池自动回收 (4fe100c)
- 7/3 QueuePersist 空壳 data 毒化防护 (b4da926)
- 7/3 adapter trim 池清理阈值收紧 (POOL_SIZE+2)
- 7/7 Letta Code CLI 恢复（npm 老包 letta@0.5.2 覆盖，已修）✅
- 7/7 池会话接口契约固化 → `ZS0003-pool-contract.md` ✅
- 7/8 `_my_msg_ids` 回归修复 (e3191fb) ✅
- 7/8 `msg.text`→`msg.content` 修复（群聊确认循环 AttributeError）
- 7/8 exit=124 根因定位 ✅ — 双路径 PROBE_TIMEOUT 不同步（shared v1.14.2=90，本地副本 v1.14.1=60），同步后消失，验证到 7/9 15:01 无复现。exit code 映射缺陷本身归呱呱（非急救优先级）
- 7/8 Python 3.14.6 全平台升级 ✅ — 三 agent 统一
- 7/9 `_last_grp_interaction` 回归修复 ✅ — 进程未重启致旧代码运行，kickstart 恢复；跨 agent 变更通知规则覆盖
- 7/9 版本管理五条规则三方闭环 ✅ — 吉量/呱呱确认
- 7/10 呱呱误判 exit=124 为 30s 超时 → 澄清：实为双路径 PROBE_TIMEOUT 不同步（60 vs 90），7/8 已解决。PROBE_TIMEOUT=90 足够覆盖 cold start，8 天零 124。瓜建议的 pre-warm 方向对但不需要

## 当前状态 (7/17 16:34)
- 🟢 三剑客全绿：ZS0001/ZS0002/ZS0003 进程 + 日志活跃
- Letta Code 升级至 **0.28.8**（从 0.28.6，后端自动更新，非手动触发）
- replies: 3,831（+30 from 7/10）
- queue: ZS0001=8, ZS0002=4, ZS0003=9（正常波动，dispatch 未中断）
- 7/13 ZS0002 日志"停在 7/13"已恢复——当前活跃
- adapter.sh v1.14.3：PROBE_TIMEOUT 90→120s 止血 + exit=124 首次内部重试不透传 (658673b)

## 零事故基线
- 基准日：2026-07-03（三件套部署日）
- 当前连续：14 天
- 规则：一旦发生 dispatch 中断 / 死信 / 失联超过 3min，基线重置并群内🚨红牌通报

## 待确认
— 无

## 已关闭
- 四座大山 ✅
- TOCTOU 竞态 (9fe8132) ✅
- U-002/U-004/U-106/P0-004 ✅
- dispatch 池泄漏 ✅
- protocol v1.0 三方闭环 ✅
- exit=124 偶发 ✅

## 待推进
- 并发 dispatch（呱呱，下一轮）
- exit code 映射缺陷（适配器超时→RetryableError 分类，归呱呱，非急救优先级）
- Python 3.14 brew 残留 — symlink 已修，brew uninstall 等大哥确认

## 最近决策
- 7/9 版本管理五条规则：Git+deploy标准、代码归属、跨agent变更通知、adapter参数保护、版本基线 — 三方确认
- 7/8 架构评审：按框架组织、monorepo≠安装包、deploy.sh 加 --agent 粒度 — 群发呱呱讨论
- 7/7 池会话 4 项不变约束固化
- 7/3 分工明确：基础设施归呱呱，ZS0003 自身归火鸡儿
