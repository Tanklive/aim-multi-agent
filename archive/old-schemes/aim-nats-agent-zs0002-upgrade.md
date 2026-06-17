# ZS0002 NATS Agent 升级方案

> **背景**：呱呱确认现状 — 旧 adapter（`aim_agent_nats_adapter.py`）格式不兼容 Veritas 协议，需要彻底重写。
> **目标**：ZS0002 完全使用 Veritas SDK，与 ZS0001 对齐，端到端通信确认。

---

## 一、现状评估

好消息是——**实际上我的 nats-agent.py 已经和呱呱的几乎完全相同**，都是基于 `AIMNATSClient` 的 Veritas 协议实现。

| 维度 | ZS0002 (吉量) | ZS0001 (呱呱) | 对齐状态 |
|------|---------------|---------------|---------|
| SDK 基础 | `AIMNATSClient`✅ | `AIMNATSClient`✅ | **已对齐** |
| 消息信封 | Veritas `{ver,id,ts,from,type,payload}`✅ | 相同✅ | **已对齐** |
| Subject | `aim.dm.*`/`aim.grp.*`/`aim.obs.*`✅ | 相同✅ | **已对齐** |
| DM 处理 | `on_dm_message` → handler.sh → send_dm✅ | 相同✅ | **已对齐** |
| 群聊处理 | `on_grp_message` → handler.sh → send_grp✅ | 相同✅ | **已对齐** |
| Observer | `emit_obs()`✅ | 相同✅ | **已对齐** |
| JetStream | `setup_streams()` + `setup_consumer()`✅ | 相同✅ | **已对齐** |
| HTTPS 发送 | 通过 `client.send_dm()`✅ | 相同✅ | **已对齐** |
| 目录结构 | `~/.aim/agents/ZS0002/`✅ | `~/.aim/agents/ZS0001/`✅ | **已对齐** |
| launchd | `com.aim.nats-agent.ZS0002.plist`✅ | `com.aim.nats-agent.ZS0001.plist`✅ | **已对齐** |
| handler.sh | Hermes CLI 回调✅ | OpenClaw 回调✅ | **各自框架适配** |
| SDK 版本 | `~/.aim/bin/aim_nats_sdk.py` (旧版)⚠️ | `~/shared/aim/aim_nats_sdk.py` (新版,含 Pin+Retry) | **不同步** |

### 核心差异

1. **SDK 版本不同步** — `~/.aim/bin/` 里的 SDK 是呱呱早期版本（无 AIMPin/RetryManager），`~/shared/aim/` 是呱呱集成后的完整版
2. **旧 adapter (`aim_agent_nats_adapter.py`)** 仍存在于 `~/shared/aim/`，使用旧消息格式，**不再使用**
3. **`aim_send_nats.py`** 发送工具还在用旧 `aim_nats_client` 风格，需要更新

---

## 二、执行计划

### 阶段 1：SDK 同步（5min）

将呱呱的 `~/shared/aim/aim_nats_sdk.py` 完整版同步到 `~/.aim/bin/`：

```bash
cp ~/shared/aim/aim_nats_sdk.py ~/.aim/bin/aim_nats_sdk.py
```

这样 ZS0002 的 nats-agent.py 就能用上 AIMPin（消息去重）+ RetryManager（指数退避重试）。

### 阶段 2：废弃旧文件（2min）

旧 adapter 和旧发送工具不再使用（保留但不运行）：

| 文件 | 处理 |
|------|------|
| `~/shared/aim/aim_agent_nats_adapter.py` | **废弃** — 格式不兼容 Veritas |
| `~/shared/aim/aim_send_nats.py` | **更新** — 改为使用 aim_nats_sdk.py |
| `~/.aim/agents/ZS0002/nats-agent.py` | ✅ **保留** — 已经是 Veritas 协议 |

### 阶段 3：handler.sh 适配检查（5min）

当前 ZS0002 的 handler.sh：
- ✅ 输入：stdin 接收 Veritas 消息信封（`from/payload.text`）
- ✅ 输出：stdout 返回回复文本
- ✅ 使用 `hermes chat -q` 调用 AI
- ✅ 不自言自语（`$FROM = $AGENT_ID` 时跳过）

需要改进：
1. 补充注释：明确标注环境变量约定（`AIM_AGENT_ID/AIM_MSG_ID/AIM_MSG_FROM/AIM_MSG_TEXT/AIM_MSG_TYPE`）
2. 调用 Hermes 时增加 `-p <profile>` 确保用 correct profile

### 阶段 4：端到端测试（关键）

```bash
# 1. 启动 ZS0002 NATS Agent
cd ~/.aim/agents/ZS0002
./nats-agent.py &
sleep 3

# 2. 从 ZS0002 发消息给 ZS0001
python3 -c "
import asyncio, sys
sys.path.insert(0, '$HOME/.aim/bin')
from aim_nats_sdk import AIMNATSClient
async def test():
    c = AIMNATSClient('ZS0002')
    await c.connect()
    await c.send_dm('ZS0001', '🐴 吉量 Veritas SDK 端到端测试，呱呱收到请回复')
    print('✅ 消息已发送')
    await asyncio.sleep(1)
    await c.close()
asyncio.run(test())
"

# 3. 在呱呱那边确认收到
```

---

## 三、文件清单

### 保留（无需修改）
| 文件 | 说明 |
|------|------|
| `~/.aim/agents/ZS0002/nats-agent.py` | NATS Agent 守护进程 ✅ |
| `~/.aim/agents/ZS0002/handler.sh` | Hermes 回调脚本 ✅ |
| `~/.aim/agents/ZS0002/com.aim.nats-agent.ZS0002.plist` | launchd 保活配置 ✅ |
| `~/.aim/agents/ZS0002/secrets/` | 密钥目录 ✅ |
| `~/.aim/agents/ZS0002/logs/` | 日志目录 ✅ |
| `~/.aim/agents/ZS0002/data/` | 数据目录 ✅ |

### 新建（本次添加）
| 文件 | 说明 |
|------|------|
| `~/.aim/agents/ZS0002/STATUS.md` | Agent 状态卡片（ID/模型/框架/能力） |

### 废弃（保留备份，不再使用）
| 文件 | 说明 |
|------|------|
| `~/shared/aim/aim_agent_nats_adapter.py` | 旧格式 adapter → 废弃 |
| `~/.aim/agents/ZS0002/agent.pid` | 运行时 PID 文件（自动管理） |

---

## 四、测试步骤

```
Test 1: SDK 导入测试
  python3 -c "import sys; sys.path.insert(0, '$HOME/.aim/bin'); from aim_nats_sdk import AIMNATSClient, make_envelope; print('SDK OK')"

Test 2: Agent 启动测试
  ./nats-agent.py --server nats://127.0.0.1:4222
  → 日志: "✅ [ZS0002] Agent 就绪，等待消息..."

Test 3: 端到端 DM 收发
  ZS0002 → send_dm → ZS0001
  → ZS0001 收到消息并回复
  → ZS0002 收到回复

Test 4: 群聊收发
  ZS0002 → send_grp("grp_trio") → ZS0001
  → 双方都能收到
```

---

## 五、时间估算

| 阶段 | 估时 | 依赖 |
|------|------|------|
| Phase 1: SDK 同步 | 2min | 无 |
| Phase 2: 废弃旧文件 | 2min | 无 |
| Phase 3: handler.sh 微调 | 5min | SDK 同步后 |
| Phase 4: 端到端测试 | 10min | ZS0001 NATS 运行中 |
| **合计** | **~20min** | |
