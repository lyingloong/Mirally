# 多账号开发工作流：目标架构与 DSH 插件规格

> 状态：Draft r2（2026-11-02 初稿；2026-11-08 按 Codex 审查 P1/P2 修订：§4.3 可信决策、§5.2 决策/提交顺序、§4.4 attempt fencing、§7 审查证据绑定）
> 目的：把当前 AgentTeams 工作流中“符合我们想法”的部分抽出来，设计成 Mirally 自己的 DSH 插件；不再受 AgentTeams 的成员数、角色模型和调度语义限制。
> 本文是实现规格，不是已完成能力。

---

## 0. 用户决策摘要

- 角色：**3 名需求师 + 3 名工程师 + 3 名审查师 + 1 名队长**。
- 队长使用 **deepseek-account**，只负责调度、决策记录、PR 巡检、状态同步。
- 三个工作组各用一条独立 USTC API：
  - Group A：`ustc`（个人）
  - Group B：`ustc-dhsun`
  - Group C：`ustc-qhwang`
- 每组一名需求师、一名工程师、一名审查师；某个账号额度受限时，任务可以移到另一组同类角色，不因单 key 耗尽停摆。
- 并发尽量打满，不允许角色长期空闲；但“打满”不等于硬造任务。
- 先写完整方案，再由新会话实现插件；不迁就 AgentTeams 的固定 8 成员上限。

---

## 1. 为什么需要新插件

当前 AgentTeams 工作流已经验证了：

- 用户点优先、AI 低风险补位；
- 需求师 → 工程师 → 验证 → 审查 → 待合并池；
- Proposal 决策门；
- 独立 worktree/PR；
- Codex 按授权合并；插件只同步 GitHub 合并结果，不创建/执行 merge；
- 进度页、种子池、配额退避。

但 AgentTeams 本身有几个不匹配：

| 限制 | 影响 |
| --- | --- |
| 成员上限约 8 | 放不下 3+3+3+1 |
| 角色与 provider 绑定在成员上 | 额度耗尽时不能按任务迁移 |
| 并发是全局 `concurrency`，不是按角色/组 | 难以让 9 个角色同时工作 |
| 任务/attempt/quality gate 语义不完全贴合 | 需要外部 `state.json` 和队长手工补 |
| 审查循环上限由平台控制 | 复杂返工容易进入 escalated |
| 不能直接持久化“种子 → 规格 → 点 → 任务”完整图 | 队长要自己维护大量状态 |

新插件要补齐的是**工作流原语**，不是再造一个 AgentTeams。

---

## 2. 角色与 Provider 分组

### 2.1 角色表

| 角色 | 实例 | Provider | 并发 | 说明 |
| --- | --- | --- | --- | --- |
| 队长 | captain | `deepseek-account` | 1 | 只调度/巡检/记录；不写实现代码 |
| 需求师 A | analyst-a | `ustc` | 1 | 种子审计、规格、Proposal |
| 工程师 A | engineer-a | `ustc` | 1 | 实现/修复 |
| 审查师 A | reviewer-a | `ustc` | 1 | 独立验证/审查 |
| 需求师 B | analyst-b | `ustc-dhsun` | 1 | 同上 |
| 工程师 B | engineer-b | `ustc-dhsun` | 1 | 同上 |
| 审查师 B | reviewer-b | `ustc-dhsun` | 1 | 同上 |
| 需求师 C | analyst-c | `ustc-qhwang` | 1 | 同上 |
| 工程师 C | engineer-c | `ustc-qhwang` | 1 | 同上 |
| 审查师 C | reviewer-c | `ustc-qhwang` | 1 | 同上 |

- **每名成员同一时间只持有一个 active task**。
- 目标并发：最多 9 个任务同时 active（3 需求 + 3 工程 + 3 审查）。
- 队长不计入并发槽。
- 审查师不能审查自己组工程师的同一任务？可以同组，但必须独立 worktree、独立复跑；跨组审查优先用于高风险点。

### 2.2 额度与 fallback

每条 provider 独立配置：

```json
{
  "id": "ustc-dhsun",
  "adapter": "ustc",
  "model": "deepseek-flash",
  "windows": [
    {"hours": 3, "budgetUsd": 30},
    {"hours": 12, "budgetUsd": 70}
  ],
  "lowWatermarkPct": 15,
  "pauseBackoffMinutes": [10, 30, 60]
}
```

规则：

1. **不自动轮换 key 规避额度**；key 是用户资产，不是调度资源。
2. provider 达到 low watermark 时，调度器：
   - 停止给该组派**新**任务；
   - 已有任务允许完成；
   - 标记 `provider_degraded`。
3. 额度耗尽或 429：
   - 该组任务进入 `provider_blocked`；
   - 队长按 `fallbackPolicy` 把**同类任务**转给另一组同角色成员；
   - 任务记录 `fallback_of` / `fallback_reason`，不静默改角色。
4. fallback 优先级：
   - 同角色跨组；
   - 同组其他角色**不做**越权（需求师不实现）；
   - 无可用同类角色时暂停，不降级质量门。
5. **deepseek-account 只给队长**；任何子代理/成员不得使用，除非用户显式开启 `captainFallbackAllowed`。
6. 每次 fallback 记录审计：原 provider、目标 provider、原因、时间、是否恢复。

### 2.3 并发与利用率

- 全局 active task 上限 = 9（可配置）。
- 每组最多 3 个 active（1A/1E/1R）。
- 需求任务不占工程/审查槽，但占对应需求师槽。
- 空槽处理：
  - 需求师空闲：种子覆盖/规格生成任务；
  - 工程师空闲：ready spec / 冲突返工 / 评估 / 测试补强；
  - 审查师空闲：spec/Proposal 复核、PR 评论巡检；
  - 不为了指标硬造无验证价值的实现。
- 连续 2 个巡检周期同一角色无任务 → 报警并调整 roster 或说明阻塞原因。

---

## 3. 工作项模型

```text
Seed（种子）
  → Requirement（具体需求）
    → Point（开发点）
      → Task（任务，有 kind / inScope / verify / acceptance）
        → Attempt（一次执行，绑定成员/provider）
          → Verification
          → Review
          → Repair（可循环）
      → PR（GitHub）
      → Codex merge gate（插件只同步结果）
```

### 3.1 Seed

- 来源：`user-seed` / `user-request` / `existing-seed` / `agent` / `history`
- 必须记录：原话/摘录/整理、来源定位、状态、关联需求/点。
- **种子不是授权**；不能因为种子存在就自动实现高风险/删除/产品取舍。
- 种子池是需求师的输入，不是队长的任务队列。

### 3.2 Requirement / Point

- Requirement 是具体需求；Point 是能独立开发、验证、合并的最小单元。
- Point 字段：

```json
{
  "id": "dev-0042",
  "title": "…",
  "source": "user|agent",
  "risk": "low|normal|high",
  "status": "inbox|proposed|queued|specifying|blocked|implementing|verifying|reviewing|repairing|awaiting_merge|done|rejected|cancelled",
  "labels": ["source:user", "proposal-required"],
  "specPath": "…",
  "pr": 123,
  "headSha": "…",
  "taskIds": {"requirements": "t1", "implementation": "t2"},
  "blockedReason": null,
  "createdAt": "…",
  "updatedAt": "…"
}
```

### 3.3 Task kind

| kind | 用途 | 必需字段 |
| --- | --- | --- |
| `requirements` | 种子/调研 → 规格 | objective, acceptance |
| `proposal` | 只写方案，不写代码 | objective, acceptance |
| `implementation` | 实现/提 PR | objective, acceptance, inScope, verify |
| `verification` | 独立复跑/证据 | objective, acceptance, verify |
| `review` | 审查 verdict | reviewedTaskId, acceptance |
| `repair` | 修复 finding | sourceTaskId/findings, inScope, verify |
| `work` | 评估/文档/冲突返工 | objective, acceptance |

### 3.4 Task 状态

`pending → claimed → in_progress → completed | failed | cancelled`

- attempt_id 是能力凭证；重派后旧 attempt 失效（接口层拒绝），但**接口拒绝不等于停止外部副作用** —— 重派必须配合 §4.4 的单写者 fencing。
- 每个 task 必须有 subject；quality task 必须有契约。
- `needs_revision` / `reject` 必须 failed 并带 findings。

---

## 4. 状态机与质量门

```text
inbox → proposed → queued → specifying → implementing → verifying → reviewing → awaiting_merge → done
```

分支：

- `proposed`：AI 点待用户 promot；用户点可直接 queued。
- `specifying`：规格/Proposal。
- `blocked`：外部/额度/容量；可 resume，不丢任务。
- `repairing`：review/verify/冲突返工。
- `cancelled`：提案撤回/不再做；终态。
- `rejected`：用户拒绝；终态。

规则：

1. 用户点优先；AI 低风险点只能在用户点不等待容量时自动启动。
2. `specifying`/`proposal` 不应占用工程并发（新插件的关键改进）。
3. 进入实现前必须有规格；高风险/产品取舍先 Proposal。
4. 验证和审查独立于实现；不能拿实现者自测当唯一证据。
5. 审查最多 3 轮自动返工；超过后 escalated 给用户，不无限循环。
6. 只有 Codex 按授权合并；插件只同步 GitHub 的实际合并结果，不创建 merge 任务、不执行 merge。
7. 任何状态转换写事件日志；恢复时以事件 + 投影重建。
8. 批准只能来自可信主体（§4.3）；重派必须终止旧 attempt 并完成单写者交接（§4.4）；审查结论只对已审 head/base 成立（§7.1）。

### 4.3 可信决策主体（decision / approve 谁能写）

**问题**：`workflow.proposal.decision` 若对模型可调用，等权于"模型自己批准"。规格必须把"谁可信"写死在接口层。

| 主体 | 判定方式 | 允许动作 |
| --- | --- | --- |
| 用户 | 插件宿主（DSH）授予的人工操作通道：桌面/网页上的确认操作，携带宿主签名的一次性 `decision_token`（不暴露给模型上下文） | approve / reject / 修改 Q 处置 |
| Codex | GitHub 上的 `APPROVED` review 或 repository owner 账号的 PR comment 指令，由插件通过 GitHub API 读取（`author_association ∈ {OWNER, MEMBER}` 且账号在 `trustedActors` 白名单） | approve / reject |
| 队长 | 只能 `decision.request`（登记待批 + 通知用户/Codex） | 不能 approve |
| 需求师/工程师/审查师/任何模型调用 | 无批准通道 | 只能提交 Q 与证据 |

规则：

1. `decision.approve|reject` 从工具面**不出现在模型可见的 tool list** 中；只在宿主内部 RPC 里注册，调用方必须携带 `decision_token`（一次性、绑定 `proposal_id + commit + 主体`、5 分钟过期）。
2. 插件每次写入 decision 都记录 `actor_type ∈ {user, codex}`、`actor_identity`、`commit`、`evidence`（用户通道：宿主操作回执；Codex 通道：review id 或 comment id + author_association）。
3. 未认证调用 → `FORBIDDEN unauthenticated_decision`，并写 `decision_rejected` 事件；模型借工具自行批准在接口上不可达。
4. 只有 `mergePolicy=codex-only` 是默认；若用户另行开启队长代批（`captainDecisionAllowed`），也必须走用户通道签发 token，并全程记录。
5. 验收用例 A1–A4（§11 Phase 0.5）覆盖：模型直接调用被拒、token 过期被拒、Codex 非白名单账号被拒、token 跨 proposal/commit 复用被拒。

### 4.4 重派单写者 fencing 与工作树终止（P1）

**问题**：重派后旧 attempt 只在插件接口层失效；失联 agent 仍可能继续写工作树、commit、push 或开 PR。同一分支两次 attempt 并发的后果是外部副作用不可回收。

规则：

1. **一个 task 一个 worktree、一个分支（一写者）**。重派默认**换新 worktree + 新分支**（`agent/<point>-<slug>-a2`），不复用旧目录；旧 worktree 标记 `orphaned`，由清理任务在确认无进程后再删。
2. attempt 表记录 `pid`、`host`、`token`、`worktree_path`、`branch`。重派流程必须按顺序：`terminate(attempt, 'reassigned')` → 确认进程退出或标记 `unconfirmed` → 创建新 attempt。`terminate` 写 `terminated_at/terminate_reason`，并向插件管理的执行者发送停止信号（DSH 侧 kill 或 sandbox 撤销）。
3. **Git 侧 fencing（防旧 attempt push）**：新 attempt 的分支由 `GitAdapter` 以 `--force-with-lease=<branch>:<old_sha>` 保护——旧 attempt 只有在它已知的 `old_sha` 仍是远端现态时才能 push；新 attempt 第一次 push 后远端前进，旧 attempt 的 push 被拒。插件在 `pr.sync` 中比对 `prs.head_sha` 与当前 attempt 的 `last_pushed_sha`，发现外部提交即把点置 `repairing` 并记录 `external_head` 事件。
4. **进程无法确认退出时**：该 task 进入 `blocked(attempt_unconfirmed)`，不派新 attempt，先等清理任务验证工作树无新写入（对 `worktree_path` 做文件 mtime/hash 快照比对，静默 N 分钟）或由用户/Codex 确认终止；确认后 `resume`。
5. 旧 attempt 迟到恢复（同一 attempt 重新连上）：接口层因 attempt_id 失效拒绝一切写入；若它已改文件，插件用第 4 条的快照发现并记录，不改写旧分支。
6. 验收用例 A5–A8：旧 attempt 迟到 push 被拒、重派后新旧 attempt 目录不共享、未确认终止时新 attempt 不派发、孤儿 worktree 在确认后才清理。

---

## 5. 需求、种子与 Proposal

### 5.1 需求师工作

- 每次巡检先检查种子池：
  - 没有具体需求的种子；
  - 状态过期/关联为空的种子；
  - 已有 spec 但未登记点的；
  - 可拆成低风险实现/评估的点。
- 产出规格到 `output/agent-workflow/specs/<point>.md`（新插件可用数据库，但保留可导出 Markdown）。
- 每个规格必须写：来源、目标、非目标、最小验证、风险、依赖、建议点号。
- 不写实现代码；不擅自 promote/start。

### 5.2 Proposal（需要决策的点）

- 只写 `docs/development/proposals/<id>.md`，不写实现。
- 问题用 PR comment `Q1..Qn`。
- Decision gate 必须同时满足：
  - 用户/Codex 对当前 proposal commit 明确批准，且批准由 §4.3 的可信主体认证；
  - 影响实施的 Q 已 resolved 或明确延后；
  - 批准记录所指向的 commit 与 `proposal.head_sha` 一致（见下方提交顺序）。
- **批准与提交的先后顺序（P2）**：
  1. 队长先把 Q 的处置写回文档并提交，得到 `proposal.head_sha = H1`；
  2. 用户/Codex 的批准必须显式引用 `H1`（`decision.approve(proposal_id, commit=H1)`）；
  3. 批准后只允许产生**不改变决定的后继提交**：`decision.record` 写入决策块时，新提交的父必须是 `H1`，且 diff 只能落在 `docs/development/proposals/<id>.md` 的决策块（`## 决策记录` 段落）内；插件记录 `approval_commit=H1`、`record_commit=H2`，并断言 `H1` 是 `H2` 的祖先。
  4. 任何落在决策块之外、或无法从 `H1` 到达的新提交都会使批准失效，必须重新批准（`proposal:approved-stale`）。
- 标签 `proposal:accepted` 只是可视标记，不能单独批准。
- 未批准不创建 Implementation PR。
- 被拒绝/撤回用 `cancel`，不进入 rejected/proposed 死循环。

---

## 6. Git / PR / 合并

### 6.1 分支与 worktree

- 每个实现/修复任务独立 worktree + 分支。
- 分支名：`agent/<point-id>-<slug>`、`feat/<point-id>-<slug>`、`docs/<slug>`。
- 不覆盖别人的工作树；不共用 checkout。
- 冲突只返工一次：把 `origin/main` 合入，解决后重新跑受影响验证。

### 6.2 PR 要求

- 标题/正文说明目的、范围、验证、未覆盖。
- 用户来源点：正文必须引用用户原话；不得只写 `[AI提议]` 或 `source:agent`。
- 只改 inScope；越界必须说明并在 PR 披露。
- CI 必须通过；审查师独立复跑。
- PR comment 是 finding/回复的持久载体；不能只发 AgentTeams 消息。

### 6.3 合并门

- 插件只把点推到 `awaiting_merge`，然后等待 GitHub 事实。
- **只有 Codex 按授权决定并执行合并**；插件不创建 `integration` 任务，也不把 merge 权限交给队长/审查师。
- Codex 合并后，插件通过 `pr.sync` 观察到 `MERGED`，同步 `origin/main`、更新点 `done`、清理 worktree。
- 不自动 merge、不自动关闭 PR、不自动改写用户分支。

---

## 7. 审查与验证

- 验证/审查必须在独立 worktree 检出 PR head。
- 必须报告：head、base、范围、复跑命令、真实结果、未覆盖。
- **证据绑定不可变 head/base（P2）**：每条 verification/review 记录必须同时写入 `reviewed_head_sha`、`reviewed_base_sha`、`merge_base_sha`、`checks_snapshot` 与 `worktree_path`。verdict 只对这三元组成立；任一项缺失即视为无效记录，不能进入 `awaiting_merge`（见 §7.1）。
- 审查 verdict：`pass` / `needs_revision` / `reject`。
- `needs_revision` 必须带 findings（id/severity/problem/requiredFix/file/line）。
- 修复后重跑验证 + 审查；不覆盖旧证据。
- 审查师不能只读实现者报告；至少独立跑关键负控制/判别力探针。
- 高风险点跨组审查（A 的实现由 B/C 审查）优先。

### 7.1 审查/验证证据与不可变 head/base 绑定（P2）

- 每条 review/verification 记录写入 `reviewed_head_sha`、`reviewed_base_sha`、`merge_base_sha`、`checks_snapshot`、`worktree_path`；缺失任一项即 `invalid`，不参与质量门。
- `pr.sync` 每次更新 `prs.head_sha`/`prs.base_sha` 时，对已有 review 计算有效性：`reviewed_head_sha == prs.head_sha` 且 `reviewed_base_sha == prs.base_sha` 且 `merge_base_sha` 仍是 `base..head` 的 merge-base，否则把该 review 标记 `stale`（**保留历史，不改写 verdict**）。
- `reviewing → awaiting_merge` 要求**存在一条 active（非 stale、非 invalid）的 `pass`**；只有 stale 的 pass 时点回到 `reviewing` 并记录 `review_stale(head_old, head_new)`。
- base 变化（`origin/main` 前进或目标分支变化）同样使 base 字段失配 → 重跑验证与审查，不沿用旧结论。
- 进度页只显示 active 结论，并列出 stale 数量与原因；不得把 stale pass 显示成当前结论。
- 验收用例 A9–A12：审查后 push 新提交 → 旧 pass 变 stale 且不得推进 awaiting_merge；base 前进 → 同上；并发两次 sync → 幂等且不复活 stale；缺失证据字段的记录被拒绝。

---

## 8. 进度页 / 可观测性

- 进度页由插件生成，不手工改 HTML。
- 数据源：点、任务、attempt、PR 缓存、verification 文件、provider 健康、容量。
- 每个点显示：目的、主要改动、当前状态、验证结论、待决定问题、PR/分支/head、负责人、更新时间、缺口。
- 分组：待你决定 / 进行中 / 已完成 / 已关闭。
- 生成物可离线打开；默认不联网，`--refresh-pr` 才拉 GitHub。
- 状态以数据库为准，页面只是投影。

---

## 9. 新插件设计

### 9.1 定位

- 名称：`mirally-workflow`（DSH 原生可信插件）。
- 不包装 AgentTeams；自己实现角色、provider 路由、任务 DAG、质量门、PR 同步和进度页。
- 语言/栈：优先 TypeScript/Node，跟 DSH 原生插件一致；状态存储用 SQLite（本地），导出 JSON/Markdown。
- 插件入口：

```json
{"id":"mirally-workflow","type":"plugin","path":"./mirally-workflow.mjs","config":{"apiVersion":1}}
```

模块同时导出：

```ts
export const mirally = { apiVersion: 1 };
export default function plugin(ctx) { /* 注册 tools/services/jobs */ }
```

具体以 DSH 插件加载契约为准；不兼容版本拒绝激活。

### 9.2 组件

| 组件 | 职责 |
| --- | --- |
| `Coordinator` | 单实例锁；调度循环；崩溃恢复；事件总线 |
| `Roster` | 9 个角色、provider、并发、健康 |
| `ProviderRouter` | 额度窗口、低水位、fallback、审计 |
| `TaskStore` | SQLite 表：points/tasks/attempts/reviews/prs/events/seeds/proposals |
| `Scheduler` | ready 队列、依赖、用户优先、空闲补位、DAG |
| `GitAdapter` | worktree、分支、commit/push、diff/冲突 |
| `GitHubAdapter` | PR list/view/comments/reviews/checks、labels、CI；不 merge |
| `QualityGate` | verify/review/repair loop，ceiling 3 |
| `ProposalGate` | Proposal PR/Q&A/approval |
| `ProgressPage` | 生成 HTML + data JSON |
| `Recovery` | 幂等重放、stale attempt、预算/容量恢复 |
| `Notifier` | 用户需要处理的决策/预算/异常报警（合并由 Codex） |

### 9.3 工具 API（给模型）

```text
workflow.status()
workflow.roster.list()
workflow.provider.health()
workflow.seed.list() / workflow.seed.audit()
workflow.point.create|promote|start|block|resume|cancel|status
workflow.task.create|claim|update|complete|fail
workflow.task.next(role)
workflow.review.submit(task_id, verdict, findings)
workflow.proposal.create
workflow.decision.request(proposal_id, commit)   # 只登记待批；不写批准结果
workflow.decision.approve|reject(proposal_id, commit, actor, evidence)   # 由可信主体调用（§4.3）
workflow.pr.sync(number)   # 只读取 GitHub 状态；不执行 merge
workflow.progress.render()
```

- 所有写操作带 `request_id`/idempotency key。
- 工具返回结构化 JSON；错误有 code/message/nextAction。
- 敏感配置不进工具返回；GitHub token 从宿主环境读取。

### 9.4 调度循环

伪代码：

```text
while running:
  health = provider.health()
  ready = scheduler.ready(now)
  for role in [analyst, engineer, reviewer]:
    for group in groups:
      if member(role, group).idle and ready_for(role, group):
        task = pick(priority: user > proposal > repair > agent)
        dispatch(task, group)
  fill_idle_roles_with_seed_or_audit()
  sync_github_prs()
  render_progress_if_dirty()
  sleep(interval)
```

- 事件驱动优先（task complete / PR comment / budget event），定时器兜底。
- 不允许同一 point 并行两个实现者；修复和验证串行。
- 失败/超时进入 retry 队列，指数退避，最大 3 次；仍失败 escalated。

### 9.5 数据表（SQLite 草案）

```sql
points(id, title, source, risk, status, spec_path, pr, head_sha, labels_json, notes, timestamps)
tasks(id, point_id, kind, status, assignee, role, provider, attempt_id, objective, acceptance_json, verify_json, in_scope_json, dependencies_json, reviewed_task_id, timestamps)
attempts(id, task_id, member, provider, status, started_at, ended_at, error,
         worktree_path, branch, pid, token, terminated_at, terminate_reason)
reviews(id, task_id, verdict, findings_json, evidence_path, created_at,
        reviewed_head_sha, reviewed_base_sha, merge_base_sha, checks_json, worktree_path)
decisions(id, proposal_id, actor_type, actor_identity, action, commit, record_commit,
          evidence, created_at)
prs(number, point_id, head_sha, base_sha, state, mergeable, checks_json, updated_at)
seeds(id, source_type, source_ref, quote, status, requirement_ids)
proposals(id, point_id, path, status, decisions_json)
events(id, type, entity, payload_json, created_at)
```

### 9.6 配置

```json
{
  "apiVersion": 1,
  "captain": {"provider": "deepseek-account", "model": "deepseek-flash"},
  "groups": [
    {"id": "a", "provider": "ustc"},
    {"id": "b", "provider": "ustc-dhsun"},
    {"id": "c", "provider": "ustc-qhwang"}
  ],
  "roles": {"analysts": 3, "engineers": 3, "reviewers": 3},
  "concurrency": {"global": 9, "perGroup": 3},
  "mergePolicy": "codex-only",
  "reviewMaxRounds": 3,
  "pollSeconds": 60,
  "statePath": "<user-data>/mirally-workflow/state.sqlite3",
  "progressPath": "<user-data>/mirally-workflow/progress.html",
  "fallback": {"enabled": true, "sameRoleOnly": true, "captainKeyForWorkers": false}
}
```

### 9.7 安全

- 不自动 merge、不自动改用户分支、不自动轮换 key。
- 不把 GitHub token/API key 写进状态或日志。
- 不读用户 home/凭据；worktree 只操作仓库。
- 用户原话必须保留；AI 生成点不得伪装成用户点。
- 变更 provider/fallback 必须用户可审计。

---

## 10. 从 AgentTeams 迁移

1. **影子模式**：新插件只读现有 `output/agent-workflow/state.json`，生成视图，不派发。
2. 手工导入 points/tasks/prs 到 SQLite；保留旧 id。
3. 运行 1–2 天对比调度建议与人工结果。
4. 切换：停止 AgentTeams 成员，确保无 active task；启用插件调度。
5. 旧状态归档；保留下线说明。
6. 任何不一致以 GitHub PR/CI 为准，不以旧状态覆盖仓库事实。

---

## 11. 新会话实现任务（建议顺序）

0.5 **可信边界先行**（本规格 §4.3/§4.4/§7.1 的验收场景 A1–A12）：decision token 通道、attempt fencing、review 绑定；这些用例先于调度器实现，避免后期返工。
1. 插件骨架 + 配置/单实例锁 + SQLite schema + `workflow.status/roster`。
2. Scheduler（9 角色、provider 健康、用户优先、空闲补位）+ 模拟测试。
3. Task/attempt/quality gate 状态机 + verify/review/repair ceiling。
4. GitAdapter + GitHubAdapter（PR 同步、评论、checks；不 merge）。
5. Proposal gate + 种子/需求池 + 进度页。
6. 迁移/影子模式 + 真实小点试运行。
7. 文档/运维手册/回滚。

**验收**：

- 9 个角色可同时 active，不超各自槽；
- 单 provider 额度耗尽时任务转到同类角色，不降质量门；
- 无任务时不伪造；种子→规格→点→PR 全链路可恢复；
- 插件从不自动 merge；
- 重启后状态/attempt/PR 不丢；
- 进度页可离线打开；
- 模型无法自行批准 decision（§4.3 用例 A1–A4 通过）；
- 重派后旧 attempt 的 push 被拒、工作树不共享（§4.4 用例 A5–A8 通过）；
- 审查后新提交或 base 变化使旧 pass 变 stale 且不推进 awaiting_merge（§7.1 用例 A9–A12 通过）。

## 11.1 与其他在途实现的替代关系（2026-11-08 核对）

| 对象 | 状态 | 与本文的关系 |
| --- | --- | --- |
| AgentTeams 常驻团队 + `tools/dev_workflow/agent_workflow.py` | main 已有，仍在用 | 本文是它的**替代/升级**目标：一旦插件落地，`state.json` 与状态助手退役（§10 迁移步骤）。在此之前本文不声称已实现。 |
| PR #25 进度页（`tools/dev_workflow/progress_page.py`） | 在途 | 本文 §8 的进度页要求与之同源；插件版本落地后由插件接管生成。 |
| PR #23 Proposal 流程 / PR #15 控制核心 | 在途 | 本文吸收其"Proposal 决策门 / 不自动合并"语义，规则以本文 §5.2/§6.3 为准。 |
| 节点图 / auto-dev 工作流文档（`docs/development/team-workflow.md`，属另一开发环境） | **本仓库 main 与各 open PR 中均不存在** | 无法在本仓库核对其实现；本文不宣称被它取代。若该环境已实现插件，请在合并本文前补充替代说明。 |


---

## 12. 非目标

- 不替代 DSH 的 Agent loop/会话/工具链。
- 不自动合并、不自动发布、不绕过用户决策。
- 不做通用 BPM/工作流平台。
- 不把 AgentTeams 的成员/任务语义硬搬过来。
- 不把 key 轮换当作额度解决方案。

---

## 13. 待用户确认

1. 插件名/包名是否用 `mirally-workflow`？
2. 状态存储放产品数据目录还是仓库 `.mirally/`？
3. 9 角色是否从第一天全开，还是先 2 组 6 角色？
4. 审查是否强制跨组（A 实现由 B/C 审）？
5. 审查循环上限 3 轮是否合适？
6. fallback 是否允许需求师临时做评估任务（不越权实现）？
7. 进度页是否需要常驻 Web 服务，还是只生成 HTML？
8. `trustedActors` 白名单是否只含 repository owner，还是允许指定协作者账号代行 Codex 批准？
9. 重派时旧 worktree 保留多久（便于取证）再自动清理？
