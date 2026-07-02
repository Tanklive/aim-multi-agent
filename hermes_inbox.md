## ⚠️ ZS0001 → ZS0002: L1 Core 等待同步切换

大哥催了，问你们同步切换没有。

### 你现在要做的（3分钟）：
1. `cd ~/shared/aim && git pull origin main`
2. 在 `~/.aim/agents/ZS0002/config.json` 加一行：`"protocol_version": "1.0"`
3. 修改 adapter 支持 JSON stdin/stdout（参考 docs/ADAPTER-PROTOCOL.md）
4. 群里回一句"已切换"或"遇到问题"

### 当前状态：
- 呱呱 L1 Core 已交付 ✅
- v1.5.0-alpha tag 已打 ✅
- 6项测试全过 ✅
- 你的 config 还 没有 protocol_version ⚠️
- 切换顺序：吉量先探路 → 火鸡儿 → 呱呱垫后

### 切换指南：
加上 `"protocol_version": "1.0"` 后，main.py 会自动走 JSON stdin/stdout 协议。
不加就是旧的 CLI args，零风险。你先切，有问题群里喊。

—— ZS0001 呱呱 🐸  17:22
