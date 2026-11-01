# 队长状态操作

> 面向队长的小型 CLI：待合并池列表、记录规格路径、一次写入 PR/分支/任务信息。
>
> 更新：2026-11-01 · 状态：active

## 为什么单独一个文件

`tools/dev_workflow/agent_workflow.py` 负责状态机、并发和配额；这里只做队长在每次报告后要做的手工动作，避免直接改 `state.json`，也不替换原有命令。

## 命令

在仓库根目录运行：

```powershell
# 待合并池：PR、head、分支、是否免占并发
.\.venv\Scripts\python.exe tools\dev_workflow\ops.py pending

# 记录需求规格路径
.\.venv\Scripts\python.exe tools\dev_workflow\ops.py spec-path dev-0002 output/agent-workflow/specs/dev-0002.md

# 一次记录实现/PR/任务，并按正常闸门推进状态
# worktree 换成你机器上的实际路径；下面是可迁移示例
.\.venv\Scripts\python.exe tools\dev_workflow\ops.py record dev-0002 `
  --status verifying `
  --branch agent/dev-0002 `
  --worktree C:\worktrees\dev-0002 `
  --pr 6 --head-sha e2518009 `
  --task implementation=t6 --task verification=t7
```

`pending --json` 输出机器可读列表。

## 边界

- `record --status` 仍走 `agent_workflow.py` 的 transition/并发/配额闸门；不会绕过 start/promote/merge 规则。
- `merging`、`done`、`blocked`、`awaiting_merge` 是受限状态，必须使用专用命令；`record` 默认拒绝，不能用来绕过合并队首/批准检查。
- `--force` 只用于明确恢复；正常流程不要使用。
- `spec-path` 要求文件已存在，且路径可从当前目录访问。
- 人类可读输出会按控制台编码降级；GBK/ASCII 控制台不会因 emoji/中文标题崩溃。
- 该工具不创建 AgentTeams 任务，任务仍由队长通过 AgentTeams 工具创建。
