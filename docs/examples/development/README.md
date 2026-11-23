# Mirally 自动开发工作流（dsh-auto-workflow）

> 更新：2026-11-23，Asia/Shanghai。状态：现行（本机常驻开发配置）。来源：[dsh-auto-workflow](https://github.com/lyingloong/dsh-auto-workflow) v1.2.0 主示例（MIT）。本目录保留上游的 driver/store/worktrees/pr 与 `workflow.json`，只补充 Mirally 的提示词、解释器和验证约定。[流程与保证](https://github.com/lyingloong/dsh-auto-workflow/blob/main/docs/workflow-summary.md) 以上游为准。

## 组件

| 文件 | 职责 |
| --- | --- |
| `workflow.json` | 一个 run 中的周期 producer、需求 worktree、3+3 工作池、draft PR 发布/轮询与 Human 自合并门禁。 |
| `driver.mjs` | command 节点使用的 JSON-only CLI。 |
| `store.mjs` | 稳定 ID、优先级/FIFO、claim、revision/epoch、审批与反馈。 |
| `worktrees.mjs` | 每需求的 attached worktree、单 commit 快照、review、brief 与受限清理。 |
| `pr.mjs` | push 需求分支、创建/更新自己的 draft PR、维护状态块/标签、轮询外部 merge。 |
| `pr-protocol.md` | 角色头、状态块、finding、merge-ready 标签和 PR 初始化约定。 |

## 本仓库配置

- **集成 checkout**：`C:\Users\lazeloong\Code\Mirally-auto-workflow`，分支 `integration/auto-workflow`，从最新 `origin/main` 合并后建立。运行期间该 checkout 必须保持干净、停在该分支且没有进行中的 merge；不要与主 checkout 或其他 Agent 同时修改它。
- **业务数据**：`C:\Users\lazeloong\Code\Mirally-auto-workflow-data-v4`，在仓库外、canonical、已初始化；保存需求队列、种子轮转、不可变产物/review、需求 worktree 和锁。业务 store 为 `version: 3`，不再接受 v2 dataRoot；初始化后不可静默更换。
- **集成方式**：`integrationMode=pull-request`，base `integration/auto-workflow`，remote `origin`，默认 `after-verified-merge` 清理；draft PR 在 review/brief 后标记 ready，外部可随时 merge，宽限期 5 分钟后才请求工作流自合并的 Human 批准。
- **权限**：command 使用 allowlist `node docs/examples/development/driver.mjs`；HTTP deny。该授权信任整个 driver，不是只读授权，也不是文件系统沙箱。agent 的工具权限由宿主策略管理。
- **模型**：按角色分工，需求 agent（`miner`、`analyst-revise`）用 `ustc-pool/deepseek-flash`，工程 agent（`engineer`）用 `ustc-pool/qwen3.8-flash-next`，审查 agent（`reviewer`）用 `ustc-pool/glm-5.3-flash`；所有候选统一 `reasoningEffort: max`。**不使用 `deepseek-account` 兜底**：USTC 统一池所有渠道都不可用时，agent 节点直接失败，运行终止；每 6 小时由会话定时提醒检查，必要时新起 run 恢复。

### 宿主版本

本机插件固定为 `1.2.0` 本地部署（`C:\Users\lazeloong\Code\agent-team-workflow\.local-deployments\1.2.0`），要求 DSH `>=0.2.2-rc.2`。v1.2.0 已按宿主顶层的 `SubagentActivationSpec` 启动 agent（`parent`、`prompt`、`cwd`、`agentOptions` 在顶层），不再需要 v1.1.0 的本地兼容补丁。重新部署时保留当前 profile 的 link 指向，或按 release 说明重新安装 `1.2.0`。

## 启动与操作

1. 在持有本 checkout 的 DSH 会话中调用 `auto_workflow_define` 提交 `workflow.json` 与本机批准的 permissions。
2. `auto_workflow_start` 绑定集成 checkout 的绝对 `cwd` 和 `{ "dataRoot": "C:\\Users\\lazeloong\\Code\\Mirally-auto-workflow-data-v4" }`，再调用 `auto_workflow_run`。
3. 向 `mine-gate` **只 enqueue 一次** producer；它每轮经 60 秒 gate、backlog 水位检查、seed claim、detached miner、队列 dispatch 后回到 gate。`maxMiningSeeds`（默认 3）限制在挖 seed 数，backlog 与在途 miner 共同决定本轮是否继续采矿；seed 空闲后会回到轮转，而不是永久消耗。
4. 用户需求用 `driver requirement` 写入；需要立即准入时再 enqueue 一次 `dispatch`。不要重复启动 producer。
5. 人工闸门：挖掘出的 mid/high 风险需求进入 `requirement-human`；外部未在宽限期 merge 时进入 `self-merge-human`。用看板或 `auto_workflow_decide` 决定。

```powershell
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v4","requirement":{"id":"req-example","source":"user","title":"具体改动","description":"期望行为与验收方式。","risk":"high","tags":["user"]}}'
node docs/examples/development/driver.mjs requirement
```

seed 恢复/轮转（`staleClaimMs` 默认 1800000，即 30 分钟；运行时不自动回收）：

```powershell
# 查看 seed 状态与过期 claim
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v4"}'
node docs/examples/development/driver.mjs status

# 回收无主/过期的 mining claim（先加 dryRun:true 预览）
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v4","dryRun":true}'
node docs/examples/development/driver.mjs recover-seeds

# 重新入队或退休一个方向
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data-v4","seedId":"seed-baseline-docs"}'
node docs/examples/development/driver.mjs rearm-seed
# node docs/examples/development/driver.mjs retire-seed
```

## 验证适配

需求 worktree 按 base 分支创建，通常没有 `.venv`、`node_modules` 或构建产物；按 [协作指南](../../../CONTRIBUTING.md) 和新 worktree 的实际情况准备，不要声称未实际运行的检查。

- Python：优先使用主 checkout 的共享解释器 `C:\Users\lazeloong\Code\Mirally\.venv\Scripts\python.exe`（需求 worktree 可用 `git rev-parse --git-common-dir` 定位主 checkout）。按改动运行目标域，例如 `-m unittest tests.test_framework_product -v`；具体分层见[验证](../../reference/validation.md)。
- Node/前端/内核：改动需要时再在需求 worktree 安装对应依赖（`npm --prefix tools/testing ci --ignore-scripts --no-audit --no-fund`、`npm --prefix frontend/control-ui ci`、`kernel/dsh` 的 pnpm 安装），或把需求收敛到可离线验证的小范围。`node --test` 的目标域同样见验证文档。
- 文档：`tools/check_docs_links.py`；源码冲突标记与 Python 语法：`tools/check_sources.py`；结构审计：`tools/check_structure.py`。三者已进入 PR 工作流的 `workflow-tooling` job，本地先跑可提前发现失败。
- 用户可见功能或安装配置变化：更新 `CHANGELOG.md` 的 `[Unreleased]` 和相关文档。
- GUI、桌面、微信或真实外部服务的验收不在自动工作流内完成；需要时在 verification 中明确写未覆盖，交由 review 与人工合并判断。

## PR 契约现状

`pr-init` 以 `manualGate:true` 运行（仓库不设 branch protection）：

- 硬性：`gh` 认证、repo 访问、允许 merge commit、`workflow:merge-ready` 标签、PR template 的 workflow 状态块。已补齐 label 和 `.github/pull_request_template.md` 状态块。
- 警告：`integration/auto-workflow` 没有 branch protection / required conversation resolution / required checks；仓库 CI 只在 PR 到 `main` 时触发，所以到集成分支的 PR 没有 GitHub 检查。工作流依赖自身 gate + 人工合并。
- 仓库仍允许 squash/rebase；合并必须用 **merge commit**，否则 `check-pr` 会明确报错而不是放宽绑定。
- 若要加分支保护，把 CI 的 PR 触发扩到 `integration/auto-workflow`，或把 base 切回 `main`。

### 公开内容

PR body、review 和评论都是公开的 GitHub 内容；不写绝对本地路径、用户名、`dataRoot`/worktree 路径、`.venv` 等机器特定信息或凭据。`pr.mjs` 会对已知的绝对路径和凭据模式做兜底脱敏，但 engineer 仍应使用仓库相对路径和通用解释器名称，reviewer 发现泄漏必须给 `revise`。此规则同样适用于 brief 和验证证据。

## 边界

- 工作流 push `auto/req-*` 分支、创建/更新自己的 draft PR；外部人或 Codex 负责 merge。只有宽限期后未 merge 且 Human 明确批准时，才由工作流用 merge commit 自合并自己的 PR。
- 不替其他 PR 背书、不绕过 branch protection、不部署、不修改宿主凭据和私有配置。
- 中断或失败执行不自动重放；先检查业务 store、worktree、分支和 PR，再显式续接或新建 run。snapshot 中断可用 `reconcile-snapshot`，seed 卡在 `mining` 可用 `recover-seeds` 回收，其余不确定副作用须人工对账。
- 业务 store 为 `version: 3`；v1/v2 的旧 dataRoot（`Mirally-auto-workflow-data*`）不迁移，保留作证据，新工作使用 v4。
- `after-verified-merge` 在验证合并后删除 worktree 和本地需求分支；提交、不可变 artifact/review、PR 引用和清理记录保留。
