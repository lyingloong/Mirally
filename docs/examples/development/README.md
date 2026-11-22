# Mirally 自动开发工作流（dsh-auto-workflow）

> 更新：2026-11-22，Asia/Shanghai。状态：现行（本机常驻开发配置）。来源：[dsh-auto-workflow](https://github.com/lyingloong/dsh-auto-workflow) v1.1.0 主示例（MIT）。本目录保留上游的 driver/store/worktrees/pr 与 `workflow.json`，只补充 Mirally 的提示词、解释器和验证约定。[流程与保证](https://github.com/lyingloong/dsh-auto-workflow/blob/main/docs/workflow-summary.md) 以上游为准。

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
- **业务数据**：`C:\Users\lazeloong\Code\Mirally-auto-workflow-data`，在仓库外、canonical、已初始化；保存需求队列、种子、不可变产物/review、需求 worktree 和锁。初始化后不可静默更换。
- **集成方式**：`integrationMode=pull-request`，base `integration/auto-workflow`，remote `origin`，默认 `after-verified-merge` 清理；draft PR 在 review/brief 后标记 ready，外部可随时 merge，宽限期 5 分钟后才请求工作流自合并的 Human 批准。
- **权限**：command 使用 allowlist `node docs/examples/development/driver.mjs`；HTTP deny。该授权信任整个 driver，不是只读授权，也不是文件系统沙箱。agent 的工具权限由宿主策略管理。
- **模型**：不固定 provider/model，使用宿主默认路由；需要有序候选时再给节点加 `config.models`。

## 启动与操作

1. 在持有本 checkout 的 DSH 会话中调用 `auto_workflow_define` 提交 `workflow.json` 与本机批准的 permissions。
2. `auto_workflow_start` 绑定集成 checkout 的绝对 `cwd` 和 `{ "dataRoot": "C:\\Users\\lazeloong\\Code\\Mirally-auto-workflow-data" }`，再调用 `auto_workflow_run`。
3. 向 `mine-gate` **只 enqueue 一次** producer；它每轮经 60 秒 gate、backlog 水位检查、seed claim、detached miner、队列 dispatch 后回到 gate。backlog 深于 3 时只 dispatch 已有队列。
4. 用户需求用 `driver requirement` 写入；需要立即准入时再 enqueue 一次 `dispatch`。不要重复启动 producer。
5. 人工闸门：挖掘出的 mid/high 风险需求进入 `requirement-human`；外部未在宽限期 merge 时进入 `self-merge-human`。用看板或 `auto_workflow_decide` 决定。

```powershell
$env:DSH_WORKFLOW_INPUT = '{"dataRoot":"C:/Users/lazeloong/Code/Mirally-auto-workflow-data","requirement":{"id":"req-example","source":"user","title":"具体改动","description":"期望行为与验收方式。","risk":"high","tags":["user"]}}'
node docs/examples/development/driver.mjs requirement
```

## 验证适配

需求 worktree 按 base 分支创建，通常没有 `.venv`、`node_modules` 或构建产物；按 [协作指南](../../../CONTRIBUTING.md) 和新 worktree 的实际情况准备，不要声称未实际运行的检查。

- Python：优先使用主 checkout 的共享解释器 `C:\Users\lazeloong\Code\Mirally\.venv\Scripts\python.exe`（需求 worktree 可用 `git rev-parse --git-common-dir` 定位主 checkout）。按改动运行目标域，例如 `-m unittest tests.test_framework_product -v`；具体分层见[验证](../../reference/validation.md)。
- Node/前端/内核：改动需要时再在需求 worktree 安装对应依赖（`npm --prefix tools/testing ci --ignore-scripts --no-audit --no-fund`、`npm --prefix frontend/control-ui ci`、`kernel/dsh` 的 pnpm 安装），或把需求收敛到可离线验证的小范围。`node --test` 的目标域同样见验证文档。
- 文档：`tools/check_docs_links.py`；源码冲突标记与 Python 语法：`tools/check_sources.py`。
- 用户可见功能或安装配置变化：更新 `CHANGELOG.md` 的 `[Unreleased]` 和相关文档。
- GUI、桌面、微信或真实外部服务的验收不在自动工作流内完成；需要时在 verification 中明确写未覆盖，交由 review 与人工合并判断。

## PR 契约现状

`pr-init` 已创建 `workflow:merge-ready` 标签。以下检查在本仓库当前设置下不通过，工作流因此依赖自身 gate 与人工合并，而不是 GitHub 分支保护：

- `integration/auto-workflow` 未设置 branch protection、required conversation resolution 或 required checks；仓库 CI 只在 PR 到 `main` 时触发，因此到集成分支的 PR 没有 GitHub 检查。
- 仓库允许 squash/rebase；本工作流要求 **merge commit**。合并这些 PR 时必须使用“Create a merge commit”，否则 `check-pr` 会明确报错而不是放宽绑定。
- 现有 `.github/pull_request_template.md` 没有 workflow 状态块；工作流创建的 PR 由 `pr.mjs` 生成正文，未改动现有模板。

若这条链路成为主开发路径，建议在集成分支启用分支保护并把 CI 的 PR 触发扩展到该分支，或在此目录的设置提交进入 `main` 后把 base 切换为 `main`。

## 边界

- 工作流 push `auto/req-*` 分支、创建/更新自己的 draft PR；外部人或 Codex 负责 merge。只有宽限期后未 merge 且 Human 明确批准时，才由工作流用 merge commit 自合并自己的 PR。
- 不替其他 PR 背书、不绕过 branch protection、不部署、不修改宿主凭据和私有配置。
- 中断或失败执行不自动重放；先检查业务 store、worktree、分支和 PR，再显式续接或新建 run。snapshot 中断可用 `reconcile-snapshot`，其余不确定副作用须人工对账。
- `after-verified-merge` 在验证合并后删除 worktree 和本地需求分支；提交、不可变 artifact/review、PR 引用和清理记录保留。
