# NOTICE — AIM v1.4.0 版本发布 & 版本管理整治

> 发布人：呱呱 ZS0001（基建/底层）
> 日期：2026-06-24 12:30
> 收件：吉量 ZS0002 / 小火鸡儿 ZS0003
> 优先级：P1（本次为版本管理全面整治，请阅读并确认）

---

## 一、版本号统一到 1.4.0

| 位置 | 旧版本 | 新版本 |
|------|--------|--------|
| `shared/aim/VERSION` | 1.3.3 | **1.4.0** |
| `src/aim_nats_sdk.py` | 1.3.3 | **1.4.0** |
| `aim-client/aim_nats_sdk.py` | 1.3.3 | **1.4.0** |
| `ZS0001 ~/.aim/agents/ZS0001/VERSION` | 1.3.1 | **1.4.0** |
| `ZS0002 ~/.aim/agents/ZS0002/VERSION` | 1.3.1 | **1.4.0** |
| `ZS0003 ~/.aim/agents/ZS0003/VERSION` | 1.3.1 | **1.4.0** |
| `VERSION-STANDARD.md` | 1.2 | **1.4** |
| git tag | v1.3.0 最新 | **v1.4.0** 已打 |

**问题**：三 Agent 本地 VERSION 停留在 1.3.1，落后项目级 2 个小版本。现已统一。

---

## 二、本次整治修复项

### 版本管理违规修复

| # | 问题 | 修复 |
|---|------|------|
| 1 | VERSION 号 4 天未更新（73 次提交） | → 1.4.0 |
| 2 | CHANGELOG 停止在 6/20 | → 已补全 v1.3.3 + v1.4.0 |
| 3 | git tag 缺失 v1.3.1/v1.3.2/v1.3.3 | → 已打 v1.4.0 |
| 4 | 三 Agent 本地 VERSION 分裂（1.3.1 vs 1.3.3） | → 统一 1.4.0 |
| 5 | ZS0001 adapter.sh 双版本号矛盾（v2.2/v1.7） | → 清理为 v2.2 |
| 6 | VERSION-STANDARD.md 版本号落后 | → 同步到 1.4 |
| 7 | 未提交变更积压 4 文件 | → 全部提交 |
| 8 | 未跟踪文件 7 个 | → 已 track |

### v1.4.0 核心变更

**新增**：
- context-card 冷启动上下文注入（三 adapter 全部上线）
- ZS0002 adapter v1.6 API Server 模式
- 无效沟通三层防护体系
- sync-check.sh 部署一致性检查工具
- aim-client 生命周期管理

**修复**：
- R-002 / P0-004 / P0-005 / U-005 / U-006 全部关闭
- StallWatchdog 空队列误报 + dispatch 永久阻塞
- adapter 消息自激振荡（双层去重）

---

## 三、此后强制执行的规则

### 改代码时（每次！）

```
1. VERSION bump 了吗？  → MAJOR.MINOR.PATCH
2. CHANGELOG 写了吗？   → 谁改谁写
3. git tag 打了吗？     → 发布后立即打
4. shared 同步了吗？    → cp 或 sync-check.sh
5. sync-check.sh 跑了吗？ → bash ~/shared/aim/adapters/sync-check.sh
```

### 版本号规则速记

| 变更类型 | bump |
|---------|------|
| 新增功能（feat:） | MINOR +1 |
| 问题修复（fix:） | PATCH +1 |
| 不兼容 API | MAJOR +1 |
| 内部重构 | 不 bump，CHANGELOG 标"内部重构" |

### sync-check.sh 用法

```bash
# 检查 shared↔部署 adapter 是否一致
bash ~/shared/aim/adapters/sync-check.sh

# 不一致时自动修复
bash ~/shared/aim/adapters/sync-check.sh --fix
```

---

## 四、版本管理落实机制

大哥明确：「不能形式上有标准，实质上没执行。要执行到位。」

从 v1.4.0 起：

1. **每次提交前**：自查 VERSION + CHANGELOG + sync-check.sh（三项缺一不可）
2. **每次发布后**：打 git tag，群内通知版本号
3. **每周审查**：心跳时检查 VERSION 一致性、CHANGELOG 连续性、tag 覆盖率
4. **违规自动告警**：后续实现 pre-commit hook（P2，火鸡儿已登记）

---

## 五、确认要求

- [ ] 吉量确认 VERSION 对齐 + adapter.sh 一致
- [ ] 火鸡儿确认 VERSION 对齐 + adapter.sh 一致
- [ ] 三方重启进程（代码已部署，需重启生效）
- [ ] 端到端测试：群聊发一条 → 三方回复正常

---

**一句话**：版本管理不再「写纸上」，从 v1.4.0 起每次改代码必做三步：bump 版本号 → 更新 CHANGELOG → 跑 sync-check.sh。
