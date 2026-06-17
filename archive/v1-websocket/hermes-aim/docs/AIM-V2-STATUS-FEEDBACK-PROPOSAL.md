# AIM V2 状态反馈 + Observer 通道方案

> 版本: v1.0
> 作者: 呱呱 🐸 + 吉量 🐴
> 日期: 2026-06-07
> 状态: 三方评审稿，待大哥确认

---

## 一、问题背景

### 1.1 当前痛点

| # | 问题 | 影响 |
|---|------|------|
| 1 | AI 处理期间发送方完全无反馈 | 大哥以为假死，实际在跑推理 |
| 2 | 推理链/中间步骤全部暴露 | 输出冗余，非技术用户看不懂 |
| 3 | 第三方无法观察 Agent 间通信 | OAS 生态需要可观测性 |
| 4 | status_feedback 没有统一协议 | 各 Agent 实现不一致，投递不可靠 |

### 1.2 设计目标

1. **发送方感知** — 消息发出后能实时看到处理进度（typing → processing → done）
2. **推理链可控** — 默认隐藏中间推理，verbose 模式按需开启
3. **Observer 通道** — 第三方可挂接观察 Agent 间通信，不影响主流程
4. **协议统一** — status_feedback 走标准化 msg_type，复用现有 WS 连接

---

## 二、方案设计（呱呱+吉量对齐版）

### 2.1 Observer 通道 ✅ 双方一致

**定义：** 只收不发的观察连接，不参与 handler 选举，不影响消息路由。

**接入方式：**
```json
{
  "cmd": "auth",
  "agent_id": "ZS0099",
  "channel": "observer",
  "handler": false,
  "watch_target": "ZS0001",
  "term": 1,
  "timestamp": 1700000000,
  "signature": "hmac_sha256"
}
```

**核心规则：**

| 规则 | 说明 |
|------|------|
| 只收不发 | observer 连接禁止调用 `_deliver`，Server 拒绝其发送请求 |
| 不影响 handler 选举 | observer 不参与 term 比较，不触发 handler 降级 |
| 可指定 watch_target | 只收特定 Agent 的消息副本；不指定则收全部广播 |
| 连接上限 | 单 agent 最多 2 个 observer 连接（防滥用） |
| 消息过滤 | observer 只收到 `chat_message` 和 `status_feedback`，不收心跳/认证等系统消息 |

**Server 端改动：**
```python
# connection_pool.py — 新增 observer 注册
def register_observer(self, agent_id: str, ws, watch_target: str = None):
    """注册 observer 连接，只收不发"""
    self._observers.setdefault(agent_id, []).append({
        "ws": ws,
        "watch_target": watch_target,
        "connected_at": time.time()
    })

# 广播时附带 observer 副本
def broadcast_to_observers(self, msg, target_agent_id: str):
    """将消息副本推给观察该 agent 的 observer"""
    for obs in self._observers.get(target_agent_id, []):
        if obs["watch_target"] is None or obs["watch_target"] == msg.from_id:
            await obs["ws"].send(msg.to_json())
```

### 2.2 Status Feedback 协议 ✅ 双方一致

**设计原则：** 复用现有 WS 连接，通过 `msg_type` 字段区分普通消息和状态反馈。

**消息格式：**
```json
{
  "cmd": "message",
  "msg_type": "status_feedback",
  "from_id": "ZS0001",
  "to_id": "ZS0002",
  "ref_msg_id": "original_msg_123",
  "status": "processing",
  "stage": "calling_ai",
  "progress": null,
  "detail": "正在调用 AI 处理...",
  "timestamp": 1700000000
}
```

**msg_type 协议区分（新增字段，向后兼容）：**

| msg_type | 说明 | 路由规则 |
|----------|------|---------|
| （缺省/空） | 普通消息，走原有逻辑 | → handler |
| `status_feedback` | 状态反馈 | → 原消息发送方的挂起队列 |
| `system_event` | 系统事件 | → 广播所有连接 |
| `observer_feed` | observer 专用推送 | → observer 连接 |

**状态枚举（status 字段）：**

| status | 含义 | 是否终态 |
|--------|------|---------|
| `received` | 消息已收到 | ❌ |
| `queued` | 排队中 | ❌ |
| `processing` | AI 处理中 | ❌ |
| `calling_ai` | 调用 AI 模型 | ❌ |
| `generating` | 生成回复中 | ❌ |
| `done` | 处理完成 | ✅ |
| `error` | 处理失败 | ✅ |
| `timeout` | 超时 | ✅ |

### 2.3 推理链隐藏 ✅ 双方一致

**默认行为：** AI 处理过程中的中间推理链不暴露给发送方，只推送状态反馈。

**Verbose 模式：** 发送方可在消息中指定 `verbose: true`，开启后推理链作为 `status_feedback` 的 `detail` 字段推送。

**实现：**
```python
# aim-agent.py — AI 处理流程
async def process_message(self, msg):
    # 1. 收到消息 → 推送 received
    await self.send_status_feedback(msg.msg_id, "received", "消息已收到")
    
    # 2. 开始处理 → 推送 processing
    await self.send_status_feedback(msg.msg_id, "processing", "正在处理...")
    
    # 3. 调用 AI（推理链默认隐藏）
    if msg.metadata.get("verbose"):
        # verbose 模式：逐段推送推理链
        async for chunk in ai_call(prompt, stream=True):
            await self.send_status_feedback(msg.msg_id, "generating", chunk)
    else:
        # 默认模式：只推送状态，不推送推理链
        result = await ai_call(prompt)
    
    # 4. 完成 → 推送 done + 最终结果
    await self.send_status_feedback(msg.msg_id, "done", "处理完成")
```

### 2.4 Message Bridge 挂起队列 ✅ 双方一致

**设计：** status_feedback 走 `message_bridge.py` 的挂起队列，由发送方的主会话心跳扫描获取。

**数据流：**
```
ZS0002 收到消息
  → AI 处理中
  → 生成 status_feedback
  → Server 查 ref_msg_id 找到原发送方 (ZS0001)
  → 写入 ZS0001 的挂起队列 (pending_incoming.jsonl)
  → ZS0001 心跳扫描 → 注入上下文
```

**message_bridge.py 扩展：**
```python
def write_status_feedback(from_id: str, ref_msg_id: str, status: str, detail: str = ""):
    """写入状态反馈到挂起队列"""
    entry = {
        "type": "status_feedback",
        "from": from_id,
        "ref_msg_id": ref_msg_id,
        "status": status,
        "detail": detail,
        "ts": time.time(),
        "datetime": datetime.now().strftime("%H:%M:%S"),
    }
    # 写入 pending_incoming.jsonl
    with open(BRIDGE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

---

## 三、吉量补充点（呱呱确认采纳）

### 3.1 节流机制 ✅

**原则：** 快步骤不推，长步骤必推，避免刷屏又避免假死。

| 场景 | 节流策略 |
|------|---------|
| 快步骤（< 3s） | 不推送 status_feedback |
| 长步骤（≥ 3s） | 必推，间隔 ≥ 5s |
| 终态（done/error/timeout） | 必推，不节流 |
| 同一 ref_msg_id | 5s 内不重复推送相同 status |

**实现：**
```python
class StatusThrottle:
    def __init__(self, min_interval: float = 5.0, fast_threshold: float = 3.0):
        self.min_interval = min_interval
        self.fast_threshold = fast_threshold
        self._last_push: Dict[str, float] = {}  # ref_msg_id → last_push_ts
        self._start_ts: Dict[str, float] = {}    # ref_msg_id → processing_start_ts
    
    def should_push(self, ref_msg_id: str, status: str) -> bool:
        # 终态必推
        if status in ("done", "error", "timeout"):
            return True
        
        now = time.time()
        start = self._start_ts.get(ref_msg_id, now)
        
        # 快步骤不推（处理时间 < fast_threshold）
        if now - start < self.fast_threshold:
            return False
        
        # 间隔检查
        last = self._last_push.get(ref_msg_id, 0)
        if now - last < self.min_interval:
            return False
        
        self._last_push[ref_msg_id] = now
        return True
```

### 3.2 Server 超时清理 ✅

**规则：** 同一 session_id 60s 无 status 更新 → Server 主动发 timeout 通知。

```python
# node.py — 超时清理任务
async def _status_timeout_checker(self):
    """定期检查超时的处理会话"""
    while True:
        await asyncio.sleep(10)  # 每 10 秒检查一次
        now = time.time()
        for session_id, info in list(self._processing_sessions.items()):
            if now - info["last_update"] > 60:
                # 超时 → 通知发送方
                await self._send_timeout_notice(info["from_id"], info["ref_msg_id"])
                del self._processing_sessions[session_id]
```

**timeout 通知格式：**
```json
{
  "cmd": "message",
  "msg_type": "status_feedback",
  "from_id": "SERVER",
  "to_id": "ZS0001",
  "ref_msg_id": "original_msg_123",
  "status": "timeout",
  "detail": "ZS0002 处理超时（60s 无更新）",
  "timestamp": 1700000060
}
```

### 3.3 绑定链路 ✅

**完整数据流：**
```
1. ZS0001 发消息给 ZS0002（msg_id=M123）
2. ZS0002 收到 → 开始 AI 处理
3. ZS0002 生成 status_feedback（ref_msg_id=M123, status=processing）
4. Server 收到 status_feedback：
   a. 查 M123 找到 from=ZS0001
   b. 查 msg_id=M123 找到 watch 该消息的连接
   c. 推送给：ZS0001 的挂起队列 + 观察 ZS0002 的 observer 连接
5. ZS0001 心跳扫描挂起队列 → 看到"处理中"
6. ZS0002 处理完成 → status=done → 同链路推送
```

**Server 端路由逻辑：**
```python
async def route_status_feedback(self, feedback_msg):
    """路由 status_feedback 到正确目标"""
    ref_msg_id = feedback_msg.ref_msg_id
    
    # 1. 查原消息找到发送方
    original = self._find_message(ref_msg_id)
    if not original:
        return
    
    # 2. 推送给发送方的挂起队列
    sender_conn = self._get_connection(original.from_id, channel="main")
    if sender_conn:
        await sender_conn.ws.send(feedback_msg.to_json())
    
    # 3. 推送给观察处理方的 observer
    for obs in self._observers.get(feedback_msg.from_id, []):
        await obs["ws"].send(feedback_msg.to_json())
```

---

## 四、协议格式汇总

### 4.1 Auth 扩展（Observer 接入）

```json
{
  "cmd": "auth",
  "agent_id": "ZS0099",
  "channel": "observer",
  "handler": false,
  "watch_target": "ZS0001",
  "term": 1,
  "version": "2.0.0",
  "timestamp": 1700000000,
  "signature": "hmac_sha256"
}
```

### 4.2 Status Feedback 消息

```json
{
  "cmd": "message",
  "msg_type": "status_feedback",
  "msg_id": "sf_abc123",
  "from_id": "ZS0002",
  "to_id": "ZS0001",
  "ref_msg_id": "original_msg_123",
  "status": "processing",
  "stage": "calling_ai",
  "detail": "正在调用 AI 处理...",
  "timestamp": 1700000010
}
```

### 4.3 Verbose 推理链推送

```json
{
  "cmd": "message",
  "msg_type": "status_feedback",
  "msg_id": "sf_abc124",
  "from_id": "ZS0002",
  "to_id": "ZS0001",
  "ref_msg_id": "original_msg_123",
  "status": "generating",
  "stage": "reasoning",
  "detail": "Step 1: 分析用户意图...\nStep 2: 检索知识库...\nStep 3: 生成回复...",
  "verbose": true,
  "timestamp": 1700000015
}
```

### 4.4 Server Timeout 通知

```json
{
  "cmd": "message",
  "msg_type": "status_feedback",
  "msg_id": "sf_timeout_001",
  "from_id": "SERVER",
  "to_id": "ZS0001",
  "ref_msg_id": "original_msg_123",
  "status": "timeout",
  "detail": "ZS0002 处理超时（60s 无更新）",
  "timestamp": 1700000070
}
```

---

## 五、实施计划

### Phase 1（1天）— Status Feedback 基础

| 模块 | 改动 | 负责 |
|------|------|------|
| msg_type 字段 | Server 端 `_deliver` 支持 msg_type 路由 | 吉量 |
| status_feedback 生成 | aim-agent.py 处理流程中插入状态推送 | 呱呱 |
| message_bridge 扩展 | 支持 status_feedback 类型写入 | 呱呱 |
| 节流机制 | StatusThrottle 类实现 | 吉量 |

### Phase 2（1天）— Observer 通道

| 模块 | 改动 | 负责 |
|------|------|------|
| observer 注册 | connection_pool.py 新增 observer 存储 | 吉量 |
| observer 消息过滤 | 只推 chat_message + status_feedback | 吉量 |
| observer CLI | aim_cli.py 支持 `--observe` 模式 | 呱呱 |

### Phase 3（0.5天）— 超时清理 + 绑定链路

| 模块 | 改动 | 负责 |
|------|------|------|
| 超时检查器 | node.py 新增 `_status_timeout_checker` | 吉量 |
| 绑定链路 | status_feedback 路由到发送方+observer | 吉量 |
| 联调验证 | 端到端测试 | 三方 |

---

## 六、验收标准

1. **基本反馈** — ZS0001 发消息给 ZS0002 → ZS0001 收到 received → processing → done 三个状态反馈
2. **节流生效** — 快速处理（<3s）不推送中间状态；长处理（>3s）每 5s 推送一次
3. **推理链隐藏** — 默认不推送 detail；verbose 模式下逐段推送
4. **Observer 接入** — ZS0099 以 observer 连接 → 收到 ZS0001↔ZS0002 的消息副本
5. **超时通知** — 60s 无更新 → Server 主动发 timeout 给发送方
6. **向后兼容** — 老客户端不传 msg_type → 走原有逻辑，无影响

---

## 七、附录：与现有方案的关系

| 本方案 | AIM-V2-UPGRADE.md | 双向通信可靠性优化方案 |
|--------|-------------------|---------------------|
| Observer 通道 | §2.2 Channel 设计扩展 | — |
| Status Feedback | 新增 | — |
| 节流机制 | 新增 | — |
| 超时清理 | 新增 | — |
| msg_type 协议 | §2.4 消息路由扩展 | — |
| message_bridge | — | §P2 watcher 扩展 |

本方案是对 AIM-V2-UPGRADE.md 的**补充**，不是替代。Status Feedback 和 Observer 是 V2 的新功能层，依赖于 V2 的 channel 多连接架构。

---

> 📌 本方案由呱呱+吉量联合起草，待大哥审阅确认后分头实施。
