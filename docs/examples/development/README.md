# Mirally 自动开发工作流（dsh-auto-workflow）

> 更新：2026-11-24，Asia/Shanghai。状态：现行（本机常驻开发配置）。来源：[dsh-auto-workflow](https://github.com/lyingloong/dsh-auto-workflow) HEAD `43698a6`（插件包版本仍标 1.2.0，含未发版的 structured-output / runtime / intake / GitHub 决策修复；MIT）。本目录保留上游 driver/store/worktrees/pr/pr-feedback/github/requirement-review 与 `workflow.json`，只补充 Mirally 的提示词、角色模型和验证约定。[流程与保证](https://github.com/lyingloong/dsh-auto-workflow/blob/main/docs/workflow-summary.md) 以上游为准。
>
> **2026-11-24 迁移**：业务 store 已从 v3 迁到 v4（decision-first 字段 + GitHub merge policy）。PR 合并不再使用看板 `merge-human`/`self-merge-human`：reviewer 通过后 `pr-watch` 轮询 GitHub 的 `[verdict=pass|revise]` 评论、正式 review、未 resolve thread、CI 和 `@workflow merge` 意图；阻塞则 `pr-request-changes` 返修，满足条件后 `pr-merge` 用 merge commit 合并。PR/issue 采用 decision-first 模板，完整证据在 `<details>`。

## 组件

| 文件 | 职责 |
| --- | --- |
| `workflow.json` | 周期 producer、图内 intake planner、每需求 worktree、3+3 工作池、GitHub issue 需求审批、PR 反馈 watcher 与 GitHub 决策合并。 |
| `driver.mjs` | command 节点使用的 JSON-only CLI。 |
| `store.mjs` | 稳定 ID、优先级/FIFO、claim、revision/epoch、seed 轮转、decision-first 字段、需求审批与反馈；store v4。 |
| `worktrees.mjs` | 每需求 attached worktree、单 commit 快照、review、decision-first brief 与受限清理。 |
| `pr.mjs` | push 需求分支、创建/更新自己的 draft PR、读取 GitHub 反馈、按 `mergePolicy` 合并或返修、验证 merge commit。 |
| `pr-feedback.mjs` | 纯函数：授权评论/review、head/window 绑定、最新判决、merge 策略。 |
| `github.mjs` | 示例共用的 `gh` 调用与仓库坐标解析。 |
| `requirement-review.mjs` | 需求审批 issue：decision-first 模板、脱敏发布、读取维护者评论决定、回写需求状态。 |
| `pr-protocol.md` | 角色头、状态块、判决标记、合并策略、需求审批 issue 与 PR 初始化约定。 |

## 本仓库配置

- **集成 checkout**：`C:\Users\lazeloong\Code\Mirally-auto-workflow`，分支 `integration/auto-workflow`，从最新 `origin/main` 合并后建立。运行期间保持干净、停在该分支且无进行中的 merge。
- **业务数据**：`C:\Users\lazeloong\Code\Mirally-auto-workflow-data-v5`。旧 v4 dataRoot 保留作证据，不再混用；v5 是新图/新 store 的唯一运行数据。
- **集成方式**：`integrationMode=pull-request`，base `integration/auto-workflow`，remote `origin`，默认 `after-verified-merge` 清理；`mergePolicy=comment`（PR 上 `@workflow merge`）。
- **PR 初始化**：`pr-init` 使用 `manualGate:true` 和 `apply:true`，创建 `workflow:merge-ready` 以及 `workflow:requirement-review` / `approved` / `rejected` 标签。
- **权限**：command allowlist 只放 `node docs/examples/development/driver.mjs`；HTTP deny。授权信任整个 driver，不是只读授权。
- **模型**：需求 agent（`intake-plan`、`miner`、`analyst-revise`）用 `ustc-pool/deepseek-flash`；工程 agent（`engineer`）用 `ustc-pool/qwen3.8-flash-next`；审查 agent（`reviewer`）用 `ustc-pool/glm-5.3-flash`。全部 `reasoningEffort: max`，**不使用 deepseek-account 或其他兜底**；USTC 池全不可用时节点失败，由 6 小时检查处理。

## 两类需求入口

### 宽泛用户请求：图内 intake planner

不要把“论文速递”这种宽泛请求直接写成 executable requirement。把原话投给图内入口：

```json
{
  "nodeId": "intake-record",
  "input": { "dataRoot": "C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v5", "text": "论文速递：每天推送相关论文" }
}
```

流程：`intake-record`（先落盘原文，状态 `planning`，不可 claim）→ `intake-plan`（agent）→ `intake-apply`（1 条具体需求 + 0–3 个去重 seed；planner `unclear` 或失败时保留原文）→ `dispatch`。派生 seed 只进正常轮转，不会自动开挖。

已经具体的需求仍用：

```powershell
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v5","requirement":{"id":"req-example","source":"user","title":"具体改动","summary":"期望行为的 1–2 句摘要","why":["对现有流程的影响"],"acceptance":["可观察的验收项"],"evidence":"完整分析、文件行号和备选方案放在这里，公开时折叠。","risk":"high","tags":["user"]}}'
node docs/examples/development/driver.mjs requirement
```

用户 source 仍 priority 0，跳过需求审批；实现 review 和人工合并门禁不跳过。

### 挖掘需求：GitHub issue 审批

miner 产出的 mid/high 需求不再占看板 Human token：

- `publish-requirement-issue` 在业务仓库创建/更新脱敏 issue，标签 `workflow:requirement-review`；
- 维护者在 issue 评论 `@workflow approve` 或 `@workflow reject <理由>`；只有 OWNER / MEMBER / COLLABORATOR 生效；
- `check-requirement-issue` 读取最新有效评论并按 `revision` 绑定，`requirement-gate` 每 30 分钟复查；
- 批准后换 `workflow:requirement-approved`，拒绝换 `workflow:requirement-rejected` 并回到同 ID 修订；
- 公开 issue 先脱敏绝对路径、主目录、邮箱、长 token 和私有地址；本地需求原文不变。

## 启动与操作

1. 在 DSH 会话中 `auto_workflow_define` 本目录 `workflow.json` 与本机 permissions。
2. `auto_workflow_start` 绑定集成 checkout 的绝对 `cwd` 和 `{ "dataRoot": "C:\\Users\\lazeloong\\Code\\Mirally-auto-workflow-data-v5" }`，再 `auto_workflow_run`。
3. 向 `mine-gate` **只 enqueue 一次** producer；它按 60 秒 gate、backlog/在途 seed 水位、seed 轮转继续采矿。
4. 宽泛需求 enqueue `intake-record`；具体需求写 `driver requirement`；需要立即准入时 enqueue `dispatch`。
5. 人工闸门：需求审批在 GitHub issue；PR 由 GitHub 上的 `[verdict=pass|revise]`/正式 review 决定，`@workflow merge` 明确要求合并后由 `pr-merge` 用 merge commit 执行；不再有看板 `self-merge-human`。

## Seed 轮转与恢复

seed 状态为 `queued` / `mining` / `proposed` / `retired`；公平性用 `rotationRound`，不使用时间冷却。`proposal:null` 回队列并记 `lastOutcome`/`lastReason`；需求 settle 后方向回队尾；`mining` seed 计入 backlog，`maxMiningSeeds` 兜底。

```powershell
# 只读状态与过期 claim
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v5"}'
node docs/examples/development/driver.mjs status

# 预览/释放过期 mining claim
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v5","olderThanMs":1800000,"dryRun":true}'
node docs/examples/development/driver.mjs recover-seeds

# 退役/重启方向
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v5","seedId":"seed-baseline-docs","reason":"方向已稳定"}'
node docs/examples/development/driver.mjs retire-seed
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v5","seedId":"seed-baseline-docs"}'
node docs/examples/development/driver.mjs rearm-seed
```

运行时不自动回收 stale claim；先 `status` 核对，再 `recover-seeds`。

## 验证适配

需求 worktree 通常没有 `.venv`、`node_modules` 或构建产物：

- Python：优先主 checkout 的共享解释器 `C:\Users\lazeloong\Code\Mirally\.venv\Scripts\python.exe`；按改动跑目标域，例如 `-m unittest tests.test_framework_product -v`。解释器缺失时用系统 Python 并在 verification 说明，不伪造结果。
- Node/前端/内核：改动需要时在需求 worktree 安装依赖（`npm --prefix tools/testing ci ...`、`npm --prefix frontend/control-ui ci`、`kernel/dsh` 的 pnpm），或收敛到可离线验证的小范围。
- 文档：`tools/check_docs_links.py`；源码冲突标记与 Python 语法：`tools/check_sources.py`。
- 用户可见功能或安装配置变化更新 `CHANGELOG.md` 的 `[Unreleased]` 和相关文档。
- GUI/桌面/微信/真实外部服务验收不在自动工作流内完成；需要时在 verification 中明确未覆盖。

## 公开内容

PR body、review、issue 和评论都是公开的 GitHub 内容。不写绝对本地路径、用户名、`dataRoot`/worktree 路径、虚拟环境路径或凭据。`pr.mjs` 和 `requirement-review.mjs` 会对已知路径/凭据模式做兜底脱敏；engineer 仍应使用仓库相对路径和通用解释器名，reviewer 发现泄漏必须给 `revise`。

## PR 契约现状

`pr-init` 以 `manualGate:true` 运行：硬性要求是 `gh` 认证、仓库访问、允许 merge commit、`workflow:merge-ready` 标签、PR template 状态块和三个需求审批标签；branch protection/required checks 是警告。仓库 CI 只在 PR 到 `main` 时触发，集成分支 PR 没有 GitHub 检查；合并必须用 **merge commit**，squash/rebase 会让 `check-pr` 明确报错。

## 边界

- 工作流 push `auto/req-*` 分支、创建/更新自己的 draft PR；外部人或 Codex 负责 merge；只有宽限期后未 merge 且 Human 明确批准才由工作流 merge commit 自合并。
- 不替其他 PR 背书、不绕过 branch protection、不部署、不修改宿主凭据和私有配置。
- 中断或失败执行不自动重放；先检查 store、worktree、分支、PR 和 issue，再显式恢复。snapshot 中断可用 `reconcile-snapshot`，seed 卡住用 `recover-seeds`。
- `after-verified-merge` 在验证合并后删除 worktree 和本地需求分支；提交、artifact/review、PR 引用和清理记录保留。
- 旧 v4 dataRoot、旧 run 和旧 PR #61/#62 保留作证据；v5 已迁移到 store v4，新的 GitHub 决策路径从 v5 继续。
