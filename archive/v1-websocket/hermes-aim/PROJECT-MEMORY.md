# AIM + OAS 项目记忆归档

> 项目级别核心原则、决策记录，非通用规则。
> 需要时通过关键词检索召回。
> 最后更新：2026-06-09

## 顶层设计原则

**AIM/OAS 核心原则（大哥2026-06-04定调）：**
先兼容天下，再形成标准，最后兼并。不是把自己当标准制定者去要求别人接入，而是兼容一切已有协议（WS/REST/ACP等），让任何框架只要能发JSON消息就能接入。ATP任务协议是建议性格式不是强制标准，能做就做不能做就当普通消息处理。目标是让全球top10 Agent框架都能对接、能接到任务、能在负责人允许下调用本地AI能力开展协作。

**方案评审原则：**
参考GPT等外部建议，与三方评审综合分析后统一下发修改方向。"先兼容，再出标准"是OAS核心演进原则。

**命名规范：**
- 统一叫"AIM"，不叫"AIM Hub"
- 版本后缀：B=BREAKING大版本，M=MINOR小版本，P=PATCH修复

## 沟通机制

**沟通协议（COMMUNICATION-PROTOCOL.md）：**
- 任务确认5分钟内回复
- 进度汇报（开始/困难/完成）
- 无回复处理：15分钟催促 → 30分钟私信 → 60分钟上报
- 消息状态追踪：sent → delivered → processing → done

**沟通方式变更（2026-06-04）：**
三方确认不再使用inbox文件通信，统一走AIM（端口18900 WebSocket）。
Agent ID：呱呱ZS0001、吉量ZS0002、小火鸡儿ZS0003。

## 2026-06-07 心跳冷却修复
- 现象：呱呱 AIM Agent 心跳被 Server AgentStateManager.handle_heartbeat() 的 cooldown 拒绝
- 根因：冷却期防断连残留，但不应该拒绝合法心跳。能发心跳说明 Agent 在线
- 修复：冷却期内收到心跳→清理冷却记录→正常走 online 恢复。删 node.py heartbeat_rejected 分支
- 文件：registry.py + node.py
- 验证：呱呱重启 aim-agent 后 1 分钟观测，心跳稳定、Server 回 heartbeat_ack、无断开无超时。

## 基础设施配置

**AIM网络配置：**
- 当前：127.0.0.1:18900（本机），所有Agent同机部署
- 跨机部署三处修改：① aim-agent.py --server 参数 ② 防火墙放开18900 ③ 自启动配置加 --server
- 认证频率：10次/60秒，按agent_id隔离（不同agent互不影响）
- 踢旧连接：先检查state==OPEN再关，避免竞态

## 2026-06-08 Observer 全链路联调（已验证 ✅）
- **状态**：已归档，Phase 2 整体完成
- **参与方**：呱呱 ZS0001 / 吉量 ZS0002 / AIM Server
- **功能验证覆盖（6类事件）：**
  - status_feedback：task_start / running / progress
  - status_update：delivered / processing
  - lifecycle：agent_online
  - presence：online
  - delivery_failed：retry 机制验证
- **验证结果：**
  - Observer 注册认证 ✅（ZS0002→Server 注册，callback 正确返回）
  - 双向通信正常 ✅（ZS0001↔ZS0002 通过 Observer 通道）
  - 广播机制正常 ✅（Server _broadcast_to_observers + _observer_bindings）
  - 15秒内6类事件全部到达 ✅
- **下一阶段条件**：有实际业务场景需要生命周期管理时再启动（多 Observer 并发测试、权限分级等）

## 2026-06-08 P3-1 心跳超时测试 (T4) ✅
- **状态**：已验证 ✅
- **参与方**：吉量 ZS0002 / AIM Server
- **内容**：注释 ZS0002 aim-agent.py 心跳代码 → Server 90s 检测到 heartbeat_timeout → 恢复心跳 → 重连验证
- **验证结果**：
  - Server 正确检测心跳超时并断开连接 ✅（13:48:36 heartbeat_timeout 触发）
  - Server 离线期间状态稳定维持 ✅（20 分钟离线无异常）
  - 心跳恢复后重连成功 ✅ 心跳+heartbeat_ack 双向正常
- **文件**：结果记录至 `~/shared/aim/tests/P3-1-test-results.md`

## 2026-06-09 AIM V2 Phase 2 正式完成 ✅
- **状态**：Phase 2 全部完成，已归档
- **覆盖内容：**
  1. ✅ Observer 全链路联调（6类事件全覆盖，15秒内到达）
  2. ✅ 心跳冷却修复
  3. ✅ P3-1 心跳超时测试（T4）
  4. ✅ ConnectionPool reload（呱呱评审通过，10项全部 ✅）
  5. ✅ V4 标准方案本地模拟测试全链路通过
  6. ✅ handler 不存在/超时/空回复 补充测试
- **三方状态：** 呱呱(ZS0001) ✅ / 吉量(ZS0002) ✅ / 小火鸡儿(ZS0003) ✅
- **决策：** 大哥已拍板"干，开干"，进入 aim watch 生产监控阶段
- **下一阶段：** aim watch 生产部署 → 先稳吉量+呱呱 → 再扩展小火鸡儿
