# 开发与协作

手工开发和 DSH AgentTeams 共用同一套分支、审查和合并规则。从最新 `origin/main` 建独立 worktree，按改动范围验证，向 `main` 提 PR。接手产品代码先读根目录 [AGENTS.md](../../AGENTS.md)，按 [Feature map](../reference/feature-map.md) 找入口。日常提交见 [协作指南](../../CONTRIBUTING.md)。

## 从哪里开始

| 场景 | 入口 |
| --- | --- |
| 当前自动开发工作流：workflow 图、driver、每需求 worktree、需求审批与 PR 契约 | [自动开发工作流示例](../examples/development/README.md) · [PR 契约](../examples/development/pr-protocol.md) |
| 了解角色分工、开发点生命周期、准入、审查和合并 | [AgentTeams 工作流](agent-teams-workflow.md) |
| 启动或接手团队、处理报告、配额阻塞和恢复 | [队长运行手册](workflow-control-core.md) |
| 检查待合并池、记录规格和 PR、生成进度页 | [状态操作](workflow-helper.md) · [进度页](agent-teams-workflow.md#进度页) |
| 发掘需求或写规格 | [需求与种子池](requirements-pool.md) · [规格模板](spec-template.md) · [提案模板](proposal-template.md) |
| 实现、验证或审查 | [审查经验库](review-experience.md) · [验证](../reference/validation.md) |

## 文件与状态归属

| 位置 | 用途与维护者 |
| --- | --- |
| `docs/development/` | 共用规则、运行手册、模板和长期需求线索。变更走 PR。 |
| `docs/examples/development/` | 当前自动开发工作流的 workflow 图、driver、PR 契约及上游来源和本机运行配置，示例保留上游来源与本地运行配置，不复制进产品文档。共用规则仍以 `docs/development/` 为准；运行数据（数据目录、每需求 worktree）在仓库外，不入库。 |
| `tools/dev_workflow/` | 仓库状态助手、队长 CLI 和只读进度页生成器。有独立的 `tests/development/` 测试。 |
| `.github/workflows/` | GitHub 上的 CI 和 Pages 发布。真实 PR、审查、当前提交和合并结果以 GitHub 记录为准。 |
| `output/agent-workflow/` | 本地团队 owner 维护的开发点、配置、执行规格、审查报告和进度页快照。保留工作中的状态；清理临时产物使用 `output/tmp/`。 |
| DSH 会话数据 | 随队长的 DSH profile 管理。同仓 DSH 的 AgentTeams 将团队事件保存在队长会话日志；旧的本机团队若有 `.agent-teams/`，仍按该环境保留。 |

在队长持有的仓库 checkout 运行状态 CLI，团队成员在自己的 worktree 实现和验证。队长用 CLI 更新本地 `state.json`，从 DSH 查询成员和任务，并在 GitHub 查证 PR 结果。每份工作状态由一个队长维护。报告和页面可重新生成，原始状态和本机配置需要保留。

仓库中的 Python 脚本可以独立使用。实际组队和分派使用启用了 [AgentTeams profile](../../kernel/dsh/packages/experimental/agent-team-profile/README.zh.md) 的 DSH 会话；集成分支上运行的常驻自动开发按 [自动开发工作流示例](../examples/development/README.md) 的图执行。`run.ps1` 启动 Mirally 产品，GitHub Actions 执行 CI 和 Pages 发布。

新团队先按[运行手册](workflow-control-core.md#1-启动或恢复)校验 DSH 工具和目录，再初始化本地状态。现有团队接手时先读取原状态和 GitHub 记录。`.agent-teams/`、`output/agent-workflow/`、账号凭据和个人模型路由均为本地资料，请在获授权的工作目录内处理。
