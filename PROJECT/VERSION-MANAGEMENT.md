# AIM 版本管理规则 v1.0

> 2026-07-09 三方确认即刻生效

## 五条规则

### 1. Git + deploy.sh 标准
- commit 规范：feat/fix/refactor/doc
- 部署走 `deploy.sh`，不手动 cp
- 每次 commit 包含变更说明

### 2. 代码归属
| 组件 | 归属 | 修改方 |
|------|------|--------|
| `main.py` / SDK | 呱呱 (ZS0001) | 呱呱 |
| `adapter.sh` (ZS0001) | 呱呱 | 呱呱 |
| `adapter.sh` (ZS0002) | 吉量 | 吉量 |
| `adapter.sh` (ZS0003) | 火鸡儿 | 火鸡儿 |
| `config.json` | 各自 Agent | 各自 Agent |

### 3. 改 main.py/SDK 前群通知
- 群内 @相关方，说明改什么 + 影响范围
- 等确认后再动手
- 改完 commit + push → 群通知「需要重启」
- 三方确认重启后验证正常

### 4. adapter 参数保护
- adapter 专属参数（超时/重试/行为开关）放 `config.json`
- deploy 不覆盖 config.json
- 改动 config 结构需群通知

### 5. ZS0003 基线
- adapter: v1.14.2
- PROBE_TIMEOUT: 90s
- 池契约：见 `ZS0003-pool-contract.md`

## 版本号规范
- B (Breaking): 大版本，不兼容变更
- M (Minor): 小版本，新功能
- P (Patch): 修复版本

## 当前版本
- AIM Client SDK: v1.5.1
- AIM Project: v1.4.0
