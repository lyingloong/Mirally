# 依赖清单、owner 与审计节奏

更新：2026-11-02，Asia/Shanghai。状态：现行。本文只描述依赖面、owner、锁文件和**只读**审计方式；不批准任何升级、删除或替换。

来源：用户种子 **SEED-DEPS**（用户原话“优化依赖”）的审计整理。它把“优化依赖”拆成可审计步骤，本身不代表已确认要升级或裁剪任何包；真正的升级/删除必须在独立开发点里做并单独验收。

## 清单与 owner

| # | 清单 / 锁文件 | 位置 | 生态 | 分类 | Owner 与更新规则 |
| --- | --- | --- | --- | --- | --- |
| D1 | `requirements-framework.txt` | 仓库根 | pip | 运行时 | 产品运行时（桌面与 Python worker，安装进根 `.venv`）。修改需产品验证；`tools/setup_framework.py` 是安装入口。 |
| D2 | `requirements-ufo.txt` | 仓库根 | pip | 可选参考 | 仅供保留的 UFO 参考适配；正在由独立点/PR 处理移除，本文件不重复处理。 |
| D3 | `kernel/dsh/.github/review-ownership/requirements.txt` | kernel | pip | kernel CI | kernel 审查机器人专用，与产品运行时隔离；随 kernel 上游维护。 |
| D4 | `kernel/dsh/pnpm-lock.yaml` + 371 个非 fixture `package.json` | kernel | pnpm workspace | 上游源码 | 同仓 DSH 上游工作区；升级需整体验证模型/工具/会话主链，另立点。各 importer 可各自声明版本范围，由锁文件解析。 |
| D5 | `frontend/control-ui/package-lock.json` + 14 个 `package.json` | frontend | npm | 固定上游 | 固定上游 Control UI 来源（`verify:source` 逐字节校验）；Mirally 自有页面在其上扩展，不随产品依赖升级。 |
| D6 | `tools/testing/package.json` + `package-lock.json` | tools | npm | 验收工具 | 浏览器/DOM 验收的独立依赖，与产品运行时隔离；只服务于离线验收脚本。 |
| D7 | 未固定的可选依赖（如 `faster-whisper`） | docs | 手动安装 | 可选 | 只在文档说明安装方式，不进入清单；是否纳入版本约束待用户决定。 |

三套包管理器（pip、pnpm、npm×2）并存是现状：Python 运行时用根 `.venv`，kernel 使用 pnpm workspace，前端与验收工具各有独立 npm 锁文件。

## 审计工具（只读，离线）

```powershell
# 人类可读摘要；有发现时退出码 1，否则 0
.\\.venv\\Scripts\\python.exe tools\\check_dependencies.py

# 机器可读报告（ASCII 转义 JSON，可安全重定向到文件）
.\\.venv\\Scripts\\python.exe tools\\check_dependencies.py --json
```

工具只读取文件，不安装、不升级、不删除、不联网。它报告：

- **同环境重复声明且约束不一致**：同一生态、同一安装环境（`product-venv`、`kernel-ci`、`frontend`、`test-tools`）内，同名直接依赖出现不同约束时列为 finding；kernel pnpm workspace 内不同 importer 的范围差异属正常解析，只记为 informational。
- **未使用候选**：按 import 名映射（`pywebview→webview`、`Pillow→PIL`、`PyYAML→yaml`、`tzdata→zoneinfo`、`winrt-*→winrt`）检查 `mirally/` 是否引用 `requirements-framework.txt` 中的直接依赖；未被引用只作为候选，不自动删除。
- **清单分类**：区分运行时、可选参考、kernel CI、前端固定上游、验收工具与 fixture；kernel 下的 fixture `package.json` 只计数、不参与重复检测，避免统计口径被测试夹具污染。
- **锁文件**：识别 D4/D5/D6 的锁文件存在性；不解析锁文件内部版本树。

当前基线（离线实测，不代表长期不变）：

```text
Manifests: 389  Declarations: 6422  Lockfiles: 3
Excluded fixture package.json: 12  Duplicate groups: 384 (conflicting: 19, informational: 19)
Findings: 0
```

## 审计节奏

- **依赖变更 PR**：改任何 `requirements*.txt`、`package.json` 或锁文件前，先在仓库根运行上述命令，并把输出附在 PR 说明。
- **常规审计**：每月或至少每个合并批次做一次只读审计，结论记入对应开发点；有 finding 时拆成独立点逐项处理，不在审计 PR 里顺手升级/删除。
- **联网安全审计**（`pip-audit`、`npm audit`、OSV 查询）需用户明确授权网络与配额后再执行；未授权时本文只保证离线一致性审计。

## 不升级 / 不删除边界

| 对象 | 规则 | 理由 |
| --- | --- | --- |
| `frontend/control-ui/**` | 不升级、不裁剪 | 固定上游来源；需按 `verify:source` 与上游策略整体更新，见[前端](../architecture/frontend.md)。 |
| `kernel/dsh/**` | 本审计不动 | 同仓 DSH 升级影响模型/工具/会话主链，需独立点与全量验证。 |
| 运行时依赖（D1） | 不删除 | 删除需真实运行验证，属独立点。 |
| `requirements-ufo.txt`（D2） | 交给对应独立点 | 避免与在飞 PR 重复。 |
| 可选依赖（D7） | 只文档化 | 是否固定版本由用户决定。 |
| 安全审计（CVE） | 不在本文联网执行 | 需要用户授权网络与配额。 |

## 基线记录

本机离线可测部分（2026-11-02，实现 worktree）：

- 清单：3 个 pip 清单 + 1 个验收工具清单 + 14 个前端 `package.json` + 371 个 kernel 非 fixture `package.json`（另有 12 个 fixture `package.json` 被排除）。
- 声明条目：6422；锁文件 3 个（`frontend/control-ui/package-lock.json`、`tools/testing/package-lock.json`、`kernel/dsh/pnpm-lock.yaml`）。
- `requirements-framework.txt` 直接依赖：17 条；未使用候选 0（启发式）。

未记录：`.venv` 体积、`kernel/dsh` 与 `frontend/control-ui` 的 `node_modules` 体积、`npm --prefix frontend/control-ui run build` 与 `python tools/setup_framework.py` 耗时。审计环境未安装前端/内核依赖且不联网，这些数值需要在准备依赖的机器上按同一命令补充，不设阈值断言。

## 待用户决策

1. 是否授权联网安全审计（`pip-audit` / `npm audit` / OSV），频率与失败处理（阻断还是报告）。
2. Python 精确 pin 是否统一改为范围 + 锁文件；前端/kernel 是否接受定期跟随上游。
3. 可选依赖（如 `faster-whisper`）是否纳入清单并给出版本约束。
4. 审计发现未使用依赖时，是允许直接删除（需验证）还是只出报告由用户逐项确认。

相关验证范围见[验证](validation.md)；工作流与合并闸门见[AgentTeams 工作流](../development/agent-teams-workflow.md)和[协作指南](../../CONTRIBUTING.md)。
