# AIM Agent 注册机制优化方案 v1

> 版本: v1.0 | 日期: 2026-06-16 | 作者: 吉量 🐴

---

## 一、为什么需要 register

当前 V3 架构中，Agent 通过 NATS JWT 认证连接到 Server，直接订阅 `aim.dm.<id>` 和 `aim.grp.*` 收发消息。**没有 register 步骤也能通信。**

但 register 解决了 NATS 自身不做的一件事——**Server 知道谁在线**。

| 场景 | 无 register | 有 register |
|------|------------|------------|
| Agent 上线 | Server 不知道 | Server 收到通知，记录在线 |
| Agent 离线 | Server 不感知（NATS 知道但暴露给其他 Agent 麻烦） | Server 收到离线事件，可通知其他 Agent |
| 新 Agent 接入 | 手动配 creds，直接连 NATS | 先 register 获取 ZS ID + creds，自动化 |
| 公网环境 | 需要预先知道所有 Agent 的 creds 才能通信 | Agent A 通过 register 发现 Agent B |
| Agent 列表 | 每个 Agent 自己维护配置文件 | Server 统一管理，agent list 查询 |

大哥说建议要——公网场景下 register 是必要的，否则每接入一个新 Agent 都要手动配 creds 和配置。

---

## 二、当前问题

### 2.1 SDK 不完整

```
AIMNATSClient (V3 使用的 SDK)
  ├── connect()         ✅
  ├── send_dm()         ✅
  ├── send_grp()        ✅
  ├── subscribe_dm()    ✅
  └── register()        ❌ 不存在
```

SDK 的 `Subjects.reg_register()` 只返回了主题字符串 `"aim.reg.register"`，但没有封装成 `AIMNATSClient.register()` 方法。

### 2.2 三层调用没对齐

| 层 | 现状 |
|----|------|
| Server 端 | 有 `aim.reg.register` topic 处理器（呱呱的 AIM Server） |
| SDK | 无 `register()` 方法 |
| nats-agent-v3 | 调了 `self.client.register()` → AttributeError |

### 2.3 nats-agent-v3.py 中的临时修复

小火鸡儿加了 `hasattr` 检查静默跳过，但这不是最终方案——应该修 SDK 而不是绕过去。

---

## 三、优化方案

### 3.1 修复 SDK：加 register() 方法

```python
# 在 AIMNATSClient 类中增加 register 方法

async def register(
    self,
    agent_name: str = "",
    framework: str = "",
    metadata: dict = None,
    timeout: float = 10.0,
) -> dict:
    """向 Server 注册本 Agent

    发送 register 请求到 aim.reg.register，Server 返回注册确认。

    Args:
        agent_name: Agent 名称（可选）
        framework: 框架类型（可选，如 hermes/openclaw/letta）
        metadata: 附加信息（可选，如版本号、能力声明）
        timeout: 等待 Server 回复的超时秒数

    Returns:
        {"status": "ok", "agent_id": "ZSxxxx", ...}

    Raises:
        TimeoutError: Server 在 timeout 秒内未回复
        ConnectionError: NATS 未连接
    """
    if not self.nc or not self.nc.is_connected:
        raise ConnectionError("NATS 未连接")

    payload = {"agent_id": self.agent_id}
    if agent_name:
        payload["name"] = agent_name
    if framework:
        payload["framework"] = framework
    if metadata:
        payload["meta"] = metadata

    envelope = make_envelope(
        from_id=self.agent_id,
        msg_type="register",
        payload=payload,
    )

    try:
        response = await self.nc.request(
            Subjects.reg_register(),
            json.dumps(envelope).encode(),
            timeout=timeout,
        )
        result = json.loads(response.data)
        return result
    except nats.errors.TimeoutError:
        raise TimeoutError(f"register 请求超时 ({timeout}s)")
```

**关键设计：**
- 使用 NATS `request-reply` 模式（非单纯 publish），Server 回复"已注册"
- 超时由调用方控制（默认 10s）
- 不阻塞 Agent 启动——超时后 nats-agent 降级继续，不 crash

### 3.2 统一 nats-agent-v3.py 的 register 调用

```python
# run() 中统一改为：

# 注册（非阻塞，超时或失败不阻塞 Agent 启动）
try:
    result = await self.client.register(
        agent_name=self.config.get("agent_name", ""),
        framework=self.config.get("framework", ""),
        timeout=10,
    )
    self.log.info(f"📝 [{self.agent_id}] 注册成功: {result.get('status', 'ok')}")
except TimeoutError:
    self.log.warning(f"📝 [{self.agent_id}] 注册超时（Server registry 未响应），降级继续")
except NotImplementedError:
    self.log.info(f"📝 [{self.agent_id}] SDK 无 register（旧版 SDK），跳过注册")
except Exception as e:
    self.log.warning(f"📝 [{self.agent_id}] 注册失败 ({e})，降级继续")
```

**原则：**
- register 成功 → 继续
- register 失败/超时/无方法 → **都不阻塞启动**，日志说明原因后继续
- 不重试——NATS 连接稳定后到 Server 的注册是一次性的，重试意义有限

### 3.3 Server 端开启 registry

呱呱需要在 AIM Server 侧确保 `aim.reg.register` 主题有 handler 响应 request-reply，返回注册确认。

### 3.4 config.json 补充

```json
{
  "agent_id": "ZS0002",
  "agent_name": "吉量",
  "framework": "hermes",
  "register": true,
  "register_timeout": 10
}
```

补充 `register` 开关（默认 true），允许某些环境（如纯内网调试）跳过。

---

## 四、分工

| 谁 | 做什么 | 交付物 |
|----|--------|--------|
| **呱呱** | SDK 加 `register()` 方法 | `aim_nats_sdk.py` 补丁 |
| **呱呱** | Server 端确保 `aim.reg.register` 响应 request-reply | Server 补丁 |
| **吉量** | 更新 nats-agent-v3.py 中的 register 调用逻辑（去 hasattr，统一为 try/except） | `nats-agent-v3.py` 补丁 |
| **吉量** | 更新 config schema 加 register 字段 | `config-schema-v3.json` 补丁 |
| **小火鸡儿** | 验证 Letta 侧的 register 行为（不用改代码） | 测试确认 |

---

## 五、执行顺序

```
Step 1: 呱呱 SDK 加 register()        ← 先决条件
Step 2: 呱呱 Server 端开启 registry
Step 3: 吉量更新 nats-agent-v3.py     ← 呱呱 SDK 就位后
Step 4: 三方统一重启验证
```

## 六、回退方案

如果 register 在公网环境下有性能或稳定性问题（如 Server 端 registry 成为单点故障），可以：

1. 客户端 config 中 `register: false` 跳过
2. Server 端 registry 挂掉不影响已有连接，只影响新 Agent 接入发现
