# Mirally 名称迁移

产品展示名、Python 包、配置目录和产品环境变量现已统一：

| 项目 | 旧名称 | 当前名称 |
| --- | --- | --- |
| Python 包和模块入口 | `openassist`、`python -m openassist` | `mirally`、`python -m mirally` |
| 产品配置及 OpenClaw 私有状态 | `config/openassist/` | `config/mirally/` |
| 产品环境变量前缀 | `OPENASSIST_` | `MIRALLY_` |
| 产品事件日志文件 | `openassist-events.jsonl` | `mirally-events.jsonl` |

OpenClaw、UFO 等上游名称及其环境变量保持上游定义。原始设计提示词、历史记录中的旧名和已有任务日志保留原样。

## 在当前仓库目录升级

先保存草稿与工作，停止 Gateway、UFO worker、Activity 采集器和 Memory worker，再关闭 Mirally。前台进程可用 `Ctrl+C` 停止；后台进程按现有启动方式停止。目录迁移期间继续写入旧配置或数据库会造成状态分离。

在项目根目录运行迁移预览：

```powershell
.\.venv\Scripts\python.exe tools\migrate_to_mirally.py
```

默认只显示计划，不写入。检查计划中的来源、目标和配置路径，确认对应当前本机环境后应用：

```powershell
.\.venv\Scripts\python.exe tools\migrate_to_mirally.py --apply
```

迁移将整个旧 `config/openassist/` 目录迁入 `config/mirally/`，保留模型凭据、Gateway 认证、会话、记忆、设备、浏览器数据及工作目录，并更新 `openclaw/openclaw.json`、`activity.yaml`、`memory.yaml`、`speech.yaml` 中受维护的模块入口、配置路径及环境变量引用。保留自己的智能体 ID、模型引用和自定义配置。

升级 checkout 后，旧私有目录可能与新 `config/mirally/` 的公开模板同时存在。只有新目录全部属于脚本识别的原版发行文件、且两侧同路径文件字节相同时，才允许迁移：先备份新模板目录，再整体搬入旧状态，仅补齐缺失的公开文件。新目录含私有配置、未知文件、修改过的模板或两侧内容冲突时会拒绝执行，须先核对并自行解决冲突；脚本不会用模板覆盖私有配置。两个目录均不接受符号链接、Windows junction 或硬链接。发行模板指纹只忽略 CRLF/LF 差异，实际文件按原始字节保留。

需要改写的配置会先备份到 `.artifacts/name-migration/`；共存的新模板目录保存在该次备份的 `checkout-scaffold/` 下。备份包含原有私有配置，应继续留在本机；整个状态目录通过搬迁保留，脚本不会另行复制全部历史数据库。应用前会重新校验两侧完整目录；过程中失败时尝试恢复两侧原状，若恢复受文件占用等因素阻断，错误会提示从备份恢复。整个过程要求相关服务保持停止，不支持并发写入。

完整目录校验会读取历史文件，大目录可能耗时较长。强杀进程或断电无法触发自动回滚；若发生中断或回滚失败，先保持服务停止，核对旧目录、新目录及本次备份中的文件位置，再恢复。备份不是完整历史副本，不应将其直接覆盖整个状态目录。

另存的 Activity/Memory profile，以及通过 `OPENCLAW_CONFIG_PATH` 指向仓库外的配置，需要按自己的部署位置更新旧模块名与路径，再运行对应配置工具验证。迁移器只处理上面列出的标准配置文件。

`openclaw/openclaw.json` 必须是标准 JSON，且不含 `$include`。OpenClaw 原生支持的 JSON5 注释、尾逗号和 include 配置不在此迁移器的解析范围内；遇到这些配置时，先按 OpenClaw 原生方式整理为一个不含 include 的标准 JSON 配置，再预览迁移。迁移器会拒绝这些格式，不会自动展开或改写外部 include 文件。

自行设置的环境变量也需要改用新前缀。例如 `OPENASSIST_WGC_HELPER` 改为 `MIRALLY_WGC_HELPER`，`OPENASSIST_SPEECH_API_KEY` 改为 `MIRALLY_SPEECH_API_KEY`，变量值沿用自己的配置。修改用户或系统环境变量后重新打开终端。`OPENCLAW_STATE_DIR`、`OPENCLAW_CONFIG_PATH` 仍使用上游名称；如果显式设置了旧配置目录，更新其路径。

仓库所在文件夹可以保留原名，日常启动仍使用 `run.ps1`。若要直接启动 Python 入口，使用根目录环境：

```powershell
.\.venv\Scripts\python.exe -m mirally
```

## 以后搬动仓库目录

在新目录按[首次安装步骤](../README.md#首次安装)重建根目录 `.venv` 和运行依赖；需要截图时重新构建 WGC helper。迁移预览可通过 `--previous-root` 指定搬动前的仓库绝对路径：

```powershell
.\.venv\Scripts\python.exe tools\migrate_to_mirally.py --previous-root '<旧仓库绝对路径>'
.\.venv\Scripts\python.exe tools\migrate_to_mirally.py --previous-root '<旧仓库绝对路径>' --apply
```

这用于更新仍指向旧根目录的产品配置路径。仓库外的 Activity/Memory 数据库、资产目录和自定义工具路径按自己的部署位置迁移；名称迁移不会重写历史正文或搬动外部数据库。相对 Activity/Memory 存储路径仍以当前仓库根目录为基准，默认数据目录仍在当前用户的 `Mirally` 本地数据目录下。

## 迁移后核对

```powershell
.\run-openclaw.ps1 config validate
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli doctor
.\.venv\Scripts\python.exe -m mirally.runtime.memory.cli status
```

若使用自定义 Activity/Memory 配置，继续提供原有 `--config` 参数。启用了 UFO、Activity 或 Memory 的环境，还需运行对应 `tools/configure_*_agent.py` 的验证；需要调整接入路径时再加 `--apply`，沿用原有模型、智能体和配置参数。随后启动 Gateway 与 Mirally，核对原会话、工作目录、模型引用和历史数据仍能访问，再恢复后台采集与记忆维护。
