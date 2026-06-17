# OAS (Open Agent Standard) 扩展层设计文档

> 版本：v1.2 Phase 0 研究
> 日期：2026-06-13（修订）
> 负责人：呱呱 🐸 (ZS0001)
> 状态：ZS0003 Round 2 评审完成，待吉量评审
>
> **v1.2 变更：** DID/信任路由合并 Phase | capability_changed 事件 | trust_source 字段 | Passport heartbeat 续期

---

## 一、设计原则

**大哥定调（2026-06-04）：**
> 先兼容天下，再形成标准，最后兼并。不是把自己当标准制定者去要求别人接入，而是兼容一切已有协议（WS/REST/ACP等），让任何框架只要能发JSON消息就能接入。

**核心原则：**
1. **兼容优先** — 支持所有主流 Agent 框架的消息格式
2. **渐进增强** — 先做基础能力，再加高级功能
3. **零侵入** — 不修改现有 AIM 核心，通过扩展层实现
4. **安全可控** — 能力声明 + 信任路由，防止滥用

---

## 二、NATS Subject 设计

```
aim.
├── dm.<agent_id>              # 私聊（已有）
├── grp.<group_id>             # 群聊（已有）
├── obs.<agent_id>             # Observer 事件（已有）
├── sys.<event>                # 系统事件（已有）
├── reg.<action>               # 注册（已有）
│
├── meta.                      # 元信息（新增）
│   ├── capability.<agent_id>  #   能力声明
│   └── heartbeat              #   心跳（已有）
│
└── ext.                       # 扩展层（新增）
    └── oas.                   #   OAS 扩展
        ├── capability.<agent_id>  # 能力 passport
        ├── did.<did_method>       # DID 解析
        └── trust.<scope>          # 信任路由
```

---

## 三、能力声明（Capability Declaration）

### 3.1 目的
让每个 Agent 声明自己的能力，其他 Agent 可以查询并决定是否调用。

> **⚠️ v1.1 明确**：`meta.capability` 与 `ext.oas.capability` 是单向派生关系。
> - `ext.oas.capability` = canonical 唯一数据源（passport 完整格式，用于跨框架互通）
> - `meta.capability` = 自动派生缓存（只含路由必需字段，用于 NATS 内快速查询）
> - **不做两套维护**，发布走 ext.oas，查询可走 meta

### 3.2 消息格式（已统一到 JSON Schema）

**发布能力声明（meta 缓存，自动从 passport 派生）：**
```json
{
  "ver": "1.0",
  "agent_id": "ZS0001",
  "capabilities": [
    {
      "id": "cap_001",
      "name": "web_search",
      "description": "搜索互联网信息",
      "input_schema": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "搜索关键词"},
          "max_results": {"type": "integer", "default": 5}
        },
        "required": ["query"]
      },
      "cost": "free",
      "rate_limit": "10/min"
    },
    {
      "id": "cap_002",
      "name": "file_read",
      "description": "读取本地文件",
      "input_schema": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "文件路径"}
        },
        "required": ["path"]
      },
      "cost": "free",
      "rate_limit": "unlimited"
    }
  ],
  "framework": "openclaw",
  "version": "1.0",
  "updated_at": "2026-06-13T10:30:00Z"
}
```

> **命名统一（v1.1）**：`params` → `input_schema`，与 passport JSON Schema 风格一致。

**查询能力：**
```json
{
  "action": "query",
  "filter": {
    "capability": "web_search",
    "framework": "any"
  }
}
```

### 3.3 NATS Subject
- 发布（canonical）：`aim.ext.oas.capability.ZS0001` → 系统自动派生到 `aim.meta.capability.ZS0001`
- 变更通知：`aim.ext.oas.capability.ZS0001.changed`（能力变更时广播，其他 Agent 收到后重新查询）
- 查询（快速）：`aim.meta.capability.query`（走缓存）
- 查询（完整）：`aim.ext.oas.capability.query`（走 canonical）

### 3.4 能力变更事件
当 Agent 能力发生变更（新增/移除/修改 capability），发布 `capability_changed` 事件：
```json
{
  "event": "capability_changed",
  "agent_id": "ZS0001",
  "changes": [
    {"action": "add", "capability_id": "cap_003"},
    {"action": "remove", "capability_id": "cap_002"},
    {"action": "update", "capability_id": "cap_001", "field": "rate_limit"}
  ],
  "timestamp": "2026-06-13T10:00:00Z"
}
```
> 其他 Agent 订阅 `aim.ext.oas.capability.*.changed` 即可收到任意 Agent 的能力变更通知。

---

## 四、OAS 能力 Passport

### 4.1 目的
标准化能力描述格式，让不同框架的 Agent 能力可以互通。

### 4.2 Passport 格式

```json
{
  "ver": "1.0",
  "passport_id": "passport_ZS0001_001",
  "agent_id": "ZS0001",
  "framework": "openclaw",
  "capabilities": [
    {
      "id": "cap_001",
      "name": "web_search",
      "category": "information",
      "description": "搜索互联网信息",
      "input_schema": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
      },
      "output_schema": {
        "type": "object",
        "properties": {
          "results": {"type": "array", "items": {"type": "string"}}
        }
      },
      "cost": "free",
      "rate_limit": "10/min",
      "examples": [
        {"input": {"query": "今天天气"}, "output": {"results": ["北京晴天..."]}}
      ]
    }
  ],
  "trust_level": "verified",
  "trust_source": "auto_local",
  "issued_at": "2026-06-09T16:00:00Z",
  "expires_at": "2026-12-09T16:00:00Z",
  "renewed_at": null
}
```

### 4.3 NATS Subject
- 发布：`aim.ext.oas.capability.ZS0001`
- 查询：`aim.ext.oas.capability.query`（request-reply）

### 4.4 信任来源（trust_source）

| trust_source | 说明 | 触发条件 |
|-------------|------|---------|
| `auto_local` | 同机自动授信 | 共享文件系统 + 同一 NATS 集群，启动时自动设为 trusted |
| `manual_admin` | 大哥手动授信 | 外部 Agent 或跨机 Agent，由大哥（admin）手动授予 |
| `inherited` | 继承信任 | 由已 trusted Agent 推荐并经过大哥确认 |

> **规则**：同机 Agent（如 ZS0001/ZS0002/ZS0003 在同一台 Mac 上）启动时自动互信为 `trusted`。
> 外部 Agent 必须先注册 → 大哥审核 → 手动授信才能获得 `trusted` 级别。

### 4.5 Passport 续期机制

**自动续期（heartbeat 驱动）：**
1. 每次 heartbeat 检查 passport 的 `expires_at`
2. 距离过期 ≤ 7 天 → 自动签发新 passport（延长 6 个月），更新 `renewed_at`
3. 签发后通过 `aim.ext.oas.capability.<agent_id>.changed` 通知其他 Agent
4. 已过期的 passport → trust_level 自动降级为 `unverified`，需重新授信

**续期事件格式：**
```json
{
  "event": "passport_renewed",
  "agent_id": "ZS0001",
  "old_passport_id": "passport_ZS0001_001",
  "new_passport_id": "passport_ZS0001_002",
  "new_expires_at": "2027-06-13T10:00:00Z",
  "timestamp": "2026-12-02T10:00:00Z"
}
```

---

## 五、DID 解析 + 信任路由（合并 Phase）

> **v1.2 决策（2026-06-13 ZS0003 评审）**：DID 和信任路由合并到同一 Phase 实现。
> 当前 JWT creds 已是签名机制，DID 不单独做，而是作为信任路由的身份基础。

### 5.1 信任路由

#### 5.1.1 目的
建立 Agent 之间的信任关系，防止滥用和恶意调用。

#### 5.1.2 信任建立规则（v1.2 新增）

| 场景 | 信任方式 | trust_source | 说明 |
|------|---------|-------------|------|
| 同机 Agent | 启动时自动 trusted | `auto_local` | 共享文件系统 + 同一 NATS 集群 |
| 外部 Agent | 大哥手动授信 | `manual_admin` | 先注册 → 大哥审核 → 手动 grant |
| 推荐授信 | trusted Agent 推荐 + 大哥确认 | `inherited` | 已有 trusted Agent 为新 Agent 担保 |

#### 5.1.3 信任级别

| 级别 | 说明 | 权限 |
|------|------|------|
| `unverified` | 未验证 / passport 过期 | 只能发消息，不能调用能力 |
| `verified` | 已验证 | 可调用公开能力 |
| `trusted` | 受信任 | 可调用所有能力 |
| `admin` | 管理员（大哥） | 完全权限，可授信其他 Agent |

#### 5.1.4 信任关系

```json
{
  "ver": "1.0",
  "from": "ZS0001",
  "to": "ZS0002",
  "trust_level": "trusted",
  "trust_source": "auto_local",
  "granted_at": "2026-06-13T10:00:00Z",
  "expires_at": "2026-12-13T10:00:00Z",
  "capabilities": ["web_search", "file_read"],
  "conditions": {
    "rate_limit": "100/hour",
    "require_approval": false
  }
}
```

#### 5.1.5 NATS Subject
- 授予：`aim.ext.oas.trust.grant`
- 查询：`aim.ext.oas.trust.query`
- 撤销：`aim.ext.oas.trust.revoke`

### 5.2 DID 解析（信任路由的身份基础）

#### 5.2.1 目的
为每个 Agent 提供全局唯一、可验证的身份标识，作为信任路由的底层身份层。

#### 5.2.2 DID 格式

```
did:aim:<agent_id>
```

**示例：**
- `did:aim:ZS0001` — 呱呱
- `did:aim:ZS0002` — 吉量
- `did:aim:ZS0003` — 小火鸡儿

#### 5.2.3 DID Document

```json
{
  "@context": "https://www.w3.org/ns/did/v1",
  "id": "did:aim:ZS0001",
  "verificationMethod": [
    {
      "id": "did:aim:ZS0001#key-1",
      "type": "Ed25519VerificationKey2020",
      "controller": "did:aim:ZS0001",
      "publicKeyMultibase": "z6Mkf5r..."
    }
  ],
  "authentication": ["did:aim:ZS0001#key-1"],
  "service": [
    {
      "id": "did:aim:ZS0001#aim",
      "type": "AIMAgent",
      "serviceEndpoint": "nats://127.0.0.1:4222"
    }
  ]
}
```

#### 5.2.4 NATS Subject
- 解析：`aim.ext.oas.did.aim.ZS0001`（request-reply）
- 注册：`aim.ext.oas.did.register`

---

## 七、兼容性设计

### 7.1 主流 Agent 框架支持

| 框架 | 消息格式 | 兼容方式 |
|------|---------|---------|
| OpenAI Assistants | OpenAI API | 适配层转换 |
| LangChain | LangChain Message | 适配层转换 |
| AutoGPT | AutoGPT Protocol | 适配层转换 |
| BabyAGI | BabyAGI Protocol | 适配层转换 |
| MetaGPT | MetaGPT Protocol | 适配层转换 |
| CrewAI | CrewAI Protocol | 适配层转换 |

### 7.2 适配层设计

```
外部框架消息 → 适配层 → AIM 标准消息 → NATS 路由
                                  ↑
                          消息格式转换
                          能力映射
                          身份验证
```

---

## 八、实施计划

### Phase 0：研究（当前）
- [x] 设计文档 v1.0
- [x] ZS0003 评审（2026-06-13）
- [x] v1.1 修订
- [ ] 吉量（ZS0002）评审
- [ ] 大哥终审

### Phase 1：能力声明（先定关系再写代码）
- [ ] 实现 `aim.ext.oas.capability.*` Subject（canonical 来源）
- [ ] 自动派生逻辑 → `aim.meta.capability.*`（缓存层）
- [ ] Agent 启动时自动发布能力
- [ ] 能力查询 API

### Phase 2：OAS Passport
- [ ] Passport 格式标准化（JSON Schema）
- [ ] Passport 签发和验证
- [ ] 与 register 扩展字段对接

### Phase 3：DID + 信任路由（合并 Phase）
- [ ] 3.1 扩展 `aim.reg.register` 字段（trust_level, capabilities）
- [ ] 3.2 实现 `aim.ext.oas.trust.*`（grant/query/revoke）
- [ ] 3.3 同机自动授信（auto_local）+ 大哥手动授信（manual_admin）
- [ ] 3.4 DID Document 生成和解析
- [ ] 3.5 权限控制（基于 trust_level 的能力访问控制）

### Phase 4：信任引擎 + 外部兼容
- [ ] 4.1 Trust Score 计算（基于任务成功率、响应时间、评价）
- [ ] 4.2 适配器接口规范（消息格式映射、能力映射、错误处理）
- [ ] 4.3 LangChain 参考适配器（跑通全链路）
- [ ] 4.4 验证通过后扩展：AutoGPT / BabyAGI / MetaGPT / CrewAI

---

## 九、参考资料

- [W3C DID Specification](https://www.w3.org/TR/did-core/)
- [Verifiable Credentials](https://www.w3.org/TR/vc-data-model/)
- [Open Agent Protocol](https://github.com/open-agent-protocol)
- [Agent Protocol](https://agentprotocol.ai/)

---

## 十、评审记录

### ZS0003 评审 Round 1（2026-06-13）

| 条目 | 反馈 | 决策 |
|------|------|------|
| meta vs ext.oas 两套能力声明 | 相互竞争，关系未说明 | ✅ 单向派生：ext.oas canonical，meta 缓存 |
| DID 成本高收益不明 | 全在 NATS 集群内，身份已有 register | ✅ Phase 5+ → Phase 3，与信任路由合并 |
| 信任路由未对接注册体系 | register 无 trust_level 字段 | ✅ 扩展 register 字段 |
| 适配层只有一个名字 | 6 个框架无接口规范 | ✅ Phase 4 先出参考实现 |
| register 是否并入 OAS | 建议扩展而非另起 subject | ✅ register 扩展字段 |
| 命名风格不一致 | params vs input_schema/output_schema | ✅ 统到 JSON Schema |

### ZS0003 评审 Round 2（2026-06-13）

| 条目 | 反馈 | 决策 |
|------|------|------|
| DID 放置 | 建议 Phase 3 → Phase 4，和信任路由合并 | ✅ 合并到同一 Phase，DID 作为信任路由底层身份层 |
| 能力更新缺触发事件 | 只有静态发布，无变更通知 | ✅ 新增 `capability_changed` 事件 + NATS subject |
| 信任何时建立 | 未定义信任建立规则 | ✅ 同机 auto_local + 大哥 manual_admin，新增 trust_source 字段 |
| Passport 缺续期方案 | expires_at 到了就失效，无法续 | ✅ heartbeat 自动续期（≤7天续6个月），过期降级 unverified |

### 待评审
- [ ] 吉量（ZS0002）
- [ ] 大哥终审

---

大哥在等我们出方案。🐸
