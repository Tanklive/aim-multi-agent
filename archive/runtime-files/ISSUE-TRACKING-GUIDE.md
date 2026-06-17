# AIM 问题跟踪指南

> 更新时间: 2026-06-09

---

## 一、问题跟踪目录

所有问题、BUG、事件统一记录到以下目录：

```
~/shared/aim/
├── issues/                    # 问题跟踪
│   ├── ISSUE-template.md      # 问题模板
│   ├── ISSUE-001-nats-auth.md # NATS 认证未启用
│   └── ISSUE-002-launchd-keepalive.md
│
├── bugs/                      # BUG 跟踪
│   └── BUG-template.md        # BUG 模板
│
├── events/                    # 事件记录
│   ├── EVT-template.md        # 事件模板
│   └── EVT-001-nats-migration-complete.md
│
└── ISSUE-TRACKER.md           # 总问题清单
```

## 二、编号规则

| 类型 | 前缀 | 示例 |
|------|------|------|
| 问题 | ISSUE- | ISSUE-001, ISSUE-002 |
| BUG | BUG- | BUG-001, BUG-002 |
| 事件 | EVT- | EVT-001, EVT-002 |

**规则**: 编号递增，不重复

## 三、创建新问题

### 1. 复制模板
```bash
cp ~/shared/aim/issues/ISSUE-template.md ~/shared/aim/issues/ISSUE-XXX-标题.md
```

### 2. 填写内容
- 状态: 待处理/进行中/已完成
- 负责人: 呱呱/吉量/小火鸡儿
- 优先级: 🔴高/🟡中/🟢低

### 3. 更新总清单
在 `ISSUE-TRACKER.md` 中添加新条目

## 四、问题分类

### 高优先级 🔴
- 服务不可用
- 安全漏洞
- 影响所有 Agent

### 中优先级 🟡
- 功能缺陷
- 性能问题
- 影响部分 Agent

### 低优先级 🟢
- 优化建议
- 文档更新
- 非关键改进

## 五、当前待处理问题

| # | 问题 | 负责人 | 优先级 |
|---|------|--------|--------|
| ISSUE-001 | NATS 认证未启用 | 呱呱 | 🔴 高 |
| ISSUE-002 | NATS Server 缺少 launchd 保活 | 呱呱 | 🔴 高 |

## 六、联系人

有问题需要记录或讨论：
- **群聊**: grp_trio（三方沟通）
- **大哥转发**: 需要协调时通过大哥转达

---

*记录人: 小火鸡儿 🐤*
