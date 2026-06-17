# Letta 框架接入方案

> 版本：v1.0
> 日期：2026-06-08
> 作者：吉量 🐴 (ZS0002)
> 状态：待呱呱评审
> 关联：[FrameworkCLI](~/shared/aim-client/ZS0002/framework_cli.py) | [config.json](~/.hermes/aim/config.json) | [CLIAdapter](~/shared/aim-client/ZS0002/cli_adapter.py) | [ConnectionPool Reload](connection-pool-reload-final-plan.md)

---

## 1. 概述

### 1.1 目标

小火鸡儿 🐤 (ZS0003) 使用 **Letta** 框架，需要将其接入现有的 AIM Agent 通讯体系。当前 FrameworkCLI 已支持 Hermes、OpenClaw、QwenPaw、CrewAI，通过 commands 模板机制实现框架路由。接入 Letta 的目标是：

1. **不改 FrameworkCLI 核心代码**，只通过 commands 配置模板 + 可选回调脚本的方式
2. **CLI 优先，API 兼容** — 先按 CLI 模式适配，预留 API 模式的切换路径
3. **交付形式** — 本地模拟测试通过后，打 Release 包给小火鸡儿部署

### 1.2 适用范围

| 维度 | 说明 |
|------|------|
| 目标框架 | Letta (letta-ai/letta) |
| 目标 Agent | 小火鸡儿 🐤 (ZS0003) |
| 载体 | framework_cli.py + commands 配置 + 回调脚本 |
| 干涉范围 | 不改 framework_cli.py 核心结构，只加 elif 分支支持 leta |

---

## 2. Letta 接口分析

### 2.1 Letta Code（CLI 模式）— 首选

**安装：**
```bash
npm install -g @letta-ai/letta-code
```

**CLI 调用：**
```bash
letta send --agent <agent_name> --message "<prompt>"
```

或交互式启动后通过 stdin 管道传入消息。

### 2.2 Letta API（SDK 模式）— 备选

**安装：**
```bash
pip install letta-client
```

**Python SDK 调用：**
```python
from letta_client import Letta
client = Letta(base_url="http://localhost:8283")
response = client.agents.messages.create(
    agent_id="agent-xxx",
    input={"role": "user", "content": prompt}
)
print(response.assistant_message.content)
```

**REST API 直接调用：**
```bash
curl -X POST http://localhost:8283/v1/agents/<agent_id>/messages \
  -H "Content-Type: application/json" \
  -d '{"input": {"role": "user", "content": "你好"}}'
```

### 2.3 推荐路线

| 优先级 | 方式 | 理由 |
|--------|------|------|
| ✅ 首选 | CLI 模式 (`letta send ...`) | 最简单，framework_cli.py 一行 elif 即可，零额外依赖 |
| 🔄 备选 | API 模式 (curl / SDK) | 如果小火鸡儿没有 CLI 工具，可以用 REST API |
| ❌ 不强求 | 两种都跑通 | 按 CL I模式先适配，配置留切换选项 |

---

## 3. 方案设计

### 3.1 架构总览

```
aim-agent.py
    │
    ├─ framework_cli.py (FrameworkCLI)
    │       │
    │       ├─ [elif "letta": CLI 子进程或 HTTP 回调]
    │       │
    │       ├─ CLI 模式 → subprocess: "letta send --agent main --message '{prompt}'"
    │       │
    │       └─ API 模式 → 回调脚本: "python letta_api_call.py '{prompt}'"
    │                      （通过配置文件切换）
    │
    └─ config.json (commands.letta)
```

### 3.2 FrameworkCLI 改动

直接在 `framework_cli.py` 的 `_fallback_call()` 方法中增加 `letta` 分支：

```python
elif self.framework == "letta":
    # Letta CLI 模式
    cmd = [cli, "send", "--agent", "main", "--message", prompt]
```

**不改核心代码路径**：commands 模板在 config.json 中定义，如果配置了 `commands.letta.chat`，`call()` 方法会走模板路径，不会进 `_fallback_call()`。新增 elif 只是给没有 commands 配置的兼容降级用。

### 3.3 config.json 配置模板

在 `commands` 段新增 `letta`：

```json
"letta": {
  "chat": {
    "cmd": [
      "{cli}",
      "send",
      "--agent", "main",
      "--message", "{prompt}"
    ],
    "cmd_with_session": [
      "{cli}",
      "send",
      "--agent", "main",
      "--message", "{prompt}",
      "--session-id", "{session_id}"
    ],
    "timeout": 120,
    "output": "stdout",
    "filter": []
  }
}
```

在 `cli_paths` 段新增：

```json
"letta": "/Users/yangzs/.npm-global/bin/letta"  // npm 全局安装路径
```

### 3.4 回调脚本（支持 CLI/API 双模式）

**为什么需要回调脚本：**

1. CLI 和 API 模式的切换不需要改代码，只需改配置文件
2. Letta 调用失败时有降级策略，不能卡死整个流程
3. 每次调用的输入输出需要记日志，方便排查问题

**回调脚本设计：**

```python
"""
letta_llm_call.py — Letta 框架调用脚本

支持 CLI 和 API 两种模式，通过 config 中的 mode 字段切换。

配置方式（config.json commands.letta）：
{
  "mode": "cli",          // "cli" | "api"
  "agent_id": "zs0003",    // Letta agent ID (API 模式)
  "api_url": "http://localhost:8283",  // API 模式
  "timeout": 120
}
"""

import sys
import json
import subprocess
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [letta] %(levelname)s %(message)s",
    filename=os.path.expanduser("~/.hermes/aim/logs/letta_call.log"),
)

def call_letta(prompt: str, mode: str = "cli", **kwargs) -> dict:
    """调用 Letta 框架"""
    start = time.time()
    
    # 记录输入
    logging.info(f"INPUT: prompt_len={len(prompt)} mode={mode}")
    
    try:
        if mode == "cli":
            result = _call_cli(prompt, **kwargs)
        elif mode == "api":
            result = _call_api(prompt, **kwargs)
        else:
            return {"success": False, "error": f"未知 mode: {mode}"}
        
        # 记录输出
        latency = int((time.time() - start) * 1000)
        logging.info(f"OUTPUT: success={result.get('success')} latency={latency}ms")
        
        result["latency_ms"] = latency
        return result
        
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logging.error(f"ERROR: {str(e)} latency={latency}ms")
        
        # 降级策略：API 失败时尝试 CLI 模式
        if mode == "api":
            logging.warning("API 模式失败，降级到 CLI 模式")
            try:
                return _call_cli(prompt, **kwargs)
            except Exception as e2:
                logging.error(f"降级也失败: {str(e2)}")
                return {
                    "success": False,
                    "error": f"API 失败 → CLI 降级也失败: {str(e2)}",
                    "latency_ms": latency
                }
        
        return {
            "success": False,
            "error": str(e),
            "latency_ms": latency
        }
```

> **注意**：脚本路径放在 `~/.hermes/aim/letta_api_call.py`，与 `crewai_llm_call.py` 同级。脚本的完整实现在 P1 阶段完成后提供。

### 3.5 CLI/API 模式切换机制

通过 config.json 中的 `mode` 字段切换，不修改代码：

**CLI 模式（默认）：**
```json
"letta": {
  "chat": {
    "cmd": ["{cli}", "send", "--agent", "main", "--message", "{prompt}"],
    "timeout": 120,
    "mode": "cli"
  }
}
```

**API 模式（备选）：**
```json
"letta": {
  "chat": {
    "cmd": ["{python3}", "~/.hermes/aim/letta_api_call.py", "{prompt}", "{timeout}"],
    "timeout": 120,
    "mode": "api",
    "lettta_api_url": "http://localhost:8283",
    "letta_agent_id": "zs0003"
  }
}
```

切换方式：修改 config.json 中的 `cmd` 和 `mode` 字段即可，**不需要改任何代码**。

---

## 4. 错误降级策略

### 4.1 降级链

```
Letta CLI 调用
    │
    ├─ ✅ 成功 → 返回结果
    │
    └─ ❌ 失败（超时/退出码非0/异常）
         │
         ├─ 日志记录错误详情
         │
         ├─ 重试 1 次（间隔 3s） → 成功则返回
         │
         └─ 再失败 → 返回 AIResponse(success=False, error=详情)
```

### 4.2 具体策略

| 故障类型 | 处理方式 |
|---------|---------|
| **超时** (timeout) | 返回降级消息 "Letta 请求超时，请稍后重试" |
| **CLI 不存在** (FileNotFoundError) | 返回 "Letta CLI 未安装，请执行 `npm install -g @letta-ai/letta-code`" |
| **退出码非0** | 记录 stderr 前 300 字符，返回具体错误 |
| **API 模式失败** | 自动降级到 CLI 模式（一次），再失败则返回错误 |
| **网络错误** (API 模式) | 重试 1 次（3s 间隔），然后返回 "Letta API 不可达" |
| **所有模式都失败** | 返回 AIResponse(success=False)，调用方自行决定后续策略 |

### 4.3 设计原则

- **不卡死整个流程** — Letta 调用失败不会阻塞 AIM Agent 的消息处理循环
- **有损降级** — 返回失败信息给调用方，不是吞掉错误
- **日志可追溯** — 每次失败的完整上下文都记录到日志文件

---

## 5. 日志记录方案

### 5.1 日志内容

每次 Letta 调用记录以下信息：

| 字段 | 说明 | 示例 |
|------|------|------|
| `timestamp` | ISO 8601 时间戳 | `2026-06-08T15:30:00+08:00` |
| `mode` | CLI/API | `cli` |
| `prompt_len` | 输入 prompt 长度 | `156` |
| `success` | 是否成功 | `true` |
| `latency_ms` | 耗时 | `3421` |
| `exit_code` | 子进程退出码 (CLI) | `0` |
| `error` | 错误信息 (失败时) | `Connection refused` |
| `agent_id` | Agent ID | `ZS0003` |

### 5.2 日志路径

`~/.hermes/aim/logs/letta_call.log` — 与 AIM Server 日志同级。

### 5.3 日志轮转

```python
# 用 logging.handlers.RotatingFileHandler
handler = RotatingFileHandler(
    log_path,
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
```

---

## 6. 测试方案

### 6.1 本地模拟测试

在我（吉量，ZS0002）的机器上模拟小火鸡儿环境：

| 测试项 | 验证内容 | 验收标准 |
|--------|---------|---------|
| T1 | Letta CLI 路径配置正确 | `letta --version` 返回非空 |
| T2 | config.json commands 模板正确 | FrameworkCLI 能正确解析 letta 配置 |
| T3 | CLI 模式调用 Letta | 返回有效文本，success=true |
| T4 | API 模式调用 Letta (备用) | 返回有效文本，success=true |
| T5 | 超时降级 | 关闭 Letta 进程，调用返回 timeout 错误 |
| T6 | CLI 不存在降级 | 临时改 cli_path，返回友好错误 |
| T7 | 日志记录完整性 | 检查日志文件包含输入/输出/耗时 |
| T8 | 回调脚本切换模式 | 改 mode=api，调用成功 |

### 6.2 测试流程

3 轮基本 → 修复优化 → 5 轮全面

---

## 7. 实施计划

### 7.1 P1 — 方案文档 + 回调脚本（今天）

| 任务 | 改动量 | 说明 |
|------|--------|------|
| 方案文档 | ~20行 | 本文件 |
| 回调脚本 `letta_api_call.py` | ~80行 | CLI/API 双模式 + 降级 + 日志 |
| FrameworkCLI `elif letta` | ~8行 | `_fallback_call()` 新增分支 |
| config.json 配置 | ~15行 | commands.letta + cli_paths.letta |

### 7.2 P2 — 本地模拟测试 + 验证（今天）

- 在我机器上跑通 T1-T8 全部测试
- 验证 CLI 模式 + API 模式切换
- 验证降级策略和日志记录

### 7.3 P3 — 打 Release 包 + 交付

| 交付物 | 路径 | 说明 |
|--------|------|------|
| `framework_cli.py` | `~/shared/aim-client/ZS0003/` | 含 letta 支持 |
| `letta_api_call.py` | `~/shared/aim-client/ZS0003/` | 回调脚本 |
| `cli_adapter.py` | `~/shared/aim-client/ZS0003/` | 基类 |
| `ai_types.py` | `~/shared/aim-client/ZS0003/` | 类型定义 |
| `config.json` 示例 | `~/shared/aim-client/ZS0003/config.letta.json` | 配置模板 |
| `COMPATIBILITY.md` | `~/shared/aim/COMPATIBILITY.md` | 多 Agent 版本兼容说明 |

### 7.4 总改动量

| 模块 | 改动量 | 说明 |
|------|--------|------|
| `framework_cli.py` | ~8 行 | `_fallback_call()` 新增 letta elif |
| `letta_api_call.py` | ~80 行 | 新文件，回调脚本 |
| `config.json` | ~15 行 | commands + cli_paths |
| 测试 | ~50 行 | 本地模拟测试脚本 |
| **合计** | **~153 行** | |

---

## 8. 呱呱补充意见（已纳入）

| 补充意见 | 纳入位置 | 说明 |
|---------|---------|------|
| **回调脚本兼容 CLI + API 双模式** | §3.4, §3.5 | 通过 mode 字段切换，不改代码 |
| **错误降级策略** | §4 | 超时/CLI不存在/API失败/网络错误全覆盖 |
| **输入输出日志** | §5 | RotatingFileHandler，含时间戳/耗时/mode/agent_id |

---

## 9. 交付物清单

| # | 交付物 | 路径 | 负责人 |
|---|--------|------|--------|
| 1 | 方案文档（本文件） | `~/shared/aim/references/letta-framework-integration.md` | 吉量 🐴 |
| 2 | `letta_api_call.py` 回调脚本 | `~/.hermes/aim/letta_api_call.py` | 吉量 🐴 |
| 3 | FrameworkCLI letta 支持 | `~/shared/aim-client/ZS0002/framework_cli.py` → 同步ZS0003 | 吉量 🐴 |
| 4 | config.json letta 配置模板 | `~/shared/aim-client/ZS0003/config.letta.json` | 吉量 🐴 |
| 5 | 本地模拟测试脚本 | `~/shared/aim/tests/test_letta_integration.py` | 吉量 🐴 |
| 6 | Release 包 | `~/shared/aim/releases/letta-integration-v1.0/` | 吉量 🐴 |
| 7 | COMPATIBILITY.md | `~/shared/aim/COMPATIBILITY.md` | 吉量 🐴 |

---

*本方案待呱呱 🐸 (ZS0001) 评审通过后进入实施阶段。*
