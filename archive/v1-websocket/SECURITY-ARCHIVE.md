# AIM v1 WebSocket 安全敏感信息归档
# 归档时间：2026-06-09
# 敏感级别：🔴 最高（已从运行目录移除，仅此副本）
# 用途：历史参考，NATS 架构已不再使用这些凭证
# 安全建议：这些密钥应立即停止使用

## WS 密钥 (HMAC)
- ZS0001: f4858a7d859d6f63...
- ZS0002: c389f70009d52c06...
- ZS0005: sk-aim...8b1c...
- ZS0004: sk-aim...fe78...
- ZS0006: sk-aim...a773...
- ZS0007: sk-aim...e6db...
- observer: de0ce58fb8035cb7...
- ZS0002_bak: b8dc58850b579f5c...

## WS Token
- ZS0001: guagua_token_zlig68
- ZS0002: jiliang_token_2026
- ZS0003: xiaohuoji_token_2026

## SSL 证书
-rw-------  1 yangzs  staff  1265  6  6 12:06 /Users/yangzs/.hermes/aim/secrets/cert.pem
-rw-------  1 yangzs  staff  1704  6  6 12:06 /Users/yangzs/.hermes/aim/secrets/key.pem

## 说明
- 以上密钥/token/证书仅用于旧 WS 体系（v1）
- NATS 架构使用新认证体系（JWT/Token），不依赖这些旧凭证
- 原始文件已从运行目录删除，移至安全归档
- 此归档不参与任何运行时
