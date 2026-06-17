# BUG-001: aim-observe 和 aim-watch 未带 Token 连接

> 状态: 待修复 | 负责人: 吉量 | 严重程度: 中等

## BUG 描述
NATS Server 已启用 Token 认证，但 `aim-observe.py` 和 `aim-watch.py` 裸连（不带 Token），导致 `Authorization Violation` 错误。

## 复现步骤
1. 启用 NATS Token 认证
2. 运行 `aim-observe.py` 或 `aim-watch.py`
3. 报错 `nats: 'Authorization Violation'`

## 期望行为
aim-observe 和 aim-watch 应从 `~/.aim/config/aim.json` 读取 `nats_token` 并带 Token 连接

## 实际行为
裸连 NATS Server，被认证拒绝

## 影响范围
- aim-observe 无法使用
- aim-watch 无法使用
- 不影响 Agent 通信（Agent 已带 Token）

## 修复方案
在 aim-observe.py 和 aim-watch.py 中添加 Token 读取逻辑：
```python
# 从配置文件读取 Token
config_path = Path.home() / ".aim" / "config" / "aim.json"
if config_path.exists():
    config = json.loads(config_path.read_text())
    credentials = config.get("nats_token", "")
```

## 测试用例
```bash
# 修复后应能正常运行
aim-observe --history 5
aim-watch --history 5
```

---
创建时间: 2026-06-09
发现人: 小火鸡儿 🐤
---
**修复时间**: 2026-06-09
**修复人**: 吉量
**修复方式**: `from_config()` 自动从 `~/.aim/config/aim.json` 读取 `nats_token`
**验证状态**: ✅ 通过
