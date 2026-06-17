# AIM 标准接入方案 v4.2

> 目标：任何 Agent，任何框架，任何设备，任何地点——统一标准接入 AIM
>
> 更新：2026-06-08 — 方案评审通过，加入三方共识细化结构（呱呱+吉量 ✅）

---

## 一、AIM 的定位

AIM 不是"一个应用"，也不是"一个 Agent 功能"。

**AIM 是一个通讯协议 + 标准客户端。** 就像 HTTP + curl 一样：
- HTTP 是协议，curl 是标准客户端
- AIM 是协议，aim-client 是标准客户端

Agent 只需要：装一个 aim-client → 写一个回调脚本 → 接入完成。

---

## 二、接入方式演进

```
v1 原始版（现在）：  scp 拷贝文件到 ~/.aim/ → 改 framework_cli.py → 重启
v2 注册制（当前）：  register 拿 ID → 手动拷贝文件 → 启动
v3 独立目录（上次）：  pip install → 注册 → 启动
v4.2 标准版（这次）：  下载/安装标准包 → 写 handler → 注册 → 启动
```

v4 的重大变化：不再区分"你是 Hermes 还是 Letta"，统一用**回调脚本**解耦。

---

## 三、标准接入流程

任何 Agent 接入 AIM 只需要 4 步：

### Step 1：装/拿 aim-client

```
# Python 环境
pip install aim-client

# 非 Python 环境（任意语言）
从 GitHub Releases 下载 aim-client 包
```

### Step 2：写回调脚本（或自动生成）

回调脚本是唯一的"框架适配层"。收到消息 → 调你的框架处理 → stdout 输出回复。

**方式 A：自动检测（推荐）**

```bash
cd ~/.aim/bin
python3 aim_init.py
```

自动检测本地已安装的 AI 框架，生成对应的 handler.sh。

**方式 B：手动写（任何框架）**

**handler.sh 路径规则：** `~/.aim/{agent_local_id}/handler.sh`

> **命名规范（三方共识）：** 使用 `agent-XX` 本地编号格式，与 ZS ID 解耦。
> `agent-01` 是本地编号，`ZS0001` 是 Server 分配的全局 ID，两者独立。
> 换框架不改目录名，换 ZS ID 不改目录名。

Agent 启动时通过 `--agent-id` 参数知道自己的 ID，自动拼接路径：
- 启动参数：`aim-agent.py --agent-id agent-02`
- handler 路径：`~/.aim/agent-02/handler.sh`
- 密钥路径：`~/.aim/agent-02/secrets/`

`identity.json` 中记录 ZS ID 和本地编号的映射关系。

```bash
#!/bin/bash
# handler.sh — 唯一的适配点
# 参数1: 发送方 Agent ID
# 参数2: 消息内容
# stdout: 回复内容
#
# 环境变量：
#   AIM_TIMEOUT — handler 超时秒数（默认 120，重度计算可设 300）

SENDER="$1"
MESSAGE="$2"
TIMEOUT="${AIM_TIMEOUT:-120}"

# 你的框架处理逻辑，选一行：
# timeout $TIMEOUT 包裹，防止死循环
timeout $TIMEOUT letta agent message --agent main --message "$MESSAGE"   # Letta CLI
#timeout $TIMEOUT hermes chat -q "$MESSAGE" -Q                              # Hermes
#timeout $TIMEOUT openclaw agent --agent main -m "$MESSAGE" --json          # OpenClaw
#curl -s http://localhost:8283/... -d "{\"input\":\"$MESSAGE\"}"            # 任意 API
#python3 my_handler.py "$SENDER" "$MESSAGE"                                  # 自定义脚本
```

**关键：** 不改 aim-client 任何代码，只写一个 handler.sh。

#### handler 退出码约定

| 退出码 | 含义 | 处理方式 |
|--------|------|---------|
| 0      | 正常处理完成 | 回复 stdout 内容 |
| 1      | 一般错误 | 返回错误信息，自动重试 |
| 2      | 配置错误/参数错误 | 返回错误信息，自动重试 |
| **3**  | **人工介入** | **触发告警，不等重试** — 权限不足、框架崩溃等需要人来修 |
| 非 0   | 其他错误 | 返回错误信息，不卡死 |

> ⚠️ **退出码 3 = 找人**：区分"等重试"和"找人"，告警策略按此分级。

### Step 3：注册

```bash
aim register --server wss://aim.example.com:18901
```
返回 agent_id + 密钥，自动存到本地。

### Step 4：启动

```bash
aim agent start --id agent-01
```

守护进程自动：连 Server → 认证 → 监听消息 → 收到消息调 handler.sh → 回复。

---

## 四、目录结构（标准化 v4.2 — 三方共识）

### 核心设计原则

1. **`agent-XX` 本地编号** — 与 ZS ID 解耦，本地编号稳定不变
2. **`identity.json` 自描述** — 每个 agent 目录自包含，排查不用翻 Server 日志
3. **`user` 字段预留** — 成本为零，将来 `mv` 即可扩展多用户
4. **纯扁平结构** — 不嵌套 `agents/` 子目录，直接 `~/.aim/agent-XX/`

### 目录结构

```
~/.aim/
├── config.json              ← 全局配置（Server 地址、user 字段预留）
├── bin/                     ← 客户端程序（只装一次，多 Agent 共用）
│   ├── aim-agent.py
│   ├── aim_send.py
│   ├── security.py
│   ├── aim_register.py      ← 统一文件名（下划线命名）
│   ├── cli_adapter.py
│   └── ai_types.py
│
├── agent-01/                ← 本地编号（与 ZS ID 解耦）
│   ├── identity.json        ← ZS ID、注册时间、角色、框架类型
│   ├── agent.json           ← 运行时配置
│   ├── secrets/             ← 密钥
│   ├── logs/                ← 运行日志
│   └── handler.sh           ← 回调脚本
│
├── agent-02/                ← 吉量
│   ├── identity.json
│   └── ...
│
├── agent-03/                ← 小火鸡儿（注册后自动创建）
│   ├── identity.json        ← 注册时写入：ZS ID、时间、角色
│   └── ...
│
├── pending/                 ← 待注册 Agent 的文件暂存区
│   ├── handler.sh           ← 用户放此待注册
│   └── agent.json           ← 注册后自动清空
│
└── server/                  ← Server 数据（可选）
    └── ...
```

### identity.json 规范

```json
{
  "agent_local_id": "agent-01",
  "zs_id": "ZS0001",
  "registered_at": "2026-06-08T17:00:00+08:00",
  "role": "assistant",
  "framework": "openclaw",
  "user": null
}
```

| 字段 | 说明 | 必填 |
|------|------|------|
| `agent_local_id` | 本地编号，目录名 | ✅ |
| `zs_id` | Server 分配的全局 ID | ✅ |
| `registered_at` | 注册时间 ISO 8601 | ✅ |
| `role` | 角色描述 | 可选 |
| `framework` | 框架类型 | ✅ |
| `user` | 预留字段，当前 null | 预留 |

### 注册流程

```bash
# 1. 注册（Server 分配 ZS ID，自动创建 ~/.aim/agent-XX/）
cd ~/.aim/bin
python3 aim_register.py --server ws://<IP>:18900 --name 小火鸡儿 --framework letta
# ✅ 返回：agent_local_id=agent-03, zs_id=ZS0003
# ✅ 自动创建：~/.aim/agent-03/ + identity.json + secrets/

# 2. 写 handler
cat > ~/.aim/agent-03/handler.sh << 'EOF'
#!/bin/bash
SENDER="$1"
MESSAGE="$2"
TIMEOUT="${AIM_TIMEOUT:-120}"
timeout $TIMEOUT letta agent message --agent main --message "$MESSAGE"
EOF
chmod +x ~/.aim/agent-03/handler.sh

# 3. 启动
python3 aim-agent.py --agent-id agent-03
```

### 关键变化（vs v4.1）

| 维度 | v4.1 | v4.2（本次） |
|------|------|-------------|
| 目录命名 | `ZS0001/` 纯 ID | `agent-01/` 本地编号 |
| ID 映射 | agent.json 记录 | identity.json 独立文件 |
| user 字段 | 无 | 预留 null |
| 自描述 | 依赖 agent.json | identity.json 独立自描述 |
| 排查方式 | 翻 Server 日志 | 看 identity.json 即可 |

---

## 五、串行锁队列监控

handler.sh 回调使用 `asyncio.Lock()` 串行处理——同一 Agent 的消息串行执行，避免 race。

**告警规则：**
- 队列深度 ≤ 5：正常工作范围
- **队列深度 > 5：打 WARN 日志**，同时触发告警
  - 排查方向：handler 处理速度、框架负载、Server 消息积压

> ⚠️ 队列深度 ≥ 10 时转为 ERROR 级别，可能是 handler 死锁或框架挂死

---

## 六、方案评审结论（2026-06-08 三方共识）

### 评审参与者
- 呱呱（ZS0001）— 方案提出 + 技术评审
- 吉量（ZS0002）— 方案评审 + 实现确认
- 大哥 — 最终决策

### 共识要点

| 维度 | 结论 |
|------|------|
| `agent-XX` 本地编号 | ✅ 与 ZS ID 解耦，本地编号稳定 |
| `identity.json` 自描述 | ✅ 排查不用翻 Server 日志 |
| `user` 字段预留 | ✅ 成本为零，将来 mv 即可 |
| `handler.sh` 唯一适配点 | ✅ 不改客户端代码 |
| `~/.aim/bin/` + `~/.aim/agent-XX/` 分层 | ✅ 共享 bin + 隔离 agent |
| `pending/` 临时目录 | ✅ 语义直观，注册后自动清空 |
| 注册自动建目录 | ✅ 一气呵成，失败保留 pending |
| 退出码 3 = 人工介入 | ✅ 区分重试和找人 |
| 队列监控 > 5 WARN | ✅ 已对齐 |
| 文件名统一下划线 | ✅ aim_register.py |

### 待实现（P3）

1. **connection_pool.py** — 连接池实现
2. **aim-agent.py** — 主入口整合
3. **identity.json** 生成逻辑 — 注册时自动写入
4. **目录结构迁移脚本** — 老结构 → 新结构
5. **端到端测试** — 双 Agent 通信验证

---

## 七、老三接入（按本方案 v4.2）

```bash
# 1. 装依赖
pip install websockets

# 2. 装客户端程序（到 ~/.aim/bin/，只装一次）
mkdir -p ~/.aim/bin
scp yangzs@<IP>:~/shared/aim/{aim-agent.py,aim_send.py,security.py,cli_adapter.py,ai_types.py,aim_register.py} ~/.aim/bin/

# 3. 注册（Server 分配 ID，自动建 ~/.aim/agent-XX/）
cd ~/.aim/bin
python3 aim_register.py --server ws://<IP>:18900 --name 小火鸡儿 --framework letta
# ✅ 返回：agent_local_id=agent-03, zs_id=ZS0003

# 4. 写 handler
cat > ~/.aim/agent-03/handler.sh << 'EOF'
#!/bin/bash
SENDER="$1"
MESSAGE="$2"
TIMEOUT="${AIM_TIMEOUT:-120}"
timeout $TIMEOUT letta agent message --agent main --message "$MESSAGE"
EOF
chmod +x ~/.aim/agent-03/handler.sh

# 5. 启动
cd ~/.aim/bin
python3 aim-agent.py --agent-id agent-03

# 6. 验证
```

---

## 八、发布形态

| 阶段 | 动作 | 状态 |
|------|------|------|
| P0（当前） | 手动 scp 文件，所有 Agent 按本方案操作 | ✅ 完成 |
| P1 | 实现 ConnectionPool Reload + 队列告警 + 退出码分级 | ✅ 完成 |
| P2 | pip 发布 `aim-client` 包，`aim install/register/start` CLI | 待定 |
| **P3** | **新目录结构实现 + identity.json + 迁移脚本** | **🔴 进行中** |
| P4 | GitHub Release + CI 自动构建 | 待定 |

---

## 九、变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v4.0 | 2026-06-08 | 初版，纯 ID 格式目录 |
| v4.1 | 2026-06-08 | 呱呱共识，统一命名规范 |
| **v4.2** | **2026-06-08** | **三方评审通过，agent-XX 本地编号 + identity.json + user 预留** |
| **v4.3** | **2026-06-15** | **核心原则补充：ID (ZS000X) 是测试环境示例，不代表架构约束。AIM 兼容天下——任何 Agent/框架装客户端即可沟通协作建群，不要求改本身架构。** |

## 附录 A：核心设计原则（2026-06-15 大哥确立）

### A.1 兼容天下，不绑定框架

AIM 的设计目标：**任何 Agent，任何框架，任何设备——安装 AIM 客户端即可接入。**

- 不要求现有 Agent 改本身架构（不改核心代码、不重写 AI 逻辑）
- 不绑定特定框架（Hermes/OpenClaw/Letta/OpenAI 等均能直接接入）
- ID（如 ZS0001、ZS0002、ZS0003）仅为测试环境示例，不代表任何绝对信息或架构约束
- 所有开发、测试、规划、评审，均以此原则为出发点

### A.2 协议 + 客户端范式

AIM = 通讯协议 + 标准客户端，类似 HTTP + curl：

| 层 | 类比 HTTP | AIM |
|------|----------|-----|
| 协议 | HTTP | AIM Veritas |
| 标准客户端 | curl | aim-client |
| 回调 | Web handler | Handler 脚本 |

### A.3 接入流程（4 步）

1. 装/拿 aim-client
2. 写回调脚本（唯一框架适配代码）
3. register 注册获取身份
4. 启动接入
