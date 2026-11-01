# 仓库布局与结构约定

更新：2026-11-02，Asia/Shanghai。状态：现行约定 + 只读审计；不代表已批准任何目录搬迁。需求来源：user-seed `SEED-REPO-STRUCTURE`（用户原话「维护仓库结构」），由开发点 dev-0020 落成可复跑检查。

本文回答“东西该放哪、什么不该入库、谁依赖谁”。执行检查用 [tools/check_structure.py](../../tools/check_structure.py)，它只报告、不改文件；目录搬迁与命名整改都要另立开发点并经用户批准。

## 顶层目录与 owner

| 位置 | 归属 | 说明与约束 |
| --- | --- | --- |
| `kernel/` | 同仓 DSH 内核 | 包含上游基线与 Mirally 覆盖；见 `kernel/dsh/UPSTREAM.md`。不移动、不改结构 |
| `frontend/control-ui/` | 固定上游前端 + Mirally 页面 | `verify:source` 校验来源；页面改动限定在既有目录内 |
| `mirally/` | 产品 Python | `core/` 领域、`runtime/` 采集/记忆/执行、`framework/` 产品 owner、`platform/`、`framework_ui/`、`assets/`；不重命名包路径 |
| `tests/` | 根级产品测试 | Python/Node 产品链路测试与 `development/` 工具测试；目录归属见整改清单 R4 |
| `tools/` | 开发与验收工具 | 命名与分类约定见下文；新增直接子目录需登记 |
| `docs/` | 现行文档 | 索引在 [文档导航](../README.md)；一个主题一篇 |
| `config/` | 产品 YAML 与模板 | `config/mirally/`、`config/mirally/ufo/`；模板约定见下文 |
| `showcase/` | 产品与愿景展示站 | 独立构建，不参与主产品启动链 |
| `UFO/`、`.gitmodules`、`requirements-ufo.txt` | 只读子模块与专用依赖 | 移除由独立 PR 处理；移除后同步本表与审计白名单 |
| `.github/` | CI 与仓库自动化 | 工作流只做验证，不承载产品行为 |
| 根文件（`AGENTS.md`、`CONTRIBUTING.md`、`README.md`、`CHANGELOG.md`、`run.ps1`、`.gitignore`、`requirements-framework.txt`） | 项目级约定与入口 | 不新增根级脚本/配置；确有需要先登记 |

## 允许位置与新增登记

- **新增顶层目录或根文件**：必须先更新本文的 owner 表，并在 `tools/check_structure.py` 的 `TOP_LEVEL_ALLOWED` 中登记；否则审计失败。未登记目录一律视为结构漂移。
- **`tools/` 直接子目录**：同样登记到 `TOOLS_SUBDIRECTORIES_ALLOWED`；新的工具脚本放在 `tools/` 根下并遵守命名约定。
- **不允许为了“整理”搬迁**：`kernel/**`、`frontend/control-ui/**`、`mirally/**` 的包路径保持稳定；搬迁会影响导入、测试、文档链接与上游基线，必须作为独立点评估并获得用户批准。
- 运行期数据（`output/`、`tmp/`、`.agent-teams/`、虚拟环境、`node_modules/`、`__pycache__/`）不入库，见下节。

## 生成物与缓存

审计扫描 **git 索引**（`git ls-files`），不扫描工作区，因此本地忽略目录不会触发告警。以下内容不得提交：

| 类别 | 示例 | 处置 |
| --- | --- | --- |
| 构建产物 | `dist/`、`*.tsbuildinfo`、`*.map`、`coverage/` | 由构建重新生成，不入库 |
| 依赖目录 | `node_modules/`、`**/.venv/` | 由安装命令生成 |
| 运行时缓存 | `__pycache__/`、`.pytest_cache/`、`.artifacts/` | 可删除重建 |
| 本机运行状态 | `output/`、`tmp/`、`.agent-teams/` | gitignore 或仅本机保留 |

新增忽略规则或改变构建输出位置属于独立改动；本约定不修改 `.gitignore`。

## `config/` 模板约定

- 仓库只提交**模板/默认值**，不提交私有凭据、账号与服务商密钥。
- 现行模板后缀不一致：`activity/memory/speech.yaml.template` 使用 `.template`，而 `execution.yaml` 与 `ufo/` 内 5 个文件无此后缀（见整改清单 R3）。在用户确认统一方案前，新增配置优先使用 `.template` 后缀并在同目录说明用途。
- 产品读取的默认值文件与“复制后修改”的模板要分开表达；不要靠文件名猜测是否含私有值。

## `tools/` 命名约定

| 规则 | 内容 |
| --- | --- |
| 允许扩展名 | `.py`、`.mjs`、`.ps1` |
| 命名前缀 | `check_`（只读审计/校验）、`verify_`（端到端/链路验证）、`build_`、`setup_`、`redraw_`、`render_` |
| 单词分隔 | Python/Node 脚本用下划线（`verify_framework.py`），PowerShell 用连字符（`build-activity-wgc.ps1`） |
| 直接子目录 | 只有登记过的 `dev_workflow/`、`testing/`；新增需登记 |

新工具先判断能否复用既有 `verify_*`/`check_*`（EXP-0001 复用优先）；一次性脚本不入库。

## 依赖方向

| 规则 | 说明 |
| --- | --- |
| `mirally/core/` → 不得 import `mirally.framework` / `mirally.runtime` | 领域层保持独立，只依赖标准库与自身 |
| `mirally/runtime/` → 不得 import `mirally.framework` | 采集/记忆/执行层不反向依赖产品 owner |
| `mirally/framework/` → 可以延迟 import `mirally/runtime/` | 产品 owner 读域层允许；保持函数内延迟导入，避免启动环 |

已知例外（审计输出为 `exceptions`，不判失败）：

| 文件 | 允许的 import | 原因 |
| --- | --- | --- |
| `mirally/runtime/speech_credentials.py` | `mirally.framework.channels` | R2：`CredentialProtector` 被语音凭据复用；修正分层需要独立点，见下表 |

审计用 AST 解析绝对与相对 import；新增例外必须在 `tools/check_structure.py` 的 `IMPORT_EXCEPTIONS` 登记并在此说明理由。

## 审计节奏

- 改动 `tools/`、`mirally/core/`、`mirally/runtime/`、顶层目录或文档索引时，提交前运行 `python tools/check_structure.py`（或带 `--json` 供脚本消费）。
- 退出码：`0` 无发现；`1` 有未登记目录、生成物、命名或依赖方向问题，或无法读取 git 索引。
- 当前为**只读报告**，尚未接入 CI 阻断（CI 步骤与多个在飞 PR 冲突，是否入 CI 属待用户决策项）；审查者仍需人工核对“入口是否正确、测试是否覆盖行为”。
- 审计失败时先修结构或补登记，不要为了让检查变绿而放宽白名单。

## 只报告、不执行的整改清单

以下问题只记录建议，不在本约定中搬迁、重命名或删除：

| # | 问题 | 建议 | 状态 |
| --- | --- | --- | --- |
| R1 | `tools/` 30+ 脚本平铺、三套前缀 | 评估是否分子目录；先保持前缀约定 | 需独立点 + 用户决策 |
| R2 | `runtime/speech_credentials.py` 反向依赖 `framework.channels` | 把 `CredentialProtector` 下沉到 `core/` 或独立模块 | 需独立点（已登记为审计例外） |
| R3 | `config/` 模板后缀不一致 | 统一 `.template` 或明确“默认值文件”规则 | 需独立点 + 用户决策 |
| R4 | 根级、内核级、前端测试分布不清 | 在 feature map/验证文档中补“跑哪套测试”导航 | 需独立点 |
| R5 | `mirally/ufo_full.py` 与 `mirally/runtime/ufo_full.py` 并存 | 确认无外部引用后随 UFO 清理决策处理 | 需独立点 + 用户决策 |

## 运行方式

```powershell
# 人类可读摘要
python tools/check_structure.py

# 机器可读报告（顶层白名单/生成物/工具命名/依赖方向）
python tools/check_structure.py --json
```

报告结构：`ok` 汇总布尔值，`checks.top_level`（未登记项）、`checks.artifacts`（跟踪的生成物）、`checks.tools`（命名违规）、`checks.imports`（违例与已知例外）。更多导航见 [Feature map](feature-map.md)、[验证与未覆盖边界](validation.md)与[协作指南](../../CONTRIBUTING.md)。
