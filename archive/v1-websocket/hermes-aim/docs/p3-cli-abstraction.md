# P3 — CLI 抽象层设计文档

> 版本: v1.1 | 状态: 设计中 | 作者: 呱呱 🐸 | Review: 吉量 🐴
> 目标: 去掉 aim-agent.py 里的 if/elif 硬编码框架路由，改由 commands 模板驱动
> v1.1 变更: 补充安全防护(shlex.quote) + session_id 回传 + delegate session 隔离

---

## 1. 问题

当前 `aim-agent.py` 有 **3 处** if/elif 硬编码框架路由：

| 位置 | 方法 | 分支数 |
|------|------|--------|
| L711-718 | `_delegate_to_agent()` | 4 (hermes/openclaw/qwenpaw/crewai) |
| L930-945 | `_call_cli()` | 4 |
| L870-890 | `_inject_to_main_session()` | 4 |

每加一个新框架（如 crewai），需要改 **3 个地方**，且每个框架的调用方式差异大：
- hermes: `hermes chat -q prompt -Q`
- openclaw: `openclaw agent --agent main -m prompt --json`
- qwenpaw: `qwenpaw agent chat --from-agent default --to-agent default --text prompt`
- crewai: 需要子进程 Python 3.12 环境

---

## 2. 设计方案

### 2.1 核心思想：命令模板 + 统一调用器

```python
# config.json 新增 commands 段
{
  "commands": {
    "hermes": {
      "chat": {
        "cmd": ["{cli}", "chat", "-q", "{prompt}", "-Q"],
        "timeout": 120,
        "output": "stdout",
        "filter": ["session_id:", "INFO:"]
      }
    },
    "openclaw": {
      "chat": {
        "cmd": ["{cli}", "agent", "--agent", "main", "-m", "{prompt}", "--json"],
        "timeout": 120,
        "output": "json",
        "json_path": "result.payloads[0].text"
      }
    },
    "crewai": {
      "chat": {
        "cmd": ["{python312}", "{script}", "{prompt}", "{timeout}"],
        "timeout": 180,
        "output": "stdout",
        "python312": "~/.local/share/uv/tools/crewai/bin/python",
        "script": "~/.hermes/aim/crewai_llm_call.py"
      }
    },
    "qwenpaw": {
      "chat": {
        "cmd": ["{cli}", "agent", "chat", "--from-agent", "default", "--to-agent", "default", "--text", "{prompt}", "--timeout", "{timeout}"],
        "timeout": 120,
        "output": "stdout",
        "filter": ["[SESSION:", "INFO:"]
      }
    }
  }
}
```

### 2.2 命令模板字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `cmd` | list[str] | 命令模板，`{variable}` 会被替换 |
| `timeout` | int | 默认超时秒数 |
| `output` | str | 输出解析方式：`stdout` / `json` / `stderr` |
| `json_path` | str | JSON 输出时，提取文本的路径（支持 `[0]` 索引） |
| `filter` | list[str] | 输出过滤：包含这些前缀的行会被移除 |
| `env` | dict | 额外环境变量（可选） |
| `python312` | str | 指定 Python 3.12 路径（crewai 专用） |
| `script` | str | 外部脚本路径（crewai 专用） |

### 2.3 模板变量

| 变量 | 来源 | 说明 |
|------|------|------|
| `{cli}` | `cli_paths[framework]` | CLI 工具路径 |
| `{prompt}` | 调用时传入 | 用户消息 |
| `{timeout}` | 调用时传入或默认 | 超时秒数 |
| `{session_id}` | `self._current_session_id` | 会话 ID |
| `{agent_id}` | `self.agent_id` | Agent ID |
| `{python312}` | 模板内定义 | crewai 专用 Python 路径 |
| `{script}` | 模板内定义 | crewai 专用脚本路径 |

---

## 3. 统一调用器

```python
import shlex

class FrameworkCLI:
    """框架 CLI 统一调用器"""
    
    def __init__(self, framework: str, commands: dict, cli_paths: dict):
        self.framework = framework
        self.cmd_config = commands.get(framework, {}).get("chat")
        self.cli_paths = cli_paths
    
    async def call(self, prompt: str, timeout: int = None, 
                   session_id: str = None, agent_id: str = None) -> dict:
        """统一调用接口，返回 {success, text, session_id, error}"""
        if not self.cmd_config:
            return {"success": False, "error": f"未配置 {self.framework} 的 chat 命令"}
        
        timeout = timeout or self.cmd_config.get("timeout", 120)
        
        # 构建命令
        cmd = self._build_cmd(prompt, timeout, session_id, agent_id)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()[:300]
                return {"success": False, "error": f"退出码 {proc.returncode}: {err}"}
            
            text, extracted_session = self._parse_output(stdout)
            result = {"success": True, "text": text}
            if extracted_session:
                result["session_id"] = extracted_session
            return result
            
        except asyncio.TimeoutError:
            return {"success": False, "error": f"超时 ({timeout}秒)"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _build_cmd(self, prompt, timeout, session_id, agent_id) -> list:
        """构建命令行（v1.1: 所有用户输入经 shlex.quote 防注入）"""
        variables = {
            "cli": self.cli_paths.get(self.framework, self.framework),
            "prompt": shlex.quote(prompt),          # P0: 防 shell 注入
            "timeout": str(timeout),
            "session_id": shlex.quote(session_id or ""),  # P0: 防注入
            "agent_id": shlex.quote(agent_id or ""),      # P0: 防注入
        }
        # 支持模板内的额外变量（如 python312, script）
        for k, v in self.cmd_config.items():
            if k.startswith("python") or k == "script":
                variables[k] = os.path.expanduser(str(v))
        
        cmd = []
        for part in self.cmd_config["cmd"]:
            # 替换 {variable}
            for var_name, var_value in variables.items():
                part = part.replace(f"{{{var_name}}}", var_value)
            cmd.append(part)
        return cmd
    
    def _parse_output(self, stdout: bytes) -> tuple:
        """解析输出（v1.1: 返回 (text, extracted_session_id)）"""
        output = stdout.decode("utf-8").strip()
        extracted_session = None
        
        # 逐行处理：先提取 session_id，再过滤
        lines = output.split("\n")
        clean_lines = []
        filters = self.cmd_config.get("filter", [])
        
        for line in lines:
            # 提取 session_id（hermes: "session_id:xxx", qwenpaw: "[SESSION:xxx]"）
            if line.startswith("session_id:"):
                sid = line.split(":", 1)[1].strip()
                if sid:
                    extracted_session = sid
                continue  # session 行不输出
            if line.startswith("[SESSION:"):
                sid = line.replace("[SESSION:", "").replace("]", "").strip()
                if sid:
                    extracted_session = sid
                continue
            # 其他 filter
            if not any(f in line for f in filters):
                clean_lines.append(line)
        
        text = "\n".join(clean_lines).strip()
        
        # JSON 提取
        if self.cmd_config.get("output") == "json":
            try:
                data = json.loads(text)
                path = self.cmd_config.get("json_path", "")
                text = self._extract_json(data, path)
            except json.JSONDecodeError:
                pass
        
        return text, extracted_session
    
    def _extract_json(self, data, path: str) -> str:
        """从 JSON 中提取文本"""
        parts = path.replace("[", ".").replace("]", "").split(".")
        current = data
        for part in parts:
            if not part:
                continue
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return str(current)
        return str(current) if current else ""
```

---

## 4. 改造方案

### 4.1 aim-agent.py 改造

**改动前（3处 if/elif）：**
```python
# _call_cli
if self.framework == "hermes":
    result = await self._call_hermes(...)
elif self.framework == "openclaw":
    result = await self._call_openclaw(...)
elif self.framework == "qwenpaw":
    result = await self._call_qwenpaw(...)
elif self.framework == "crewai":
    result = await self._call_crewai(...)
```

**改动后（统一调用，v1.1）：**
```python
# __init__ 中初始化
self.cli = FrameworkCLI(self.framework, self._commands, self._cli_paths)

# _call_cli 简化为（v1.1: 自动回传 session_id）
async def _call_cli(self, prompt, timeout=None):
    result = await self.cli.call(prompt, timeout, self._current_session_id, self.agent_id)
    if result.get("session_id"):
        self._current_session_id = result["session_id"]
    return result

# _delegate_to_agent 简化为（v1.1: 不传 session_id，隔离 session）
async def _delegate_to_agent(self, target_agent, content, sender, msg_id):
    framework = self.AGENT_FRAMEWORK.get(target_agent)
    cli = FrameworkCLI(framework, self._commands, self._cli_paths)
    # 不传 session_id — delegate 用别人框架，应该新建 session
    return (await cli.call(prompt, timeout=120, agent_id=target_agent)).get("text")
```

**删除的代码：**
- `_call_hermes()` — 被模板替代
- `_call_openclaw()` — 被模板替代
- `_call_qwenpaw()` — 被模板替代
- `_call_crewai()` — 被模板替代
- `_delegate_hermes()` — 被模板替代
- `_delegate_openclaw()` — 被模板替代
- `_delegate_qwenpaw()` — 被模板替代
- `_delegate_crewai()` — 被模板替代

**净减少：~200 行 if/elif 代码**

### 4.2 config.json 改造

```json
{
  "cli_paths": {
    "hermes": "/Users/yangzs/.local/bin/hermes",
    "openclaw": "/Users/yangzs/.npm-global/bin/openclaw",
    "qwenpaw": "/Users/yangzs/.qwenpaw/bin/qwenpaw",
    "crewai": "/Users/yangzs/.local/bin/crewai"
  },
  "commands": {
    "hermes": {
      "chat": {
        "cmd": ["{cli}", "chat", "-q", "{prompt}", "-Q"],
        "timeout": 120,
        "output": "stdout",
        "filter": ["session_id:", "INFO:"]
      }
    },
    "openclaw": {
      "chat": {
        "cmd": ["{cli}", "agent", "--agent", "main", "-m", "{prompt}", "--json"],
        "timeout": 120,
        "output": "json",
        "json_path": "result.payloads.0.text"
      }
    },
    "qwenpaw": {
      "chat": {
        "cmd": ["{cli}", "agent", "chat", "--from-agent", "default", "--to-agent", "default", "--text", "{prompt}", "--timeout", "{timeout}"],
        "timeout": 120,
        "output": "stdout",
        "filter": ["[SESSION:", "INFO:"]
      }
    },
    "crewai": {
      "chat": {
        "cmd": ["{python312}", "{script}", "{prompt}", "{timeout}"],
        "timeout": 180,
        "output": "stdout",
        "python312": "~/.local/share/uv/tools/crewai/bin/python",
        "script": "~/.hermes/aim/crewai_llm_call.py"
      }
    }
  }
}
```

---

## 5. 新框架接入流程（改造后）

**接入新框架只需要：**
1. 在 `config.json` 的 `commands` 段加一个模板
2. 在 `cli_paths` 段加 CLI 路径
3. 完成

**不需要改 aim-agent.py 的任何代码。**

---

## 6. 向后兼容

| 场景 | 处理方式 |
|------|---------|
| 旧 config.json 没有 `commands` | 自动用 `cli_paths` + 默认模板 |
| 旧框架（hermes/openclaw/qwenpaw） | 模板和原来行为一致 |
| 新框架（crewai/未来） | 只需加模板 |
| `_inject_to_main_session()` | 也改为模板驱动 |

---

## 7. 测试用例

| 编号 | 场景 | 预期 |
|------|------|------|
| T01 | hermes 框架 chat | 行为与改造前一致 |
| T02 | openclaw 框架 chat | 行为与改造前一致 |
| T03 | qwenpaw 框架 chat | 行为与改造前一致 |
| T04 | crewai 框架 chat | 行为与改造前一致 |
| T05 | 新加一个测试框架（只改 config） | 不改代码，直接可用 |
| T06 | config 缺少 commands 段 | 自动降级到 cli_paths 逻辑 |
| T07 | delegate 到其他 agent | 模板路由正确 |
| T08 | inject_to_main_session | 模板路由正确 |

---

## 8. 文件变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `aim-agent.py` | 修改 | 删除 4 个 _call_xxx + 4 个 _delegate_xxx，改用 FrameworkCLI |
| `framework_cli.py` | **新增** | FrameworkCLI 类 |
| `config.json` | 修改 | 新增 `commands` 段 |
| `test_framework_cli.py` | 新增 | 测试脚本 |

---

## 9. 实现时间线

| 阶段 | 时间 | 内容 |
|------|------|------|
| 今晚 | 2h | 设计文档 + framework_cli.py 骨架 |
| 明早 | 2h | config.json commands 模板 + aim-agent.py 改造 |
| 明午 | 1h | 测试验证 + 回归 |

---

*— 设计文档 v1.1 完成。*

---

## 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-06-05 | 初始设计 |
| v1.1 | 2026-06-05 | 吉量 review 后修正：① shlex.quote 防注入 ② session_id 回传 ③ delegate session 隔离 |
