# AgentTeams 仓库开发工作流

本文定义 Mirally 的常驻开发工作流：需求师持续产出开发点，工程师在独立 worktree 实现并提 PR，审查师验证和审查，**审查通过后仍需用户批准才能合并**。当前实现范围是流程文档、状态机和状态助手；真实开发循环尚未启动。

## 为什么不是一次性 DAG

早期做法是为单个任务预先写死“需求 → 实现 → 审查 → 合并”的 DAG。它适合一次性任务，但不能持续接收新开发点，也不能表达“等待用户审批合并”和“配额暂停后恢复”。本工作流改为：

```text
用户加点 ─┐
          ├──> Backlog（source=user / agent）
AI 发现 ──┘          │
                     ▼
              队长调度器
      用户点优先；load < 并发数才启动
                     │
                     ▼
   需求规格 → 实现 → 验证 → 审查 ──fail──> 修复 → 复审
                     │ pass
                     ▼
          awaiting_merge（等待用户批准；队首不占并发）
                     │ 用户批准
                     ▼
              集成合并 → 汇报 → done
```

队长（主 Agent）读状态、建任务、向用户汇报；成员在各自 worktree 工作。每个开发点按同一套任务模板动态创建，不再为每个任务重写整条计划。

## 角色与编制

默认并发数为 **3**：

| 成员 | 数量 | 职责 |
| --- | --- | --- |
| 需求师 | 1 | 盘点、拆分、写规格；维护 AI 候选点 |
| 工程师 | 3 | 每人在独立 worktree 实现一个开发点，提 PR，按审查意见修复 |
| 审查师 | 1 | 独立验证、代码审查、通过后等待用户批准再合并 |

一个 AgentTeams 成员不能同时持有两个未完成任务，所以并发数 3 需要 3 个工程师。审查默认串行；如果持续排队，可增加第二名审查师。队长不占开发并发。

## 状态机

每个开发点是一个状态机实例：

```text
queued → specifying → implementing → verifying → reviewing
                                                │ fail
                                                └→ repairing → reviewing
                                                │ pass
                                                ▼
                                         awaiting_merge
                                                │ 用户批准
                                                ▼
                                             merging → done
```

另有 `proposed`（AI 点待用户审核）、`rejected`、`cancelled`、`blocked`。
`awaiting_merge` 表示审查已通过，但**不允许合并**，必须等用户批准。

## 并发规则

```text
active_dev     = 正在需求/实现/验证/审查/修复的点数
merge_waiting  = awaiting_merge 的点数
exempt         = min(1, merge_waiting)       # 只有队首免占并发
load           = active_dev + max(0, merge_waiting - exempt)
free_slots     = max(0, concurrency - load)
```

- 只有 `free_slots > 0` 才启动新开发。
- 等待用户批准合并的队列里，**只有队首**不占并发；第二个已审查通过的点仍然占一个并发名额，避免 PR 无限堆积。
- 用户批准后点进入 `merging`，完成后 `done`，队首释放，下一个等待项成为新的免占队首。
- 配额暂停期间 `free_slots` 不用于启动新开发。

示例（并发 3）：

- 1 个实现中 + 1 个待合并 → `load=1`，可再启动 2 个；
- 2 个实现中 + 2 个待合并 → `load=3`，不再启动；
- 3 个实现中 → `load=3`，不再启动。

## 调度优先级与 AI 点

1. **用户点优先**。用户提供的点直接进入 `queued`，先占开发槽；如果用户点会占满所有空槽，`start` 会拒绝启动 AI 点，把空槽留给用户点。
2. 用户待开发点为 0、仍有空槽时，需求师可以自行发现新的 AI 点。
3. AI 点满足以下全部条件时，可**不经用户预审直接进入开发**：
   - 没有用户待开发点；
   - 有空闲并发槽；
   - AI 在飞数 < `max_auto_agent_in_flight`（默认 1）；
   - 当日自动启动数 < `max_auto_agent_per_day`（默认 2）；
   - 风险为 `low`；
   - 不涉及权限、安全、数据迁移、协议、外部服务或大重构。
4. 不满足条件时，AI 点进入 `proposed`，由队长汇总后请用户批准或驳回。
5. 自动启动的 AI 点必须标注：
   - backlog `source=agent`、`auto_started=true`；
   - 分支前缀 `agent/`；
   - PR 标题带 `[AI提议]`；
   - PR 标签 `source:agent`、`auto-start`；
   - 队长汇报中写明“AI 提议，未经预先审核”。
6. AI 点仍必须经过审查和**用户合并批准**，自动启动不等于自动合并。
7. 需求师发现新点时要去重：检查 open PR、最近合并、backlog、已有分支；优先小范围、低风险、可验证的改进。

## 审查与合并闸门

- 审查 fail：审查师在 PR 留 comment 和具体 findings，点回到 `repairing`；修复后重新审查，不需要用户介入。
- 审查 pass：审查师只提交 `verdict=pass` 和 PR 评论，**不 merge**；点进入 `awaiting_merge`。
- 队长向用户展示队首：

```text
待你批准合并（1 个，不占并发）：
  dev-0007 [AI提议] 微信提示文案优化  PR #12
  审查：pass｜测试：...｜风险：低
  回复：合并 dev-0007 / 退回 dev-0007 <理由>
```

- 用户批准后，队长才创建 integration 任务；审查师执行普通 merge（`gh pr merge --merge`，不用 squash/rebase），确认 `origin/main` 包含合并提交。
- 用户退回：进入 `repairing`；如果决定不做，标记 `rejected` 并关闭 PR。
- 合并冲突：integration 先把 `origin/main` 合入分支并解决冲突；如果冲突解决改变了语义，重新验证/审查后再请用户批准。

## 配额与 30 分钟退避

主路由始终是 **ustc**。只有某个任务因 ustc 配额阻塞、进入退避重启时，才允许用 `deepseek-account` 作临时 fallback；任务恢复后切回 ustc。

默认配置：

```json
"quota": {
  "enabled": true,
  "retry_after_minutes": 30,
  "backoff_minutes": [30, 60, 120],
  "max_retries": 2,
  "fallback": {
    "enabled": true,
    "provider": "deepseek-account",
    "model": "deepseek-flash",
    "mode": "quota_restart_only"
  }
}
```

处理流程：

1. 队长执行 `block <id> --reason quota`，记录 `blocked_until`，第一次等待 30 分钟。
2. 保留 worktree、分支和 PR，不丢进度；不要为了绕开配额重建任务或重复开 PR。
3. 用 `schedule_create` 安排 30 分钟后的会话提醒。
4. 提醒到期后先尝试 ustc 重试；仍失败则按 `[30, 60, 120]` 退避，并报告 `retry_exhausted`。
5. 如果希望在退避期间继续该任务，可临时加一个 `deepseek-account` 成员接管；只允许这个阻塞任务使用 fallback，其他新任务仍走 ustc。
6. fallback 任务完成后执行 `resume` 清除 fallback 标记，恢复 ustc 主路由。
7. 超过重试次数仍失败：暂停工作流并请用户决定，不无限重试。

`schedule_create` 是会话级提醒；DSH 关闭后不会在后台唤醒。状态文件保留阻塞信息，重新打开后按需恢复。

## 团队生命周期

1. 队长创建常驻团队：1 名需求师、3 名工程师、1 名审查师。
2. 首次只审批一次工作流规则和一个 bootstrap 任务；后续每个开发点的任务由队长动态创建，不再逐点审批计划。
3. 队长在成员报告、用户消息或提醒到期时推进状态；成员不自行合并。
4. 所有开发点结束、用户暂停或删除团队时归档。

> AgentTeams 不是后台守护进程。工作流依赖 DSH 会话和团队存活；关闭后暂停，状态文件用于恢复。

## 状态与配置

默认路径（`output/` 已被 gitignore）：

- `output/agent-workflow/state.json`：backlog 和状态；
- `output/agent-workflow/config.json`：本机配置。

默认配置关键项：

```json
{
  "concurrency": 3,
  "max_auto_agent_in_flight": 1,
  "max_auto_agent_per_day": 2,
  "merge_queue_exempt": 1,
  "primary_route": {"provider": "ustc", "model": "deepseek-flash"},
  "quota": {"retry_after_minutes": 30, "backoff_minutes": [30, 60, 120]},
  "branch_prefixes": {"user": "feat/", "agent": "agent/"}
}
```

## 状态助手

`tools/agent_workflow.py` 只管理状态和调度计算，不调用 GitHub 或 AgentTeams API。队长用它避免手算并发和漏掉人工闸门。以下命令中的 `python` 代指共享 `.venv` 解释器。

初始化：

```powershell
C:\Users\lazeloong\Code\Mirally\.venv\Scripts\python.exe tools\agent_workflow.py init
```

常用命令：

```powershell
# 用户提供开发点；直接进入 queued
python tools/agent_workflow.py add --source user --title "优化微信端文字提示" --risk normal

# AI 提案（默认 proposed，需用户批准）
python tools/agent_workflow.py add --source agent --title "补齐失败重试文案" --risk low

# 审查当前调度：load / free / 队首 / 下一步 action
python tools/agent_workflow.py status
python tools/agent_workflow.py next

# 用户批准 AI 提案；或满足条件时自动启动
python tools/agent_workflow.py promote dev-0002
python tools/agent_workflow.py promote dev-0002 --auto

# 有空槽时开始开发；记录分支、worktree、PR 和任务
python tools/agent_workflow.py start dev-0001
python tools/agent_workflow.py set-status dev-0001 implementing \
  --branch feat/weixin-text --worktree C:\Users\lazeloong\Code\Mirally-weixin-text \
  --pr 12 --task implementation=t-abc

# 审查结果
python tools/agent_workflow.py review-pass dev-0001
python tools/agent_workflow.py review-fail dev-0001 --reason "失败文案不可操作"

# 用户批准合并（只允许队首）；合并完成后记录
python tools/agent_workflow.py merge-approved dev-0001
python tools/agent_workflow.py merge-done dev-0001

# 用户退回合并
python tools/agent_workflow.py merge-rejected dev-0001 --reason "语气不合适"

# 配额阻塞与恢复
python tools/agent_workflow.py block dev-0001 --reason quota
python tools/agent_workflow.py resume dev-0001

# 校验状态
python tools/agent_workflow.py validate
```

命令输出均为 JSON（`status` 加 `--json` 也是），便于队长读取。`set-status` 不能直接设置 `blocked`、`awaiting_merge`、`merging`、`done`，必须使用对应专用命令。

## 队长的循环

```text
1. status/next
2. 有 quota 阻塞 → 等待/恢复，不派新任务
3. 有 merge_head → 向用户展示队首，等待批准；不创建 integration 任务
4. 有用户待开发点 → 按 free_slots 创建需求/实现任务并 start
5. 用户队列为空且有空槽 → 让需求师发现；低风险则 promote --auto 并标注
6. 审查 pass → review-pass，生成待批准队列；fail → review-fail + 修复/复审
7. 用户批准 → merge-approved + integration 任务；merge-done 后汇报
8. 无工作 → 让需求师补充候选，或等待用户
```

每个开发点的任务顺序：

```text
requirements → implementation → verification → review
                                      ↑             │ fail
                                      └── repair ───┘
                                      │ pass
                                      ▼
                               awaiting_merge（用户批准）
                                      ▼
                                  integration
```

## Worktree 与验证

- 每个开发点使用独立 worktree，建议 `<仓库父目录>\Mirally-worktrees\<id>`；不要在主 checkout 直接改已跟踪文件。
- 用户点分支前缀 `feat/`，AI 点前缀 `agent/`。
- 共享 Python 解释器：`C:\Users\lazeloong\Code\Mirally\.venv\Scripts\python.exe`；在 worktree 中运行时使用绝对路径。
- 前端改动需要在对应 worktree 安装 Node 依赖；验证命令由需求规格按改动类型给出。
- 合并后清理本地 worktree 和分支，避免磁盘累积。
- 不提交凭据、生成物或与开发点无关的改动。

## 恢复与限制

- 状态文件丢失时，可从 open PR、分支、AgentTeams 任务输出重建；先核对再继续，不重复开 PR。
- 配额 fallback 只在退避重启期间使用；正常调度和新建团队默认 ustc。
- 审查师是单点瓶颈；并发提高或审查排队时增加第二名审查师。
- 自动启动的 AI 点仍可能低价值；用每日上限、标签、合并闸门和用户随时叫停控制。
- 用户批准合并是最后一道人工闸门；任何自动任务都不能绕过。
