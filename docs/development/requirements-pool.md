# 需求与种子池

本文件保存“值得做、但还不是已批准开发点”的需求线索，以及已经形成的具体需求。它不是任务队列，也不替代 `output/agent-workflow/state.json`。

## 1. 用途

- 保存用户提出的需求原文和整理后的需求，避免只存在于对话里。
- 保存用户/需求师/Agent 提出的“种子”，供需求师在发掘新点时选择性参考。
- 记录每个具体需求来自哪个种子、哪条用户原话或其他来源，便于 Codex/审查者理解“为什么做”。
- 种子是灵感，不是授权：不会自动变成开发点，也不会自动进入实现。

## 2. 来源类型

| 类型 | 含义 | 处理方式 |
| --- | --- | --- |
| `user-request` | 用户直接提出的需求 | 原话保留；整理后写清范围；用户点优先 |
| `user-seed` | 用户提出的方向/想法，尚未拆成具体需求 | 只作为种子；可被需求师引用 |
| `agent-seed` | 需求师/审查师/Agent 提出的候选 | 必须标 `agent`，不得冒充用户需求；低风险才可自动启动 |
| `existing-seed` | 从既有 backlog、文档、产品记录或历史点整理出的种子 | 标来源文档/点 ID；不改变原文含义 |

种子状态：`seed`（仅记录）、`selected`（已被某个需求引用）、`planned`（已排期）、`active`（实现/设计进行中）、`done`、`rejected`。

### 2.1 引用性质与可追溯

- **逐字**：用「」完整引号，不改字、不插省略号。
- **摘录**：跨消息/跨段截取，标「摘录：」；省略号只表示省略，不得当作完整原话。
- **整理**：需求师/队长整理，标「整理：」，不得称原话。
- **定位**：本文件中的用户表述来自本会话（2026-11-02）；原始对话不公开，无法核验的文本不能标为完整原话。历史需求定位到 dev ID/PR；文档种子给相对链接。
- 新增条目时把引用性质写清楚；不要把“整理后”与“原文”混在同一列而不标记。

## 3. 种子池

### 3.1 用户种子（2026-11-02）

| 种子 ID | 种子 | 来源 | 原始表述/依据 | 状态 | 关联需求 |
| --- | --- | --- | --- | --- | --- |
| `SEED-README-OPT` | 优化 README | `user-seed` | 逐字：「优化readme」 | seed | — |
| `SEED-REPO-STRUCTURE` | 维护仓库结构 | `user-seed` | 逐字：「维护仓库结构」 | seed | — |
| `SEED-DEPS` | 优化依赖 | `user-seed` | 逐字：「优化依赖」 | seed | — |
| `SEED-DEAD-CODE` | 清理旧无用代码 | `user-seed` | 逐字：「清理旧无用代码」 | seed | — |
| `SEED-STATIC-UI` | 优化静态界面（如有） | `user-seed` | 逐字：「优化静态界面（如果有的话）」 | seed | — |
| `SEED-RESEARCH-IDEAS` | 调研新想法（产品各方面） | `user-seed` | 逐字：「调研新想法（本产品的各个方面吧）」 | seed | — |

### 3.2 用户需求形成的种子

| 种子 ID | 种子 | 来源 | 原始表述/依据 | 状态 | 关联需求 |
| --- | --- | --- | --- | --- | --- |
| `SEED-MEMORY` | 调研并建设 Mirally 自有记忆系统 | `user-request` | 整理：“调研记忆机制，考虑我们怎么做自己的记忆系统；实现我们自己的记忆系统” | selected | `REQ-MEMORY-RESEARCH`、`REQ-MEMORY-IMPL`、`REQ-MEMORY-ACTIVITY` |
| `SEED-ACTIVITY` | 活动记录完善度与记忆/日历融合 | `user-request` | 整理：“活动记录啥的目前功能完善吗；记忆系统怎么和活动记录、日历之类的有机融合，是否可扩展” | selected | `REQ-ACTIVITY-AUDIT`、`REQ-MEMORY-ACTIVITY` |
| `SEED-MODEL-PROVIDER` | 适配其他模型服务商接口 | `user-request` | 逐字：“能否适配上其他模型服务商的接口？” | selected | `REQ-MODEL-PROVIDER` |
| `SEED-UFO-CLEANUP` | 移除 UFO 子模块与专用测试依赖 | `user-request` | 摘录：“UFO的测试啥的可以去掉了…submodule也可以去掉了”（跨消息摘录，非全文） | active | `REQ-UFO-CLEANUP` |
| `SEED-TEST-NOISE` | 优化本地验证噪声/临时目录 | `user-request` | 摘录：“本地验证噪声…可以对应提需求尝试优化”（摘录，非全文） | selected | `REQ-UFO-CLEANUP`（部分） |
| `SEED-WORKFLOW-CONTROL` | 仓库内维护 AgentTeams 控制核心 | `user-request` | 摘录：“控制核心也属于我们的workflow，所以也要放到仓库…要和产品隔离开”（摘录，非全文） | active | `REQ-WORKFLOW-CONTROL` |

### 3.3 既有/历史种子

| 种子 ID | 种子 | 来源 | 依据 | 状态 | 关联需求 |
| --- | --- | --- | --- | --- | --- |
| `SEED-FEATURE-MAP` | feature map 补开发工作流入口 | `existing-seed` | dev-0011 规格；PR #3 漏更新地图 | active | `REQ-FEATURE-MAP` |
| `SEED-WEIXIN-TEXT` | 微信端中文提示优化 | `existing-seed` | dev-0001 | done | `REQ-WEIXIN-TEXT` |
| `SEED-CLI-ENCODING` | 状态助手非 UTF-8 输出修复 | `existing-seed` | dev-0002 | done | `REQ-CLI-ENCODING` |
| `SEED-WORKFLOW-STATE` | 状态助手并发锁/CI 子集 | `existing-seed` | dev-0009、dev-0010 | active | `REQ-WORKFLOW-STATE` |

## 4. 具体需求

| 需求 ID | 具体需求 | 来源 | 种子 | dev 点 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `REQ-MEMORY-RESEARCH` | 调研现有记忆机制并设计自有记忆系统 | `user-request` | `SEED-MEMORY` | dev-0013 | queued；规格已完成 |
| `REQ-MEMORY-IMPL` | 实现自有记忆系统 | `user-request` | `SEED-MEMORY` | dev-0014 | queued；依赖 0013 |
| `REQ-MEMORY-ACTIVITY` | 记忆与活动/日历融合及扩展性 | `user-request` | `SEED-MEMORY`、`SEED-ACTIVITY` | dev-0015 | queued；依赖 0013/0016 |
| `REQ-ACTIVITY-AUDIT` | 活动记录完善度评估 | `user-request` | `SEED-ACTIVITY` | dev-0016 | queued；规格已完成 |
| `REQ-MODEL-PROVIDER` | 其他模型服务商接口适配调研 | `user-request` | `SEED-MODEL-PROVIDER` | dev-0017 | queued |
| `REQ-UFO-CLEANUP` | 移除 UFO 子模块与专用测试依赖 | `user-request` | `SEED-UFO-CLEANUP` | dev-0012 | PR #16 open |
| `REQ-WORKFLOW-CONTROL` | 控制核心/工作流默认值/PR 规范 | `user-request` | `SEED-WORKFLOW-CONTROL` | — | PR #15 open |
| `REQ-FEATURE-MAP` | feature map 开发工作流章节 | `agent` | `SEED-FEATURE-MAP` | dev-0011 | verifying；PR #17 |
| `REQ-WEIXIN-TEXT` | 微信端中文提示优化 | `user` | `SEED-WEIXIN-TEXT` | dev-0001 | done；PR #5 |
| `REQ-CLI-ENCODING` | CLI 非 UTF-8 输出修复 | `agent` | `SEED-CLI-ENCODING` | dev-0002 | done；PR #6 |
| `REQ-WORKFLOW-STATE` | 状态并发锁 + CI Python 子集 | `agent` | `SEED-WORKFLOW-STATE` | dev-0009/0010 | PR #13/#14 open |

## 5. 需求师如何使用

1. 开始调研前先读本文件，再查 open PR、最近合并、state 和已有规格。
2. 可以引用 1–2 个种子作为方向，但种子不是需求：必须结合产品目标、现有代码和用户优先级，说明为什么现在做。
3. 新规格必须写“需求来源”：
   - 直接来自用户：引用用户原话（可整理，但不得改变原意）。
   - 来自种子：写种子 ID，并说明选择理由。
   - Agent 自己提出的：标 `agent`，不得写 `user`。
4. 不自动把种子 promote/start；用户点优先。Agent 低风险点仍按状态助手准入规则。
5. 发现新种子时追加到本文件，不要直接创建开发点；需要开发时再登记具体需求并链接种子。
6. 去重：同一文件/同一目标已有 open PR 或 active 点时，不重复造点；只在原需求下补充。

## 6. 维护

- **单一维护者**：队长或指定的需求师负责最终写入；其他需求师/工程师在任务/PR 中提交种子和建议，合并后由维护者统一追加，避免并行分支各改一份。
- **未合并协调**：未合并分支里的新种子先在 PR/任务说明中标出，维护者按 ID 去重；重复条目合并到原条目，不新建平行需求。
- **状态同步时机**：`state.json` 是执行状态权威；本文件只在需求登记、进入待合并池、合并/关闭时更新，不要求每次状态变化都同步，避免频繁冲突。
- 用户新增需求/种子时，维护者追加原文/摘录/整理标记和定位。
- 具体需求状态变化时更新“关联需求/状态”，不删除历史。
- `done/rejected` 保留，供后续复盘和学习。
- 本文件只记录开发需求与方向，不记录凭据、私有数据或日常任务流水账。
- 具体执行状态仍以 `output/agent-workflow/state.json` 和 AgentTeams 任务为准；本文件不替代状态机。
