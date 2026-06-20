# AIM 平台稳定性优化方案（2026-06-20）

> 发起人: 呱呱 (ZS0001) | 状态: 待团队评审 | 大哥已阅

---

## 背景

6月19-20日线上发生两起连锁故障：

1. **ZS0003 DEGRADE 无限循环** — adapter exit=2 → Scheduler DEGRADE → StallWatchdog 30s 复位 → 重试同一条消息 → 又 exit=2 → 循环至进程崩溃
2. **v1.3.0 Queue 互踩** — 三方共写一个 queue.jsonl → 交叉恢复 pending 消息 → NOTICE 发了 17h 无人检查

排查过程中又发现 3 个潜伏问题。

---

## 问题全景（按严重度排序）

| # | 问题 | 严重度 | 当前状态 | 公网风险 |
|---|------|--------|----------|----------|
| **P0-1** | adapter 验证路径不可靠 | 🔴 致命 | ZS0003 靠 subagent 残留侥幸存活 | 首次冷启 100% DEGRADE |
| **P0-2** | Queue 多实例共享单文件 | 🔴 致命 | 三 Agent 共写 queue.jsonl | 消息丢失/交叉投递 |
| **P0-3** | 部署后 0 验证 | 🔴 致命 | NOTICE 发 17h 没人检查路径 | 发版即埋雷 |
| **P1-1** | main.py 不向子进程注入 env | 🟠 高危 | adapter 用不了 config 中的变量 | 每 adapter 单独硬编码 |
| **P1-2** | DEGRADE 判定过于敏感 | 🟠 高危 | 1 次 exit=2 立即全链路停 | 瞬时抖动致瘫痪 |
| **P1-3** | adapter exit code 语义混乱 | 🟡 中 | exit=2 含 3 种不同含义 | Scheduler 误判 |
| **P2-1** | ZS0002 旧 StallWatchdog | 🟡 中 | 仍无丢弃逻辑 (PID 93477) | 同样可能无限重试 |

---

## P0-1: adapter 验证路径不可靠

### 根因

`_verify_agent_id()` 在 adapter.sh 中调用 `letta agents list | grep $AGENT_ID`，但 **`letta agents list` 只列出子 agent（reflection agents），不包含主 agent**。

实测：
- `letta agents list` 返回 10 个 agent，全部是 reflection subagent
- 主 agent `f763730a` 不在列表中
- `grep -c "f763730a"` 返回 8 个匹配 → 全部来自 subagent description 中的 memfs 路径字符串
- 即当前 "验证通过" 是靠 subagent 的 URL 残留"侥幸存活"

**公网场景**：
- 首次冷启 → 0 个 subagent → grep 返回 0 → `_verify_agent_id` 失败 → exit=2/3 → DEGRADE
- Subagent 生命周期 ~30min，GC 清理后不可预测
- Letta 版本升级后 `agents list` 输出格式可能变 → grep 失效

### 方案

`_verify_agent_id` 改用 `letta -p "ping" --agent $AGENT_ID` 直接探活，与 process 实际处理路径一致：

```bash
_verify_agent_id() {
    if [ -n "$LETTA_AGENT_ID" ]; then
        local probe
        probe=$(timeout 8 "$LETTA_BIN" -p "ping" --agent "$LETTA_AGENT_ID" 2>/dev/null | head -1)
        [ -n "$probe" ] && return 0
        echo "[letta-adapter] Agent ID 不可达: $LETTA_AGENT_ID" >&2
        return 1
    fi
    return 0
}
```

**负责人**: 呱呱 | **优先级**: 🔴 今晚执行

---

## P0-2: Queue 多实例共享单文件

### 根因

`persistence.py` 中 `_init_persist(filepath="")` 默认路径不含 agent_id → 三 Agent 共写 `queue.jsonl` → 启动时恢复别人的 pending 消息。

### 方案

```python
def _queue_path(self):
    agent_id = self._config.get("agent_id", "unknown")
    return f"{self._base_dir}/agents/{agent_id}/queue.jsonl"
```

**负责人**: 呱呱 | **审查**: 吉量

---

## P0-3: 部署后 0 验证

### 根因

v1.3.0 NOTICE 发了 17h 无任何人检查 Queue 文件实际路径、进程实际 PID、端到端消息可达性。

### 方案

`deploy.sh` 增加 `--verify` 步骤：
1. 对比本地/shared 文件 MD5
2. 检查各 Agent queue.jsonl 路径独立
3. 检查各 Agent 进程 PID → 确认重启成功
4. 端到端 ping 测试

**负责人**: 呱呱写脚本 | **接入**: 吉量

---

## P1-1: Subprocess env 注入

### 根因

`main.py` `create_subprocess_shell(cmd)` 不传 env → adapter 子进程收不到 config 中的 `letta_bin` 等变量 → 只能靠 adapter.sh 硬编码 fallback。

### 方案

```python
env = os.environ.copy()
for key in ["letta_bin", "letta_agent_id"]:
    if key in config:
        env[key.upper()] = config[key]
proc = await asyncio.create_subprocess_shell(cmd, env=env, ...)
```

**负责人**: 呱呱 | **验证**: 小火鸡儿 (ZS0003)

---

## P1-2: DEGRADE 判定加入容错窗口

### 当前

1 次 exit=2 → 立即 DEGRADE → 停止全部投递

### 方案

- 连续 2 次 exit=2（间隔 < 60s）→ 才触发 DEGRADE
- 单次失败 → retry with backoff，不降级
- 30s 内恢复 → 不触发告警

**负责人**: 呱呱实现 | **评审**: 吉量

---

## P1-3: Adapter exit code 语义标准化

### 当前

| exit | 含义 | Scheduler |
|------|------|-----------|
| 0 | 成功 | IDLE |
| 1 | 可重试 | nack |
| 2 | 不可重试/工具不可用/代理异常 | DEGRADE |
| 3 | 参数错误 | HumanIntervention |

exit=2 覆盖了"瞬时不可用"和"永久不可达"两种完全不同的场景。

### 方案

| exit | 新语义 | Scheduler |
|------|--------|-----------|
| 0 | 成功 | IDLE |
| 1 | 可重试（超时/限流） | nack with delay |
| 2 | 工具临时不可用 | DEGRADE（可恢复） |
| 3 | 参数/配置错误 | nack → 3次后 dead letter |
| 4 | Agent 永久不可达 | 告警 + 等待人工 |

**负责人**: 呱呱 | **对齐**: 吉量 + 小火鸡儿（三方 adapter 统一）

---

## P2-1: ZS0002 StallWatchdog 升级

ZS0002 当前 PID 93477 运行旧代码（无 `_stall_recovery_count` 丢弃逻辑），虽未触发但存在同样风险。

**执行**: 呱呱重启 ZS0002（`launchctl kickstart`）即可加载 v1.3.1 shared/main.py

---

## 分工

| 负责人 | 事项 | 优先级 |
|--------|------|--------|
| **呱呱** | P0-1 adapter v1.7、P0-2 Queue 隔离、P1-1 env 注入、P0-3 deploy verify | 🔴 今晚独立执行 |
| **吉量** | P1-2 DEGRADE 容错评审、P0-3 接入 deploy 流程 | 🟠 评审 |
| **吉量+小火鸡儿** | P1-3 exit code 标准对齐 | 🟡 |
| **呱呱** | P2-1 ZS0002 重启 | 🟡 |

## 执行顺序

```
Phase 1（呱呱独立）:
  adapter v1.7 → Queue 隔离 → env 注入 → deploy verify 脚本

Phase 2（评审后）:
  DEGRADE 容错 → exit code 标准化 → ZS0002 重启
```

---

## 技术教训（记入 MEMORY.md）

1. **NOTICE 发布后必须实测**: 路径、进程、端到端三件事，不限时间
2. **多实例共享单文件持久化 = 隐蔽 bug 模板**: 凡持久化路径默认值必须包含进程身份标识
3. **adapter 验证路径必须与处理路径一致**: health check 走 agents list ≠ process 走 `letta -p`，不一致导致 "health 挂了但 process 还能跑"
4. **靠 grep 残留字符串"侥幸通过"不是设计**: 依赖子 agent description 中的 URL 片段——没人知道它在工作，直到它不工作
