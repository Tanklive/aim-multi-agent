# AIM 即时上下文 2026-07-08
## 当前阶段
v1.5.1 稳定运行，退避兜底中。池会话契约已固化。

## 阻塞
— 无硬阻塞。**exit=124 偶发** — e3191fb 已修复分类（RetryableError+退避），dispatch 不再中断。但 adapter 超时根因尚不明确（letta --new 偶发 >60s，adapter 124→1 转换未生效，需进一步排查）

## 近期修复 (7/3~7/8)
- 7/3 L1 熔断器：三态 + 指数退避 (91ab110)
- 7/3 dispatch 池自动回收 (4fe100c)
- 7/3 QueuePersist 空壳 data 毒化防护 (b4da926)
- 7/3 adapter trim 池清理阈值收紧 (POOL_SIZE+2)
- 7/7 Letta Code CLI 恢复（npm 老包 letta@0.5.2 覆盖，已修）✅
- 7/7 池会话接口契约固化 → `ZS0003-pool-contract.md` ✅
- 7/8 `_my_msg_ids` 回归修复 (e3191fb) ✅
- 7/8 `msg.text`→`msg.content` 修复（群聊确认循环 AttributeError）
- 7/8 Python 3.14 symlink 修复（→3.13.13）⚠️ 需重启生效

## 已关闭
- 四座大山 ✅
- TOCTOU 竞态 (9fe8132) ✅
- U-002/U-004/U-106/P0-004 ✅
- dispatch 池泄漏 ✅
- protocol v1.0 三方闭环 ✅

## 待推进
- **exit=124 根因修复** — 分类已修，adapter 超时根因待查
- 并发 dispatch（呱呱，下一轮）
- Python 3.14 brew 残留 — symlink 已修，brew uninstall 等大哥确认

## 最近决策
- 7/7 池会话 4 项不变约束固化
- 7/3 分工明确：基础设施归呱呱，ZS0003 自身归火鸡儿
- 7/2 adapter v1.14.1 双协议支持
- 7/1 protocol v1.0 三方闭环
