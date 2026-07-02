# AIM Client 运行时服务发现 — 架构与数据流

> 定位：AIM Client 功能，与 AIM Server（NATS + Registry）无关
> 作者：吉量

---

## 一、定位

```
AIM Server 侧（NATS + Registry）
  └─ 不参与。只负责消息路由，不管理 Agent 的 API key

AIM Client 侧（main.py + adapter.sh）
  └─ 运行时服务发现 ← 这个功能在这里
       从 Agent Card 读取 API 端点信息
       注入 adapter 环境变量
       adapter 用这些变量调用 Agent Runtime
```

---

## 二、Key 从哪来

### 当前状态（混乱）

```
同一个 key "aim-adapter-local-key" 同时存在三处：

1. ~/.hermes/.env          → API_SERVER_KEY=aim-adapter-local-key    （手动写）
2. ~/.hermes/config.yaml   → platforms.api_server.extra.key          （hermes config set）
3. ~/.aim/agents/ZS0002/
     config.json           → adapter_env.HERMES_API_KEY              （手动写）

三处独立维护，没有任何同步机制。改一处另外两处不知道。
```

### Key 的真实来源

**Key 是 Hermes Gateway 自己要求的。** 源码依据：

```python
# hermes-agent/gateway/platforms/api_server.py L770
self._api_key: str = extra.get("key", os.getenv("API_SERVER_KEY", ""))

# L958
if not self._api_key:
    return None  # 无 key 时不验证（不安全）
```

即 Hermes Gateway 的 API Server 模块需要 key，从两个地方读：
1. `config.yaml` → `platforms.api_server.extra.key`（优先）
2. `.env` → `API_SERVER_KEY`（fallback）

**这个 key 是 Hermes 自己的事，应该由 Hermes Gateway 管理。** AIM Client 不应该帮 Hermes 管理 key，只需要知道 key 是什么。

---

## 三、完整流程（设计目标）

```
┌─ 步骤 1：Hermes 侧设置 key（用户操作，一次性）────────────────┐
│                                                                │
│  hermes gateway setup                                          │
│    → 生成 key → 写入 ~/.hermes/.env                            │
│      HERMES_GATEWAY_KEY=sk-xxxx                                │
│                                                                │
│  hermes gateway restart                                        │
│    → Gateway 从 config.yaml 或 .env 读取 key                   │
│    → API Server 启动，验证所有请求的 Authorization header       │
└────────────────────────────────────────────────────────────────┘
                              │
                              │ 用户复制 key（或自动 discover）
                              ▼
┌─ 步骤 2：AIM Client 声明服务（config.json，一次性）──────────┐
│                                                                │
│  {                                                             │
│    "services": {                                               │
│      "api": {                                                  │
│        "url": "http://127.0.0.1:8642",                         │
│        "auth": {                                               │
│          "type": "bearer",                                     │
│          "credential": "${HERMES_GATEWAY_KEY}"                 │
│        }                                                       │
│      }                                                         │
│    }                                                           │
│  }                                                             │
│                                                                │
│  credential 是 ENV 引用，不是明文。                              │
│  公网部署时 url 改成 https://hermes.example.com:8642            │
└────────────────────────────────────────────────────────────────┘
                              │
                              │ AIM Client 启动时
                              ▼
┌─ 步骤 3：AIM Client 运行时服务发现（main.py 自动）──────────┐
│                                                                │
│  services = config.get("services", {})                          │
│  api = services.get("api")                                     │
│  if api:                                                       │
│      url = api["url"]                                          │
│      cred_ref = api["auth"]["credential"]   # "${HERMES_GATEWAY_KEY}"
│      if cred_ref.startswith("${"):                             │
│          env_var = cred_ref[2:-1]           # "HERMES_GATEWAY_KEY"
│          cred = os.getenv(env_var, "")                          │
│      adapter_env["AIM_API_URL"] = url                          │
│      adapter_env["AIM_API_CREDENTIAL"] = cred                  │
└────────────────────────────────────────────────────────────────┘
                              │
                              │ dispatch → adapter
                              ▼
┌─ 步骤 4：adapter 通用调用（框架无关）────────────────────────┐
│                                                                │
│  : ${AIM_API_URL:=""}                                          │
│  : ${AIM_API_CREDENTIAL:=""}                                   │
│                                                                │
│  if [ -n "$AIM_API_URL" ] && [ -n "$AIM_API_CREDENTIAL" ]; then│
│      # API Server 通道                                          │
│      curl -H "Authorization: Bearer *** ...                     │
│  else                                                          │
│      # CLI 降级（Letta / 无 API Server 的 Agent）              │
│      $AGENT_BIN chat -q ...                                    │
│  fi                                                            │
└────────────────────────────────────────────────────────────────┘
```

---

## 四、为什么这是 AIM Client 功能

```
AIM 的架构中：

  AIM Server = NATS 消息总线 + Registry（服务注册/发现）
    → 不管 Agent 内部怎么处理消息
    → 不管 Agent 有没有 API Server
    → 不管 key 是什么

  AIM Client = main.py + adapter.sh + Agent Runtime
    → 收到 NATS 消息 → dispatch 给 adapter
    → adapter 调用 Agent Runtime 处理
    → Agent Runtime 可能是 API Server、CLI、webhook 等
    → 需要知道端点 URL 和认证凭证 ← 这就是服务发现

  所以 API endpoint 的发现和传递 = AIM Client 的职责。
```

---

## 五、各框架如何声明

| 框架 | services.api | 说明 |
|------|-------------|------|
| Hermes | url:8642, bearer ${HERMES_GATEWAY_KEY} | Hermes Gateway 自带 API Server |
| OpenClaw | url:18789, bearer ${OPENCLAW_KEY} | OpenClaw Gateway 模式 |
| Letta | 不填 api | 无 API Server，adapter 自动 CLI |
| 公网 Hermes | url:https://host:8642 | 只改 url，其他不变 |

**任何框架接入 AIM → 只需在 config.json 填 services.api。** 不需要改 adapter.sh，不需要改 main.py。

---

## 六、硬目录变量化

当前硬编码：
```
adapter.sh:  : ${HERMES_API_URL:="http://127.0.0.1:8642"}
main.py:     self.adapter_env["HERMES_API_URL"] = "http://127.0.0.1:8642"
config.json: /Users/yangzs/.local/bin/hermes
```

变量化后：
```
config.json:  services.api.url → main.py 读取 → AIM_API_URL env
adapter.sh:   : ${AIM_API_URL:=""}        ← 框架无关，由 main.py 注入
config.json:  env.HERMES_BIN="/usr/local/bin/hermes"    ← 已是变量
```

无 `/Users/yangzs/`，无 `hermes` 特定变量名。

---

## 七、v3.1 更新（2026-06-24 三方 Review 后）

### 采纳项

#### 1. services.api 字段拆细（火鸡儿建议）
```json
"services": {
  "api": {
    "url": "http://127.0.0.1:8642",
    "health_path": "/health",
    "timeout_ms": 5000,
    "required": true,
    "auth": {"type": "bearer", "credential": "${HERMES_GATEWAY_KEY}"}
  }
}
```
- `health_path` + `timeout_ms` → health probe 从 config 读，不硬编码
- `required: true/false` → 区分"API 挂了"和"不需要 API"（Letta=false）

#### 2. credential 安全边界（火鸡儿建议）
**config.json 永远存 `${VAR}` 引用，不存明文 key。**
main.py 运行时展开，config.json 内容不落地 credential 明文。

#### 3. services 可扩展（呱呱建议）
```json
"services": {
  "api":     { ... },
  "tts":     {"provider": "edge_tts"},
  "vision":  {"provider": "openai", "model": "gpt-4o"}
}
```
当前只实现 api，字段设计留好扩展口。

#### 4. sync-check 扩展（火鸡儿建议）
`sync-check.sh` 新增：检查各 Agent config.json 里 services.api 声明是否与 Agent Card 的运行时能力一致。

#### 5. main.py 实现（呱呱确认）
```python
# L4: 服务发现 — services.api → 通用 AIM_API_* 变量（插在 L839 之后）
services = self.config.get("services", {})
api_svc = services.get("api", {})
if api_svc:
    self.adapter_env["AIM_API_URL"] = api_svc.get("url", "")
    auth = api_svc.get("auth", {})
    cred_ref = auth.get("credential", "")
    if cred_ref.startswith("${") and cred_ref.endswith("}"):
        self.adapter_env["AIM_API_CREDENTIAL"] = os.getenv(cred_ref[2:-1], "")
```
改动量约 10 行，不破坏现有 adapter_env 逻辑。

### 推迟项

#### ${ENV} 解析提 common 层（呱呱建议）
方向对，当前只有 main.py 一处需要，`resolve_env_ref()` 内联即可。等 adapter.sh 需要直接读 config 时再提取。

### 折中项

#### adapter 双路径（呱呱建议 vs 现有设计）
不彻底去掉 fallback（API 挂了就没回复太危险）。改为**快速失败**：health check 超时 3s → 不可达立刻 CLI，不等 API 超时 40s。总延迟可控，同时保留降级能力。
