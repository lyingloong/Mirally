# AgentTeams 队长运行手册

共用准入、角色和合并规则见 [AgentTeams 工作流](agent-teams-workflow.md)。目录、工具与状态归属见 [开发与协作](README.md)。

## 1. 启动或恢复

1. 在队长持有的仓库 checkout 核对仓库、分支和已有改动。新环境按 [DSH AgentTeams profile 指南](../../kernel/dsh/packages/experimental/agent-team-profile/README.zh.md#use-this-package) 启用团队工具。同仓版本可用 `list_agents` 和 `team_task_list` 核对成员及任务，其他 DSH 版本以会话实际提供的工具为准。派发任务时显式交代成员使用的 worktree，团队 profile 自身不创建 worktree。
2. 新团队使用以下命令初始化本地队列，再按需求、实现和审查分配角色。现有团队先运行 `status` 和 `validate`，对照 GitHub 和原团队的任务；`init` 只补缺失文件，不能从 PR 恢复任务。
3. 模型和成员路由在 DSH profile 及本机 `output/agent-workflow/config.json` 配置。没有设置 `primary_route` 时沿用当前 DSH profile。需要配额备用路由时填写 `quota.fallback.provider`、`quota.fallback.model` 并显式启用它。凭据由 DSH 的账号或凭据配置管理。
4. 用当前 roster 和状态助手输出安排工作；进度页从已有记录生成。完整命令见 [状态操作](workflow-helper.md) 与 [工作流命令](agent-teams-workflow.md#状态助手)。

~~~powershell
.\.venv\Scripts\python.exe tools\dev_workflow\agent_workflow.py init
.\.venv\Scripts\python.exe tools\dev_workflow\agent_workflow.py status --json
.\.venv\Scripts\python.exe tools\dev_workflow\agent_workflow.py validate
~~~

## 2. 每轮调度

- 先读取 `state.json`、DSH 团队状态、待处理报告和 GitHub 的最新 PR 记录。核对卡住、重复或已经关闭的任务和 PR。
- 用 `next` 读取建议动作，按实时并发和角色空闲情况派发。缺候选时查 [需求与种子池](requirements-pool.md)，把新任务的相关资料位置交给成员。
- 收到成员报告后，通过状态 CLI 记录 PR、当前提交、分支、验证和任务 ID。审查失败进入返工，修复后回到验证和审查。发现 PR 有新提交、未解决的阻塞意见或 CI 失败时核对实际状态，再决定返工或复审。
- 交付待合并队首用 `next --deliver`。GitHub 合并由用户或已获授权的 Codex 执行。队长核实 GitHub 合并提交已在 `origin/main` 后，通过 `merge-done` 或 `merge-reconcile` 记账。
- 到达配额或容量限制时按 [退避与恢复](agent-teams-workflow.md#配额与-30-分钟退避)保留已有任务。会话提醒适用于仍运行的 DSH，重新启动后从本机状态接续。

## 3. 验证和交接

- 按 [验证](../reference/validation.md) 选择目标域测试和 CI 子集。完整 Python 套件可选，使用仓库依赖即可运行。各 worktree 独立构建，端到端使用隔离数据和回环服务。
- 工程师保留 PR 中的实际变更与验证记录；审查师针对当前提交记录结论。可复用的发现按[审查经验库](review-experience.md)记录，产品决定按[产品思考记录](../product/product-notes.md)追溯。
- GitHub 报告、人工决策和本地状态冲突时，先查证来源。记录无法核实的差异，由持有相应状态的维护者处理。
