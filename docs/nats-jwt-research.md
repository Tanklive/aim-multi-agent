# NATS JWT 认证体系 — 调研报告

**调研人**：吉量 🐴 (ZS0002)
**日期**：2026-06-10
**文档位置**：本文件

---

## 一、NATS JWT 三层体系

NATS 的 JWT 认证分三层，通过签名链建立信任：

```
┌─────────────────────────────────────┐
│   Operator (操作员)                  │ ← 根信任，自签名 NKEY
│   - 创建/签署 Accounts               │
│   - 定义系统账户                      │
├─────────────────────────────────────┤
│   Account (账户)                     │ ← Operator 签署
│   - 隔离的 subject 命名空间           │
│   - 可设置 Import/Export 权限         │
│   - 可创建 Users                      │
├─────────────────────────────────────┤
│   User (用户)                        │ ← Account 签署
│   - 具体的 client 连接凭证            │
│   - 可限制 pub/sub 权限              │
│   - 可限制 JetStream 访问             │
└─────────────────────────────────────┘
```

每个层级对应一个 **JWT + NKEY（Ed25519 公私钥对）**。

---

## 二、核心概念

### NKEY (NATS Key)
- Ed25519 密钥对
- 前缀标识类型：`O` = Operator, `A` = Account, `U` = User
- 示例：`Oxxx...` / `Axxx...` / `Uxxx...`

### NSC 工具链
- **nsc**：NATS 的官方 JWT 管理 CLI 工具
- 管理 Operator/Account/User 完整生命周期
- 生成 JWT 文件 + NKEY 文件
- 安装：`brew install nats-io/nats-tools/nsc`

### .creds 凭证文件
Client 连接用的文件，包含 User JWT + NKEY Seed，格式：
```
-----BEGIN NATS USER JWT-----
eyJ...
------END NATS USER JWT------
************************* IMPORTANT *************************
NKEY SEED (User)
SUA...
************************************************************
```

---

## 三、认证流程

```
1. Client → Server: User JWT + NKEY 签名(challenge)
2. Server: 验证签名链 User→Account→Operator
3. Server: 从 Operator JWT 获取 Account 公钥
4. Server: 验证 User 的 pub/sub 权限
5. 建立连接
```

---

## 四、Server 配置方式

### Memory Resolver（推荐 — 适合本地开发）
```yaml
operator: "/path/to/operator.jwt"
resolver: MEMORY
resolver_preload: {
  AAccountKey: "/path/to/account.jwt"
}
```

### URL Resolver（适合生产集群）
```yaml
operator: "/path/to/operator.jwt"
resolver: URL
resolver_url: "https://account-server:9090/jwt/v1/accounts/"
```

---

## 五、nats-py 连接方式

```python
# 方式1：.creds 文件（推荐）
nc = await nats.connect(
    "nats://localhost:4222",
    user_credentials="~/.aim/agents/ZS0002/nats.creds"
)

# 方式2：JWT + NKEY 字符串（动态传递）
nc = await nats.connect(
    "nats://localhost:4222",
    user_jwt="eyJ...",
    nkey_seed="SUA...",
)
```

---

## 六、方案对比 — 适用 AIM 场景

| 维度 | 方案 A：Memory Resolver | 方案 B：URL Resolver | 方案 C：混合 Token+JWT |
|------|------------------------|---------------------|----------------------|
| 复杂度 | 低 | 中 | 低 |
| 动态管理 | 需重启 Server | 支持 | 需重启 |
| 适用场景 | 本地开发/局域网 | 公网多机集群 | 过渡方案 |
| 外部依赖 | 无 | nats-account-server | 无 |
| 安全等级 | 高 | 高 | 中 |

### 推荐：方案 A（Memory Resolver）

理由：
1. 只有 3 个 Agent + Observer，规模极小
2. 局域网环境，不需要动态分发
3. Memory Resolver 配置简单、稳定
4. Server 重启即可加载新配置
5. 先落地 JWT，后续可按需升级 URL Resolver

---

## 七、实施步骤

### Step 1：安装 NSC
```bash
brew install nats-io/nats-tools/nsc
```

### Step 2：初始化 Operator + Account
```bash
# 创建 NSC 工作目录
nsc init --name AIM --output-dir ~/aim-server/nsc/

# 创建 Account
nsc add account --name aim-account
```

### Step 3：为每个 Agent 创建 User + 导出 .creds
```bash
# 吉量
nsc add user --account aim-account --name ZS0002 \
    --output-dir ~/.aim/agents/ZS0002/
nsc generate creds --account aim-account --user ZS0002 \
    --output-file ~/.aim/agents/ZS0002/nats.creds

# 呱呱（呱呱自行操作）
nsc add user --account aim-account --name ZS0001 \
    --output-dir ~/.aim/agents/ZS0001/
# 导出 .creds 发给呱呱

# 小火鸡儿
nsc add user --account aim-account --name ZS0003 \
    --output-dir ~/.aim/agents/ZS0003/
# 导出 .creds 发给小火鸡儿

# Observer（只读权限）
nsc add user --account aim-account --name observer \
    --output-dir ~/.aim/agents/observer/
# 创建时加 --allow-sub "aim.obs.>" --deny-pub ">" 限制
```

### Step 4：更新 Server 配置
```yaml
nats.conf 变更：
  删除：authorization { token: "..." }
  添加：
    operator: "/Users/yangzs/aim-server/nsc/AIM/operator.jwt"
    resolver: MEMORY
    resolver_preload: {
      AAccountNkey: "/Users/yangzs/aim-server/nsc/AIM/accounts/aim-account/aim-account.jwt"
    }
```

### Step 5：SDK 适配
`aim_nats_sdk.py` 的 `from_config()` 读取 `agents/{id}/nats.creds` 作为 credentials：

```python
@classmethod
def from_config(cls, agent_id, server=None):
    config = load_config()
    nats_url = server or config["nats_server"]
    creds_path = f"{CONFIG_DIR}/agents/{agent_id}/nats.creds"
    return cls(agent_id, nats_url, credentials=creds_path)
```

### Step 6：通知各 Agent 更新配置
- Server 重启后旧的 Token 连接全部失效
- 各 Agent 需要拿到自己的 .creds 文件
- Observer 单独一个 User（只读权限）

---

## 八、权限控制建议

每个 Agent 的 User 应限制最小权限：

| User | 允许发布 | 允许订阅 | JetStream |
|------|---------|---------|-----------|
| ZS0001 (呱呱) | aim.dm.ZS0001, aim.grp.> | aim.dm.>, aim.grp.>, aim.obs.> | 读写 |
| ZS0002 (吉量) | aim.dm.ZS0002, aim.grp.> | aim.dm.>, aim.grp.>, aim.obs.> | 读写 |
| ZS0003 (小火鸡儿) | aim.dm.ZS0003, aim.grp.> | aim.dm.>, aim.grp.>, aim.obs.> | 读写 |
| observer | (无) | aim.obs.> | 只读 |

具体实现可以用 nsc 的 `--allow-pub` / `--allow-sub` 参数，也可用 `--bearer-token` 标记 Observer 为无发布权限的 bearer 类型。

---

## 九、风险与注意事项

### Operator NKEY 必须保护
- Operator NKEY（`~/aim-server/nsc/AIM/AIM.nk`）是根信任
- 泄露 = 攻击者可签发任意 Account/User
- 建议：备份但不上传到代码仓库

### 过渡期兼容
- 切换到 JWT 后旧 Token 认证的连接全部失效
- 需要 Server 重启后各 Agent 同时更新
- 建议先发 .creds 给各 Agent 确认就绪后再切换

### 与现有配置的兼容性
- `from_config` 已支持 `credentials` 参数（见 SDK P0 实现）
- SDK 的 `connect()` 自动识别：文件路径→`.creds`，字符串→token
- 只需在 `aim.json` 增加 `credentials_path` 字段即可

---

## 十、后续待讨论事项

1. Observer 用户是否需要创建独立的 Account 还是共用 Account
2. 是否需要为 Observer 做 bearer token（免 NKEY 签名验证）
3. 密钥备份策略（Operator NKEY 必须备份）
4. 切换窗口——三方协调安排 Server 重启时间
5. 是否需要为 JetStream 加单独的 Consumer/Stream 权限
