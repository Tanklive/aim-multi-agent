# AIM 目录结构重整方案 v3（终版）

> 发起人：呱呱🐸 → 整合人：吉量🐴
> 日期：2026-06-09
> 状态：**终版（大哥过目后执行）**
> Q1~Q6 结论已定，各方意见已汇总

---

## 一、决策摘要（Q1~Q6 结论）

| 编号 | 事项 | 结论 | 决策依据 |
|------|------|------|---------|
| **Q1** | Server 路径 | ✅ **`~/aim-server/`** | 大哥确认方向 OK，呱呱推荐，隐藏独立 |
| **Q2** | `.aim/` 改名 | ✅ **保持 `~/.aim/` 不改名** | 已有数据，改名波及所有引用 — 老三意见 |
| **Q3** | plist 位置 | ✅ **放 `~/Library/LaunchAgents/`** | 系统标准位置，plist 本身指向运行目录 — 老三意见 |
| **Q4** | deploy 触发 | ✅ **先手动 `./deploy.sh`，稳定后加 git hook** | 快速验证，避免自动同步引入问题 — 老三意见 |
| **Q5** | 编号规则 | ✅ **REQ/ISSUE/BUG/EVT-XXX 递增编号，统一模板** | 清晰可追溯 |
| **Q6** | 迁移节奏 | ✅ **0 归档→1 新结构+NATS迁移→2 代码同步验证→3 旧目录清理** | 分步推进，每步可验收 |

### 各方意见汇总

**🐸 呱呱：**
- ✅ 三层分离同意
- 补充：tests/ 分 unit/integration/e2e 三级

**🐴 吉量：**
- ✅ 全部同意，负责 SDK/bin/docs/ 整理 + `.aim/` 迁移

**🐤 小火鸡儿：**
- ✅ Q2 保持 `~/.aim/` 不改名
- ✅ Q3 plist 放系统目录
- ✅ Q4 先手动 deploy.sh
- ✅ 分工接受（Agent 目录 + tests + archive）
- 补充建议：`src/` 加 `common/`，tests/ 分 unit/integration/e2e，archive/ 按版本

---

## 二、完整目录结构

### 2.1 基础设施层 — `~/aim-server/`

```
~/aim-server/
├── nats.conf                        # NATS 配置（端口 4222/8222）
├── data/
│   └── jetstream/                   # JetStream 持久化数据
├── logs/
│   └── nats-server.log              # Server 日志
├── registry.py                      # Agent 注册表
├── aim_server.py                    # AIM Server 主入口
├── aim_observer.py                  # Observer 事件
├── launchd/
│   ├── com.nats.server.plist        # → ln -s ~/Library/LaunchAgents/
│   └── com.aim.server.plist
└── scripts/
    ├── start.sh
    ├── stop.sh
    └── status.sh
```

### 2.2 应用层 — `~/.aim/`

```
~/.aim/
├── bin/                             # 共享工具（三方共用）
│   ├── aim                          # CLI 入口
│   ├── aim_nats_sdk.py              # NATS 客户端 SDK
│   ├── aim_send.py                  # 发消息工具
│   ├── aim-watch.py                 # 实时监控
│   ├── aim_nats_adapter.py          # 适配层基类
│   ├── aim_pin.py                   # 去重组件
│   └── framework_cli.py             # AI 框架调用
│
├── common/                          # 🆕 通用模块（老三建议）
│   ├── aim_pin.py                   # 去重（跨 Agent 共享）
│   └── aim_retry.py                 # 重试（跨 Agent 共享）
│
├── agents/                          # Agent 专属（各自独立）
│   ├── ZS0001/                      # 呱呱
│   │   ├── nats-agent.py
│   │   ├── handler.sh
│   │   ├── config.json
│   │   ├── secrets/
│   │   ├── logs/
│   │   └── data/
│   ├── ZS0002/                      # 吉量（同结构）
│   └── ZS0005/                      # 小火鸡儿（同结构）
│
├── data/                            # 共享数据
│   └── messages.jsonl
│
└── config/
    └── aim.json                     # 全局配置
```

### 2.3 开发仓库 — `~/shared/aim/`

```
~/shared/aim/
├── src/                             # 源码
│   ├── server/                      # Server 代码
│   │   ├── registry.py
│   │   ├── aim_server.py
│   │   └── aim_observer.py
│   ├── bin/                         # 共享工具
│   │   ├── aim_nats_sdk.py
│   │   ├── aim_send.py
│   │   ├── aim-watch.py
│   │   ├── aim_nats_adapter.py
│   │   ├── aim_pin.py
│   │   └── framework_cli.py
│   ├── common/                      # 🆕 通用模块（老三建议）
│   │   ├── aim_pin.py
│   │   └── aim_retry.py
│   └── agents/                      # Agent 模板
│       ├── nats-agent.py
│       └── handler.sh
│
├── clients/                         # 客户端制作
│   ├── ZS0001/build.sh + config.json + dist/
│   ├── ZS0002/（同结构）
│   └── ZS0005/（同结构）
│
├── requirements/                    # 需求管理
│   ├── REQ-001-nats-migration.md
│   ├── REQ-002-directory-restructure.md
│   ├── REQ-template.md
│   └── backlog.md
│
├── issues/                          # 问题跟踪
│   ├── ISSUE-001-nats-auth.md
│   ├── ISSUE-002-sdk-duplicate.md
│   ├── ISSUE-template.md
│   └── README.md
│
├── bugs/                            # BUG 跟踪
│   ├── BUG-001-handler-timeout.md
│   ├── BUG-002-offline-dedup.md
│   ├── BUG-template.md
│   └── README.md
│
├── events/                          # 事件记录
│   ├── EVT-001-phase1-launch.md
│   ├── EVT-002-nats-cutover.md
│   └── README.md
│
├── tests/                           # 测试代码（三级分离—呱呱建议）
│   ├── unit/                        #   🆕 单元测试
│   │   ├── test_sdk_basic.py
│   │   ├── test_adapter_pin_retry.py
│   │   └── conftest.py
│   ├── integration/                 #   🆕 集成测试
│   │   ├── test_nats_poc.py
│   │   └── test_nats_full_suite.py
│   └── e2e/                         #   🆕 端到端测试
│       └── test_e2e.py
│
├── archive/                         # 旧代码归档（按版本—老三建议）
│   └── v1-websocket/
│       ├── node.py
│       ├── connection_pool.py
│       ├── lifecycle.py
│       ├── delivery.py
│       ├── retry_integration.py
│       ├── aim-agent.py
│       ├── aim-light-agent.py
│       └── ARCHIVE-V1-README.md     # 🆕 归档说明
│
├── scripts/                         # 工具脚本
│   ├── deploy.sh                    # 同步到运行目录
│   └── migrate_to_nats.py           # 迁移脚本
│
├── docs/                            # 文档
│   ├── AIM-NATS-PROTOCOL.md
│   ├── AIM-NATS-ARCHITECTURE.md
│   ├── aim-veritas.md
│   ├── aim-nats-architecture-final.md  # 完整架构方案（吉量出）
│   ├── DIRECTORY-RESTRUCTURE.md
│   ├── CHANGELOG.md
│   └── README.md
│
├── config/                          # 配置模板
│   ├── nats.conf.template
│   └── agent-config.template.json
│
├── README.md
├── VERSION
└── .gitignore
```

---

## 三、分工（终版，根据框架特长）

| 角色 | 职责范围 | 具体负责 |
|------|---------|---------|
| **🐸 呱呱（老大）** | Server 层 | `~/aim-server/` 创建 + NATS 配置迁移 + `deploy.sh` 主逻辑 + Server 进程管理 |
| **🐴 吉量（老二）** | SDK + 方案 | `~/.aim/bin/` 整理 + docs 整理 + `.aim/` 迁移 + 架构方案文档终版 |
| **🐤 小火鸡儿（老三）** | Agent + 测试 | `~/.aim/agents/` 目录整理 + tests 整理（unit/integration/e2e） + archive 归档（按版本） |

---

## 四、执行计划

### Phase 0：归档 — 今天（吉量负责）
- 旧 WS 代码（node.py/connection_pool/delivery 等）移入 `archive/v1-websocket/`
- 加 `ARCHIVE-V1-README.md` 说明
- 验证 NATS 链路不受影响

### Phase 1：创建目录结构 + 迁移 NATS — 今天（呱呱+吉量）
1. 呱呱：创建 `~/aim-server/`，迁移 nats.conf + JetStream 数据
2. 呱呱：`~/Library/LaunchAgents/com.nats.server.plist` 指向新区
3. 吉量：调整 `~/.aim/bin/` + `~/.aim/agents/` 结构
4. 吉量：创建 `~/.aim/config/aim.json`

### Phase 2：代码同步 + 验证 — 明天（三方）
1. 吉量：完成 `deploy.sh`（从 `~/shared/aim/` 同步代码）
2. 小火鸡儿：整理 tests 分 unit/integration/e2e
3. 三方：跑通全部测试，验证通信正常

### Phase 3：旧目录清理 — 后天
1. 删除 `~/.openclaw/config/nats-server.conf`
2. 删除 `~/.openclaw/data/nats/`
3. 删除 `~/.hermes/aim/nats-data/`
4. 删除松散旧文件

---

## 五、三方确认表

| 事项 | 🐸 呱呱 | 🐴 吉量 | 🐤 小火鸡儿 |
|------|---------|---------|------------|
| Q1 `~/aim-server/` | ✅ | ✅ | — |
| Q2 保持 `~/.aim/` | — | ✅ | ✅ |
| Q3 plist 系统目录 | — | ✅ | ✅ |
| Q4 先手动 deploy | — | ✅ | ✅ |
| Q5 编号规则 | — | — | — |
| Q6 迁移节奏 | — | — | — |
| 分工 | ✅ Server | ✅ SDK/docs | ✅ Agent/tests |
| 三层分离 | ✅ | ✅ | ✅ |
| common/ 加 src/ | — | — | ✅建议 |
| tests 分三级 | ✅建议 | — | — |

> ✅ = 同意 / ✅建议 = 同意并补充 / — = 尚未发表意见

---

## 六、附件

### deploy.sh（终版）

```bash
#!/bin/bash
# AIM Deploy — 从开发仓库同步到运行目录
set -e

SHARED_DIR="$HOME/shared/aim"
SERVER_DIR="$HOME/aim-server"
AIM_DIR="$HOME/.aim"

echo "=== AIM Deploy ==="

# 1. 同步 Server 代码
echo "[1/3] Syncing Server..."
cp -r "$SHARED_DIR/src/server/"* "$SERVER_DIR/"

# 2. 同步共享工具 + common 模块
echo "[2/3] Syncing shared tools..."
for f in aim_nats_sdk.py aim_send.py aim-watch.py aim_nats_adapter.py aim_pin.py framework_cli.py; do
    cp "$SHARED_DIR/src/bin/$f" "$AIM_DIR/bin/"
done
cp -r "$SHARED_DIR/src/common/"* "$AIM_DIR/common/" 2>/dev/null || true

# 3. 同步 Agent 模板
echo "[3/3] Syncing agent templates..."
for agent in ZS0001 ZS0002 ZS0005; do
    if [ -d "$AIM_DIR/agents/$agent" ]; then
        cp "$SHARED_DIR/src/agents/nats-agent.py" "$AIM_DIR/agents/$agent/"
        if [ ! -f "$AIM_DIR/agents/$agent/handler.sh" ]; then
            cp "$SHARED_DIR/src/agents/handler.sh" "$AIM_DIR/agents/$agent/"
        fi
    fi
done

echo "=== Deploy complete ==="
```

### 问题清单（小火鸡儿记录进度）

| # | 事项 | 状态 | 负责 | 备注 |
|---|------|------|------|------|
| 1 | 重新注册（新JWT→新配置） | ⏳ 待推进 | 三方 | 目录迁移后执行 |
| 2 | Server目录迁移到 ~/aim-server/ | ⏳ Phase 1 | 呱呱 | 含 nats.conf + JetStream 数据 |
| 3 | 旧WS代码归档到 v1-websocket/ | ⏳ Phase 0 | 吉量 | node.py/delivery/connection_pool 等 |
| 4 | 新Observer/Observer+JWT开发 | ⏳ Phase 2后 | 吉量 | 全新开发，约~100行 |
| 5 | 方案文档过目 | ⏳ 大哥过目 | 吉量 | ~/shared/aim/aim-nats-architecture-final.md |
| 6 | 三方联调迁移 | ⏳ Phase 3 | 三方 | 停旧WS，切NATS |
