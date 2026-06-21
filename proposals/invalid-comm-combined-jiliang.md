# AIM 无效通讯治理：综合方案

> 吉量 (ZS0002) 综合呱呱输出侧 + 火鸡儿分层治理 + 自己的输入侧拦截
> 2026-06-21

## 一、三方方案对照

| | 呱呱 (ZS0001) | 小火鸡儿 (ZS0003) | 吉量 (ZS0002) |
|---|---|---|---|
| 焦点 | 输出侧 `_has_substance` 翻转 | 分层治理架构 + L3 终态标记 | 输入侧 `_skip_adapter_for_operational` 前置拦截 |
| 方法 | 默认无效 → 找正向信号 | L1/L2/L3 三层 + 分工 | `_strip_politeness` ratio + 入站分类 |
| 互补性 | 兜底（输出侧最后防线） | 架构（源头分类不进 LLM） | 前置（入站时截断，零 token） |

三方不冲突，可叠加。

## 二、综合三层方案

### L1: 单消息判定（呱呱主导）

已合入 `_has_substance` 翻转（默认无效）+ 6 类正向信号。

**吉量补充：第 7 类反信号**

火鸡儿建议了确认循环模式。具体落地——在 `_has_substance` 中增加：

```python
# 第 7 类反信号：确认循环/状态同步模式
_CONFIRM_LOOP_PATTERNS = [
    r'收到.*确认',           # "收到确认"
    r'都(在|跑|齐|到位)了',   # "都跑起来了""都齐了"
    r'(继续干活|随时待命|状态正常|一切正常|待命中?|等指令)',
    r'(三兄弟|三(方|向)).*(齐|通|正常|到位)',
    r'有(活|任务|需要).*(喊|叫|招呼|同步)',
]
```

如果消息匹配这些模式 + 长度 < 60 字 + 无数字/错误/问句 → 即使其他正向信号命中，也判无效。

**设计原则**：反信号优先级低于「错误/异常」和「问句/请求」。如果消息同时包含 BUG/报错和「收到确认」，仍判有效（可能是确认收到 BUG 报告）。

### L2: 群聊回路抑制（吉量实现）

> 小火鸡儿建议冷却从 30s 扩到 300s + 三方环路检测 + 内容相似度。
> 吉量这边补充具体算法。

#### 2.1 当前状态

`main.py` 已有：
- `grp_reply_cooldown_sec: 30s`（L416）
- `_last_grp_reply` 字典（L417）
- `_is_confirm_loop`（但只检测单对单确认）

**问题**：
- 30s 太短，三方循环周期 >30s
- `_is_confirm_loop` 不检测 A→B→C→A 环路
- 内容相似度只用精确匹配（`_LOOP_CONTENT_MAX_LEN=20`），带 emoji 变体就跳过

#### 2.2 增强方案

**2.2.1 三方环路检测**

```python
def _detect_trio_loop(self, group_id: str, from_id: str, text: str) -> bool:
    """检测群聊三方循环 A→B→C→A"""
    recent = list(self._grp_recent_msgs.get(group_id, []))[-5:]
    if len(recent) < 2:
        return False
    
    # 取最近 3 条不同发送者的消息
    senders = []
    for msg in reversed(recent):
        if msg['from'] not in [s['from'] for s in senders]:
            senders.append(msg)
        if len(senders) >= 3:
            break
    
    if len(senders) < 3:
        return False
    
    # 当前发送者 == 第 3 条消息的发送者 → 环路
    if from_id == senders[2]['from']:
        # 检查内容相似度
        core_text = self._normalize_for_similarity(text)
        for s in senders[:2]:
            core_s = self._normalize_for_similarity(s['text'])
            if self._text_similarity(core_text, core_s) > 0.7:
                return True
    return False
```

**2.2.2 内容相似度（去 emoji/称呼/标点后比对）**

```python
def _normalize_for_similarity(self, text: str) -> str:
    """归一化文本用于相似度对比"""
    import re
    # 去掉 emoji
    t = re.sub(r'[🐸🐴🐤✨👂🤝🦊🤖🔥📋📊📡🛡️🔧⚙️🎯💡✅👍👌🔴🟡🟢⚠️]', '', text)
    # 去掉称呼
    t = re.sub(r'(呱呱|吉量|小火鸡儿|火鸡儿|大哥)', '', t)
    # 去掉标点和多余空格
    t = re.sub(r'[，,。.!！?？、：:；;…\s]+', '', t)
    return t

def _text_similarity(self, a: str, b: str) -> float:
    """Jaccard 相似度（2-gram）"""
    if not a or not b:
        return 0.0
    a_grams = {a[i:i+2] for i in range(len(a)-1)}
    b_grams = {b[i:i+2] for i in range(len(b)-1)}
    if not a_grams or not b_grams:
        return 0.0
    return len(a_grams & b_grams) / len(a_grams | b_grams)
```

**2.2.3 动态冷却**

```python
# 正常冷却
GRP_COOLDOWN_NORMAL = 60    # 60s（从 30s 上调）
# 检测到三方环路时
GRP_COOLDOWN_LOOP = 300     # 300s（5 分钟）
# 冷却随无效轮数升级
GRP_COOLDOWN_FATIGUED = 600  # 连续 2+ 轮无效 → 10 分钟
```

冷却规则：
- 正常 → 60s
- 三方环路触发 → 300s
- 连续 2 轮 INEFFECTIVE → 600s
- 有新话题（内容相似度 < 0.3）→ 恢复 60s

### L3: 终态标记（小火鸡儿主导，吉量输入拦截补充）

#### 3.1 吉量的输入拦截（融入 L3）

火鸡儿的 L3 是在 `_handle_message` 中分类。我的 `_skip_adapter_for_operational` 增强是同一个位置的不同实现路径。建议合并：

在 `_skip_adapter_for_operational` 中增加（当前 L790-806 之后）：

```python
# 群聊消息：前置分类，INFO/ACK 不进 adapter
if not msg.is_dm:
    msg_type = self._classify_msg_type(text)
    if msg_type in ('ACK', 'INFO'):
        self.logger.debug(
            f" [{msg.msg_id[:8]}] L3-{msg_type}: from={msg.from_id} "
            f"ratio={self._last_politeness_ratio:.2f}"
        )
        return True  # 跳过 adapter
```

#### 3.2 分类函数

```python
def _classify_msg_type(self, text: str) -> str:
    """L3 消息分类：DISCUSSION / TASK / INFO / ACK"""
    import re
    
    # 先剥离礼貌用语
    core, ratio = self._strip_politeness(text)
    self._last_politeness_ratio = ratio
    
    # ACK: 纯确认
    if len(core) <= 8 and core in self._ACK_CORE_WORDS:
        return 'ACK'
    
    # INFO: 状态同步 / 无待办
    INFO_PATTERNS = [
        r'^(收到|看到了).*(确认|状态|情况|报告)',
        r'(一切|状态|系统|链路|通道).*(正常|畅通|恢复|OK|ok)',
        r'都(在|跑|齐|到位|上线)了',
        r'(待命中?|等指令|随时待命|继续干活)',
        r'(三兄弟|三方).*(齐|通|正常|到位)',
        r'(没有|暂无|没).*(任务|工作|问题|异常)',
        r'有(活|任务|需要).*(喊|叫|招呼|同步)',  # "有活喊你"
    ]
    for pat in INFO_PATTERNS:
        if re.search(pat, core):
            return 'INFO'
    
    # 高礼貌剥离率 + 无有效信号 → INFO
    if ratio > 0.4 and not self._has_substance(core):
        return 'INFO'
    
    # TASK: 有具体待办
    if re.search(r'(请|帮我|需要|能否|麻烦).*(做|处理|修|查|部署|提交|测试|联调)', core):
        return 'TASK'
    if re.search(r'TODO|FIXME|待办|任务|分配', core):
        return 'TASK'
    
    # DISCUSSION: 需要多方讨论
    if re.search(r'[？?]', core) or re.search(r'(怎么|如何|为什么|能不能|要不要)', core):
        return 'DISCUSSION'
    
    # 默认
    if self._has_substance(text):
        return 'DISCUSSION'
    return 'INFO'
```

#### 3.3 分类处理策略

| 类型 | adapter | 回复 | 说明 |
|------|---------|------|------|
| TASK | ✅ 必须 | ✅ 必须 | 有具体待办 |
| DISCUSSION | ✅ 必须 | 可回复 | 需要讨论 |
| INFO | ❌ 跳过 | ❌ 不回复 | ack 即可 |
| ACK | ❌ 跳过 | ❌ 不回复 | 收到即止 |

## 三、综合效果推演

以刚才 5 分钟的实际流量为例（17 条 adapter 调用）：

| 消息 | 当前 | L1+L3 后 |
|------|------|---------|
| "收到呱呱！状态对齐就放心了 ✨🐴✨ 暂时没别的事" | adapter OK | L3→INFO→skip |
| "收到 ✅ 池化2 + stall 30s，三方配置对齐确认" | adapter OK | L1 反信号(收到.*确认)→无效 |
| "✨🐴✨ 收到确认，三方互通正常。待命等大哥指令" | adapter OK | L3→INFO→skip |
| "收到，_my_msg_ids 修复确认，链路畅通 ✨🐴✨" | adapter OK | L3→INFO→skip |
| "P0-005 死锁，exit=2" | adapter OK | TASK→adapter ✅ |

预估：17 → 3~4 条。节省约 75% 的无效 adapter 调用。

## 四、分工

| 人 | 范围 | 具体 |
|----|------|------|
| 呱呱 | L1 第 7 类反信号 | `_has_substance` 增加确认循环模式检测 |
| 吉量 | L2 群聊回路 + 输入拦截融入 L3 | 三方环路检测、相似度、动态冷却、分类函数 |
| 小火鸡儿 | L3 终态标记架构 | 在 `_handle_message` 中接入分类，INFO/ACK 不进 adapter |

## 五、实现优先级

1. **L3 终态标记** — 效果最直接（源头截断，零 token）
2. **L1 第 7 类反信号** — 兜底保障
3. **L2 动态冷却** — 防极端循环

三者加入后，群聊中只有 TASK（具体待办）和 DISCUSSION（真讨论）才会触发 adapter 调用。状态同步/确认/客套全部在入站时截断。
