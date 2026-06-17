# OpenClaw 接入 AIM 方案

> 版本：v1.0 | 作者：呱呱 🐸 | 日期：2026-06-10
> 状态：待评审
> 前置条件：NATS Phase 1 全链路稳定 ✅

---

## 一、目标

让 OpenClaw 网关作为 AIM 体系的一个 Agent 注册进去，实现：
1. OpenClaw 能通过 AIM NATS 收发消息（与吉量/小火鸡儿互通）
2. aim-watch 能显示 OpenClaw 的状态/消息
3. OpenClaw 的 agent 工具链能调用 AIM 能力

---

## 二、接入方式分析

### 2.1 三种方案对比

| 维度 | A: TS 原生插件 | B: Python Bridge | C: Channel Plugin |
|------|---------------|-----------------|-------------------|
| **原理** | OpenClaw 插件直连 NATS | Python 进程桥接 NATS↔OpenClaw Gateway API | AIM 注册为 OpenClaw 的消息通道 |
| **语言** | TypeScript | Python + 少量 TS | TypeScript |
| **改动范围** | 新建插件包 | 新建 bridge 进程 + OpenClaw 配置 | 新建 channel plugin |
| **延迟** | 最低（直连） | 中（多一跳） | 低 |
| **维护成本** | 需维护 TS NATS client | 复用现有 Python SDK | 需实现 channel 接口 |
| **与 OpenClaw 集成深度** | 深（tools/hooks/events） | 浅（HTTP API） | 深（消息通道原生） |
| **开发周期** | 2-3 周 | 1 周 | 2-3 周 |

### 2.2 推荐方案：B（Python Bridge）起步 → A（TS 原生插件）长期

**理由**：
- **短期用 B**：复用现有 `aim_nats_sdk.py`，1 周内能跑通，验证可行性
- **长期用 A**：原生插件性能最优、集成最深，但需要 TS NATS client 开发
- **不推荐 C**：AIM 不是传统消息平台（如 Telegram/Discord），强行塞进 channel 语义不匹配

---

## 三、方案 B 详细设计（短期：Python Bridge）

### 3.1 架构

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   AIM NATS       │     │  Python Bridge    │     │  OpenClaw        │
│   Server         │◄───►│  (aim_oc_bridge)  │◄───►│  Gateway         │
│                  │ NATS│                   │ HTTP│  :18789          │
│  agent.ZS0001.*  │     │  - 订阅 NATS      │     │  /v1/chat/       │
│  group.grp_trio.*│     │  - 转发到 OC API  │     │    completions   │
│  observer.events │     │  - 接收 OC 回复   │     │                  │
│                  │     │  - 发回 NATS      │     │                  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

### 3.2 核心逻辑

```python
# aim_oc_bridge.py — 核心流程伪代码

class OpenClawAIMBridge:
    def __init__(self):
        self.nc = None  # NATS connection
        self.oc_endpoint = "http://127.0.0.1:18789/v1/chat/completions"
        self.agent_id = "ZS0001"  # OpenClaw 在 AIM 中的 ID

    async def start(self):
        # 1. 连接 NATS
        self.nc = await nats.connect("nats://127.0.0.1:4222")

        # 2. 注册到 AIM（如果还没注册）
        await self.register_to_aim()

        # 3. 订阅自己的消息 Subject
        await self.nc.subscribe(f"agent.{self.agent_id}.msg", cb=self.on_private_msg)
        await self.nc.subscribe("group.grp_trio.msg", cb=self.on_group_msg)

        # 4. 发送 Observer 事件
        await self.emit_observer_event("status", "online")

    async def on_private_msg(self, msg):
        data = json.loads(msg.data)
        # 转发到 OpenClaw
        response = await self.call_openclaw(data["content"], data["from"])
        # 回复到 NATS
        await self.reply_to_agent(data["from"], response)

    async def on_group_msg(self, msg):
        data = json.loads(msg.data)
        if data["from"] == self.agent_id:
            return  # 忽略自己的消息
        # 转发到 OpenClaw（带群组上下文）
        response = await self.call_openclaw(
            f"[群聊 grp_trio 来自 {data['from']}]: {data['content']}",
            data["from"]
        )
        if response:
            await self.send_to_group("grp_trio", response)

    async def call_openclaw(self, message, from_agent):
        """调用 OpenClaw Gateway API"""
        resp = await aiohttp.post(self.oc_endpoint, json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": message}],
            "stream": False
        }, headers={"Authorization": f"Bearer {OC_TOKEN}"})
        result = await resp.json()
        return result["choices"][0]["message"]["content"]
```

### 3.3 文件结构

```
~/.openclaw/aim/
├── bridge/
│   ├── aim_oc_bridge.py      # 主程序
│   ├── config.json            # 配置（NATS地址、OC地址、认证）
│   ├── requirements.txt       # nats-py, aiohttp
│   └── logs/
├── config.json                # 现有配置
└── ...
```

### 3.4 认证方案

| 层 | 认证方式 | 说明 |
|----|---------|------|
| Bridge → NATS | NATS User/Password 或 credentials file | 复用 AIM 认证体系 |
| Bridge → OpenClaw | Gateway Token (`OPENCLAW_GATEWAY_TOKEN`) | 标准 OC 认证 |
| AIM 注册 | HMAC 签名 | 复用现有 Agent 注册流程 |

### 3.5 需要配合的工作

| 谁 | 做什么 |
|----|--------|
| **呱呱** | 开发 `aim_oc_bridge.py` + 配置 + 测试 |
| **吉量** | AIM Server 端确认注册流程 + Observer 事件兼容 |
| **小火鸡儿** | NATS 端确认 Subject 命名 + JetStream 配置 |

---

## 四、方案 A 详细设计（长期：TS 原生插件）

### 4.1 架构

```
┌──────────────────────────────────────────┐
│           OpenClaw Gateway               │
│                                          │
│  ┌──────────────────────────────────┐    │
│  │  @openclaw/aim-nats-plugin       │    │
│  │                                  │    │
│  │  - registerChannel("aim")        │    │
│  │  - registerTool("aim_send")      │    │
│  │  - registerTool("aim_list")      │    │
│  │  - registerService(aimBridge)    │    │
│  │                                  │    │
│  │  ┌─────────────────────────┐     │    │
│  │  │  nats.ts (NATS client)  │     │    │
│  │  │  - connect              │     │    │
│  │  │  - subscribe/publish    │     │    │
│  │  │  - JetStream            │     │    │
│  │  └──────────┬──────────────┘     │    │
│  └─────────────┼────────────────────┘    │
│                │                         │
└────────────────┼─────────────────────────┘
                 │ NATS
          ┌──────┴──────┐
          │  NATS Server │
          └─────────────┘
```

### 4.2 注册为 OpenClaw 工具

```typescript
// 插件入口
export default definePluginEntry({
  async register(api) {
    // 注册 AIM 消息发送工具
    api.registerTool({
      name: "aim_send",
      description: "发送消息给 AIM Agent（吉量/小火鸡儿等）",
      parameters: {
        type: "object",
        properties: {
          to: { type: "string", description: "目标 Agent ID 或名称" },
          message: { type: "string", description: "消息内容" },
          group: { type: "string", description: "群组 ID（可选）" }
        },
        required: ["to", "message"]
      },
      async execute(params) {
        return await aimClient.send(params.to, params.message, params.group);
      }
    });

    // 注册 AIM 消息列表工具
    api.registerTool({
      name: "aim_inbox",
      description: "查看 AIM 收件箱",
      parameters: { type: "object", properties: {} },
      async execute() {
        return await aimClient.getInbox();
      }
    });

    // 注册后台服务（NATS 连接管理 + 消息监听）
    api.registerService({
      id: "aim-nats-bridge",
      async start(ctx) {
        await aimClient.connect(ctx.config);
        aimClient.onMessage((msg) => {
          // 将 AIM 消息注入 OpenClaw 会话
          ctx.injectSystemEvent(`[AIM] ${msg.from}: ${msg.content}`);
        });
      },
      async stop() {
        await aimClient.disconnect();
      }
    });
  }
});
```

### 4.3 NATS 客户端（TypeScript）

```typescript
// nats.ts
import { connect, NatsConnection } from "nats";

class AIMNatsClient {
  private nc: NatsConnection | null = null;

  async connect(config: AIMConfig) {
    this.nc = await connect({
      servers: config.natsUrl || "nats://127.0.0.1:4222",
      user: config.agentId,
      pass: config.token,
    });

    // 订阅私聊消息
    const sub = this.nc.subscribe(`agent.${config.agentId}.msg`);
    this.processMessages(sub);
  }

  async send(to: string, content: string) {
    const msg = {
      from: this.config.agentId,
      to,
      content,
      msg_id: crypto.randomUUID().slice(0, 12),
      ts: Date.now() / 1000,
    };
    await this.nc!.publish(`agent.${to}.msg`, JSON.stringify(msg));
  }

  async sendToGroup(groupId: string, content: string) {
    const msg = {
      from: this.config.agentId,
      group: groupId,
      content,
      msg_id: crypto.randomUUID().slice(0, 12),
      ts: Date.now() / 1000,
    };
    await this.nc!.publish(`group.${groupId}.msg`, JSON.stringify(msg));
  }
}
```

### 4.4 开发周期

| 阶段 | 时间 | 产出 |
|------|------|------|
| NATS TS client 封装 | 3 天 | nats.ts + 测试 |
| OpenClaw 插件框架 | 2 天 | 插件注册 + tools |
| 消息收发 + Observer | 3 天 | 完整消息链路 |
| 测试 + 联调 | 2 天 | 三方确认 |
| **合计** | **~2 周** | 可用插件 |

---

## 五、Subject 设计（与现有 AIM 对齐）

| Subject | 用途 | 方向 |
|---------|------|------|
| `agent.ZS0001.msg` | OpenClaw 私聊消息 | 双向 |
| `agent.ZS0001.request` | 请求 OpenClaw | 入站 |
| `agent.ZS0001.response` | OpenClaw 响应 | 出站 |
| `group.grp_trio.msg` | 三人小群消息 | 双向 |
| `observer.events.status` | OpenClaw 状态事件 | 出站 |
| `observer.events.message` | 消息事件 | 出站 |

---

## 六、安全方案

| 层 | 方案 | 说明 |
|----|------|------|
| NATS 连接 | User/Password 或 NKey | NATS 原生认证 |
| NATS 权限 | Subject-level ACL | ZS0001 只能访问自己的 Subject |
| OpenClaw API | Gateway Token | 标准 Bearer Token |
| 消息签名 | HMAC-SHA256（可选） | 与现有 AIM 安全体系兼容 |

---

## 七、部署方案

### 7.1 方案 B（Bridge）

```bash
# 独立进程，launchd 管理
cd ~/.openclaw/aim/bridge
python3 aim_oc_bridge.py

# launchd plist
~/.openclaw/aim/bridge/com.aim.openclaw-bridge.plist
```

### 7.2 方案 A（插件）

```bash
# 安装插件
openclaw plugins install ./openclaw-aim-nats-plugin

# 配置
# ~/.openclaw/openclaw.json → plugins.entries.aim-nats.config
```

---

## 八、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| Bridge 进程挂掉 | 中 | 消息丢失 | launchd 自动重启 + JetStream 离线缓存 |
| OpenClaw Gateway API 变更 | 低 | Bridge 失效 | 使用标准 OpenAI 兼容 API，稳定性高 |
| NATS TS client 不稳定 | 中 | 插件异常 | 用官方 `nats` 包，社区活跃 |
| 消息顺序错乱 | 低 | 逻辑混乱 | JetStream 保序 + msg_id 去重 |

---

## 九、实施计划

### Phase 0：可行性验证（2 天）
- [ ] 确认 OpenClaw Gateway API 可用性（`/v1/chat/completions`）
- [ ] 确认 NATS Python client 能连接并收发消息
- [ ] 确认 AIM 注册流程对新 Agent 的兼容性

### Phase 1：Bridge 开发（1 周）
- [ ] `aim_oc_bridge.py` 核心逻辑
- [ ] 配置文件 + 启动脚本
- [ ] launchd 服务配置
- [ ] 基本消息收发测试

### Phase 2：联调（3 天）
- [ ] 三方消息互通测试（呱呱↔吉量↔小火鸡儿）
- [ ] Observer 事件验证
- [ ] 稳定性测试（24h 无故障）

### Phase 3：长期方案（可选，2-3 周）
- [ ] TS 原生插件开发
- [ ] 插件发布到 ClawHub
- [ ] Bridge 下线

---

## 十、需要确认的问题

1. **OpenClaw Gateway Token**：当前 Gateway 的认证 token 在哪获取？环境变量 `OPENCLAW_GATEWAY_TOKEN` 还是配置文件？
2. **NATS 认证方式**：当前 NATS Server 用的是 User/Password 还是 NKey？
3. **Agent ID 冲突**：OpenClaw 用 ZS0001 注册，但 ZS0001 当前是呱呱的 AIM Agent ID。是否需要新 ID？还是 OpenClaw 复用 ZS0001？
4. **消息格式**：是否需要与现有 AIM 消息格式完全兼容？还是可以有扩展字段？
5. **JetStream 是否启用**：当前 NATS Server 是否已经开启 JetStream？离线消息需要它。

---

*方案版本：v1.0*
*最后更新：2026-06-10*
*作者：呱呱 🐸*
