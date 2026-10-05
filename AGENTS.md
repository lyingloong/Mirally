# Agent 协作约定

本文件适用于本仓库的开发与维护任务。开始工作前先阅读本文、`README.md` 和涉及模块的说明。用户明确提出的任务与授权优先；遇到边界冲突时先说明并询问，不自行扩大范围。

## UFO 子模块边界

- `UFO/` 是外部内核 Git submodule，默认视为只读依赖，不是产品代码编辑区。
- 可以阅读 UFO 源码和文档、检查版本及运行相关测试；不得为修复 Mirally 问题直接修改其源码、配置、依赖清单或测试。
- 产品适配、兼容修复、交互回调及配置桥接放在 `openassist/runtime/`；相关回归测试放在根目录 `tests/`。
- 若必须做运行时兼容补丁，应限制作用范围，确保异常退出时恢复，并测试真实 UFO 导入链路。不要用伪造模块或吞掉异常掩盖依赖问题。
- 未经用户明确授权，不更新 UFO gitlink、切换子模块分支、执行 `git submodule update --remote`，也不修改 `.gitmodules`。
- 确需内核修改或升级时，先说明适配层不能解决的原因和影响；取得授权后按 fork 工作流维护、验证，再更新主仓库指向。
- `UFO/logs/` 等被忽略的运行产物可以由程序或测试正常生成，这不意味着可以编辑子模块的受版本控制文件。
- 当前默认入口保留 OpenClaw Control UI，UFO 作为可选智能体通过 `integrations/openclaw/ufo/` Harness 和产品 worker 接入原生 `normal` Session 的 HostAgent/AppAgent 桌面链路。独立 `--ufo` 入口仍保留。Galaxy 是 UFO³ 的跨设备编排层，当前未接入；不要为了消除导入错误而添加无用的 Galaxy 配置或启动其服务。

## 产品代码与配置

- `openassist/core/`：对话、会话记忆、任务控制。
- `openassist/runtime/`：UFO 接入、配置桥接、语音 Provider。
- `openassist/platform/`：Windows 输入、窗口和截图隔离。
- `openassist/ui/`：界面、浮窗和状态展示。
- `config/openassist/ufo/` 是产品使用的 UFO 运行时 YAML 的唯一维护位置，不向 UFO 源码目录复制配置。
- 语音配置在 `config/openassist/speech.yaml`，与 UFO 智能体模型配置分开。
- 配置模板使用通用协议和占位值，不把开发者使用的服务商、模型或凭据设为产品默认值。
- Gateway UFO 智能体使用宿主准备的模型路由；worker 不读本地 `agents.yaml` 凭据。插件配置使用官方定向补丁，保留默认智能体、已有配置与 allowlist。不要编辑 `node_modules/` 或 OpenClaw 发行包。
- 包名、配置路径和环境变量仍使用 `openassist`，展示名称是 Mirally；不要顺带重命名兼容接口。

## 数据与执行安全

- 本地 `agents.yaml`、`speech.yaml` 含凭据，不覆盖、不提交、不在输出中展示。检查配置时只输出所需的非敏感字段。
- 任务日志和截图可能包含私人桌面、邮件和用户输入。按需读取，不整段输出截图 base64，不把调试数据加入版本控制。
- 普通问答不应触发截图或桌面动作；敏感操作必须经过用户确认，旧任务的确认不能授权新任务。
- 默认验证不调用真实模型、不操作用户应用。真实桌面或 API 验证须在用户任务授权范围内，并说明副作用。
- 区分界面状态与执行器实际能力；未经验证，不宣称原生链路已经支持某项暂停、取消、补充或截图隔离行为。

## 工作与验证

- 开始时检查主仓库和子模块状态，保留已有改动；不要自动清理、撤销或暂存他人的工作。
- 优先沿用现有模块和接口。修复范围聚焦当前问题，不顺带重构无关代码。
- 使用根目录 `.venv/Scripts/python.exe`，不使用系统 Python 或 `UFO/.venv`。
- 修改导入或初始化适配时，除了 mock 单元测试，还应在隔离进程和临时配置目录中验证真实 UFO 初始化，避免使用本地凭据。
- 文档中的规划目标不等于已实现功能；改变接入边界或用户操作时同步相关说明。

产品层测试（仓库根目录 PowerShell）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

完成前检查改动与子模块状态，和开始时的状态对照：

```powershell
git diff --check
git status --short
git -C UFO status --short
git diff --submodule=short -- UFO .gitmodules
```

仅在用户要求时 commit 或 push。提交只包含本次相关文件；交接时说明改动、验证结果和未覆盖的风险。

界面设计与 Windows 验证入口见 `docs/frontend-experience-goals.md`、`docs/desktop-experience-implementation.md`；代码组织见 `openassist/ARCHITECTURE.md`。GUI 验证脚本顺序运行，避免窗口相互干扰。
