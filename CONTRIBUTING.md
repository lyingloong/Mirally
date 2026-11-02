# 协作开发

Mirally 使用 `main` 作为集成分支。多人或多个 Agent 可以并行开发：每项任务从最新 `main` 创建独立分支和工作目录，完成后向 `main` 提交 PR，由集成者审查、验证并合并。用户指定其他目标分支时，以该任务要求为准。

AgentTeams 常驻开发工作流（需求/实现/审查、并发和合并闸门）见 [AgentTeams 工作流](docs/development/agent-teams-workflow.md)。审查师维护 [审查经验库](docs/development/review-experience.md)，记录可复用准则和反复出现的问题。

## 开始任务

按用户任务从 [Feature map](docs/reference/feature-map.md) 定位功能入口、状态 owner 和代表测试，再阅读对应专题与代码；能力范围见[能力矩阵](docs/reference/capabilities.md)。主要入口或归属发生变化时，在同一 PR 更新地图。

先明确要解决的问题、影响范围和可验证的结果；涉及共用接口、协议或同一批文件时，尽早协调接口和责任，避免各自做出不兼容的修改。开始前检查目标分支、现有任务及工作区状态，不覆盖其他人的未提交改动。

在现有仓库中，PowerShell 示例：

```powershell
git fetch origin
git worktree add -b codex/example-feature ../mirally-example-feature origin/main
cd ../mirally-example-feature
git status --short --branch
```

将示例分支和目录替换为任务专属名称。不同工作目录可共享 Git 历史，但不共享未提交的文件；不要让多个 Agent 同时修改同一工作目录。已有分支只能在一个 worktree 中检出。

每个新工作目录都需要按 [README](README.md) 安装自己的 `.venv`、Node 依赖并构建；这些忽略文件不会随 worktree 自动复制。`kernel/dsh/` 是同仓源码；UFO 子模块与其专用测试依赖已移除，默认产品链路不需要 UFO 源码，`mirally/runtime/ufo/` 适配保留作参考。

## 隔离运行与数据

默认产品数据位于 `%LOCALAPPDATA%\Mirally\framework`，会被不同工作目录共用。测试运行前设置独立的 `MIRALLY_HOME` 或显式 `--home`，并使用未被占用的端口和测试账号；不要复用或停止用户正在使用的宿主、微信连接和私有配置。不要提交凭据、个人数据或调试产物。真实服务调用和桌面操作遵守 [AGENTS.md](AGENTS.md) 的授权与验证要求，GUI 验证顺序进行。

## 提交与合并

1. 围绕一个可审查的目标提交改动，运行与变更相称的测试，检查 `git status` 和 diff。文档改动运行 `python tools/check_docs_links.py`，并保持现行文档只描述已实现行为；变更用户可见功能时按约定更新文档及更新记录。
2. 推送个人分支，在 GitHub 创建以 `main` 为 base 的 PR，说明动机、主要变化、共享接口影响、实际验证和未验证项。用户点必须在 PR 中保留“用户原始需求”一节：可整理为通顺语句，但不得改变原意，并与“改动”逐项对应；AI 提议点不冒充用户需求。不要将私人配置或与任务无关的改动放入 PR。
3. 集成者检查 PR 的目标分支、完整 diff、行为与安全影响，以及当前提交对应的自动检查；结合改动做必要的本地或实际界面验证。检查失败先修复，不能仅凭 CI 通过认定所有场景均已验证。
4. 如 `main` 有新变化，检查是否需要更新分支；解决冲突后复查结果并重新运行受影响的验证。使用普通合并操作，不强推或改写他人分支。合并后确认 `main` 的结果并告知相关参与者。

PR 检查和仓库规则是两回事：GitHub 只有在仓库设置中启用分支保护或规则集后才会强制执行相应要求。不要假定工作流文件本身已阻止直接推送或强制审查。
