# AIM 项目版本管理标准


> 版本：1.1

> 更新：2026-06-18 16:20

> 负责人：呱呱（基建/开发/安全/底层逻辑）


---


## 一、版本号格式


**简化版 SemVer**：MAJOR.MINOR.PATCH（如 1.3.0）


- **MAJOR**：不兼容的 API 变更

- **MINOR**：向后兼容的功能新增

- **PATCH**：向后兼容的问题修复


**预发布版本**：1.3.0-alpha.1 / 1.3.0-beta.1 / 1.3.0-rc.1


**注意**：

- 三字段 SemVer，全项目统一

- 等三方都稳定了、真要发 release 了再考虑完整 SemVer 规范


---


## 二、各模块版本号位置


| 模块 | 文件 | 版本号变量 | 标准写法 |

|------|------|-----------|----------|

| 项目级 | shared/aim/VERSION | 无 | 1.3.0（文件内容） |

| SDK | src/aim_nats_sdk.py | VERSION | VERSION = "1.3.0" |

| Protocol | src/aim_nats_sdk.py | PROTOCOL_VERSION | PROTOCOL_VERSION = "1.0" |

| Protocol | src/aim_nats_sdk.py | MIN_PROTOCOL_VERSION | MIN_PROTOCOL_VERSION = "1.0" |

|.aim-client | aim_client/__init__.py | VERSION | VERSION = "1.3.0" |

| aim-watch | src/aim-watch.py | VERSION | VERSION = "2.1.0"（下次项目 MAJOR→2.0 时纳入统一版本） |

| 适配器 | 各 adapter.sh | 注释标记 | `# v1.7`（当前用注释标记，后续标准化为 adapter info 模式的 version 字段） |


**注意**：

- aim-watch 临时独立版本（2.1.0），下次项目 MAJOR→2.0 时纳入统一版本管理

- Queue / Scheduler / Security / Registry / GroupAdmission 等子模块从 aim_client/__init__.py 读取版本号


---


## 三、版本号更新规则


### 3.1 核心模块变更（SDK / aim-client / Registry）


- **MAJOR 变更**：所有模块 MAJOR + 1，MINOR = 0

- **MINOR 变更**：所有模块 MINOR + 1


### 3.2 子模块变更（Queue / Scheduler / Security）


- **MAJOR 变更（接口变化）**：aim-client MAJOR + 1

- **MINOR 变更（接口变化）**：aim-client MINOR + 1

- **MINOR 变更（内部实现）**：子模块 MINOR + 1，aim-client 不变


### 3.3 适配器变更


- 独立版本号，不影响其他模块


---


### 3.4 版本号冲突仲裁


当 SDK / aim_client / 项目级版本号不一致时：


- **SDK 是基础设施**，以 SDK 为基准

- aim_client 版本号 = SDK.VERSION

- 项目级 VERSION 文件 = SDK.VERSION

- 任何模块的版本号不得低于 SDK.VERSION


## 四、协议版本检查


### 4.1 Protocol Version（PROTOCOL_VERSION）


- 由 SDK 定义，用于 agent 握手时做兼容检查

- 格式："1.0"（主版本.次版本）

- 检查：peer.PROTOCOL_VERSION < my.MIN_PROTOCOL_VERSION → WARNING（不拒绝）


### 4.2 Module Version（VERSION）


- 各模块的版本号，用于开发追踪和发布管理

- 格式："1.2.1"（MAJOR.MINOR.PATCH）

- 检查：SDK.VERSION < aim-client.MIN_SDK_VERSION → 拒绝


### 4.3 实现方式


```python

# src/aim_nats_sdk.py

PROTOCOL_VERSION = "1.0"

MIN_PROTOCOL_VERSION = "1.0"


async def connect(self):

    # ... 连接 NATS ...

    # Protocol Version 检查（轻量级）

    # 变量已定义（PROTOCOL_VERSION / MIN_PROTOCOL_VERSION）

    # 变量已定义（PROTOCOL_VERSION / MIN_PROTOCOL_VERSION）

    # TODO: Phase 2+ 实现 AgentCard 查询和运行时版本比对


---


## 五、CHANGELOG.md 使用规范


### 5.1 文件位置


- 文件：shared/aim/CHANGELOG.md

- 格式：Markdown


### 5.2 记录格式


```markdown

## [1.2.1] — 2026-06-18


### 新增

- 新增功能 1

- 新增功能 2


### 变更

- 变更内容 1

- 变更内容 2


### 修复

- 修复问题 1

- 修复问题 2

```


### 5.3 更新原则


- 谁改了代码谁更新 CHANGELOG.md

- 每次版本发布前更新 CHANGELOG.md

- 记录所有影响功能的行为变更


---


- **非发布改动**：不移除或改动 API 的改动，CHANGELOG 标类型即可

- **内部重构**（大量代码改动但不变功能）：不 bump 版本号，CHANGELOG 标"内部重构"

## 六、版本发布流程（简化版）


### 6.1 开发阶段


- 使用预发布版本（如 1.2.1-alpha.1）

- 三方同步到 ~/shared/

- 测试验证


### 6.2 测试阶段


- 升级到 1.2.1-rc.1

- 端到端测试

- 三方确认


### 6.3 正式发布


- 版本号：1.2.1

- 更新 CHANGELOG.md

- 同步到 ~/shared/

- 三方重启进程

- 验证发布


---


## 七、版本管理频率


### 7.1 发布频率


- **MINOR 发布**：每周 1-2 次（功能新增或问题修复）

- **MAJOR 发布**：按需（不兼容的 API 变更）

- **预发布版本**：开发阶段随时


### 7.2 发布时机


- **必须发布**：

  - 不兼容的 API 变更（MAJOR）

  - 严重问题修复（MINOR）

  - 安全漏洞修复（MINOR）

  - 核心功能新增（MINOR）


- **建议发布**：

  - 小问题修复（MINOR）

  - 优化改进（MINOR）

  - 文档更新（MINOR）


- **暂不发布**：

  - 代码重构（不影响功能）

  - 注释更新（不影响功能）

  - 测试代码更新（不影响功能）


### 7.3 发布周期


- **开发周期**：：1-2 天（MINOR）

- **测试周期**：1 天（MINOR）

- **发布周期**：1-2 小时（MINOR）

- **MAJOR 发布周期**：1 周（需要充分测试）


---


## 八、版本管理要求


### 8.1 发布前必须完成


- [ ] 版本号对齐（项目级 / aim_client / SDK）

- [ ] CHANGELOG.md 更新

- [ ] 协议版本检查实现

- [ ] 代码同步到 ~/shared/

- [ ] 三方重启进程

- [ ] 端到端测试通过

- [ ] 三方评审通过


### 8.2 发布后必须完成


- [ ] 记录发布时间到 CHANGELOG.md

- [ ] 通知所有相关方

- [ ] 监控发布后 24 小时

- [ ] 记录发布结果到 CHANGELOG.md


### 8.3 版本号管理要求


- [ ] 版本号必须对齐（项目级 / aim_client / SDK）

- [ ] 版本号必须符合 MAJOR.MINOR 格式

- [ ] 预发布版本必须带后缀（-alpha.1 / -beta.2 / -rc.1）

- [ ] 版本号不能降级（不能从 1.2 降到 1.1）


### 8.4 CHANGELOG.md 管理要求


- [ ] �每次版本发布前必须更新 CHANGELOG.md

- [ ] CHANGELOG.md 必须记录所有影响功能的行为变更

- [ ] CHANGELOG.md 必须记录发布时间

- [ ] CHANGELOG.md 必须记录发布结果（成功/失败）


---


## 九、版本管理规范


### 9.1 版本号命名规范


- **格式**：MAJOR.MINOR.PATCH（如 1.2.1）

- **预发布版本**：MAJOR.MINOR.PATCH-阶段.序号（如 1.2.1-alpha.1）

- **阶段**：alpha / beta / rc

- **序号**：从 1 开始递增


### 9.2 CHANGELOG.md 编写规范


- **格式**：Markdown

- **结构章节**：新增 / 变更 / 修复

- **每条记录**：简洁明了，不超过 50 字

- **时间格式**：YYYY-MM-DD


### 9.3 版本发布规范


- **发布前**：三方评审，通过后发布

- **发布时**：同步到 ~/shared/，三方重启进程

- **发布后**：监控 24 小时，记录发布结果


### 9.4 版本回滚规范


- **回滚前**：确认旧版本兼容性

- **回滚时**：同步到 ~/shared/，三方重启进程

- **回滚后**：端到端验证，记录回滚结果


---


## 十、版本回滚策略（简化版）


### 10.1 回滚前检查


- 确认旧版本兼容性

- 确认 CHANGELOG.md 有回滚记录


### 10.2 回滚步骤


1. 回滚代码到旧版本

2. 更新版本号

3. 更新 CHANGELOG.md

4. 同步到 ~/shared/

5. 重启所有进程

6. 端到端验证


### 10.3 回滚后必须完成


- [ ] 记录回滚时间到 CHANGELOG.md

- [ ] 记录回滚原因到 CHANGELOG.md

- [ ] 通知所有相关方

- [ ] 监控回滚后 24 小时

- [ ] 记录回滚结果到 CHANGELOG.md


---


## 十一、版本管理工具（暂不实现）


**注意**：等三方都稳定了、真要发 release 了再做，现在做就是过度工程。


### 11.1 version_check.sh


**功能**：

- 读取所有模块版本号

- 输出对照表（谁在什么版本）

- 检测不一致


**接口**：

```bash

bash version_check.sh

```


**输出**：

```

AIM 项目版本检查

==================

项目级 VERSION: 1.3.0

aim_client: 1.3.0

SDK: 1.3.0

aim-watch: 2.1.0（独立工具）


版本号对齐：✅

```


### 11.2 version_update.sh


**功能**：

- 自动升版本

- 联动升级依赖模块


**接口**：

```bash

bash version_update.sh <模块> <MAJOR|MINOR>

```


**示例**：

```bash

bash version_update.sh aim_client MINOR  # aim_client: 1.3.0 → 1.3.0

bash version_update.sh SDK MAJOR       # SDK: 1.3.0 → 2.0.0

```


**输出**：

```

版本更新成功

============

aim_client: 1.3.0 → 1.3.0

SDK: 1.3.0 → 1.3.0（联动升级）

项目级 VERSION: 1.3.0 → 1.3.0（联动升级）

```


### 11.3 version_compat.sh


**功能**：

- 检查各 Agent 运行的版本跟 Protocol Version 是否兼容


**接口**：

```bash

bash version_compat.sh

```


**输出**：

```

协议版本兼容性检查

==================

ZS0001: PROTOCOL_VERSION=1.0 ✅

ZS0002: PROTOCOL_VERSION=1.0 ✅

ZS0003: PROTOCOL_VERSION=1.0 ✅


协议版本兼容：✅

```


---


## 十二、版本号示例


### 12.1 当前版本号（2026-06-18）


| 模块 | 版本号 |

|------|--------|

| 项目级 VERSION | 1.2.1 |

| aim_client | 1.2.1 |

| SDK | 1.2.1 |

| aim-watch | 2.1.0（临时独立，下次 MAJOR→2.0 合并） |


### 12.2 版本号示例


- 1.0：初始版本

- 1.1：新增功能

- 1.2：修复问题

- 2.0：不兼容的 API 变更

- 1.2.1-alpha.1：预发布版本

- 1.2-rc.1：候选发布版本


---


## 十三、版本管理原则


1. **轻量级**：3 个人同机开发，MAJOR.MINOR.PATCH 就够了

2. **统一入口**：aim_client/__init__.py 的 VERSION 是统一入口

3. **轻量无负担**：shared/aim/VERSION 和 CHANGELOG.md 留着不删

4. **统一版本**：aim-watch 下次 MAJOR→2.0 时纳入统一版本，不再独立

5. **协议分离**：Protocol Version 和 Module Version 分离

6. **记录完整**：谁改了代码谁更新 CHANGELOG.md

7. **简化流程**：版本工具等稳定再搞，现在做就是过度工程


---


## 十四、版本管理负责人


- **负责人**：呱呱（基建/开发/安全/底层逻辑）

- **参与者**：吉量、小火鸡儿

- **评审**：三方评审，通过后执行


---


## 十五、版本管理检查清单


### 15.1 发布前检查


- [ ] 版本号是否对齐（项目级 / aim_client / SDK）

- [ ] CHANGELOG.md 是否更新

- [ ] 协议版本检查是否实现

- [ ] 代码是否同步到 ~/shared/

- [ ] 三方是否重启进程

- [ ] 端到端测试是否通过

- [ ] 三方评审是否通过

- [ ] 验证 shared 版和部署版版本号一致（联调失败最大坑）


### 15.2 发布后检查


- [ ] 记录发布时间到 CHANGELOG.md

- [ ] 通知所有相关方

- [ ] 监控发布后 24 小时

- [ ] 记录发布结果到 CHANGELOG.md


---


## 十六、版本管理相关文件


- shared/aim/VERSION：项目级版本号

- shared/aim/CHANGELOG.md：变更日志

- shared/aim/VERSION-STANDARD.md：本文件（版本管理标准）

- aim_client/__init__.py：aim_client 版本号（统一入口）

- src/aim_nats_sdk.py：SDK 版本号 + 协议版本号

- src/aim-watch.py：aim-watch 版本号（独立工具）


---


---


**注意**：本标准是简化版，等三方都稳定了、真要发 release 了再考虑 SemVer 和版本管理工具。


---


---

## 十七、日常操作速查

> 三方 Agent 每次改代码时一眼扫到该干什么，不用翻具体章节。

**改代码时**：
- [ ] VERSION 号升了吗？（看 3.1-3.2）
- [ ] CHANGELOG 更新了吗？（看 5.3）
- [ ] shared 同步了吗？（看 六）
- [ ] 部署版和 shared 版一致吗？（看 十五）

**版本冲突时**：
- 📌 以 SDK 为基准（看 3.4）

**发布前**：
- 📌 跑 十五 检查清单

**内部重构**：
- 📌 不 bump 版本号，CHANGELOG 标"内部重构"（看 5.3）

**回滚**：
- 📌 跑 十 回滚流程

**子模块说明**：
- aim-watch 临时独立版本（2.1.0），下次 MAJOR→2.0 纳入统一版本；Queue / Scheduler / Security 按 3.2 子模块规则

## 十八、版本管理和项目开发流程耦合（[手动] 标记，自动化脚本待 P2）


### 17.1 开发阶段


**开始新功能开发**：

- 手动[自动]创建预发布版本（如 1.2.1-alpha.1）

- 更新 CHANGELOG.md（新增功能计划）

- 同步到 ~/shared/


**提交代码时**：

- 手动[自动]检查版本号是否对齐

- 手动[自动]检查 CHANGELOG.md 是否更新

- 检查失败则拒绝提交


**开发完成**：

- 升级到 rc.1（如 1.2.1-rc.1）

- 更新 CHANGELOG.md（开发完成）

- 同步到 ~/shared/


### 17.2 测试阶段


**单元测试**：

- 手动[自动]检查协议版本兼容性

- 检查失败则拒绝测试


**集成测试**：

- 手动[自动]检查版本号是否对齐

- 手动[自动]检查 CHANGELOG.md 是否更新

- 检查失败则拒绝测试


**端到端测试**：

- 手动[自动]检查协议版本兼容性

- 手动[自动]检查版本号是否对齐

- 检查失败则拒绝测试


**测试通过**：

- 升级到正式版本（如 1.2.1）

- 更新 CHANGELOG.md（测试通过）

- 同步到 ~/shared/


### 17.3 发布阶段


**发布前**：

- 手动[自动]检查版本号是否对齐

- 手动[自动]检查 CHANGELOG.md 是否更新

- 手动[自动]检查协议版本检查是否实现

- 检查失败则拒绝发布


**发布时**：

- 手动[自动]更新 CHANGELOG.md（发布时间）

- 手动[自动]同步到 ~/shared/

- 手动[自动]通知所有相关方


**发布后**：

- 手动[自动]记录发布结果到 CHANGELOG.md

- 手动[自动]监控发布后 24 小时

- 手动[自动]通知所有相关方


### 17.4 回滚阶段


**回滚前**：

- 手动[自动]确认旧版本兼容性

- 手动[自动]确认 CHANGELOG.md 有回滚记录

- 检查失败则拒绝回滚


**回滚时**：

- 手动[自动]更新版本号

- 手动[自动]更新 CHANGELOG.md（回滚原因）

- 手动[自动]同步到 ~/shared/

- 手动[自动]通知所有相关方


**回滚后**：

- 手动[自动]记录回滚结果到 CHANGELOG.md

- 手动[自动]监控回滚后 24 小时

- 手动[自动]通知所有相关方


### 17.5 自动化脚本（暂不实现）


**注意**：等三方都稳定了、真要发 release 了再做，现在做就是过度工程。


**pre-commit.sh**：

- 提交代码前手动[自动]检查版本号是否对齐

- 提交代码前手动[自动]检查 CHANGELOG.md 是否更新


**pre-test.sh**：

- 测试前手动[自动]检查协议版本兼容性

- 测试前手动[自动]检查版本号是否对齐


**pre-release.sh**：

- 发布前手动[自动]检查版本号是否对齐

- 发布前手动[自动]检查 CHANGELOG.md 是否更新

- 发布前手动[自动]检查协议版本检查是否实现


**post-release.sh**：

- 发布后手动[自动]更新 CHANGELOG.md（发布时间）

- 发布后手动[自动]通知所有相关方


**pre-rollback.sh**：

- 回滚前手动[自动]确认旧版本兼容性

- 回滚前手动[自动]确认 CHANGELOG.md 有回滚记录


**post-rollback.sh**：

- 回滚后手动[自动]更新 CHANGELOG.md（回滚原因）

- 回滚后手动[自动]通知所有相关方


---


## 十九、版本管理更新记录


- 2026-06-18：创建版本管理标准 v1.0（呱呱）

- 2026-06-18：创建版本管理标准 v1.0（呱呱）
  - 统一版本号格式（MAJOR.MINOR.PATCH）
  - 对齐版本号（项目级 / aim_client / SDK = 1.2.1）
  - 添加协议版本检查
  - 添加 CHANGELOG.md 使用规范
  - 添加版本管理频率（每周 1-2 次 MINOR 发布）
  - 添加版本管理要求（发布前/后必须完成）
  - 添加版本管理规范（命名/编写/发布/回滚）
  - 简化版本发布流程
  - 简化版本回滚策略
  - 添加版本管理和项目开发流程耦合（开发/测试/发布/回滚）
- 2026-06-18：三方评审 v1.1（吉量/小火鸡儿）
  - 10项评审意见全部修复
  - 新增十七章日常操作速查卡片
  - 修复3个笔误（MAJOR.MINOR注释、release拼写、监控乱码）
  - 版本号格式统一为 MAJOR.MINOR.PATCH
  - 耦合章"自动"→"手动[自动]"，标注自动化脚本待 P2
  - 补充冲突仲裁、非发布改动策略、shared一致性检查 — 修复两字段/三字段矛盾、

  耦合章"自动"→"手动"、补充冲突仲裁、补充非发布改动策略、补充shared一致性检查

  - 统一版本号格式（MAJOR.MINOR）

  - 对齐版本号（项目级 / aim_client / SDK = 1.2.1）

  - 添加协议版本检查

  - 添加 CHANGELOG.md 使用规范

  - 添加版本管理频率（每周 1-2 次 MINOR 发布）

  - 添加版本管理要求（发布前/后必须完成）

  - 添加版本管理规范（命名/编写/发布/回滚）

  - 添加版本管理和项目开发流程耦合（开发/测试/发布/回滚）

  - 简化版本发布流程

  - 简化版本回滚策略


---


**注意**：本标准是简化版，等三方都稳定了、真要发 release 了再考虑 SemVer 和版本管理工具。
