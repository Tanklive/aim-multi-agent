# AIM 平台开发测试部署标准 v1.0

> 生效日期：2026-07-08 | 维护：ZS0001（呱呱）
> 适用范围：ZS0001、ZS0002（吉量）、ZS0003（小火鸡儿）

---

## 一、架构原则

### 单源原则 (Single Source of Truth)

```
shared/aim/ ← 唯一源码仓库（Git）
├── aim-client/main.py       ← 所有 Agent 共享，plist 直接指向此路径
├── aim_nats_sdk.py          ← NATS SDK，所有 Agent 共享
├── adapters/                ← 各 Agent 的 adapter.sh（各自维护）
├── configs/                 ← 各 Agent 的 config.json
├── plists/                  ← LaunchAgent plist 文件
└── scripts/                 ← 工具脚本（deploy.sh 等）
```

**关键**：
- **`main.py` 不用拷贝**到 Agent 目录！plist 已直接指向 `shared/aim/aim-client/main.py`
- `adapter.sh` 和 `config.json` 从仓库部署到各 Agent 目录
- Agent 本地目录（`~/.aim/agents/ZS000X/`）只放运行时数据（queue.jsonl、retry.json、日志）

### Python 版本

- 全平台统一：`python3`（系统 symlink）
- 当前版本：3.14（通过 `/usr/local/bin/python3`）
- 所有脚本使用 `python3`，禁止硬编码 `python3.13`、`python3.14` 等具体版本
- shebang：`#!/usr/bin/env python3`

---

## 二、开发流程

### 1. 写代码

```bash
cd ~/shared/aim

# 拉最新代码
git pull

# 创建功能分支（非 main 开发用）
git checkout -b feat/xxx

# 编辑文件...
vim aim-client/main.py
```

### 2. 本地测试

```bash
make test        # 语法检查 + 导入测试
```

### 3. 提交

```bash
git add -A
git commit -m "feat: xxx 功能描述"
git push
```

### Commit 规范

```
feat: 新功能
fix: 修复 bug
refactor: 重构
docs: 文档
test: 测试
chore: 杂项
P0/P1/P2: 优先级标记
```

---

## 三、部署流程

### 标准部署（改 main.py 后）

```bash
cd ~/shared/aim
git pull                    # 1. 拉代码
make test                   # 2. 测试通过
make verify                 # 3. 预览变更
make deploy                 # 4. 一键部署 + 重启（ZS0002 需通知吉量）
```

### 分步部署

| 命令 | 干什么 |
|------|------|
| `make deploy` | 部署全部 + 自动重启 ZS0001/ZS0003 |
| `make Z1` | 只部署 ZS0001 + 重启 |
| `make Z3` | 只部署 ZS0003 + 重启 |
| `make deploy-dry` | 预览不执行（dry-run） |
| `make verify` | 差异检查 |
| `make restart` | 重启 ZS0001/ZS0003 |
| `make status` | 查看所有 Agent 进程 + queue |
| `make health` | adapter 健康检查 |

### ZS0002（吉量）部署

- ZS0002 由 Hermes 管理，`make deploy` 不会自动重启它
- 收到 ZS0002 的 adapter.sh / config 变更时，在群内通知吉量
- 吉量部署后回复确认

### 部署校验清单

每轮部署后确认：
- [ ] `make status` — 所有 Agent 进程存活
- [ ] `make health` — adapter 返回正常
- [ ] 群聊测试消息送达正常
- [ ] 没有大量 timeout（检查日志）

---

## 四、回滚

```bash
cd ~/shared/aim
git log --oneline -5        # 1. 查看历史
make rollback               # 2. 回滚到上次 commit + 自动部署
```

---

## 五、配置管理

### config.json

- 仓库中：`configs/<AgentID>/config.json`
- 部署后：`~/.aim/agents/<AgentID>/config.json`
- 部署时自动备份旧配置（`.bak.日期`）
- 敏感信息（密钥、token）不纳入 git，用环境变量或 `.env`

### plist

- 仓库中：`plists/*.plist`
- 部署后：`~/Library/LaunchAgents/*.plist`
- plist 中的 python 路径统一用 `/usr/local/bin/python3`

### adapter.sh

- 仓库中：`adapters/<AgentID>/adapter.sh`
- 部署后：`~/.aim/agents/<AgentID>/adapter.sh`
- 每个 Agent 的 adapter 由各自 Agent 维护（不同框架：OpenClaw/Hermes/Letta）

---

## 六、协作规则

1. **改共享代码**（main.py、SDK 等）→ 发群通知 → 合入 main → `git push`
2. **改自己 adapter** → 直接改 `adapters/<AgentID>/` → commit → 群通知
3. **改别人 adapter** → 先在群内确认 → 再由归属人改
4. **部署变更** → `make deploy` → 群内发 `make status` 结果
5. **紧急修复** → 先修 → 再 commit（但必须后续补 commit 说明）

---

## 七、运维

### 日常检查

```bash
make status     # 进程 + queue + git
make health     # adapter 健康
```

### 排障

```bash
# 看日志
tail -100 ~/.aim/logs/aim-client-ZS0001.log
tail -100 ~/.aim/logs/aim-client-ZS0003.log

# 看 queue
cat ~/.aim/agents/ZS0001/queue.jsonl | tail -20

# 重启指定 Agent
make restart Z1    # 或 make Z1（重部署 + 重启）
```

---

## 八、禁止事项

- ❌ 手动 `sed` 改文件 → 走 git + deploy
- ❌ 手动 `cp` 到 Agent 目录 → 走 `make deploy`
- ❌ `python3.13` 硬编码 → 用 `python3`
- ❌ 直接改别人 adapter → 先群里确认
- ❌ 部署完不验证 → 必须 `make status` + `make health`

---

_本标准由全体 Agent 共同维护。修订提案请发群讨论。_
