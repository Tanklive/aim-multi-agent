# Exit Code 对齐验证

> 日期: 2026-06-20
> 问题: U-102 — main.py 对 adapter 退出码 3/4 的解读是否正确
> 结论: ✅ 映射正确，三端语义对齐

## main.py 映射

| exit code | 异常类型 | 行为 |
|:--:|------|------|
| 0 | (正常) | 消息处理成功，回复已发送 |
| 1 | RetryableError | 可重试（session忙等） |
| 2 | DegradeError/RetryableError | 依降级窗口判断 |
| 3 | HumanInterventionError | FATAL — 永久停止，需大哥介入 |
| 4 | DegradeError | AGENT_UNREACHABLE — 降级可恢复 |

## 三端适配器语义

### ZS0001 (OpenClaw)
- 退出码: 0/1/2 — 不使用 3/4
- `info` 返回标准格式: `{"provider":"openclaw","version":"v2.0","project":"1.3.3"}`
- ✅ 简化映射正确

### ZS0002 (Hermes)
- 退出码: 0=SUCCESS, 1=RETRY, 2=DEGRADE, 3=FATAL, 4=AGENT_UNREACHABLE
- v1.3 对齐: 未知参数/缺参数 exit=2→3
- ✅ 与 main.py 映射一致

### ZS0003 (Letta)
- exit 3: `_detect_letta` CLI 不存在 → FATAL ✅
- exit 4: `_verify_agent_id` agent 数据不在磁盘 → AGENT_UNREACHABLE ✅
- ✅ 与 main.py 映射一致

## 结论

- **代码层面**: 无修复需要
- **U-102 状态**: 已验证通过，关闭
