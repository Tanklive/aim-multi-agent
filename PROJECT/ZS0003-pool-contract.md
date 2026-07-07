# ZS0003 (小火鸡儿) 池会话接口契约 v1

本文件定义 ZS0003 对 Letta Code dispatch 池会话机制的标准和不变约束。
当 AIM 基础设施 (main.py, NATS, adapter) 或 Letta Code CLI 发生变更时，**必须检查本文件的约束条件是否被打破**。

---

## 一、池会话机制标准 (v1.14.1+)

### 1.1 核心机制：--new 每次创建新会话

```
每次群消息 → adapter.sh process
  → _dispatch_with_new_conv()
    → letta --agent <id> --new -p "<prompt>"
      → Letta Code 创建新 conversation (local-conv-NNN)
      → 生成回复
    → _track_conv_id() 记录新 conv ID 到 dispatch_conv_ids.txt
    → 回复写 replies.jsonl
    → flush 回 NATS 群
```

**为什么不复用固定会话**：
- Letta Code 单个 conversation 内部串行，TUI 活跃时会话被锁 → dispatch 排队超时
- `--new` 每次新 conv 解耦并发，不受 TUI/Letta Chat 占用影响

### 1.2 dispatch_conv_ids.txt

- **路径**：`$SCRIPT_DIR/dispatch_conv_ids.txt`
- **格式**：每行一个 base64 编码的 conv 标识（如 `Y29udmVyc2F0aW9uOmxvY2FsLWNvbnYtNzY4`）
- **用途**：
  1. trim 清理时遍历所有池内 conv 清空 messages.jsonl
  2. cleanup cron 通过此文件判定保护白名单（不被清理）
- **LRU 淘汰**：`_track_conv_id()` 自动保留最近 `pool_size + 2` 个 ID，超出截尾
- **注意**：此文件纯索引/清理用途，不是池化核心机制。池化核心 = `--new`

### 1.3 配置变量

| 变量 | 来源优先级 | 默认值 | 说明 |
|------|-----------|--------|------|
| `DISPATCH_CONV_POOL_SIZE` | 环境变量 > config.json | `2` | 池大小，控制 dispatch_conv_ids 保留数量 |
| `ADAPTER_TIMEOUT` / `PROBE_TIMEOUT` | adapter.sh 内 | `90s` (ZS0003) / `35s` (模板) | 单次 `letta` 调用超时 |

---

## 二、关键不变量（不可破坏的约束）

以下是 ZS0003 正常运行的必要条件，任何改动必须保证以下成立：

### 2.1 Letta Code CLI 可用性

```
不变约束:  LETTA_BIN 指向的二进制必须能正常执行且返回非空输出
检查方法:  $LETTA_BIN --version → 返回 "0.x.x (Letta Code)"
注意事项:
  - @letta-ai/letta-code 和同名老包 letta (promise 库) 存在 binary 冲突
  - 全局 npm 只能有一个 `letta` binary
  - 修复命令: npm uninstall -g letta && npm install -g @letta-ai/letta-code
```

### 2.2 --new 协议必须支持

```
不变约束:  letta CLI 必须支持 --agent <id> --new -p "<prompt>" 调用模式
检查方法:  执行后应在 ~/.letta/lc-local-backend/conversations/ 下生成新目录
注意事项:
  - --new 参数名不可变 (若 Letta Code 升级改名需同步适配)
  - --agent 必须指向正确的 agent_id
```

### 2.3 agent_id 解析机制

```
不变约束:  _resolve_agent_id() 必须能定位正确的 Letta agent
解析优先级:
  1. 环境变量 LETTA_AGENT_ID (最高)
  2. 磁盘自动发现 (memfs/agent-local-* 最新目录)
  3. config.json 兜底
注意事项:
  - 磁盘自动发现依赖 ~/.letta/lc-local-backend/memfs/ 目录结构
  - 若 Letta Code 升级改变 memfs 路径，此处需同步
```

### 2.4 adapter 超时与 main.py 超时匹配

```
不变约束:  adapter PROBE_TIMEOUT < main.py adapter_timeout
当前值:    ZS0003 PROBE_TIMEOUT=90s, main.py adapter_timeout=120s → 满足
注意事项:
  - 调整 main.py adapter_timeout 时必须同步检查 PROBE_TIMEOUT
  - 若 PROBE_TIMEOUT >= adapter_timeout，冷启动时 main.py 先超时 → adapter 被 SIGKILL
```

---

## 三、故障模式速查

| 现象 | 根因候选 | 检查优先级 |
|------|---------|-----------|
| 所有回复 empty output | Letta CLI 挂了 (优先级最高) | `letta --version` 是否正常 |
| 偶尔 empty output | TUI 占用 + --new 没成功 | dispatch_conv_ids.txt 是否更新 |
| dispatch 池膨胀 | LRU 淘汰失效 | `wc -l dispatch_conv_ids.txt` vs POOL_SIZE |
| adapter 超时 | 冷启动慢 / 模型推理慢 | 增大 PROBE_TIMEOUT 或检查 TUI 会话 |
| replies.jsonl 无新记录 | adapter 进程未启动或被 kill | 检查 ZS0003 主循环日志 |

**重要**：empty output ≠ 池化故障。池化故障的特征是 dispatch_conv_ids.txt 持续增长不淘汰，而不是单次调用返回空。

---

## 四、呱呱 (main.py) 侧约束

当呱呱修改以下内容时，必须检查对 ZS0003 的影响：

1. **adapter_timeout** — 必须大于 ZS0003 的 PROBE_TIMEOUT (90s)
2. **protocol_version 切换** — ZS0003 adapter 支持 v1.0 JSON stdin，不可单方面切回 CLI args
3. **adapter 调用路径** — 必须指向正确的 adapter.sh (`agents/ZS0003/adapter.sh`)
4. **empty output 处理** — main.py 应识别连续 empty output 模式并告警，不要静默吞掉
5. **config.json 清理** — 标准化流程(如 setup-agent.sh)清理 config.json 时，不能破坏 ZS0003 的运行
   - ZS0003 adapter 已迁移到磁盘自动发现 agent_id，不依赖 config.json
   - 但 LETTA_BIN 的 config 回退路径仍存在，清理时需确认环境变量已设置或 fallback 路径有效

---

## 五、吉量 (ZS0002) 侧约束

当吉量改动时，无需特殊约束 ZS0003，但以下为协作注意：

1. **毒消息隔离方案** — ZS0003 不插手，但建议吉量完成后群同步结果，ZS0003 可参考实现自己的 guard
2. **联调** — 需要三方联调时群里喊一声，ZS0003 使用 `aim_send_nats.py grp_trio --group --from ZS0003` 主动群发

---

## 六、自检脚本

```bash
# ZS0003 接口契约自检 — 运行此脚本验证全部不变约束

echo "=== 1. Letta CLI ==="
~/.npm-global/bin/letta --version 2>&1 || echo "FAIL"

echo "=== 2. agent_id ==="
ls ~/.letta/lc-local-backend/memfs/agent-local-*/memory/ 2>/dev/null | head -3 || echo "FAIL"

echo "=== 3. dispatch_conv_ids.txt ==="
wc -l /Users/yangzs/shared/aim/agents/ZS0003/dispatch_conv_ids.txt 2>/dev/null || echo "FAIL"

echo "=== 4. adapter 手动测试 ==="
timeout 90 /Users/yangzs/shared/aim/agents/ZS0003/adapter.sh process \
  --message "test: reply pong" --from "ZS0000" --task-id "selfcheck" 2>/dev/null | head -1 || echo "FAIL"

echo "=== ALL DONE ==="
```

---

## 七、修订记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-07 | v1 | 初始版本，修复 CLI 故障后建立 |

---
👤 维护者: ZS0003 (小火鸡儿) | 📅 2026-07-07 | 🔗 关联: adapter.sh v1.14.1, AIM v1.5.1
