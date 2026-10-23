# Mirally

本分支默认运行自有 personal agent 框架：以同仓 DSH 源码内核承载会话与模型执行，Mirally 拥有产品状态、自有界面、活动与记忆、提醒、受控行动及扩展接口。OpenClaw 不参与新主链，旧维护基线保留在 `feat/mirally-openclaw-desktop` 的 `2eeb778a`。

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-framework.txt
.\.venv\Scripts\python.exe tools\setup_framework.py
.\run.ps1
```

需要 Windows、Node 22.19+（22.x）或 24+ 和 WebView2；模型服务由你配置。活动采集、后台模型与一般主动联系默认关闭。安装、模块关系、生态兼容、迁移回退和验证边界见[第一版使用与开发说明](docs/framework-v1.md)，长期方向见[产品框架方案](docs/personal-agent-framework.md)。

已接通原生流式对话与历史、停止和重启恢复、记忆与明确偏好、提醒和持久收件箱、可选主动检查、Skill/MCP/可信插件及独立确认的桌面任务入口。第一版仍需真实外部模型、桌面任务效果和长期成本验收；未交付签名安装包、跨设备推送或完整长期陪伴能力。

此前 Mirally 的开屏动画、相伴 M、海獭／飞狐桌宠、侧栏／图标／伙伴三形态、原生窗口菜单和透明裁剪、十个工作入口、材料预览与交接已接到新框架；保留草稿、会话及单一原生消息链。活动范围确认、暂停／恢复、只读原始历史与高级记忆浏览也已迁移。具体保留项、文件格式和验收边界见 [体验迁移对照](docs/framework-experience-migration.md)。

<details>
<summary>OpenClaw 维护基线说明（仅适用于显式 --openclaw 入口）</summary>

以下旧入口、配置与默认行为仅用于维护对照，不是新框架的运行说明。使用 `run.ps1 --openclaw` 显式打开，迁移前先停止另一宿主及共享数据 owner。

![Mirally 相伴 M 图标](mirally/assets/mirally-128.png)

产品与愿景展示网页见 [Everyday Ally 展示站](showcase/README.md)，可独立运行，包含 AI 主视觉及工作、记忆、娱乐场景演示。

Mirally 是来自 MIRA + ally 的 Windows 助手。当前版本以完整的 [OpenClaw](https://github.com/openclaw/openclaw) 为核心，在独立桌面窗口中承载官方 Control UI，并应用 Mirally 品牌、晴空蓝主题、启动动画和相伴 M 图标。

**原生功能和配置管理方式保留。** 对话、历史、模型、权限、记忆、工具、自动化和插件继续使用 OpenClaw 的实现。外壳统一产品文案与图标、隐藏上游推广卡，品牌替换不会改写消息或服务配置。

UFO 是 OpenClaw 下的桌面执行插件和内部子任务能力。Mirally 主智能体通过 `sessions_spawn(agentId="ufo")` 委派明确的桌面任务，由产品维护的 Agent Harness 和 worker 调用 UFO 原生 HostAgent/AppAgent；主智能体负责规划与结果汇总，OpenClaw 管理模型、会话和审批。UFO 不提供独立界面或直接对话入口。

## 启动

已配置本项目的环境，在项目根目录执行：

```powershell
.\run.ps1
```

也可以使用明确的 OpenClaw 入口：

```powershell
.\run-openclaw.ps1 mirally
```

启动页连接已有 Gateway；本地 Gateway 没有运行时，会使用现有配置在后台启动官方 `gateway run`。页面就绪后进入完整原生工作空间。关闭或最小化窗口不停止 Gateway 中的工作；后续再次打开仍连接同一实例。

窗口使用现有的相伴 M 图标，支持任务栏、最小化、最大化、文字选择、上传和下载。外部链接交给默认浏览器。点击标题栏左上角的窗口图标，或按 `Alt+Space`，可打开原生窗口菜单，使用重新连接、重启 Gateway、在浏览器中打开和关闭窗口等功能；标题栏下面不再单独显示「⋯」菜单行。Windows 的窗口控制保留，系统设置仍在原生页面中。

「设置 → 模型」提供服务商配置：选择或新增服务商，填写 Base URL、API 格式及实际模型 ID，并可设为默认主模型。模型 ID 可以含 `/`，默认模型的服务商前缀由程序补齐。已有 API Key 不回显，留空保留原值；新输入的密钥不写入浏览器存储。原生账号和默认模型设置保留，保存通过官方定向补丁完成，不调用模型；仅打开或升级页面不会改变既有私有配置。

「API 基础地址（Base URL）」填写服务基础地址，例如 OpenAI 兼容服务的 `https://relay.example/api/gpt/v1`，保留服务商要求的代理路径。程序会追加请求路径；填写以 `/responses`、`/chat/completions` 或 `/v1/messages` 结尾的具体接口地址时，页面会提示改为基础地址。

「设置 → 活动与记忆」集中查看活动捕获配置、采集范围、最近记录、后台记忆维护状态，以及已保存和待审核记忆。可以按关键词查找记忆、分页查看正文，并通过「查看活动历史」「打开网页版监测」检查原始记录、截图和诊断。窗口图标菜单也提供设置和历史快捷入口。

活动采集默认开启，启动 Mirally 桌面时自动按当前范围采集。「设置 → 活动与记忆」的启用开关会持久保存偏好；暂停只影响本次采集，关闭窗口结束本次采集而不改变偏好。已有明确关闭的配置仍保持关闭。后台 Memory 服务由 Gateway 管理，页面区分默认开启的本地整理和默认关闭的模型语义整理；查看历史和搜索已有记忆不调用模型。没有派生索引时仍可查看原始历史，Activity 知识页中的候选/事实与持久活动记忆分别展示。

启用后台 Memory 后，默认每小时检查原始批次；只有稳定窗口的模型总结与匹配的完成回执持久提交后，才清理对应截图、原始正文及处理索引中的副本。本地规则摘要不能单独授权删除；模型整理默认关闭，未取得回执的原始资料继续保留。清理后仍可检索有损的持久摘要，完整原始证据无法重新打开。设置页展示清理进度与实际释放空间，周期由 `memory.yaml` 的 `raw_retention` 配置；配额暂停和旧积压限制见下文及 [Activity Memory](docs/activity-memory.md#hourly-raw-cleanup)。

更新执行器插件后，可点「窗口图标 → 重启 Gateway…」，确认后在当前窗口自动重启并重新连接，不必关闭 Mirally。请先停止任务、保存未发送草稿；重启会中断所有连接到该 Gateway 的客户端。菜单项只处理已核实属于本项目的本机 `gateway run`；远程服务和系统托管服务仍按原部署方式重启。「重新连接」只刷新连接，不重启服务。

## 与你并肩工作

点击原生侧栏底部或窗口图标菜单中的「侧边伙伴」，让 Mirally 留在屏幕边缘，你可以继续使用其他应用。鼠标靠近时连续展开，离开后立即开始收缩；输入框有焦点或留有草稿时保持展开。最小化后仍可从任务栏恢复，完整工作台随时可返回。

- **随时补充**：工作中可以发送新信息，通过原生补充机制调整当前任务。草稿、附件和会话与完整工作台共用；文件选择及拖入沿用原生上传流程。
- **办公起步**：「整理会议」「准备汇报」「比较方案」准备可编辑草稿，保留已有输入与附件；由你确认发送。
- **看见进展与交付**：展示实际任务和工具状态，完成后留下简短回复与工作区入口。文件预览、已更改文件和结构化产物沿用原生 Files 面板；不会把回答中的任意路径都当成已生成文件。
- **接着上次**：查看当前会话的原生摘要及最近回复，切换近期会话。「接着推进」先准备可编辑草稿，由你决定怎样继续，不建立另一份项目记忆。

要分析当前窗口，可将专用截图手柄拖到目标窗口，松开后确认图片预览并发送问题。这次传入的是一张当前画面；普通发送和后台活动采集不会自动给对话附上截图。模型必须实际支持图片，并在「设置 → 模型」的对应模型中勾选「支持图片输入」，保存为原生 `input: ["text", "image"]` 能力声明。仅修改连接时保留已有能力，新增模型默认只接收文字；勾选不会自动截屏，也不能让文本模型获得视觉能力。文本模型仍可能显示附件缩略图，但不会直接收到图像内容。

需要审批或回答问题时，点击「查看需要你决定的事」回完整工作台；处理后回到伙伴视图。详细边界见[桌面套壳说明](docs/openclaw-desktop-shell.md)。

首次展示可以按[办公协作演示](docs/demo/README.md)使用两份合成材料，核对运行中补充、来源冲突和真实文件交付。伙伴也能显示原生已加载会话中的后台工作与需查看项；不承诺覆盖未加载的所有会话。

## 首次安装

需要 Windows x64、Python 3.10 或更高版本，以及 Microsoft Edge WebView2 Runtime。已有的项目 `.venv` 可以复用。新环境先创建虚拟环境：

```powershell
py -3.10 -m venv .\.venv
.\.venv\Scripts\python.exe -m pip install -r requirements-openclaw.txt
.\.venv\Scripts\python.exe tools\setup_openclaw.py
```

安装脚本使用项目内便携式 Node `24.21.0`、锁定的 OpenClaw `2026.9.6` 与原生依赖，不修改系统 Node，不安装系统服务。固定版本的原因见 [原生安装说明](docs/openclaw-native-setup.md)。

已有旧版模型配置时，可迁移一次：

```powershell
.\.venv\Scripts\python.exe tools\configure_openclaw.py
.\run-openclaw.ps1 config validate
```

已有 OpenClaw 配置不会被覆盖。全新环境通过 `.\run-openclaw.ps1 onboard` 使用官方引导。后续模型服务商可在 Mirally 的「设置 → 模型」中配置；服务、权限和插件仍按 OpenClaw 的原生方式管理。

## 启用 UFO 桌面执行器

UFO 是可选执行器，其锁定依赖建议使用 Python 3.10（64 位）。初始化 main 使用的同一子模块版本，并将依赖安装到根目录环境：

```powershell
git submodule update --init UFO
.\.venv\Scripts\python.exe -m pip install -r requirements-ufo.txt
.\.venv\Scripts\python.exe tools\configure_ufo_agent.py --model "provider/vision-model"
.\.venv\Scripts\python.exe tools\configure_ufo_agent.py --model "provider/vision-model" --apply
```

将 `provider/vision-model` 换成已经在 OpenClaw 配好的视觉模型引用。脚本先验证；加 `--apply` 后通过官方配置补丁添加内部 `ufo` 定义与插件，并将这个执行器加入主 Agent 的子任务授权，保留原有授权、不改变默认智能体、模型凭据或已有插件路径。若同名定义已有其他配置，使用 `--agent-id` 指定新名称；调用方不是 `main` 时，用 `--requester-agent-id` 指定已有的调用方。

Gateway 空闲时通过「窗口图标 → 重启 Gateway…」重启，然后向 Mirally 主 Agent 提出明确的桌面任务，由它通过 `sessions_spawn` 委派内部执行器。内部 UFO 不出现在 Mirally 的直接聊天选择器中，普通 UFO 会话会在启动 worker 前拒绝。当前执行器支持 API Key 认证的 OpenAI-compatible Chat Completions；自定义请求头、代理、其他 API 协议和图片附件会明确拒绝。运行中可停止，普通文字补充会使旧计划失效；具有可独立撤销来源权限的补充暂不支持。

等待时可在 `.artifacts/ufo-runs/<runId>.jsonl` 查看具体初始化阶段、模型/工具等待耗时、重试与收尾；实时查看方法见 [UFO 诊断说明](docs/ufo-openclaw-agent.md#等待时查看具体阶段)。界面的通用“等待响应”文案暂未细分 UFO 阶段。修改执行器插件后需重启 Gateway，单独关闭再打开窗口不会加载插件更新。

UFO 操作当前交互桌面，会占用鼠标、键盘和窗口。Mirally 桌面窗口登记后从 UFO 截图和控件列表中排除；外部浏览器控制台不具备此窗口隔离。普通问答和桌面任务都从 Mirally 主 Agent 发起；常规安装不自动启用这个可选执行器。详细链路、设置和验证边界见 [UFO 子任务接入](docs/ufo-openclaw-agent.md)。

## 原生入口与后台运行

完整浏览器界面和 CLI 始终可用：

```powershell
.\run-openclaw.ps1 dashboard
.\run-openclaw.ps1 --help
.\run-openclaw.ps1 gateway status
```

也可以自行在终端运行 `.\run-openclaw.ps1 gateway run`，随后打开 Mirally；套壳会复用该进程。终端启动的 Gateway 可用 Ctrl+C 停止。套壳自动启动的 Gateway 是独立后台进程，本地日志与启动 PID 记录在私有状态目录 `mirally-host/`；关闭桌面窗口不会结束它，也不会安装开机启动服务。

默认智能体仍由 OpenClaw 执行。文件、命令和独立浏览器已通过真实后台任务验证，用户实测没有干扰鼠标。启用可选 UFO 执行器后，主 Agent 委派的桌面子任务才使用前台桌面观察和执行链路。

## 配置与数据

| 位置 | 内容 |
| --- | --- |
| `config/mirally/openclaw/openclaw.json` | 私有原生配置、模型凭据和 Gateway 认证 |
| `config/mirally/openclaw/` | 原生会话、记忆数据库与设备状态 |
| `%LOCALAPPDATA%/Mirally/openclaw/workspace/` | 新初始化的默认工作目录，保存身份、记忆文件和附件；已有配置沿用自己的路径 |
| `config/mirally/openclaw/mirally-webview/` | 桌面窗口的浏览器数据、原生设备配对和界面偏好 |
| `config/mirally/openclaw/mirally-host/` | 本地后台启动日志与进程记录 |
| `config/mirally/openclaw.template.json` | 可提交的无凭据配置模板 |
| `config/mirally/activity.yaml.template` | 活动观察数据层模板，默认开启；数据保存在用户本地目录 |
| `config/mirally/memory.yaml.template` | 独立后台记忆层模板；本地维护默认开启，模型语义整理默认关闭 |
| `integrations/openclaw/package-lock.json` | 完整原生运行依赖版本 |
| `mirally/assets/` | 正式矢量源与统一导出的 PNG、ICO |

私有状态目录、`.venv`、`.artifacts/`、`node_modules/` 和 `output/` 已被 Git 忽略。配对使用原生的一次性浏览器流程；套壳不会向页面回传已保存的模型密钥，也不会打印认证 URL。

旧版本使用 `openassist` 包名、`config/openassist/` 和 `OPENASSIST_` 环境变量。升级前保存工作并停止 Gateway、UFO worker、Activity 采集器和 Memory worker，再关闭 Mirally；在项目根目录先预览本机迁移，再应用：

```powershell
.\.venv\Scripts\python.exe tools\migrate_to_mirally.py
.\.venv\Scripts\python.exe tools\migrate_to_mirally.py --apply
.\run-openclaw.ps1 config validate
```

迁移保留原有配置、凭据、会话及工作目录，更新产品启动路径；Python 入口改为 `python -m mirally`，产品环境变量改用 `MIRALLY_`。仓库所在文件夹可以保留原名；以后搬动目录时可通过 `--previous-root` 指定旧根目录。步骤、范围和检查方式见[名称迁移说明](docs/mirally-name-migration.md)。

迁移到新机器或新的仓库目录时，按首次安装步骤重建根目录 `.venv` 和运行依赖；需要截图时重新构建 WGC helper。保留的私有 OpenClaw 配置可能含旧的工作目录、插件/skill 路径和 MCP 启动命令，应通过原生配置工具更新这些位置，再运行相应 `configure_*_agent.py` 的验证与 `--apply`。沿用已有智能体 ID、模型引用和自定义 `--activity-config`、`--memory-config` 参数，避免把原配置改回默认值；不要覆盖模型凭据。Activity/Memory YAML 中的相对存储路径以当前仓库根目录为基准，默认数据位于当前用户本地目录；显式绝对路径及已有历史数据需要自行迁移，程序不会自动搬库。

活动观察数据层默认开启，随 Mirally 桌面启动采集；可在设置页持久关闭。
需要定制范围或存储时，复制 `config/mirally/activity.yaml.template` 为
`activity.yaml`；已有显式 `enabled: false` 不会自动开启。诊断和只读查询使用：

```powershell
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli doctor
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli status
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli query --limit 20
```

需要观察采集器本身时，可以直接启动独立的本地监控界面。它使用隔离的测试目录，不读取模型凭据，也不会修改正式 Activity 配置：

新机器先按下文「截图和 OCR」步骤准备 WGC helper；未构建时仍能采集窗口和 UIA，但没有截图或 OCR，启动终端和监控页会提示 `helper_missing`。

```powershell
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli monitor --test
```

命令会绑定到 `127.0.0.1`、打印一次性访问地址并打开浏览器。测试模式默认完整采集：持续多显示器截图与 OCR、UIA 正文和属性、已提交输入文本变化、剪贴板更新及复制/剪切/粘贴事件，截图保留在 `.artifacts/activity-monitor/`。界面提供最新优先的事件时间线、截图预览、来源和调度诊断，以及暂停/恢复/停止控制。勾选「显示内容」查看正文和图片；这只改变显示，不改变采集。需要不打开浏览器时使用 `--no-open`，退出监控使用终端 `Ctrl+C`。监控与正式传感器共用单实例锁。

不需要手动配置即可测试；只测基础元数据和 UIA 结构时添加 `--metadata-only`。旧 `--content`、`--persist-images` 参数保留兼容，但现在已经是测试默认行为。已有正式 `activity.yaml` 不会自动覆盖，旧配置仍按原设置运行；要使用丰富内容配置，参照最新模板调整并重启采集器。

当前组织层使用 `activity.v2`，以动作、对象、变化、证据和未知项输出事实时间线；`knowledge-timeline` 可独立读取。`knowledge-events` 将同一窗口片段中的动作、窗口状态和 UIA/OCR 文字组织为 `activity.event.v1` 事件，保留来源和未知项，便于检查用户在一段时间内的使用轨迹；窗口状态不能单独证明用户阅读、保存或发送了内容。命令默认查询已组织数据，添加 `--sync` 可有界追赶原始库，详见 [Activity 事件视图](docs/activity-events.md)。运行结束后会补处理最后落库的记录，采集来源状态和无正文计数持久保存在 `collector_diagnostics`。旧投影查询会提示重建，避免混用版本。新样本的检查、修复和未验证边界见 [Activity 新数据检查](docs/activity-fresh-data-review.md)。

需要同时检查原始观察、ActivityItem、Episode、完整 Evidence、事件/时间线、Agent Context、摘要、记忆和诊断时，使用开发者历史浏览器：`.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli browse --test`，或通过 `--database`、`--knowledge-database`、`--assets` 指定已有库。它只读打开原始库，不启动采集器；默认只追赶可重建的派生索引，`--no-sync` 则连派生库也使用 SQLite 只读模式。页面和数据层说明见 [Activity 开发者浏览器](docs/activity-browser.md)。

窗口标题和浏览器地址栏 URL 已作为 `window_context` 事件采集：标题使用 Win32 前台窗口 API，浏览器 URL 使用有界 UI Automation worker；同一浏览器窗口导航后会更新上下文。模板与 `monitor --test` 默认启用，可在 `activity.yaml` 的 `collection.window_titles`、`collection.browser_urls` 中分别关闭；已有监控进程需要重新启动以加载改动。采集层之上的 Activity Knowledge 组织层会把 Observation 增量投影成活动项、Episode、FTS 索引和 Agent 上下文，可用 `knowledge-process`、`knowledge-query`、`knowledge-summary`、`knowledge-candidates`、`knowledge-facts` 和 `knowledge-status` 调试。组织器的追赶周期、批大小、Episode 空闲窗口和迟到宽限期可在 `activity.yaml` 的 `knowledge` 段配置。OpenClaw 默认 agent 和未来支持 MCP 的 harness 通过本地 `mirally-activity` MCP server 按需检索这些上下文，不会固定注入每轮 prompt；UFO 原生循环不直接消费这条知识接口。完整接入说明见 [Activity Agent Access](docs/activity-agent-access.md)。完整设计见 [Activity Knowledge Layer](docs/activity-knowledge-layer.md)，采集层与 Screenpipe 的差距见 [Activity 与 Screenpipe 对齐情况](docs/activity-screenpipe-gap.md)。

已有 OpenClaw 安装需要单独启用共享活动工具：先运行 `.\.venv\Scripts\python.exe tools\configure_activity_agent.py` 验证，再加 `--apply` 应用官方定向补丁；可通过 `--activity-config` 选择历史测试库配置。Activity 通过本地 `mirally-activity` stdio MCP 接入，迁移时禁用旧 native plugin，并加载产品维护的原生 Activity skill，使模型能发现工具、核查历史覆盖，再回答“能看到我使用电脑的 activity 吗”等问题。OpenClaw 管理工具发现和子进程生命周期；只有实际调用 Activity 工具才访问数据，不启动采集器，历史查询会有界追赶索引。健康结果区分历史可读、覆盖日期和最后记录的采集状态，不把最近空记录当成没有历史。用 `.\run-openclaw.ps1 mcp doctor mirally-activity --probe --json` 检查原生连接，历史数据和九项 MCP 工具调用可用 `tools\verify_activity_agent.py --activity-config <历史配置> --mcp` 验证；实际聊天回归见 [Activity Agent Access](docs/activity-agent-access.md)。原生 UFO 循环不直接消费 Activity 知识。Episode 提供观察计数摘要、封存状态和采集缺口，尚不声称理解用户任务语义。

第一版已实现版本化 Observation、单写者 SQLite、资产生命周期、隐私策略、游标和
agent 控制租约。完整启用后，基础观察契约要求同时具备前台基础元数据、输入元数据和受限
UIA 结构；Raw Input helper 聚合计数并通知剪贴板变化，有界 UIA worker 返回结构和获准正文。
旧配置缺少其中一项时不会静默当作完整采集，状态会显示 `degraded`。
最新默认采集范围包括截图、UIA 正文、输入文本、剪贴板和 OCR，音频未接入；已有配置仍按原范围运行。后台有界队列合并周期检查、输入停顿、点击和滚动触发，未变化的图像不重复保存；OCR 成功后不重复识别，失败时最多退避补试两次，以 `ocr_result` 关联原始帧。截图遮罩按窗口遮挡关系计算，避免后台 Mirally 窗口遮黑其他应用；UIA 完整读取声明隐私范围内的树，原始文字和结构不按字符、节点、深度或 JSON 大小裁剪。摘要和查询另设预算并标注截断；采集超时或读取失败保留诊断。图片配额与低磁盘保护生效；年龄参数不绕过原始资料的模型回执保护，也不保证到期删除，图片配额不足时暂停整个采集。实现与限制见 [丰富内容采集](docs/activity-rich-capture.md)。

WGC 现在复用常驻会话，避免周期截图反复开关捕获描边，并尽力请求关闭边框；Windows 权限限制下仍可能显示边框。默认过滤显示器黑色超过 90%、窗口范围黑色超过 80% 的帧，只保留过滤诊断，不执行 OCR 或保存图片；阈值按实际捕获区域计算。暂停/停止会释放会话，恢复后按需重建。更新此实现后重启 Activity 采集器即可，不需要重启 Gateway；其他机器需要重新构建 helper。监控页 `wgc_session` 可查看实际会话与边框请求状态。

截图和 OCR 需要从产品源码构建 WGC helper。新机器先安装 Visual Studio / Build Tools 的「使用 C++ 的桌面开发」组件（MSVC x64/x86 和 Windows SDK），再在仓库根目录运行：

```powershell
.\tools\build-activity-wgc.ps1
```

脚本自动发现已安装的编译器和完整 Windows SDK，不依赖开发机路径或固定版本；输出到 `tools/bin/desktop_wgc.exe`，运行时按仓库位置自动寻找，无需设置环境变量。迁移机器或更新 helper 源码后重新构建并重启采集器。只有自定义 helper 位置时才设置 `MIRALLY_WGC_HELPER`；旧机器的绝对路径不要带到新机器。Python OCR 依赖由 `requirements-openclaw.txt` 安装，识别语言还需要对应 Windows OCR 语言包。

然后在自有 `activity.yaml` 中同时显式授权 `collection.default_level: content` 和
`visual.enabled: true`，最新模板已设置。`visual.scope: monitors` 显式捕获所有显示器，改为 `foreground` 可只捕获前台窗口；两者不互相 fallback。正文与边界框保存在 Observation，无法读取、超时或语言包缺失保留诊断，不伪造内容。

后台记忆层默认在没有对话时持续整理新增 Activity：每 2 秒处理最多 200 条，按 5 分钟窗口保存带来源的本地情景摘要与近期工作快照。缺少 `memory.yaml` 或未填写 `enabled` 时默认开启，显式关闭仍保留；模型语义整理默认关闭，开启后才会使用 OpenClaw 模型生成推断摘要与待审核事实。原始资料清理必须等待稳定窗口的模型总结与匹配完成回执持久提交；失败、预算等待和未整理窗口继续保留，关闭模型后此前成功且回执仍匹配的窗口仍可清理。摘要有损，已清理的截图和完整 OCR/UIA/输入原文无法重新打开。

启用模型整理仅自动补处理最近一小时尚存的原始资料，尚无全历史自动补处理。更早已完成本地规则整理、但未建立模型任务的记录可能持续保留并占用图片配额；仅开启模型或点击「继续」不保证释放这些积压。配额不足会暂停整个活动采集；合格批次清理释放空间后需在设置中手动继续。若磁盘空间允许，可调大当前 `activity.yaml` 的 `storage.image_quota_mib`，按设置页提示停止本次并重新开始以加载配置，仍显示暂停时再点「继续」；这只扩大容量，不补处理或删除旧资料。

默认 agent 在相关问题中自动召回少量已有记忆，并可通过原生搜索或 Memory MCP 深查。已有安装可用 `.\.venv\Scripts\python.exe tools\configure_memory_agent.py --apply` 注册服务和 MCP；配置与边界见 [Activity Memory](docs/activity-memory.md)。

## 当前范围

完整 Control UI 直接加载，Mirally 通过独立的展示层统一品牌与默认主题，不替换其设置或业务交互。默认外观是「晴空蓝」，深色模式对应蓝灰配色；原生其他主题、颜色模式和自定义选项仍可使用。Discord 推广卡被隐藏，Discord 渠道功能保留。默认龙虾标记换为相伴 M，用户自己设置的 Agent 头像不覆盖。

启动标语是「与你并肩，让想法向前。」：环绕光带、相伴 M 入场、粒子与柔光在等待期间循环，不显示引擎连接名，也不虚报进度。会话上下文、压缩、长期记忆和运行中补充由 OpenClaw 与模型配置决定，没有旧版固定交流轮数的限制。当前未配置 embedding 或专门搜索服务；可选服务仍需要按原生方式配置。

当前侧边伙伴属于主入口 `.\run-openclaw.ps1 mirally`，与完整 Control UI 共用原生状态。旧 UFO/Tk 界面及实验 Tk OpenClaw 客户端已删除，不再维护第二套界面；历史代码可通过 Git 标签 `mirally-pre-openclaw-2026-10-03` 查看。

## 开发与验证

桌面外壳实现见 `mirally/control_ui_shell.py`，原生进程连接见 `mirally/openclaw_host.py`。页面由 WebView2 直接从 Gateway 加载；侧边伙伴复用该页面的原生会话状态和交互，没有新模型调用、记忆数据库或运行依赖。适配固定的 OpenClaw `2026.9.6` 内部 Control UI 结构，升级后需要核对。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_openclaw_host -v
```

生命周期检查不调用模型。实际原生功能与桌面验收范围见 [套壳说明](docs/openclaw-desktop-shell.md) 和 [原生验证记录](docs/openclaw-native-validation.md)。保留全部原生入口不代表所有外部服务、渠道和平台交互都已配置或测试。

[文档导航](docs/README.md) · [更新记录](CHANGELOG.md) · [迁移路线](docs/openclaw-integration-roadmap.md) · [图标资源](mirally/assets/README.md)

OpenClaw 与 UFO 的上游许可证和版权声明保留。当前使用未修改的官方 OpenClaw 发行包；UFO 作为只读 submodule 使用主仓库锁定的版本，产品适配位于 `mirally/runtime/ufo/`。


</details>
