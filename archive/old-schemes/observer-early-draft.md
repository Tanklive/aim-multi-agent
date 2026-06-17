# Observer 早期方案草案 (Early Draft)

> **状态:** 🟢 ZS0003（小火鸡儿）已上线，待 grp_trio 三方讨论
> **路线:** 方案2 — 吉量写 draft → shared/aim/ → ZS0003 已上线 → 三方在 grp_trio 讨论 → 出正式版给大哥过目
> **作者:** 吉量 🐴 (ZS0002) | 日期: 2026-06-10
> **对齐文件:**
>   - `observer-migration-plan.md` — Observer Veritas 迁移方案
>   - `observer-integration-result.md` — 61 项测试 100% 通过验证
>   - `aim-veritas-observer-slim.md` — Observer 瘦身方案（Server 不中转）
>   - `PLAN-observer-aimwatch-jwt.md` — 统一开发计划（Phase 0-3）
>   - `observer-interface-spec.md` — Observer 事件格式规范（v1.1，呱呱出品）
>   - `memory/projects/aim-watch-spec.md` — aim-watch 规格（呱呱出品，已就位）

---

## 一、现状总结

### 1.1 已完成的

| 项目 | 状态 | 详情 |
|------|------|------|
| 旧 WS Observer (aim_observer.py) | ✅ 已验证 | ~121 行，订阅 `observer.events.*` |
| NATS SDK `emit_obs()` | ✅ SDK 已实现 | ~/.aim/bin/aim_nats_sdk.py L1056, 发到 `aim.obs.<agent_id>` |
| Observer 集成测试 | ✅ 61 项全通过 | 6 种事件类型全覆盖 |
| aim-watch.py (NATS 版) | ✅ 已存在两份 | `src/bin/aim-watch.py` + `~/.aim/bin/aim-watch.py`，274 行 |
| observer-interface-spec.md | ✅ v1.1 finalized | 呱呱出品的完整接口规范 |
| nats-agent.py Observer 推送 (ZS0003 实测) | ✅ 完整流程验证通过 | 7 种事件 + aim-watch 联动展示 ✅ |

### 1.2 待定项的

| 项目 | 状态 | 说明 |
|------|------|------|
| Server 端 Observer 代码清理 | 🔲 待讨论 | `aim_server_nats.py` 中的 emit_observer_event 是否删除 |
| Observer 瘦身方案采纳 | 🔲 待三方过 | 方案 `aim-veritas-observer-slim.md`  |
| 新 Observer 代码路径 | 🔲 待讨论 | `~/.aim/bin/aim-observe.py` vs 复用现有 aim_observer.py |
| aim-watch 终版方案 | 🔲 待三方过 | 呱呱已出 spec，等齐了一起过 |
| JWT 只读凭证 | 🔲 Phase 3 | 依赖 Phase 0-2 先行 |
| 三层测试 (T1/T2/T3) | 🔲 代码写完后再测 | 沿用 AIM 标准 3+5 轮测试 |

---

## 二、方案选项

### Option A: Observer 复用 SDK（推荐 ✅）

```
Observer ──→ AIMNATSClient ──→ subscribe("aim.obs.>")
                                       ↓
                                收到 → 格式化输出 → 终端
```

**架构特点:**
- Server **不中转** Observer 事件 — Agent 直推 NATS
- Observer = 纯消费者（只订阅 `aim.obs.>`，不 publish）
- SDK 统一连接管理（自动重连、ping、限流）
- Observer 只负责展示逻辑

**代码估算:** ~60 行新代码（或基于现有 ~121 行精简）

### Option B: 保持独立 Observer（不依赖 SDK）

**架构特点:**
- 独立 nats-py 连接，裸 subscribe
- 零 SDK 依赖

**代码估算:** ~70 行

### 建议

Option A — SDK 已覆盖连接管理（自动重连、ping/interval），Observer 只需要专注展示逻辑。而且 SDK 是 Veritas 客户端标准库，所有 Agent 都该用它，Observer 也应该统一。这和 `aim-veritas-observer-slim.md` 的结论一致。

---

## 三、Observer 流设计

### 3.1 nats-agent.py Observer 推送标准

每个 Agent（nats-agent.py）在消息处理过程中**必须**按以下顺序推送 Observer 事件：

| 阶段 | 状态值 (status) | 触发时机 | 示例 detail |
|------|-----------------|----------|-------------|
| 1. 收到消息 | `received` | 去重检查通过后 | `"收到来自 ZS0001 的消息: 你好"` |
| 2. 开始处理 | `processing` | 进入 _process_message | `"AI 处理中..."` |
| 3. AI 调用 | `ai_start` | 调用 AI 框架前 | `"调用 AI 框架处理"` |
| 4a. AI 回复 | `ai_done` | AI 返回非空内容 | `"AI 回复: 我是吉量..."` |
| 4b. AI 无回复 | `ai_empty` | AI 返回空内容 | `"AI 未生成回复"` |
| 5a. 回复完成 | `completed` | 消息已发送 | `"已回复"` |
| 5b. 处理出错 | `error` | 异常捕获 | `"连接超时"` |

**ZS0003 实测效果（aim-watch 联动展示）：**
```
[14:08:46] 📨 ZS0001 → ZS0003 | 完整流程测试
[14:08:46] 📥 ZS0003 received — 收到来自 ZS0001 的消息
[14:08:46] ⚙️ ZS0003 processing — AI 处理中
[14:08:46] 🤖 ZS0003 ai_start — 调用 AI 框架处理
[14:08:51] ⚠️ ZS0003 ai_empty — AI 未生成回复
```

**实现代码（nats-agent.py _process_message 标准模板）：**
```python
async with self.semaphore:
    try:
        await self.client.emit_obs("received", msg_id, f"收到来自 {from_id} 的消息")
        await self.client.emit_obs("processing", msg_id, f"AI 处理中...")
        await self.client.emit_obs("ai_start", msg_id, f"调用 AI 框架处理")
        reply = await self._call_ai(prompt)
        if reply:
            await self.client.emit_obs("ai_done", msg_id, f"AI 回复: {reply[:80]}")
            # ... 发送回复 ...
            await self.client.emit_obs("completed", msg_id, "已回复")
        else:
            await self.client.emit_obs("ai_empty", msg_id, "AI 未生成回复")
    except Exception as e:
        await self.client.emit_obs("error", msg_id, str(e))
```

**前提：SDK 的 `emit_obs()` 已实现限流（3 条/s/agent，超出丢弃）。**
参考: `~/.aim/bin/aim_nats_sdk.py` L1053-1067

### 3.2 完整数据流

```
┌─────────────┐                    ┌───────────────┐
│  Agent A    │  NATS pub          │  Observer     │
│  (ZS0001)   │────────────────────▶│  (anyone)     │
│             │ aim.obs.ZS0001     │               │
│  SDK emit_obs()                  │  subscribe    │
│    ↓         │                   │  aim.obs.>    │
│  rate_limit  │                    │               │
│  (3/s/agent) │                    │  --agent      │
└─────────────┘                    │  ZS0001 过滤   │
                                   └───────────────┘
```

**关键点:**
1. Agent 用 SDK 的 `emit_obs()` 推状态到 `aim.obs.<agent_id>`
2. Observer 订阅 `aim.obs.>` 收所有 Agent 事件
3. 按需过滤（`--agent ZS0001` 等价于 `aim.obs.ZS0001`）
4. Server **不参与** Observer 事件的中转

### 3.3 事件格式（与 observer-interface-spec.md v1.1 对齐）

**SDK emit_obs 输出:**
```json
{
  "ver": "1.0",
  "agent_id": "ZS0002",
  "status": "processing",
  "detail": "正在分析代码",
  "msg_id": "msg-xxx",
  "ts": 1717737650.123
}
```

**Observer 事件类型（6 种，与 observer-integration-result.md 一致）:**
| 类型 | 来源 | 说明 |
|------|------|------|
| `status_feedback` | Agent AI 处理 | processing/completed/error |
| `retry_event` | 重传机制 | 重传触发/退避 |
| `cache_event` | 离线缓存 | 缓存命中/恢复/溢出 |
| `recovery_event` | 会话恢复 | 恢复完成 |
| `status_update` | Agent 状态变更 | online/busy/offline |
| `system_event` | 系统事件 | heartbeat/error |

**心跳格式:**
```json
{
  "ver": "1.0",
  "agent_id": "ZS0002",
  "status": "heartbeat",
  "detail": "alive",
  "ts": 1717737710.004
}
```

### 3.4 限流

SDK 层已实现 `emit_obs` 限流（3 条/s/agent，超出的直接丢弃），Observer 侧无需再限流。

参见: `~/.aim/bin/aim_nats_sdk.py` L1053-1067

---

## 四、aim-watch 设计

### 4.1 aim-watch vs aim-observe 关系

| 工具 | 订阅 | 展示风格 | 用途 |
|------|------|----------|------|
| **aim-observe** | `aim.obs.>` | 简洁一行，JSON 可选 | 监控 Agent 状态 |
| **aim-watch** | `aim.dm.>` + `aim.grp.>` + `aim.obs.>` | 彩色多行 + 状态图标 | 看全貌（消息 + 状态） |

### 4.2 aim-watch 核心能力

基于呱呱的 aim-watch-spec.md + ZS0003 实测效果：

```
aim-watch                          # 看全部 Agent 的消息+状态
aim-watch --agent ZS0001           # 只看呱呱
aim-watch --history 10             # 回放最近 10 条
aim-watch --json                   # JSON 输出，机器可读
```

**权限模型：只读监控，无权限限制**
- aim-watch 是只读监控工具（订阅 `aim.dm.>` + `aim.grp.>` + `aim.obs.>`，不 publish）
- 任何 Agent 都可以查看任何其他 Agent 的处理过程
- 不需要额外的权限检查
- 只需要 NATS Token 认证连接（所有已注册 Agent 都有）

**订阅的 subjects:**
- `aim.dm.>` — 私聊消息
- `aim.grp.>` — 群聊消息
- `aim.obs.>` — Observer 状态事件

**展示要求:**
- 彩色输出 + 状态图标（见 STATUS_ICONS 定义）
- 时间戳格式化到 `HH:MM:SS`
- 消息内容完整显示（不截断）
- 状态事件显示 Agent + 状态 + 进度
- 完整流程展示（消息 → received → processing → ai_start → ai_done/ai_empty → completed/error）

**ZS0003 实测的 STATUS_ICONS（已实现）:**
```
📨 dm / 📢 grp — 消息收发
📥 received — 收到消息
⚙️ processing — 处理中
🤖 ai_start — AI 调用
✅ ai_done — AI 回复
⚠️ ai_empty — AI 无回复
✅ completed — 完成
❌ error — 错误
💓 heartbeat — 心跳
🟢 online / 🔴 offline — 在线状态
```

### 4.3 现有代码复用

现有 `~/.aim/bin/aim-watch.py`（274 行）已基本覆盖上述能力。需要讨论：
- 是否微调即可，还是需要重写
- 和呱呱的 aim-watch-spec.md 对齐哪些点

---

## 五、与 PLAN-observer-aimwatch-jwt.md 的对齐

| Phase | 产出 | 当前状态 | 待讨论 |
|-------|------|----------|--------|
| **Phase 0** — SDK 重构 | 认证层（3 模式）、重连层（指数退避） | SDK 已有 from_config + token 认证 | 是否新增 creds/JWT 模式 |
| **Phase 1** — Observer 开发 | `~/.aim/bin/aim-observe.py` ~150 行 | aim_observer.py 已有（121 行，旧 subject） | 直接复用还是重写 |
| **Phase 2** — aim-watch | `~/.aim/bin/aim-watch.py` ~200 行 | 已存在 274 行的工作版本 | 呱呱已出 spec，等对齐 |
| **Phase 3** — JWT 认证 | JWT 只读凭证 | 未开始 | 需要呱呱 Server 配合 |

### 分工提议

| 任务 | 建议负责人 | 说明 |
|------|-----------|------|
| Server 端 Observer 代码清理 | 🐸 呱呱 | 删 aim_server_nats.py 中的 emit_observer_event |
|| SDK emit_obs 微调/保持 | 🐴 吉量 | 接口已就绪，参数对齐 |
|| aim-observe.py | 🐴 吉量 | 基于 SDK 的 Observer 瘦身 |
|| aim-watch 终版 | 🐴 吉量 + 🐤 ZS0003 | 基于呱呱 spec + ZS0003 实测展示效果做终版 |
|| Observer 推送标准 | 🐴 吉量 | 写入文档 §3.1（本次已更新） |
|| nats-agent.py 推送对齐 | 🐤 ZS0003 | 对齐标准推送模板并验证 |
|| JWT 凭证 + Server 侧 ACL | 🐸 呱呱 | 需要 NATS Server 配置 |
|| 联调验证 | 🐴 + 🐸 + 🐤 | T1/T2/T3 三轮 |

---

## 六、待三方讨论的问题

1. **Server 是否中转 Observer 事件？**
   - 瘦身方案说「不中转」，PLAN 和 observer-interface-spec 说「Server 有 broadcast」
   - 🐴 倾向：Agent 直推 NATS，Server 不中转（瘦身方案方向）

2. **Observer 代码路径？**
   - `~/.aim/bin/aim-observe.py`（新文件，按 SDK 风格）
   - 还是复用/精简现有的 `aim_observer.py`（旧 WS 风格）
   - 🐴 倾向：新建 `aim-observe.py`，用 SDK，~80 行干净版

3. **aim-watch 是否直接从现有代码微调？**
   - 现有 `~/.aim/bin/aim-watch.py`（274 行）基本可用
   - 呱呱的 spec 可能有新要求
   - 🐴 倾向：现有代码微调，呱呱 spec 的差异点在群里对齐

4. **JWT 优先级？**
   - Phase 3 还是提前到 Phase 1？
   - 🐴 倾向：Phase 3，先把 Observer 跑起来

5. **Observer 是否需要有历史回放？**
   - JetStream consumer 还是独立实现
   - aim-watch 已有 `--history` 参数
   - 🐴 倾向：复用现有 JetStream 回放逻辑

6. **心跳格式统一？**
   - SDK `emit_obs("heartbeat", detail="alive")` 已经跑通
   - 是否需要定义专门的心跳 subject

---

## 七、测试策略

沿用 AIM 标准三阶段测试：

| 轮次 | 范围 | 方法 |
|------|------|------|
| T1 (3 轮) | 基本功能 | 手动运行 Observer，确认 `aim.obs.*` 消息实时展示 |
| T2 (修复) | 修正 T1 问题 | fix review 问题，对齐各 Agent 版本 |
| T3 (5 轮) | 全面覆盖 | 多 Agent、断线重连、限流触发、JetStream 回放 |

**核心测试用例：**
1. Agent A emit_obs → Observer 收到并显示
2. 多个 Agent 同时发 obs 事件 → 正确乱序展示
3. aim-watch 同时展示消息 + obs 事件
4. Observer 断线自动重连
5. `--agent ZS0001` 过滤正确
6. `--history 10` JetStream 回放
7. SDK 限流触发（>3/s）→ 超出丢弃，不阻塞

---

## 八、文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `~/.aim/bin/aim-observe.py` | 🆕 新建 | Observer CLI，基于 SDK，~80 行 |
| `~/.aim/bin/aim-watch.py` | 🔄 微调 | 现有 274 行，对齐呱呱 spec 后微调 |
| `~/.aim/bin/aim_nats_sdk.py` | 🔄 保持 | emit_obs() 已实现，无需大改 |
| `aim_server_nats.py` | 🔄 清理 | 🐸 呱呱删 emit_observer_event |
| `aim_observer.py` (旧 WS) | 📦 归档 | 后续可删或保留为参考 |

---

## 九、下一步

1. **ZS0003 上线后 → grp_trio 讨论以上 6 个问题**（ZS0003 已上线）
2. 达成一致后出正式版 Observer 方案
3. 给大哥过目最终版
4. 按分工各自实现（优先对齐 ZS0003 已实测的 aim-watch 展示和 Observer 推送标准）
5. T1/T2/T3 三轮联调
