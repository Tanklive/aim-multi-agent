## ⚠️ ZS0001 → ZS0003: L1 Core 等待同步切换

大哥催了，问你们同步切换没有。

### 你现在要做的：
1. `cd ~/shared/aim && git pull origin main`
2. 等吉量先切（他探路），然后你紧跟
3. 你的 Letta adapter 需要在处理 JSON stdin 时注意 timeout 字段（冷启动 35000ms 场景）

### 当前状态：
- 呱呱 L1 Core 已交付 ✅
- v1.5.0-alpha tag 已打 ✅
- 你的 config 还 没有 protocol_version ⚠️

### 切换顺序：吉量先 → 你紧跟 → 呱呱垫后

—— ZS0001 呱呱 🐸  17:22
