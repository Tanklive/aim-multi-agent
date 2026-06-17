# AIM 目录结构重整方案（讨论稿）

> 发起人：呱呱🐸
> 日期：2026-06-09 09:01
> 状态：三方讨论中
> 触发：大哥指令 — "要把开发仓库、Server等分结构，不能一点章法没有"

---

## 一、当前问题

### 1.1 现状：一锅粥

```
~/shared/aim/                    # 开发仓库？运行目录？全是？
├── aim_server_nats.py           # Server 代码
├── registry.py                  # Server 注册表
├── aim_agent_nats.py            # Agent 代码
├── aim_agent_nats_adapter.py    # Agent 适配层
├── aim_nats_sdk.py              # SDK（也存在 ~/.aim/bin/）
├── aim_nats_client.py           # SDK（另一个版本？）
├── aim_send_nats.py             # 发消息工具
├── aim_observer.py              # Observer
├── framework_cli.py             # AI 调用
├── aim_pin.py                   # 去重组件
├── aim-light-agent.py           # 旧版 Agent
├── aim-agent.py                 # 旧版 Agent（68KB！）
├── node.py                      # 旧版 Server（1779行，该删）
├── connection_pool.py           # 旧版连接池
├── lifecycle.py                 # 旧版生命周期
├── delivery.py                  # 旧版投递
├── security.py                  # 安全模块
├── test_nats_poc.py             # 测试
├── test_nats_full_suite.py      # 测试
├── test_adapter_pin_retry.py    # 测试
├── test_sdk_basic.py            # 测试
├── run_e2e_test.py              # 测试
├── interop_test.py              # 测试
├── migrate_to_nats.py           # 迁移脚本
├── aim_nats/                    # 子目录（SDK+测试）
├── bin/                         # 子目录（部分工具）
├── tests/                       # 子目录（测试）
├── agents/                      # 子目录（Agent）
├── .pytest_cache/               # 测试缓存
├── .venv/                       # Python 虚拟环境
├── AIM-NATS-ARCHITECTURE.md     # 文档（10+个）
├── AIM-NATS-PROTOCOL.md
├── AIM-NATS-SLIM-PLAN.md
├── AIM-SERVER-SLIMMING-PLAN.md
├── AIM-ARCHITECTURE.md
├── AIM-INSTALL-GUIDE.md
├── aim-veritas.md
├── nats-upgrade-plan.md
├── adapter-interface-proposal.md
├── aim-nats-integration-plan.md
├── nats-phase2-test-checklist.md
└── ...（100+ 文件）
```

### 1.2 问题清单

| # | 问题 | 影响 |
|---|------|------|
| 1 | Server 代码和 Agent 代码混在一起 | 职责不清，部署混乱 |
| 2 | NATS 配置在 `~/.openclaw/config/` | 违反 aim-veritas §5 规范 |
| 3 | SDK 有两份（shared/aim + .aim/bin） | 版本不一致风险 |
| 4 | 旧代码没删（node.py 等 4400+ 行） | 误导、增加维护成本 |
| 5 | 测试代码和业务代码混放 | 目录臃肿 |
| 6 | 文档散落（10+ 个 .md） | 找不到、重复 |
| 7 | 运行时直接引用 shared/aim/ | 开发仓库 ≠ 运行目录 |

---

## 二、目标结构

### 2.1 原则

1. **开发与运行分离** — `~/shared/aim/` 是开发仓库，`~/.aim/` 是运行目录
2. **Server 与 Agent 分离** — Server 代码不在 Agent 目录下
3. **共享工具统一** — SDK/CLI 放 `~/.aim/bin/`，用参数区分身份
4. **Agent 目录隔离** — 每个 Agent 独立目录，互不交叉
5. **旧代码归档** — 不删但归档，保留历史

### 2.2 运行目录（~/.aim/）

```
~/.aim/
├── server/                          # 🔧 Server 专属（呱呱负责）
│   ├── nats.conf                    #    NATS 配置
│   ├── data/                        #    JetStream 持久化
│   │   └── jetstream/
│   ├── logs/                        #    Server 日志
│   ├── registry.py                  #    Agent 注册表（211行精简版）
│   ├── aim_server.py                #    Server 主入口（~300行）
│   └── aim_observer.py              #    Observer 事件（~150行）
│
├── bin/                             # 🔧 共享工具（三方共用）
│   ├── aim                          #    CLI 入口
│   ├── aim_nats_sdk.py              #    NATS 客户端 SDK（唯一一份）
│   ├── aim_send.py                  #    发消息工具
│   ├── aim-watch.py                 #    实时监控
│   ├── aim_nats_adapter.py          #    适配层基类
│   ├── aim_pin.py                   #    去重组件
│   └── framework_cli.py             #    AI 框架调用
│
├── agents/                          # 🔧 Agent 专属（各自负责）
│   ├── ZS0001/                      #    呱呱
│   │   ├── nats-agent.py            #      Agent 主入口
│   │   ├── handler.sh               #      消息处理回调
│   │   ├── config.json              #      Agent 配置
│   │   ├── secrets/                 #      密钥
│   │   ├── logs/                    #      Agent 日志
│   │   └── data/                    #      Agent 数据
│   ├── ZS0002/                      #    吉量
│   │   └── ...（同结构）
│   └── ZS0005/                      #    小火鸡儿
│       └── ...（同结构）
│
├── data/                            # 🔧 共享数据
│   └── messages.jsonl               #    消息归档
│
└── docs/                            # 🔧 文档（集中管理）
    ├── AIM-NATS-PROTOCOL.md
    ├── AIM-NATS-ARCHITECTURE.md
    └── MIGRATION.md
```

### 2.3 开发仓库（~/shared/aim/）

```
~/shared/aim/                        # 📦 开发仓库（代码同步源）
├── src/                             # 源码（与运行目录对应）
│   ├── server/
│   │   ├── registry.py
│   │   ├── aim_server.py
│   │   └── aim_observer.py
│   ├── bin/
│   │   ├── aim_nats_sdk.py
│   │   ├── aim_send.py
│   │   ├── aim-watch.py
│   │   ├── aim_nats_adapter.py
│   │   ├── aim_pin.py
│   │   └── framework_cli.py
│   └── agents/
│       ├── nats-agent.py            # Agent 模板
│       └── handler.sh               # Handler 模板
│
├── tests/                           # 测试代码
│   ├── test_nats_poc.py
│   ├── test_nats_full_suite.py
│   ├── test_e2e.py
│   ├── test_sdk_basic.py
│   ├── test_adapter_pin_retry.py
│   └── conftest.py
│
├── archive/                         # 旧代码归档
│   ├── v1-websocket/
│   │   ├── node.py
│   │   ├── connection_pool.py
│   │   ├── lifecycle.py
│   │   ├── delivery.py
│   │   ├── retry_integration.py
│   │   ├── aim-agent.py
│   │   └── aim-light-agent.py
│   └── README.md
│
├── scripts/                         # 工具脚本
│   ├── migrate_to_nats.py
│   └── deploy.sh                    # 同步到 ~/.aim/ 的部署脚本
│
├── docs/                            # 文档
│   ├── AIM-NATS-PROTOCOL.md
│   ├── AIM-NATS-ARCHITECTURE.md
│   ├── aim-veritas.md
│   └── CHANGELOG.md
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

## 三、迁移步骤

### Phase 0：清理（今天）
1. 删除 `shared/aim/` 中的旧代码 → 移到 `archive/v1-websocket/`
2. 删除重复的 SDK 文件（只保留一份在 `bin/`）
3. 删除 `.pytest_cache/`、`__pycache__/`、`.venv/`

### Phase 1：运行目录重整（今天~明天）
1. NATS 配置迁移到 `~/.aim/server/`
2. NATS 数据迁移到 `~/.aim/server/data/`
3. NATS 日志迁移到 `~/.aim/server/logs/`
4. 更新 launchd plist 指向新路径
5. 更新 `nats-server.conf` 中的路径

### Phase 2：代码迁移（明天）
1. Server 代码同步到 `~/.aim/server/`
2. SDK/工具同步到 `~/.aim/bin/`
3. Agent 代码同步到 `~/.aim/agents/{id}/`
4. 部署脚本（`deploy.sh`）自动完成同步

### Phase 3：验证（后天）
1. NATS Server 启动正常
2. 三个 Agent 能连接、注册、收发消息
3. Observer 事件正常
4. 测试全部通过

---

## 四、讨论事项

### Q1：NATS 二进制放哪？
- A：`/usr/local/bin/nats-server`（系统路径，brew 安装）→ ✅ 不动
- B：`~/.aim/server/nats-server`（自包含）→ 需要改 PATH

### Q2：shared/aim/ 还保留吗？
- A：保留为纯开发仓库，`deploy.sh` 同步到 `~/.aim/`
- B：直接用 `~/.aim/` 既是开发又是运行（简单但不规范）

### Q3：Agent 模板还是各自独立？
- A：`shared/aim/src/agents/` 放模板，`deploy.sh` 复制到各 Agent 目录
- B：各 Agent 目录完全独立，没有模板

### Q4：deploy.sh 怎么写？
```bash
# 同步 Server
cp src/server/*.py ~/.aim/server/
# 同步 SDK
cp src/bin/*.py ~/.aim/bin/
# 同步 Agent 模板
for agent in ZS0001 ZS0002 ZS0005; do
  cp src/agents/nats-agent.py ~/.aim/agents/$agent/
done
```

### Q5：何时切换？
- A：先切目录结构，代码不动（风险低）
- B：目录结构 + 代码一起切（一步到位）

---

## 五、分工建议

| 角色 | 负责 |
|------|------|
| 🐸 呱呱 | Server 目录重整 + deploy.sh + NATS 配置迁移 |
| 🐴 吉量 | SDK/工具目录重整 + docs 整理 |
| 🐤 小火鸡儿 | Agent 目录重整 + 测试代码整理 |

---

**请各方在群里回复：**
1. 对目录结构有无异议？
2. Q1~Q5 的选择？
3. 分工是否接受？
4. 时间节点建议？

大哥在等我们出方案，今天内要给结论。🐸
