# AIM 配置变量化 — 终版方案 v2

> 2026-06-17 | 呱呱牵头 | 吉量+火鸡儿联合设计
> 大哥指令：固化值变量化，消除绝对路径，迁移友好

---

## 一、硬编码全量清单（三方合并扫描）

| # | 位置 | 固化值 | 类型 | 首次发现 |
|---|------|--------|------|---------|
| 1 | `adapter.sh` L43 | `IDENTITY_FILE=".../ZS0001"` | 路径+ID | 呱呱 🔸OpenClaw专有 |
| 2 | `adapter.sh` L92 | `"to": "ZS0001"` | Agent ID | 呱呱 🔸OpenClaw专有 |
| 3 | `adapter.sh` L10 | `WORKSPACE="$HOME/.openclaw/workspace"` | 路径 | 呱呱 🔸OpenClaw专有 |
| 4 | `main.py` L191 | `peer_id in ("ZS0001","ZS0002","ZS0003")` | 信任域 | 呱呱 |
| 5 | `security.py` L27 | `allowlist: ["ZS0001","ZS0002","ZS0003"]` | 默认白名单 | 呱呱 |
| 6 | `group_admission.py` L136-138 | `owner="ZS0001", members=[...]` | grp_trio | 呱呱 |
| 7 | `launchd plist` | `/Users/yangzs/...` 全绝对路径 | 路径 | 呱呱 |
| 7b | ZS0003 plist | `LETTA_AGENT_ID` 环境变量与 config.json 重复 | 配置冗余 | 火鸡儿 🐤 |
| 8 | `main.py` L211 | `"nats://127.0.0.1:4222"` | NATS URL | 吉量/火鸡儿 |
| 9 | `registry.py` / `group_admission.py` | `"nats://127.0.0.1:4222"` 默认值 | NATS URL | 吉量/火鸡儿 |
| 10 | `main.py` L260 | `subscribe_grp("grp_trio", ...)` | 群聊名 | 吉量/火鸡儿 |
| 11 | SDK `aim_nats_sdk.py` | `"nats://127.0.0.1:4222"` 默认 | NATS URL | 吉量/火鸡儿 |

**共计：12 处硬编码**（呱呱 7 处 + 吉量/火鸡儿补 4 处 + 火鸡儿配置冗余 1 处）

---

## 二、设计原则

1. **分层配置，各安其位**：全局共用的走 `aim.json`，Agent 专属的走 `config.json`
2. **环境变量 > 配置文件 > 代码默认**（三级 fallback）
3. **一个值只定义一次**，所有引用指向它
4. **跨机器/用户/Agent 迁移**：只改配置，不动代码

### 配置分层

```
~/.aim/config/aim.json          ← 全局（机器级）：NATS、信任域、默认群、路径
~/.aim/agents/{ID}/config.json  ← Agent 级：adapter路径、framework、exec_model
~/.aim/agents/{ID}/identity.json ← Agent 身份卡：serial、name、capabilities
环境变量                         ← 运行时覆盖（容器/CI 场景）
```

---

## 三、aim.json 全局配置（吉量方案，呱呱补充）

```json
{
  "nats_server": "nats://127.0.0.1:4222",
  "auth_mode": "jwt",
  "trusted_peers": ["ZS0001", "ZS0002", "ZS0003"],
  "default_group": "grp_trio",
  "default_group_name": "三方群聊",
  "default_group_owner": "ZS0001",
  "paths": {
    "aim_root": "~/.aim",
    "adapters": "~/.aim/adapters",
    "shared": "~/shared/aim"
  },
  "agents": {
    "ZS0001": { "name": "呱呱", "framework": "openclaw" },
    "ZS0002": { "name": "吉量", "framework": "hermes" },
    "ZS0003": { "name": "小火鸡儿", "framework": "letta" }
  }
}
```

**字段职责：**
- `nats_server` → SDK + main.py + registry/group_admission 的默认连接
- `trusted_peers` → `verify_peer()` + `security.py` 默认 allowlist
- `default_group*` → grp_trio 的一切
- `paths` → 所有目录变量化的锚点
- `agents` → 注册表索引（不存 secret，creds 走 creds_path）

---

## 四、Agent config.json 扩展（呱呱方案，保留 Agent 专属）

```json
{
  "agent_id": "ZS0001",
  "agent_name": "呱呱",
  "nats_server": "nats://127.0.0.1:4222",
  "framework": "openclaw",
  "creator": "aim-client",
  "creator_version": "1.0.0",
  "adapter_cmd": "~/.aim/adapters/openclaw/adapter.sh",
  "adapter_timeout": 15,
  "execution_model": "realtime",
  
  "paths": {
    "identity_json": "~/.aim/agents/ZS0001/identity.json",
    "logs_dir": "~/.aim/agents/ZS0001/logs",
    "creds_path": "~/.aim/agents/ZS0001/aim.creds"
  }
}
```

> config.json 保持 Agent 粒度。`paths` 段为可选项，默认从 `aim.json` 全局 paths + agent_id 派生。

### 火鸡儿补充：plist 不写 Agent 专属 ID
> 吉量认可。当前 ZS0003 的 LETTA_AGENT_ID 在 plist 和 config.json 各写一遍，违反"一个值只定义一次"原则。
> **修正**：plist 只写不变的环境变量（AIM_HOME、AIM_AGENT_ID 由 launchd EnvironmentVariables 注入），Agent 专属 ID 统一从 config.json 读，由 aim-client 启动时注入。

---

## 五、环境变量对照表

| 变量名 | 覆盖字段 | 默认值 | 典型场景 |
|--------|---------|--------|---------|
| `AIM_NATS_URL` | nats_server | `nats://127.0.0.1:4222` | 换 NATS 端口/地址 |
| `AIM_HOME` | paths.aim_root | `~/.aim` | 换机器/安装位置 |
| `AIM_SHARED` | paths.shared | `~/shared/aim` | 共享目录迁移 |
| `AIM_AGENT_ID` | agent_id | `ZS0001` | CI 多 Agent 部署 |
| `AIM_DEFAULT_GROUP` | default_group | `grp_trio` | 群聊名变更 |
| `AIM_TRUSTED_PEERS` | trusted_peers | `ZS0001,ZS0002,ZS0003` | 加新 Agent |

---

## 六、逐项改动

### 6.1 SDK `load_global_config()` （吉量方案）

```python
def load_global_config() -> dict:
    """加载 ~/.aim/config/aim.json，环境变量覆盖"""
    cfg_path = Path.home() / ".aim" / "config" / "aim.json"
    cfg = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
    # 环境变量覆盖
    if os.environ.get("AIM_NATS_URL"):
        cfg["nats_server"] = os.environ["AIM_NATS_URL"]
    if os.environ.get("AIM_DEFAULT_GROUP"):
        cfg["default_group"] = os.environ["AIM_DEFAULT_GROUP"]
    if os.environ.get("AIM_TRUSTED_PEERS"):
        cfg["trusted_peers"] = os.environ["AIM_TRUSTED_PEERS"].split(",")
    return cfg
```

### 6.2 main.py 信任域 → aim.json

```python
# 原: return peer_id in ("ZS0001", "ZS0002", "ZS0003")
cfg = load_global_config()
peers = cfg.get("trusted_peers", ["ZS0001"])
return peer_id in peers
```

### 6.3 security.py 默认 allowlist

```python
# 原: DEFAULT_CONFIG = {"allowlist": ["ZS0001","ZS0002","ZS0003"], ...}
# 改为：默认空，运行时从 aim.json 注入
```

### 6.4 main.py 群聊订阅 → aim.json

```python
# 原: for gid in ["grp_trio"]:
cfg = load_global_config()
for gid in cfg.get("default_group", "grp_trio").split(","):
    await self.transport.subscribe_grp(gid, self._on_grp)
```

### 6.5 group_admission.py grp_trio → aim.json

```python
cfg = load_global_config()
grp = GroupInfo(
    group_id=cfg.get("default_group", "grp_trio"),
    name=cfg.get("default_group_name", "Default Group"),
    owner=cfg.get("default_group_owner", self.agent_id),
    members=cfg.get("trusted_peers", [self.agent_id]),
)
```

### 6.6 adapter.sh 彻底变量化（呱呱方案）

```bash
#!/bin/bash
# ── 变量解析 ──
: ${AIM_HOME:="$HOME/.aim"}
: ${AIM_AGENT_ID:?"AIM_AGENT_ID required"}
: ${AIM_SHARED:="$HOME/shared/aim"}
: ${AIM_WORKSPACE:="$HOME/.openclaw/workspace"}

CONFIG_FILE="$AIM_HOME/config/aim.json"
AGENT_CONFIG="$AIM_HOME/agents/$AIM_AGENT_ID/config.json"
IDENTITY_FILE="$AIM_HOME/agents/$AIM_AGENT_ID/identity.json"
LOG_DIR="$AIM_HOME/agents/$AIM_AGENT_ID/logs"
QUEUE_DIR="$AIM_WORKSPACE/.aim-queue"
REPLY_DIR="$AIM_WORKSPACE/.aim-replies"

# ... mode dispatch ...
# DM 模板: "to": "$AIM_AGENT_ID"（不再写死 ZS0001）
```

### 6.7 launchd plist → EnvironmentVariables 注入（呱呱改进吉量方案）

```xml
<!-- plist 中加环境变量块，launchd 原生支持 -->
<key>EnvironmentVariables</key>
<dict>
    <key>AIM_HOME</key>
    <string>/Users/yangzs/.aim</string>
    <key>AIM_AGENT_ID</key>
    <string>ZS0001</string>
</dict>
```
> 这比 wrapper.sh 更干净，launchd 原生支持 EnvironmentVariables 字典。

---

## 七、执行计划（依赖链）

```
Phase A: 配置先行（无代码改动，零风险）
  ├─ A1: 创建 ~/.aim/config/aim.json（如已存在则扩展字段）
  ├─ A2: 扩展 agent ZS0001/config.json 加 paths 段
  └─ A3: ZS0002/ZS0003 config.json 同步加 paths 段

Phase B: SDK 层
  ├─ B1: SDK 加 load_global_config()
  └─ B2: SDK 默认 NATS URL 改为读 aim.json

Phase C: 应用层
  ├─ C1: main.py 三处硬编码 → load_global_config()
  ├─ C2: registry.py / group_admission.py NATS URL → 读 aim.json
  ├─ C3: security.py 去硬编码默认值
  └─ C4: adapter.sh 变量声明块替换全硬编码

Phase D: 系统层
  ├─ D1: launchd plist 加 EnvironmentVariables
  └─ D2: 重启进程 → 全链路验证

Phase E: 验证
  ├─ E1: 三 Agent 各自启动验证
  ├─ E2: 换群聊名/加新 Agent → 只改 aim.json 验证
  └─ E3: 环境变量覆盖验证
```

---

## 八、改动范围统计

| 文件 | 改动行 | 性质 |
|------|--------|------|
| `aim.json`（新建/扩展） | +15 | 配置 |
| `ZS0001/config.json` | +6 | 配置 |
| `ZS0002/config.json` | +6 | 配置 |
| `ZS0003/config.json` | +6 | 配置 |
| `aim_nats_sdk.py` | +30 | 新增函数 |
| `main.py` | ~15 | 替换硬编码 |
| `registry.py` | ~3 | NATS URL |
| `group_admission.py` | ~10 | 默认群 + NATS URL |
| `security.py` | ~3 | 默认值 |
| `adapter.sh` | +20 | 变量声明块 |
| `ZS0001.plist` | +8 | EnvironmentVariables |

**总计：~122 行，不改逻辑，只换值来源。**

---

## 九、风险与回滚

| 风险 | 概率 | 缓解 |
|------|------|------|
| aim.json 不存在 | 低 | SDK `load_global_config()` 返回 `{}`，代码默认值兜底 |
| adapter.sh $AIM_AGENT_ID 未设置 | 中 | `: ${AIM_AGENT_ID:?}` 立即报错退出 |
| plist EnvironmentVariables 不生效 | 低 | 启动日志可验证，回退到 wrapper.sh |
| 旧进程缓存旧 .pyc | 中 | 改代码后清理 `__pycache__` + 重启 |

回滚方案：git revert 全部改动，恢复硬编码版本。config 文件不改代码逻辑。
