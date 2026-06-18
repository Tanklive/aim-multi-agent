# AIM Phase 2 — "直通"架构升级方案

> 版本: v1.0 | 日期: 2026-06-15 | 制定: 呱呱 + 大哥

---

## 1. 目标

去掉 `.aim-queue/` 和 `.aim-replies/` 文件中间层，nats-agent 直接调框架 adapter，NATS JetStream 做消息排队+持久化。

```
现状（5跳）:
NATS → nats-agent → .aim-queue/ → 框架 poll → AI处理 → .aim-replies/ → nats-agent poll → NATS

目标（2跳）:
NATS → nats-agent → call_adapter() → NATS
```

---

## 2. 文件目录结构

```
~/shared/aim/
├── adapters/
│   ├── README.md                    # 接口规范文档（吉量写）
│   ├── DEGRADE.md                   # 降级策略（呱呱+吉量合写）
│   ├── openclaw/
│   │   ├── adapter.sh               # OpenClaw 适配器（呱呱写）
│   │   └── constraints.md           # OpenClaw 约束（呱呱写）
│   ├── hermes/
│   │   ├── adapter.sh               # Hermes 适配器（吉量写）
│   │   └── constraints.md           # Hermes 约束（吉量写）
│   └── letta/
│       ├── adapter.sh               # Letta 适配器（小火鸡儿写）
│       └── constraints.md           # Letta 约束（小火鸡儿写）★新增
│
├── nats-agent-v3/
│   ├── nats-agent-v3.py             # V3 主程序（吉量写）
│   └── call_adapter.py              # adapter 调用模块（吉量写）
│
└── config/
    └── config-schema-v3.json        # config.json 新增字段定义（吉量写）
```

### 现有文件（不动）

```
~/.aim/agents/ZS0001/                # 呱呱配置目录
~/.aim/agents/ZS0002/                # 吉量配置目录
~/.aim/agents/ZS0003/                # 小火鸡儿配置目录
├── config.json                      # +framework, +adapter_cmd, +adapter_timeout ★新增字段
~/.aim/wrappers/                     # launchd wrapper 脚本
~/Library/LaunchAgents/              # plist（V2 保留，V3 以 --mode direct 新增 plist）
```

---

## 3. 接口规范（吉量定义）

### 3.1 adapter 标准接口

```bash
# 输入
aim-adapter process --message "内容" --from "来源Agent ID"

# 输出
exit code:
  0 = 正常，stdout 为回复内容
  1 = 可重试错误（框架忙）
  2 = 异常，降级到文件队列
  3 = 需人工介入（框架挂了/配置错误）

# 超时
默认 120s，可在 config.json 中配置
```

### 3.2 config.json 新增字段

```json
{
  "framework": "openclaw",                          // openclaw | hermes | letta
  "adapter_cmd": "~/.aim/adapters/openclaw/adapter.sh",
  "adapter_timeout": 120
}
```

### 3.3 call_adapter.py 逻辑

```python
def call_adapter(message, from_id, config):
    cmd = config["adapter_cmd"]
    timeout = config.get("adapter_timeout", 120)
    
    result = subprocess.run(
        [cmd, "process", "--message", message, "--from", from_id],
        capture_output=True, timeout=timeout
    )
    
    if result.returncode == 0:
        return result.stdout    # 成功，发回 NATS
    elif result.returncode == 1:
        return RETRY             # 可重试，nats-agent 重试最多3次
    elif result.returncode == 2:
        return DEGRADE           # 降级到文件队列
    elif result.returncode == 3:
        return HUMAN             # 通知大哥
```

---

## 4. 降级策略（呱呱+吉量合写 DEGRADE.md）

| 场景 | 触发条件 | 行为 |
|------|----------|------|
| adapter 超时 | >120s 无响应 | 降级到文件队列 |
| exit 1 重试3次后仍失败 | 累计3次 | 降级到文件队列 |
| exit 2 | adapter 主动降级 | 降级到文件队列 |
| exit 3 | 框架挂了 | 通知大哥，不重试 |
| Letta session 互斥 | `letta -p` 阻塞 | 自动降级文件队列，空闲后消费 |

降级路径：`直接调用 → 文件队列（30min TTL）→ JetStream 持久化（7天保留）`

### V2/V3 并行

- V2 文件队列路径**完全不删**，继续跑
- V3 以 `nats-agent-v3.py --mode direct` 独立启动（新 plist）
- ZS0001 切 V3 时：停 V2 plist，启 V3 plist
- 任一 Agent 不切：不影响其他 Agent（V2 兼容）

---

## 5. 分工

### ✨🐴✨ 吉量（核心开发）

| 任务 | 交付物 | 工作量 |
|------|--------|--------|
| 接口规范文档 | `shared/aim/adapters/README.md` | 0.5h |
| config.json schema 定义 | `shared/aim/config/config-schema-v3.json` | 0.5h |
| call_adapter 模块 | `shared/aim/nats-agent-v3/call_adapter.py` | 1h |
| nats-agent V3 主程序 | `shared/aim/nats-agent-v3/nats-agent-v3.py` | 2h |
| Hermes adapter | `shared/aim/adapters/hermes/adapter.sh` | 0.5h |
| Hermes constraints | `shared/aim/adapters/hermes/constraints.md` | 0.5h |
| 降级策略（与呱呱合写） | `shared/aim/adapters/DEGRADE.md` | 0.5h |
| **吉量合计** | | **3.5h** |

### 🐸 呱呱（基建+OpenClaw适配）

| 任务 | 交付物 | 工作量 |
|------|--------|--------|
| OpenClaw adapter | `shared/aim/adapters/openclaw/adapter.sh` | 0.5h |
| OpenClaw constraints | `shared/aim/adapters/openclaw/constraints.md` | 0.5h |
| 降级策略（与吉量合写） | `shared/aim/adapters/DEGRADE.md` | 0.5h |
| ZS0001 切换到 V3 | plist 更新 + 验证 | 0.5h |
| **呱呱合计** | | **2h** |

### 🐤 小火鸡儿（Letta适配+测试）

| 任务 | 交付物 | 工作量 |
|------|--------|--------|
| Letta adapter | `shared/aim/adapters/letta/adapter.sh` | 0.5h |
| **Letta constraints** ★ | `shared/aim/adapters/letta/constraints.md` | 0.5h |
| ZS0003 端到端验证 | 测试报告 | 1h |
| Letta V3 切换 | plist 更新 | 0.5h |
| **小火鸡儿合计** | | **2.5h** |

---

## 6. 执行顺序

```
Step 0: 三人确认方案（当前）                           ← 现在
Step 1: 吉量出接口规范 + call_adapter.py（1h）         ← 吉量先
Step 2: 呱呱+小火鸡儿各写 adapter.sh（并行，0.5h）      ← 接口定了就并行
Step 3: ★小火鸡儿先写 constraints.md（0.5h）           ← adapter.sh 写完马上出
Step 4: 吉量整合 nats-agent V3（2h）                   ← 吉量主导
Step 5: 三方各自切 V3，端到端验证（1h）                 ← 联调
Step 6: 降级策略兜底确认 + 大哥终审（0.5h）             ← 收尾
```

### ★ 为什么小火鸡儿的 constraints.md 要提前？

> 呱呱写 adapter 时不知道 Letta 的坑（session 互斥、超时行为、`letta -p` 返回码约定等），全靠猜。
> constraints.md 写清楚了。呱呱的 adapter 才能一次写对，别让呱呱写完 adapter 才发现 Letta 不支持某个行为。

---

## 7. 成功标准

- [ ] 三 Agent 在 V3 下互发消息，不走文件队列，秒级响应
- [ ] Letta session 忙时自动降级文件队列，消息不丢
- [ ] exit 3（框架挂）通知大哥，不循环重试
- [ ] V2/V3 并行不冲突，任一方切到 V3 不影响其他 Agent
- [ ] 三个 constraints.md 齐全，新 Agent 接入有参考
