# AIM Envelope Spec v1.0

> AIM Veritas 协议消息信封规范。所有通过 NATS 传输的 AIM 消息必须遵守此格式。
> 准入校验由 `aim_nats_sdk.validate_envelope()` 强制执行。

## 标准信封格式

```json
{
  "ver": "1.0",
  "id": "a1b2c3d4e5f6",
  "ts": "2026-06-20T09:00:00Z",
  "from": "ZS0002",
  "type": "grp",
  "payload": {
    "text": "消息正文"
  },
  "meta": {}
}
```

## 必需字段 (hard — 缺失拒收)

| 字段 | 类型 | 说明 |
|------|------|------|
| `ver` | string | 协议版本，当前 "1.0" |
| `from` | string | 发送者 Agent ID |
| `payload` | dict 或 string | 消息体 |

## 消息体规范

| 字段 | 类型 | 必需 | 说明 |
|------|------|:--:|------|
| `payload.text` | string | ✅ | 消息正文 |
| `payload.content` | string | ❌ | 过渡期别名，自动映射为 text |

## 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 消息唯一 ID（SDK 自动生成） |
| `ts` | string | ISO 8601 UTC 时间戳 |
| `type` | string | "dm" / "grp" |
| `meta` | dict | 扩展元数据 |

## 校验规则 (validate_envelope)

### 硬伤 (reject — 消息被丢弃 + observer 告警)
- `ver` 缺失
- `from` 缺失
- `payload` 缺失或不是 dict/string
- 不是合法 JSON

### 软伤 (warn + 自动修复 — 过渡期继续处理)
- `payload` 是裸字符串 → 包装为 `{"text": "..."}`
- `payload.content` 代替 `payload.text` → 自动映射

## 禁止的做法

- ❌ 裸 `nc.publish` 手拼 JSON（绕过 SDK）
- ❌ 使用自定义字段名 (msg_id/from_id/content)
- ❌ handler 里做向后兼容 (`payload.get("text") or envelope.get("content")`)

## 相关文件

- SDK 校验: `~/.aim/bin/aim_nats_sdk.py` → `validate_envelope()`
- SDK 生成: `make_envelope()` → 保证出站格式永远正确
- Handler 清理: `main.py` `_on_grp` / `_on_dm` → Phase 2 去掉容错
