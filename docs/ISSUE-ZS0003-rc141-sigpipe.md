# ZS0003 rc=141 SIGPIPE 全链路排查与修复

> 日期: 2026-06-22 | 负责人: 小火鸡儿 (ZS0003) | 协作: 呱呱 (ZS0001) 提供数据分析和建议

---

## 一、问题发现

**时间**: 2026-06-22 18:30  
**触发**: 大哥让各 Agent 检查 AIM 服务状态，呱呱分析群聊记录发现 ZS0003 的 AIM Client 日志中大量 rc=141

**数据（呱呱分析）**:
- 全量 rc=141: 33 次
- 当日集中爆发: 26 次（18:33-19:01）
- Degrade 触发: 15 次/2h
- OFFLINE 触发: 3 次/2h
- 群聊消息有效率: 100% 确认类（"收到""在的""随时呼我"），有效技术推进 0 条

**症状**: 从大哥和呱呱的视角，ZS0003 只在群里回"收到大哥"，对 POST-01/02/03 的技术追问全被截断未发出。

---

## 二、根因链（五层，逐层深挖）

### 第 1 层：冷启动超时（v1.10.0 修复）
- **根因**: PROBE_TIMEOUT=15s 不够。`letta --new` 冷启动 ~5s + 大 prompt >15s 边界
- **修复**: 15→25s
- **状态**: ✅ 部分缓解，但 rc=141 继续发生（rc=141 是 SIGPIPE，不是超时 124）

### 第 2 层：adapter 退出码吞码（v1.11.0 修复）
- **根因**: adapter.sh 第 373 行 `rc -ne 0 → exit 2` 把所有非零退出码吞成 Degrade。main.py 第 3203 行早已有 rc=141 → RetryableError 处理，但 adapter 没透传
- **修复**: rc=141 时 exit 141（透传），不进 exit 2
- **架构意义**: 接口层不应转码——adapter 透传真实退出码，让 main.py 的退避机制工作

### 第 3 层：冷启动重试条件错误（v1.12.0 修复）
- **根因**: `[ $rc -ne 124 ]` 对 rc=0（成功）也为真 → 成功后多余做一次 timeout 调用 → 第二次触发 SIGPIPE
- **修复**: 改为 `[ $rc -eq 124 ] || [ $rc -eq 141 ]`（白名单式精确匹配）
- **教训**: 退出码判断用白名单（-eq）而非否定式（-ne），否定式隐式包含了不该重试的退出码

### 第 4 层：main.py 代码未 commit（6/22 21:01 修复）
- **根因**: main.py 中 rc=141 → RetryableError 的代码（第 3203 行）只在磁盘修改但从未 commit 到 git。进程 20:38 启动加载了旧代码，不认识 exit 141，走 `else: unknown exit=141 → FATAL`
- **修复**: commit + 重启进程
- **教训**: 改了对方仓库的代码要 commit，不然进程重启后加载的是旧版本

### 第 5 层：set -o pipefail（v1.12.0 最终修复）
- **根因**: adapter.sh 第 2 行 `set -euo pipefail`。当 adapter.sh 被 main.py `asyncio.create_subprocess_shell` 调用时，脚本内部第一个 `$(ls ... | head -1)` 子进程继承了来自父进程的破损 pipe fd，pipefail 下直接 SIGPIPE (exit 141)
- **关键**:
  - 不是 conv ID 追踪逻辑问题
  - 不是 `echo` 后的竞态
  - 不是 nested `$()` 设计缺陷
  - **就是 pipefail 在 subprocess 场景下的副作用**
- **修复**: 去 pipefail，保留 `-eu`。脚本内无数据管道（所有管道已改为临时文件或 python3），pipefail 无保护价值
- **验证**: 5/5 全通 exit=0

---

## 三、排查过程中的关键误判

1. **误判 1**: 把 rc=141 归因为并发管道竞争（第 2 层）→ 不够深，并发竞争只是表象
2. **误判 2**: 把 adapter 退出码透传当作完整解决方案（第 4 层）→ 修了退出码映射但没修产生 141 的原因
3. **误判 3**: 把 `_track_conv_id` 中的 `wc -l | tr` 管道当作触发点 → pipefail 下**任何** `$()` 都触发，与具体内容无关
4. **误判 4**: 反复尝试改函数内部逻辑（移 echo 位置、换 python3 替代管道、拆分 local 声明）→ 治标不治本，根因在 bash 选项层

---

## 四、最终修复方案

**改动**: adapter.sh 第 2 行 `set -euo pipefail` → `set -eu`（一行改动）

**版本演进**:
| 版本 | 修复 | 层面 |
|:--:|------|------|
| v1.10.0 | PROBE_TIMEOUT 15→25s | 参数 |
| v1.11.0 | rc=141 接口层透传 exit 141 | 接口契约 |
| v1.12.0 | 去 pipefail + 冷启动重试白名单修正 | bash 选项 + 控制流 |

**为什么 pipefail 无保护价值**:
- 脚本内所有数据管道（`base64 -d | sed`）已在 v1.12.0 改为 python3 或临时文件方案
- 保留 `-eu` 仍然提供未定义变量检查和命令错误退出保护

---

## 五、设计原则总结

1. **接口层不转码**: adapter 透传真实退出码，让 main.py 决策
2. **退出码用白名单**: `[ $rc -eq X ]` 而非 `[ $rc -ne Y ]`
3. **bash 选项做减法**: 去掉不再需要的保护（pipefail），保留仍有价值的保护（-eu）
4. **排查前先二分**: 直接逐行定位比猜测快 10 倍
5. **改了别人的代码要 commit**: 磁盘修改 ≠ 运行中生效

---

## 六、参考

- gotchas 热记忆: `[[reference/aim/gotchas.md#post-02-全链路-rc141-sigpipe-排查与修复-2026-06-22]]`
- adapter 当前版本: `~/.aim/agents/ZS0003/adapter.sh` (v1.12.0)
- shared 同步: `~/shared/aim/adapters/letta/adapter.sh`
- 呱呱分析报告: `~/.openclaw/workspace/memory/ZS0003-rc141-issue.md`
