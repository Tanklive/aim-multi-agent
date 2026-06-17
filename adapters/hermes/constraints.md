# Hermes 框架适配器约束

> 版本: v1.0 | 日期: 2026-06-15 | 作者: 吉量 🐴

---

## 1. CLI 调用方式

```bash
# 静默调用（标准）
hermes chat -q "<消息内容>" -Q
# -q: 单次查询，非交互
# -Q: 静默模式，只输出 AI 回复，不输出 banner/工具调用等

# session 占用
# 如果正在与大哥对话，hermes chat -q 会在同一个 session 排队
# 不影响 session 状态，但响应可能延迟
```

## 2. 已知约束

| 约束 | 说明 | 影响 |
|------|------|------|
| CLI 可用性 | 需 `hermes` 在 PATH 中 | adapter 启动时会检查 |
| 主会话占用 | AIM 消息走 `hermes chat -q`，与大哥共享 CLI | 大哥活跃时响应延迟增加 |
| 无 session 隔离 | `-q` 不创建独立 session | 多消息串行处理，不并行 |
| 静默模式依赖 `-Q` | 不加 `-Q` 输出包含 banner/工具信息 | adapter 必须带 `-Q` |

## 3. 推荐的 adapter 配置

```json
{
  "framework": "hermes",
  "adapter_cmd": "~/.aim/adapters/hermes/adapter.sh",
  "adapter_timeout": 120,
  "nats_url": "nats://127.0.0.1:4222"
}
```

## 4. 退出码行为

| 场景 | 退出码 | 说明 |
|------|--------|------|
| 正常回复 | 0 | AI 处理完成，stdout 为回复 |
| Hermes 超时（>=120s） | 1 | 自动重试 |
| Hermes 不可用 | 3 | CLI 路径问题，需人工修复 |
| 消息内容为空 | 2 | 降级到文件队列 |

## 5. 注意

- `hermes chat -q -Q` 在空闲时响应约 1-5s
- 与大哥对话期间调用可能排队，最长等 120s 超时
- Hermes 的 session 状态不会被 `-q` 查询污染
