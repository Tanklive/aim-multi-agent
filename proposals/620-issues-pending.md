# 620 待修复问题项

> 2026-06-20 20:10 · 5轮审计 · 原始需求/架构/协议/定位对照

---

## 一、审计矩阵

| 轮次 | 范围 | 方法 | 结果 |
|:--:|------|------|:--:|
| R1 | 需求对照 | 原始需求→规划→方案→标准→红线 逐项对照 | 4 项偏差 |
| R2 | 功能验证 | 群消息/DM/确认循环/校验拦截 实发实测 | 全部通过 |
| R3 | 代码质量 | md5/版本/缓存/同步 全量扫描 | 3 项不一致 |
| R4 | 619+620回溯 | 日志关键词/告警/心跳/observer | 2 项回调 |
| R5 | 端到端全链路 | 异构框架/DM/grp/容错/降级 | 1 项鉴权 |

---

## 二、问题清单

### 🔴 严重

#### 620-A01: main.py 部署副本不同步
- **现象**: shared `main.py` (1148行) ≠ 三个Agent部署副本 (1082行)，差66行
- **根因**: 改 shared 后未 cp 到 `~/.aim/agents/{id}/aim-client/`
- **风险**: 虽然进程从 shared 目录直接运行（lsof cwd确认），但部署副本长期不一致会导致：(a) 应急重启时误用旧版 (b) 三方审查时版本混淆
- **责任**: 🐴 吉量
- **修复**: 全量同步 `main.py` 到三个部署目录

#### 620-A02: adapter.sh 版本混乱
- **现象**: 5个adapter，5个不同版本 (v1.3 / v1.4 / v1.5 / v1.6.1 / v1.8.2)
- **根因**: 没有统一的版本编号体系；模板 openclaw v1.3 ≠ ZS0001 部署版 v1.5
- **风险**: 异构框架间行为不一致，联调时版本对齐困难
- **责任**: 三方 (各自adapter)
- **修复**: 统一版本号规范 `specs/adapter-version-standard.md`；各Adapter header 标注 AIM Adapter Interface 版本

#### 620-A03: ZS0003 持续性降级风暴
- **现象**: alerts.log 记录 19 次连续 degrade_storm，agent_offline CRITICAL (>4198s)
- **根因**: Letta adapter health 探针间歇性报 `letta CLI 不可用 (exit=3)`，但 openclaw gateway health 正常——两条健康检查路径不一致
- **风险**: 假阳性 CRITICAL 告警消耗注意力，队列积压触发 StallWatchdog→恢复→再积压循环
- **责任**: 🐤 火鸡儿
- **修复**: ZS0003 health_probe 切换为 adapter 统一接口，不再直调 Letta CLI

#### 620-A04: ZS0001 StallWatchdog 持续自愈失败
- **现象**: main.py 日志反复 `投递循环异常: unknown exit=-9` + `trim_exit=2`，StallWatchdog #1/#2/#3 循环
- **根因**: adapter 返回 exit=-9（未定义退出码），Scheduler 无法归类，消息无限重试
- **风险**: 队列积压，投递卡死
- **责任**: 🐸 呱呱
- **修复**: adapter 所有退出路径对齐 exit code 标准 (0/1/2/3/4)；未知退出码归为 FATAL(3)

### 🟡 中危

#### 620-B01: .pyc 缓存污染风险
- **现象**: 23个 `.pyc` 文件，含 cpython-313 和 cpython-314 双份缓存
- **根因**: Python 3.13 和 3.14 混合使用，`-B` 启动标志未在所有进程统一
- **风险**: 代码更新后旧字节码可能仍被加载
- **修复**: `deploy-verify.sh` 加 .pyc 清理步骤；统一 Python 版本或全部加 `-B`

#### 620-B02: aim_nats_sdk.py 额外副本
- **现象**: `shared/aim/src/aim_nats_sdk.py` 与主版本 md5 不一致
- **根因**: `src/` 目录为遗弃路径，未被清理
- **风险**: 混淆。低风险（不在任何 sys.path 中）
- **修复**: 删除或归档

#### 620-B03: 确认循环检测器分工不明确
- **现象**: shared main.py 有两套确认循环检测：我写的 `_is_confirm_loop()`（确认循环跳过）和呱呱的 `纯确认消息，静默跳过`——后者实际生效
- **根因**: 并行开发时未合并设计
- **风险**: 重复代码，未来维护混淆
- **修复**: 统一为一套；保留呱呱版本（更简洁），移除我的 `_is_confirm_loop()`

### 🟢 低危/建议

#### 620-C01: 天然令牌消耗偏高
- **现象**: 24小时 1.63亿 token（~¥3100），日均 session 2221
- **分析**: 确认循环期间大量消息被处理。修复后应下降
- **监控**: 72小时内观察趋势

#### 620-C02: deploy-verify 缺少 SDK validator 功能测试
- **现状**: 仅检查文件存在和进程运行，不测试 `validate_envelope()` 实际拦截
- **建议**: 加 malformed message 注入 + envelope_invalid 事件验证

#### 620-C03: 告警风暴冷静期偏短
- **现状**: alertd CRITICAL 冷却 120s，但持续降级可达 19 次/5min
- **建议**: CRITICAL 冷却升到 300s，避免刷屏

---

## 三、R1 需求对照偏差

| 需求 | 状态 | 偏差 |
|------|:--:|------|
| Transport 7方法 | ✅ | request() 已补 |
| Adapter 4接口 | ✅ | process/health/info/cancel |
| Agent Card execution_model | ✅ | realtime/deferred/batch |
| Message/Task 分层 | ⚠️ | schema 定义在 types.py，但 dispatch 未区分 Chat/Task |
| 三级降级模型 | ⚠️ | P1-2 DEGRADE 滑动窗口已实现，L0/L2 降级路径未完整 |
| 安全模型 v1 (白名单+限流) | ✅ | SecurityManager chain_steps=2 |
| 三层身份 | ✅ | UUID+serial+name |
| Discovery 最小实现 | ✅ | Registry KV 注册+在线查询 |
| 生命周期6态预留 | ⚠️ | schema 占位，仅 ONLINE/OFFLINE/DEGRADE 实际使用 |
| 架构红线 (Client≠Runtime) | ✅ | 通信、调度、身份均在 AIM Client 侧 |
| 确认循环检测 | ✅ | 呱呱版 `纯确认消息，静默跳过` 生效 |

---

## 四、619 问题回归结果

| 619-ID | 问题 | 回归 |
|--------|------|:--:|
| 620-01 | StallWatchdog 自愈无效 | ✅ 已修，清零移到成功路径 |
| 620-02 | Letta TUI session 占用 | ⚠️ ZS0003 health probe 仍报 letta CLI 不可用 |
| 620-03 | ZS0003 queue 积压 | ✅ 当前 0 items |
| 620-04 | health 探针假阴性 | ✅ memfs 替代 grep |
| 620-05 | adapter v1.7 升级 | ✅ |
| 620-06 | ZS0001 StallWatchdog | ❌ 复发，exit=-9 新问题 |
| 620-07 | exit 3/4 健康探针路径 | ✅ 标准定稿 |
| 620-08 | 单点故障全群静默 | ⚠️ alertd 已部署但 ZS0003 假阳性告警 |

---

## 五、修复优先级

| 优先级 | 编号 | 项 | 估 |
|:--:|------|-----|:--:|
| 🔴 P0 | A04 | ZS0001 exit=-9 修复 | 小 |
| 🔴 P0 | A03 | ZS0003 health probe 对齐 | 中 |
| 🔴 P1 | A01 | main.py 部署副本同步 | 小 |
| 🟠 P2 | A02 | adapter 版本统一 | 中 |
| 🟠 P2 | B01 | .pyc 缓存清理 | 小 |
| 🟡 P3 | B03 | 确认循环检测器合并 | 小 |
| 🟢 P4 | C01-C03 | 观察/优化 | — |
