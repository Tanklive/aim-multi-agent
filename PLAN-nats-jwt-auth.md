# NATS JWT 认证迁移方案 v2

> 版本：v2.1 — 2026-06-11
> 基于 v1.0 的调研结果更新，增加了 nsc 安装、权限配置、吊销机制、过渡策略等实操细节

---

## 1. 现状

当前 AIM 使用共享 Token 认证（所有 Agent 共用同一个 token）：

```yaml
# ~/aim-server/nats.conf
authorization {
  token: "YOUR_NATS_TOKEN_HERE"
}
```

所有 Agent 从 `~/.aim/config/aim.json` 读取同一个 `nats_token`。

**风险**：
- 一个 token 泄露 = 所有 Agent 认证信息泄露
- 无权限粒度（无法限制谁发什么/订阅什么）
- 无法按 Agent 单独吊销 — 换 token 需要所有 Agent 重新配置
- 不能混合多认证方式

## 2. 目标

改用 NATS JWT/NKEY 认证体系，实现：

- **每 Agent 独立凭据** — 每个 Agent 有自己的 NKEY + JWT，泄露一个不影响其他
- **细粒度权限控制** — 可限制每个 Agent 能订阅/发布的 Subject
- **可吊销** — Server 端可吊销单个 Agent 的 JWT，无需更换共享 Token
- **无额外依赖** — nats-py 已内置 `.creds` 文件支持，无需额外 Python 安装

## 3. NATS JWT 体系架构（调研结论）

### 三层密钥层级

```
Operator（最高权限，仅用于签发 Account）
  └── Account（AIM 系统账户，隔离不同系统/团队）
       ├── User ZS0001（呱呱 — 有 Subject 权限）
       ├── User ZS0002（吉量 — 有 Subject 权限）
       └── User ZS0003（小火鸡儿 — 有 Subject 权限）
```

### 关键设计原则（从官方文档确认）

1. **JWT 是配置描述，不是会话令牌** — NATS JWT 描述：实体的公钥 ID、签发者公钥 ID、实体的能力权限。认证通过 Ed25519 签名链完成，Server 从不知晓私钥
2. **所有 JWT 必须使用 Ed25519 签名** — 其他算法自动拒绝
3. **去中心化** — Operator 签发 Account JWT，Account 管理者签发 User JWT，两者可分离实体操作
4. **权限存储在 User JWT 中** — 每次连接时 Server 验证，修改后 `nsc push` 即时生效

### 吊销机制（调研确认）

NATS 支持 **两种吊销**，均存储在 Account JWT 中：

| 吊销类型 | 命令 | 效果 |
|---------|------|------|
| User 吊销 | `nsc revocations add-user` | 吊销某个 User 的公钥和吊销时间 T |
| Activation 吊销 | `nsc revocations add_activation` | 阻止某 Account 访问特定 export |

**时间戳机制**：在时间 T 吊销后，任何在 T **之前**签发的 JWT 都失效，T **之后**重新签发的 JWT 仍有效。这样可以在不中断服务的情况下换发凭据。

**即时生效**：`nsc push` 后将吊销信息写入 Account JWT → NATS Server 即时检测 → 已连接的被吊销客户端被立即断开连接。

## 4. 架构变更

### 4.1 新增目录结构

```
~/aim-server/
├── nats.conf              # 现有配置（需增加 operator + resolver）
├── auth/                  # 新增认证目录
│   ├── operator.nk        # Operator NKEY（最高权限，妥善保管）
│   ├── operator.jwt       # Operator JWT（公开，指向 Account Resolver）
│   ├── aim_account.nk     # AIM 系统 Account NKEY
│   ├── aim_account.jwt    # AIM 系统 Account JWT
│   └── resolver_cache/    # Account JWT 缓存目录
├── creds/                 # 分发到各 Agent 的凭据文件
│   ├── ZS0001.creds       # 呱呱凭据（含 User JWT + NKEY seed）
│   ├── ZS0002.creds       # 吉量凭据
│   └── ZS0003.creds       # 小火鸡儿凭据
└── data/                  # 现有 JetStream 数据目录
```

### 4.2 Agent 凭据位置

```
~/.aim/
├── config/
│   └── aim.json           # 配置（结构升级，见下文）
├── agents/
│   ├── ZS0001/
│   │   └── aim.creds      # 每个 Agent 自己的 creds 文件（→ nats-py 自动识别）
│   ├── ZS0002/
│   │   └── aim.creds
│   └── ZS0003/
│       └── aim.creds
```

### 4.3 aim.json 配置升级

```json
{
  "nats_server": "nats://127.0.0.1:4222",
  "nats_token": "MeUz84...",                    // 过渡期保留，Phase 3 移除
  "auth_mode": "jwt",                           // "token" | "jwt" | "mixed"
  "agents": {
    "ZS0001": {
      "name": "呱呱",
      "framework": "openclaw",
      "creds_path": "~/.aim/agents/ZS0001/aim.creds"
    },
    "ZS0002": {
      "name": "吉量",
      "framework": "hermes",
      "creds_path": "~/.aim/agents/ZS0002/aim.creds"
    },
    "ZS0003": {
      "name": "小火鸡儿",
      "framework": "letta",
      "creds_path": "~/.aim/agents/ZS0003/aim.creds"
    }
  }
}
```

## 5. 实施步骤

### Phase A：工具安装 + 清理缓存

```bash
# 1. 安装 nsc
go install github.com/nats-io/nsc/v2@latest
# nsc 会安装到 $GOPATH/bin/，确保在 PATH 中

# 2. 初始化 nsc 环境（创建默认的 nats.io 目录）
# nsc init 会在 ~/.nsc/ 下创建配置目录

# 3. 确认 SDK 兼容性
# nats-py 2.15.0+ 已内置 .creds 文件支持，无需额外安装
# SDK 的 from_config() 自动识别 credentials 参数类型
```

### Phase B：生成密钥层级

nsc 自动管理 JWT 和 NKEY 的生命周期。创建顺序：Operator → Account → Users。

```bash
# 1. 创建 Operator（NATS 认证体系的根）
#    --output-dir 指定 Operator JWT/NKEY 输出位置
nsc init --name AIM --output-dir ~/aim-server/auth/

# 2. 创建 Account（AIM 系统账户）
#    所有 Agent 共用此 Account
nsc add-account --name AIMSystem

# 3. 按顺序创建 User（每 Agent 一个，带 Subject 权限）
#    发布权限：允许发送 DM、群聊、观测、系统消息
#    订阅权限：仅允许接收自己的 DM、群聊、观测

# 呱呱（全网通，允许系统级操作）
nsc add-user --account AIMSystem --name ZS0001 \
  --allow-pub "aim.dm.>,aim.grp.>,aim.obs.ZS0001,aim.sys.>" \
  --allow-sub "aim.dm.ZS0001,aim.grp.>,aim.obs.>,aim.sys.>" \
  --deny-sub "_INBOX.>" \
  --output-dir ~/aim-server/creds/

# 吉量
nsc add-user --account AIMSystem --name ZS0002 \
  --allow-pub "aim.dm.>,aim.grp.grp_trio,aim.obs.ZS0002,aim.sys.>" \
  --allow-sub "aim.dm.ZS0002,aim.grp.grp_trio,aim.obs.>,aim.sys.>" \
  --deny-sub "_INBOX.>" \
  --output-dir ~/aim-server/creds/

# 小火鸡儿
nsc add-user --account AIMSystem --name ZS0003 \
  --allow-pub "aim.dm.>,aim.grp.grp_trio,aim.obs.ZS0003,aim.sys.>" \
  --allow-sub "aim.dm.ZS0003,aim.grp.grp_trio,aim.obs.>,aim.sys.>" \
  --deny-sub "_INBOX.>" \
  --output-dir ~/aim-server/creds/

# 4. 将生成的 creds 分发到各 Agent 目录
# nsc 自动生成 creds 文件，拷贝即可
cp ~/aim-server/creds/ZS0001.creds ~/.aim/agents/ZS0001/aim.creds
cp ~/aim-server/creds/ZS0002.creds ~/.aim/agents/ZS0002/aim.creds
cp ~/aim-server/creds/ZS0003.creds ~/.aim/agents/ZS0003/aim.creds

# 5. 保护安全
chmod 600 ~/.aim/agents/ZS*/aim.creds
find ~/aim-server/auth/ -name "*.nk" -exec chmod 600 {} \;
```

### Phase C：配置 Server

修改 `~/aim-server/nats.conf`：

```yaml
# 新增 Operator JWT 配置
operator: "/Users/yangzs/aim-server/auth/operator.jwt"

# Account Resolver 配置（告诉 Server 去哪里找 Account JWT）
resolver {
  type: full                    # 本地文件缓存 + URL 推送
  dir: "/Users/yangzs/aim-server/auth/resolver_cache"
  allow_delete: true            # 允许删除已缓存的 Account（用于吊销）
  interval: "2m"               # 自动同步间隔
  timeout: "1.9s"
}

# 过渡策略：保留 Token 认证，实现 Mixed 模式
authorization {
  token: "YOUR_NATS_TOKEN_HERE"
}
```

> **注意**：NATS Server 支持同时配置 `operator` JWT + `authorization.token`。在同一个 Server 上，使用 Token 的客户端和使用 JWT 的客户端可以共存。这保证了 **零停机过渡**。

### Phase D：nsc push（推送配置到 Server）

```bash
# 将 Account JWT 推送到 Server
nsc push -a AIMSystem -u nats://127.0.0.1:4222

# 验证
nsc list-accounts
```

### Phase E：更新 SDK 认证逻辑

SDK 的 `from_config()` 已内置 `.creds` 文件识别：

```python
# 当前 AIMNATSClient.connect() 中的逻辑：
if self.credentials:
    if os.path.isfile(self.credentials):
        kwargs["user_credentials"] = self.credentials  # JWT .creds 文件
    else:
        kwargs["token"] = self.credentials  # Token 字符串
```

**from_config() 变更**：
- 读取 aim.json 中对应 Agent ID 的 `creds_path`
- 优先使用 `.creds` 文件
- `creds_path` 不存在时降级使用 `nats_token`（过渡期兼容）

### Phase F：按 Agent 逐个切换

按"先 Observer（只读，风险最小）→ 手动工具 → Agent 守护进程"的顺序逐个切换：

1. **优先切换 aim-observe/aim-watch**（只读 Observer，风险最低）
   - 修改 from_config() 读取 creds_path
   - 验证 Observer 连接和消息接收正常
2. **手动工具**（非生产路径，用于测试）
   - aim_send.py 支持 creds 认证
   - 手动发消息验证双向通信
3. **Agent 守护进程**（生产路径）
   - aim_agent_nats.py 切换认证方式
   - 重启 Agent 进程，验证完整通信链路
4. **呱呱的 nats-agent.py**（对方维护，但我同步方案）
   - 呱呱自行修改认证参数

## 6. 过渡策略（零停机）

### Phase 1：并行运行（Mixed 模式）

Server 同时开启 `operator` + `authorization.token`：

```
        Token 认证              JWT 认证
    ┌─────────────────┐   ┌─────────────────┐
    │ aim-observe     │   │ nats-agent      │
    │ aim_send        │   │ aim_agent_nats  │
    │ aim-watch       │   │ aim-observe(JWT)│
    └────────┬────────┘   └────────┬────────┘
             │                      │
             ▼                      ▼
    ┌───────────────────────────────────────┐
    │        NATS Server (Port 4222)        │
    │   Operator + Token 认证 同时有效      │
    └───────────────────────────────────────┘
```

- 原有的 Token 连接不受影响
- 新建的 JWT/creds 连接正常认证
- 两个体系可同时运行，逐一验证

### Phase 2：逐个迁移

各 Agent 按"Observer → 工具 → Agent"顺序逐一切换：

1. 为 Agent 生成 JWT + 分发 creds 文件
2. 修改 Agent 的 SDK 配置或代码，读取 creds 文件
3. 重启 Agent 进程
4. 在 grp_trio 群确认通信正常

### Phase 3：清理 Token

当所有 Agent 都切换到 JWT 后，从 nats.conf 中移除 `authorization.token`，从 aim.json 中移除 `nats_token`。

## 7. 权限矩阵

### 7.1 建议初始权限（统一，后续按需细化）

| Subject | ZS0001 呱呱 | ZS0002 吉量 | ZS0003 小火鸡儿 |
|---------|:----------:|:----------:|:--------------:|
| pub aim.dm.* | ✅ | ✅ | ✅ |
| sub aim.dm.{self} | ✅ | ✅ | ✅ |
| pub aim.grp.* | ✅ | ✅ (grp_trio) | ✅ (grp_trio) |
| sub aim.grp.* | ✅ | ✅ | ✅ |
| pub aim.obs.{self} | ✅ | ✅ | ✅ |
| sub aim.obs.* | ✅ | ✅ | ✅ |
| pub aim.sys.* | ✅ | ✅ | ✅ |
| sub aim.sys.* | ✅ | ✅ | ✅ |

> 注：`aim.dm.*` 的 pub 理论上只允许发给自己，但目前 NATS User JWT 不支持 `pub self.sub` 动态模式，所以先允许全发。后续可通过 Account export/import 做更细管控。

### 7.2 权限调整

```bash
# 查看当前权限
nsc edit-user --name ZS0002

# 编辑（如限制吉量只能发 grp_trio）
nsc edit-user --name ZS0002 \
  --allow-pub "aim.dm.>,aim.grp.grp_trio,aim.obs.ZS0002,aim.sys.>" \
  --allow-sub "aim.dm.ZS0002,aim.grp.grp_trio,aim.obs.>,aim.sys.>"

# 推送到 Server
nsc push -a AIMSystem -u nats://127.0.0.1:4222
```

## 8. 吊销操作指导

### 吊销某个 Agent（紧急情况）

```bash
# 1. 吊销用户的公钥（在当前时间）
nsc revocations add-user --account AIMSystem --name ZS0002

# 2. 推送到 Server（被吊销的客户端会被立即断开）
nsc push -a AIMSystem -u nats://127.0.0.1:4222

# 3. 重新签发
nsc add-user --account AIMSystem --name ZS0002 \
  --allow-pub "..." --allow-sub "..." \
  --output-dir ~/aim-server/creds/

# 4. 分发新的 creds
cp ~/aim-server/creds/ZS0002.creds ~/.aim/agents/ZS0002/aim.creds
```

### 查看吊销记录

```bash
nsc revocations list-users --account AIMSystem
```

### 删除吊销（谨慎使用）

```bash
# 仅在确信旧 token 已全部过期后操作
nsc revocations delete-user --account AIMSystem --name ZS0002
```

## 9. SDK 兼容性

### Python nats-py 对 creds 的支持

```python
import nats

# ✅ .creds 文件（包含 JWT + NKEY seed）
nc = await nats.connect(
    servers=["nats://127.0.0.1:4222"],
    user_credentials="/path/to/aim.creds"
)

# ✅ Token 字符串（过渡期兼容）
nc = await nats.connect(
    servers=["nats://127.0.0.1:4222"],
    token="MeUz84..."
)
```

### SDK 认证自动识别

`AIMNATSClient.__init__()` 的 `credentials` 参数：

| 传入值 | 识别方式 | 认证模式 |
|--------|---------|---------|
| `""` (空字符串) | 未设置 | 裸连（开发调试） |
| `"MeUz84..."` (无此文件的字符串) | `os.path.isfile()` 返回 False | Token 认证 |
| `"~/.aim/agents/ZS0002/aim.creds"` | `os.path.isfile()` 返回 True | JWT/NKEY 认证 |

**不需要修改 `connect()` 的认证逻辑** — 只需要在 `from_config()` 中根据 `auth_mode` 字段决定传入 `nats_token` 还是 `creds_path`。

## 10. 安全注意事项

1. **Operator NKEY 是根密钥** — `operator.nk` 文件是整个认证体系的信任锚。泄露 = 任何人有能力签发任意 Account/User。建议备份到离线存储，生产文件权限 `600`
2. **creds 文件包含 NKEY seed** — 等同于明文密码（私钥种子）。`chmod 600` 保护
3. **JWT 有过期时间** — nsc 默认签发不过期。建议生产环境指定 `--expiry 30d`（30天后需重新签发）
4. **nsc 本地存储** — `nsc init` 会在 `~/.nsc/` 创建本地存储。`nsc` 的所有操作都是本地执行，通过 `nsc push` 同步到 Server
5. **不允许跨 Account 通信** — 默认隔离。如果未来有多个 NATS 系统通过不同 Account 接入，需配置 import/export

## 11. 测试验证

```bash
# 1. 基础连接测试（creds 文件）
python3 -c "
import nats, asyncio
async def test():
    nc = await nats.connect(
        'nats://127.0.0.1:4222',
        user_credentials='~/.aim/agents/ZS0002/aim.creds'
    )
    print(f'Connected as: {nc.connected_url}')
    await nc.close()
asyncio.run(test())
"

# 2. 权限测试 — 尝试订阅其他 Agent 的 DM（应拒绝）
nc.subscribe("aim.dm.ZS0001")  # 吉量不应能订阅呱呱的私聊

# 3. 功能测试 — 正常发送群聊消息
nc.publish("aim.grp.grp_trio", b'Hello from JWT test')

# 4. 吊销测试 — 吊销后验证旧 creds 被拒绝

# 5. 重连测试 — 断开 NATS Server，观察 Agent 自动重连并恢复通信
#    （停 Server → 等待重连日志 → 启 Server → 验证连接恢复 → 发消息验证通信正常）
```

## 12. 分工建议（呱呱负责的部分）

参考 aim-nats-migration 技能的三方分工：

| 角色 | 框架 | 负责部分 |
|------|------|---------|
| 🐸 呱呱 | OpenClaw | Server 升级（nats.conf 增加 operator/resolver）、nsc 安装、部署脚本 deploy.sh 更新 |
| 🐴 吉量 | Hermes | SDK from_config() 修改、creds 分发脚本、文档、本机测试验证 |
| 🐤 小火鸡儿 | Letta | Letta handler.sh 认证适配、联调测试 |

### 呱呱需要做的事：

1. 安装 nsc：`go install github.com/nats-io/nsc/v2@latest`
2. 初始化 nsc：`nsc init --name AIM --output-dir ~/aim-server/auth/`
3. 执行 Phase B 的 nsc 命令（在 Server 机器上）
4. 修改 nats.conf（增加 operator + resolver 配置）
5. 重启 NATS Server
6. 推送 Account JWT：`nsc push -a AIMSystem`
7. 将生成的 creds 分发到各 Agent 目录

## 13. 参考资料

- [NATS JWT Authentication/Authorization](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/jwt)
- [nsc 工具文档](https://docs.nats.io/using-nats/nats-tools/nsc)
- [nsc Revocation](https://docs.nats.io/using-nats/nats-tools/nsc/revocation)
- nats-py `user_credentials` 参数：支持 .creds 文件（JWT + NKEY seed 合并文件）
- 现有方案文档：`~/shared/aim/PLAN-nats-jwt-auth.md` (v1.0)

## 14. 时间估计

| 步骤 | 时长 | 负责 |
|------|------|------|
| 安装 nsc + 初始化 | 15min | 🐸 呱呱 |
| 生成密钥层级 | 10min | 🐸 呱呱 |
| 修改 nats.conf | 15min | 🐸 呱呱 |
| SDK from_config 适配 | 15min | 🐴 吉量 |
| creds 分发脚本 + 测试 | 20min | 🐴 吉量 |
| Letta 认证适配 | 15min | 🐤 小火鸡儿 |
| 逐个切换 + 联调测试 | 30min | 三方联调 |
| **总计** | **~1h40min** | |

| **v2.1 实际时间线调整**（基于呱呱评审反馈） | | |
|------|------|------|
| Phase A: 安装 nsc + 初始化 | 15min | 🐸 呱呱（先验证 brew 源，不行 go install 备用） |
| Phase B: 生成密钥层级 | 10min | 🐸 呱呱 + 吉量同步 SDK 改动 |
| Phase C: 修改 nats.conf | 15min | 🐸 呱呱（改前 `cp nats.conf nats.conf.bak.$(date +%s)`） |
| Phase D: nsc push | 5min | 🐸 呱呱 |
| Phase E: SDK from_config 适配 + 分发脚本 | 20min | 🐴 吉量（与 Phase A 并行） |
| Phase F: 逐个切换 + 联调测试 | 45min | 三方联调（含重连测试） |
| **总计** | **~1h40min** | A → B → C → D → 联调，非完全并行 |

**实际执行序列**：
```
呱呱: A(装nsc) → C(nats.conf) → D(nsc push)
吉量: B+E(SDK+分发, 与A并行)
三方: F(切换+联调, 含重连测试)
```

---

**讨论事项**：
1. 权限粒度：初始阶段所有 Agent 统一权限，后续是否需要细化？（如限制 Observer 为只读）
2. JWT 过期时间：是否设置 `--expiry 30d`？还是不过期（日常运维方便）？
3. 过渡策略：Observer 先切 → 工具切 → Agent 切，还是统一一次性切换？
