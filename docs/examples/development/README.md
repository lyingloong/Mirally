# 本地自动开发工作流（dsh-auto-workflow）

> 更新：2026-11-07，Asia/Shanghai。状态：现行（本仓库开发工具）。依据：[dsh-auto-workflow](https://github.com/lyingloong/dsh-auto-workflow) v1.0.0 官方示例与本仓库的运行配置。

本目录是 `dsh-auto-workflow` 唯一主示例（GitHub 项目本地持续开发）的本地 materialized 副本，供本仓库的持续开发运行使用。`driver.mjs`、`store.mjs`、`worktrees.mjs` 来自插件 v1.0.0（MIT）；`workflow.json` 只对 agent 提示词补充了本仓库的环境与验证适配，流程语义以上游示例文档为准，不在本仓库复制。

## 本仓库的配置

- **集成 checkout**：独立的 Git worktree，分支 `integration/auto-workflow`，从最新 `origin/main` 建立；不直接使用主 checkout，也不直接改 `main`。运行期间该 checkout 必须保持干净、停在 `integration/auto-workflow`，且没有进行中的 merge。
- **业务数据**：仓库外的一个 canonical `dataRoot`（不能是 symlink），保存需求队列、种子、不可变产物与 review、需求 worktree 和锁；初始化后不可更换。
- **权限**：定义不包含权限。`command` 由用户在会话中显式批准（本机运行使用完整命令授权），`http` 为 deny。driver 只做本地 Git 与业务文件操作；图不 push、不开 PR、不部署。
- **模型**：不固定服务商，使用宿主默认路由；需要有序候选时在节点 `models` 中配置。

## 启动与操作

1. 会话中调用 `auto_workflow_define` 提交 `workflow.json` 与用户批准的 permissions。
2. `auto_workflow_start` 绑定集成 checkout 的绝对 `cwd` 和 `{ "dataRoot": "…" }`，再调用 `auto_workflow_run`。
3. 向 `mine-gate` **仅 enqueue 一次** producer，之后由它按 60 秒闸门周期挖掘种子并准入队列。
4. 用户需求用 `node docs/examples/development/driver.mjs requirement` 写入；需要立即准入时再 enqueue 一次 `dispatch`。
5. `requirement-human` 与 `merge-human` 是人工闸门：用会话顶部的**工作流**看板或 `auto_workflow_decide` 决定。批准合并只做本地 `git merge --no-ff`；远端 push/PR 仍由用户显式执行。

## 验证适配

需求 worktree 没有 `.venv`：agent 通过 `git rev-parse --git-common-dir` 找到主 checkout，使用其 `.venv/Scripts/python.exe`。按改动运行目标域测试；全量 `-m unittest discover -s tests` 还包含可选 UFO 参考测试，不作为必需门禁。文档改动运行 `tools/check_docs_links.py` 与 `tools/check_sources.py`。

## 边界

- 每项需求一个保留的 detached worktree，没有每需求分支；默认 `after-verified-merge` 在验证合并后清理 worktree，提交与不可变记录保留。
- 中断或失败执行不自动重放；先检查仓库与业务数据，再显式续接或新建 run。
- 不自动 push、开 PR、合并远端或部署，不修改宿主凭据与私有配置。
