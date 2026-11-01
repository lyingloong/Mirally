# AgentTeams 控制核心

> 开发工作流的队长运行手册。它不是产品功能文档，不进入产品运行时；只服务仓库开发与 Agent 协作。
>
> 更新：2026-11-02 · 状态：现行 · 配置看 `output/agent-workflow/config.json`，实时状态看 `output/agent-workflow/state.json`

## 1. 不可变规则

1. **绝不自动 merge。** 合并由用户/Codex 负责；PR 池可以堆积，不因此暂停开发。
2. **用户点优先。** 用户提出的点先进入队列；AI 点不得挤占用户点的槽位。
3. **AI 自动启动只限 `source=agent` + `risk=low`。** normal/high 必须用户确认。
4. **待合并池全部不占开发并发。** 运行配置 `merge_queue_exempt` 覆盖全部池内 PR。
5. **任务创建/状态推进只能由队长做。** 成员不建任务；状态走 `agent_workflow.py` 的 transition/闸门，不绕过。
6. **质量门：目标域测试 + CI 是权威。** 全量 `unittest discover` 不是 PR 门禁；未准备 UFO 前置时的失败属预期。
7. **冲突允许。** 不要求新点完全避开 open PR 文件；遇到冲突，工程师 rebase/merge 后返工一次解决即可。
8. **找不到合适 low 点就暂停自动发掘**，报告原因；不硬凑、不降低风险标准。
9. **每轮只做必要动作。** 不重复创建已存在/进行中的任务，不重复派发已完成任务。

## 2. 编制与路由

| 角色 | 人数 | 模型 | 备注 |
| --- | --- | --- | --- |
| 队长 | 1 | 当前会话 | 巡检、建任务、状态、池、报告 |
| 需求师 | 2 | `ustc/deepseek-flash` | 并行产规格；P0-only |
| 工程师 | 3 | `ustc/deepseek-flash` | 独立 worktree/分支/PR |
| 审查师 | 1 | `ustc/qwen3.8-flash-next` | 独立验证 + 代码审查 |

## 3. 单点生命周期

```text
proposed
  → queued        （用户批准 / AI low 自动）
  → specifying
  → implementing  （engineer + worktree + PR）
  → verifying     （reviewer 独立 worktree）
  → reviewing     （代码审查）
  → awaiting_merge（待合并池；不自动 merge）
  → merging → done（用户/Codex 批准后）
```

失败：`repairing → verifying → reviewing → awaiting_merge`。

每点任务链：

- requirements（如需）
- implementation
- verification
- review
- repair（review 失败时）

verification/review 只能由 reviewer-qwen 完成；工程师不得自审。

## 4. 调度策略

优先级：

1. 用户新点 / 用户返工
2. 已有 active 任务的验证/审查
3. 已 ready 的 low 规格 auto-start
4. 补需求规格（保持 1–2 份 ready buffer）
5. 空闲时报告/等待

并发：

- 开发并发 `3`
- AI 自动在飞上限 `2`（留 1 个用户槽）
- 无每日限额
- 池内 PR 全部免占
- `load = active_dev + max(0, merge_waiting - merge_queue_exempt)`

启动条件：

- 有 free slot
- 有 ready low 规格
- 无用户待处理/容量等待
- AI 在飞 < 2

## 5. 需求发掘

- 两个需求师可并行；每个任务产 1 份规格，或明确 2 份时再批量。
- 规格 P0-only：100–150 行、20–30 分钟；不写完整盘点。
- 方向可以广：dev tooling、CI、文档一致性、测试卫生、小型可靠性、并发/编码、仓库守卫、体验文案的小修。
- 不与用户产品取舍冲突；权限/协议/数据迁移/前端大改不自动做。
- 不要求零冲突；但应优先与 open PR 不重叠，减少返工。
- 如果找不到合理 low 点：暂停本轮自动发掘，记录已检查方向和拒绝理由；不要为凑数降风险。

## 6. 验证口径

权威顺序：

1. 改动目标域测试
2. CI 离线子集 / PR checks
3. 全量 `unittest discover`（可选，仅在有 UFO 前置时）

全量 discover 的前置：

```powershell
git submodule update --init UFO
.\.venv\Scripts\pip.exe install -r requirements-ufo.txt
```

未准备前置时的整批失败（UFO/可选依赖）**不是回归**；不要用它否掉 PR。

子进程临时目录不可写的环境：

```powershell
New-Item -ItemType Directory -Force output\tmp | Out-Null
$env:TEMP = (Resolve-Path output\tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m unittest tests.development.test_agent_workflow -v
```

CI runner 使用真实 Temp，不需要此覆盖。

## 7. 冲突与合并

- 不要求新规格避开 open PR 文件；冲突由工程师在实现/返工中 rebase/merge 解决一次。
- 合并顺序由用户/Codex 决定；队长不做 merge，也不因池子大而停开发。
- 状态助手的 `merge-approved` 只接受待合并队首；用户/Codex 可直接在 GitHub 合并任意已审 PR。非队首被合并后，队长按 reconciliation 记录（必要时 force），队列顺序不是授权，也不是合并前置。
- 若用户要求准备合并，队长只提供：PR 链接、head、CI、冲突/风险、建议顺序。

## 8. 报告处理

收到成员报告后：

1. 读 state / task / PR / CI，不凭记忆。
2. 用 `agent_workflow.py` 推进状态；受限状态走专用命令。
3. 记录 PR/head/branch/worktree/task ids。
4. 创建下一环任务（verify/review/repair），检查依赖和不重复。
5. 检查是否有空闲槽位/ready 规格；按第 4 节补位。
6. 不自动 merge；不创建 integration 除非用户批准。
7. 用户点的 PR 必须保留“用户原始需求”一节：可整理为通顺语句，但不得改变原意，并与改动逐项对应；AI 提议点不得冒充用户需求。

## 9. 巡检（30 分钟）

1. 读本文件 + `state.json` + `agent_teams_status`。
2. 检查 active/pending 任务是否卡住、重复、状态滞后。
3. 有报告未处理 → 先处理报告。
4. 无进行中的 requirements 且 ready 规格 < 2 → 给空闲需求师建任务。
5. 有 free slot + ready low → auto-promote/start。
6. 池子只记录，不因池大暂停开发。
7. 无合适 low 点 → 暂停发掘并报告。
8. 异常/角色/模型/配额问题 → 报告用户。

## 10. 工具入口

- 状态助手：`tools/dev_workflow/agent_workflow.py`
- 队长操作（待合并池、spec-path、record）：`tools/dev_workflow/ops.py`
- 规格模板：`docs/development/spec-template.md`
- 状态机/配置：`output/agent-workflow/state.json`、`config.json`（本地运行数据）
- 代表测试：`tests/development/test_agent_workflow.py`、`test_workflow_ops.py`
