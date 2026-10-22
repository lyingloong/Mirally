# OpenClaw 原生运行与验收

Mirally 已改用完整的 OpenClaw。`python -m mirally`、默认 `run.ps1` 或 `run-openclaw.ps1 mirally` 启动[完整原生界面的桌面套壳](openclaw-desktop-shell.md)。UFO 仅作为主 Agent 委派的内部桌面子代理，见 [子代理接入](ufo-openclaw-agent.md)。原生 Control UI 与 CLI 直接使用 OpenClaw 的会话、工具、记忆、审批和插件，不经过旧 Mirally 的任务分类或控制器。界面适配参见 [接入路线](openclaw-integration-roadmap.md)。

## 环境与安装

从旧 `openassist` 环境升级时，先停止运行中的服务，使用 `tools/migrate_to_mirally.py` 预览，再加 `--apply` 迁移本地配置和状态。已有模型凭据、会话与工作目录继续保留；详细步骤见[名称迁移说明](mirally-name-migration.md)。以下路径与命令使用新名称。

运行环境为 Windows x64、项目内便携式 Node `24.21.0`。Gateway 和官方客户端固定为 `2026.9.6`，依赖由 `integrations/openclaw/package-lock.json` 锁定。没有安装系统服务，也没有修改系统 Node 或全局 npm 包。

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe tools\setup_openclaw.py
.\.venv\Scripts\python.exe tools\configure_openclaw.py
.\run-openclaw.ps1 config validate
```

安装脚本下载官方 Node 压缩包并核对 SHA256，安装完整 `openclaw` 与 `@openclaw/gateway-client`。初始化脚本从已有的本地 `APP_AGENT` 配置复制模型线路，生成本地 Gateway 认证令牌；已有 OpenClaw 配置会保留，后续改动通过原生命令或界面完成。

新初始化的日常对话工作目录为 `%LOCALAPPDATA%/Mirally/openclaw/workspace/`，与开发仓库分开，避免普通聊天额外加载整个代码项目的 Git 和项目记忆上下文。使用 `--workspace '<目录>'` 可显式指定其他位置，相对路径按仓库根目录解析。已有配置和工作目录不会自动迁移；查看实际路径使用 `.\run-openclaw.ps1 config get agents.defaults.workspace`。移机时也需复制这个用户目录里的身份、记忆文件和附件，再用官方定向补丁更新本机路径。

可提交模板使用通用服务地址和模型占位值，安装时填写自己的模型配置。此前本机 Qwen3.8-flash 中转线路验证了思考参数映射：仅设置界面的 `off` 而缺少对应模型元数据时，该中转仍可能按自己的默认方式思考。具体配置、复测结果与适用范围见[响应耗时定位](response-latency.md)；这份历史验收配置不作为产品默认值，其他模型需按实际能力配置。

图片能力也需要在原生模型条目中声明。公开模板的视觉模型占位项使用 `input: ["text", "image"]`，应替换为实际支持图片的模型；缺少这个字段时，原生模型可能按纯文字处理，即使前端成功添加图片，OpenClaw 也可能在请求前省略它。已有环境可在「设置 → 模型 → 支持图片输入」中修正对应模型条目，或使用官方定向配置命令，不覆盖整个配置或更换密钥。本次默认线路缺少该字段的修复已回读有效目录并确认 `text+image`，记录在本机 `.artifacts/model-vision-verification/repair.json`；这验证能力声明与配置保留，未调用模型 API。初始化脚本仍保留已有配置；服务商的实际图片支持应单独核对，录音和视频转写也不是这个字段所保证的能力。

默认 Agent 的原生显示名称为 Mirally，头像使用当前正式矢量图标导出的 PNG，复制到工作目录的 `avatars/` 下。原生界面已验证可以正确显示名称、头像和验收会话；这属于官方身份配置，未改写原生界面源码。

模型对自己的认识来自工作目录中的 `IDENTITY.md`，仅设置界面的显示名称并不足够。初始化脚本会用 `config/mirally/openclaw-workspace/IDENTITY.md` 预置 Mirally 的身份：名称来自 MIRA 与 ally，定位为与你并肩工作的 AI 伙伴。底层模型、引擎和能力仍须按实际配置如实说明。OpenClaw 检测到已填写的身份后，会自行完成首次出生流程，不再要求给助手起名。

对已经运行过的环境，或通过原生 `onboard` 创建的工作目录，可以单独补齐身份：

```powershell
$mirallyWorkspace = .\run-openclaw.ps1 config get agents.defaults.workspace --json | ConvertFrom-Json
.\.venv\Scripts\python.exe tools\configure_openclaw.py --identity-workspace $mirallyWorkspace
.\run-openclaw.ps1 agents set-identity --agent main --from-identity
```

若使用了其他工作目录或 Agent，请替换为实际路径与 ID。第一步只填充缺失、空白或与当前官方模板一致的身份文件，保留已经自行编写的身份及已有头像文件；第二步使用原生命令将身份同步到界面和渠道。`AGENTS.md`、`SOUL.md`、`USER.md` 与记忆文件不由此脚本改写。身份文件是可持续编辑的原生工作区内容，不会在每次启动时重置；后续普通对话将读取更新的身份，无须清空会话或重启 Gateway。

2026-10-04 已在两个独立原生会话中实际询问「你是谁？」与「你好，怎么称呼你？」：当前配置的模型均自行介绍为 Mirally，没有再次要求起名。原生流程自行清理了 `BOOTSTRAP.md`；核对配置时仅身份及原生修改时间发生变化，其他设置保留，`AGENTS.md`、`SOUL.md`、`USER.md` 未变，Gateway 持续运行。脱敏结果保存在本机 `output/openclaw-native-verification/identity/verification.json`。

上述初始化面向已有 `config/mirally/ufo/agents.yaml` 的迁移环境。全新环境可在安装运行依赖后使用 `.\run-openclaw.ps1 onboard` 完成官方引导。下载需要网络；npm 沿用进程的标准代理配置，不写入仓库或修改全局配置。

`2026.9.8` 在本机可以执行文件任务，但 `sessions.create` 反复报 `Session creation publication owner is no longer current`，无法作为完整会话界面的基线。上游已有相同的 Windows 回归报告及修复 PR，因此暂时固定为此前版本，待修复发布后重新验收升级：

- [2026.9.8 的同类报告](https://github.com/openclaw/openclaw/issues/164310)
- [Windows 路径与会话创建的修复 PR](https://github.com/openclaw/openclaw/pull/162033)

没有修改 OpenClaw 源码或绕开原生会话机制。较新版本的状态留在本机忽略目录 `.artifacts/openclaw-2026.9.8-state/`，此前版本使用新状态目录，避免较新数据库直接被旧版本打开。

## 配置模型服务商

Mirally 桌面窗口的「设置 → 模型」页顶部提供「模型服务」区域，原生账号及默认模型表单仍可使用。选择已有服务商或新增服务商，填写 Base URL、API 格式和实际模型 ID；勾选设为默认主模型后，保存同时更新默认路由。此表单仅支持 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages 三种协议。

「API 基础地址（Base URL）」填写操作路径之前的地址，保留服务商要求的代理路径。例如 OpenAI 兼容服务可填 `https://relay.example/api/gpt/v1`，程序自动追加 `/responses` 或 `/chat/completions`。不要填完整操作地址；表单会拒绝这些后缀并提示基础地址。Anthropic 的操作路径是 `/v1/messages`，应去掉整个后缀，不能只去掉 `/messages`，否则会重复追加 `/v1`。

接口格式须匹配服务商实际支持的协议。HTTP 200 仍可能包含不规范的流式事件，导致 Responses 解析失败；若已验证该服务的 Chat Completions 正常，可在模型页切换为对应格式，保留基础地址、密钥和模型 ID。文本验证不代表工具调用或图片能力也已通过。

实际模型 ID 按服务商要求填写，可以包含 `/`。例如选择服务商 `relay`、模型 ID 为 `vendor/model`，保存的默认主模型引用是 `relay/vendor/model`，模型 ID 输入框无需再加 `relay/`。已有 API Key 不回显，留空保留原值；新输入的密钥不写入浏览器存储。

修改已有模型 ID 时，若 fallback、工具、别名等配置仍引用旧 ID，页面会提示先处理引用或添加新模型；原条目含模型级私有设置时应添加新模型，保留原条目。

保存通过官方定向补丁维护配置，保留其他服务商和原有设置；打开或升级界面不会自动修改私有配置。保存不调用模型，也不测试服务商连接。需要确认模型可用时，按下文验收方式主动发起任务；真实模型验证会产生 API 用量。

模型页同时说明不同任务的模型用途：主对话使用默认主模型；标题等内部短任务使用轻量辅助路由；子代理按其配置或调用方继承；活动记忆使用独立整理路由，留空沿用主对话模型。轻量辅助模型尚未配置时，宿主可按提供商自动选择；选择「沿用主模型」会禁用这种自动替代。子代理的当前默认模型与思考级别在页面展示，单个 Agent、会话或任务仍可覆盖默认值。

「模型支持推理」是能力声明，「主对话默认思考」才是任务设置。推理请求格式必须匹配实际服务商；兼容 Qwen 的服务可能要求 `qwen`（顶层 `enable_thinking`）或 `qwen-chat-template`（`chat_template_kwargs.enable_thinking`）。当能力声明缺失或请求格式不符时，选择 `off` 也可能无法关闭服务端思考。格式需要按接口验证，不能根据模型名称替所有服务商设置默认值。

「模型最大输出」声明模型本身的输出上限；各任务还可请求更小的输出。活动记忆整理固定请求关闭推理，其独立输出上限为 128–16000 tokens，不改变对话或标题任务。整理模型与输出上限可在模型页单独保存，开关、每小时调用上限及每日预算在「活动与记忆」管理。保存保留已有预算及历史用量，等待额度恢复不会因重启或重新保存而重置。

## 日常入口

桌面窗口可直接通过 `.\run.ps1` 或 `.\run-openclaw.ps1 mirally` 启动：复用已有 Gateway，缺少本地进程时在后台启动；窗口关闭后 Gateway 继续工作。

希望自行在终端管理 Gateway 时，可以按下列方式使用完整原生入口。

先在一个终端启动 Gateway：

```powershell
.\run-openclaw.ps1 gateway run
```

服务就绪后，在另一个终端进入完整原生界面：

```powershell
.\run-openclaw.ps1 dashboard
```

Dashboard 使用原生的一次性浏览器配对流程，不需要把模型密钥贴进浏览器。默认只监听本机 `127.0.0.1:18789`。工作由 Gateway 执行；关闭 Control UI 标签页不会等同于停止任务。前台 Gateway 终端中的 Ctrl+C 会关闭服务。

CLI 也可直接使用全部原生命令，例如：

```powershell
.\run-openclaw.ps1 --help
.\run-openclaw.ps1 agent --help
.\run-openclaw.ps1 browser status
.\run-openclaw.ps1 configure
```

默认独立浏览器使用 `openclaw` 配置档和 `headless: true`，有单独的浏览器数据目录。Control UI 的普通浏览器窗口只是操作入口，后台网页工具使用另一份无窗口浏览器。需要登录的任务应按网站实际要求处理，不能把独立浏览器等同于自动取得用户浏览器的登录态。

Mirally 配置模板允许网页抓取和浏览器访问本机开发页面：`localhost`、`127.0.0.1` 和 IPv6 回环 `::1` 的所有端口。对应官方字段分别为 `tools.web.fetch.ssrfPolicy.allowedHostnames` 与 `browser.ssrfPolicy.allowedHostnames`；已有环境应通过原生定向补丁将这三个主机名合并到现有列表。工具执行权限与网页地址策略是独立配置，完整执行权限不会自动修改这些列表。

回复中的文件路径可直接打开原生文件预览。Mirally 会补充识别 Windows 相对路径，例如行内代码中的 `memory\通知.md`，按当前会话的工作目录读取，兼容中文和空格；原有 Markdown 文件链接继续使用原生入口。预览中的「在文件树中显示」可定位文件目录，会话文件区也可打开本机工作目录。代码块中的示例与命令不会自动变成文件入口。

## 文件与配置

| 位置 | 用途 |
| --- | --- |
| `integrations/openclaw/package.json` 和 lockfile | 可提交的完整原生运行依赖 |
| `.artifacts/node/` | 本机便携式 Node |
| `.artifacts/openclaw-runtime.json` | 本机启动路径和版本 |
| `config/mirally/openclaw.template.json` | 可提交的配置模板，不含真实凭据 |
| `config/mirally/openclaw/openclaw.json` | 私有模型凭据、Gateway 认证和原生配置 |
| `%LOCALAPPDATA%/Mirally/openclaw/workspace/` | 新初始化的默认 Agent 工作目录和原生身份、记忆文件；实际位置由 `agents.defaults.workspace` 决定 |
| `config/mirally/openclaw/` 的其他内容 | 原生数据库、会话、设备、浏览器和插件状态 |
| `output/openclaw-native-verification/` | 本机验收输出与脱敏诊断 |

`config/mirally/openclaw/`、`.artifacts/`、`output/` 都已被 Git 忽略。私有状态备份包含凭据和会话，应继续只保存在本机。

## 验收方式与边界

2026-10-03 已通过首批真实文件任务和 16 项 Gateway 检查，具体结果与未覆盖范围见 [首批原生验收记录](openclaw-native-validation.md)。

以下命令会调用真实的已配置模型，产生正常 API 用量：

```powershell
.\.venv\Scripts\python.exe tools\verify_openclaw_native.py
& .\.artifacts\node\node-v24.21.0-win-x64\node.exe integrations\openclaw\verify-native.mjs --live
```

第一个脚本每次生成独立的中文资料、唯一标记、会话和产物目录，要求原生 Agent 读取、写摘要、通过后台命令计算 SHA256。脚本独立回读文件、核对事实和哈希，避免旧产物造成假通过。

第二个脚本使用官方 Gateway 客户端和原生 RPC，核对文件任务历史与工具记录，检验新建会话、订阅、断开重连后的历史和同会话追问，并实际调用网页读取与独立浏览器。测试客户端是本地后台诊断程序；其认证方式不作为之后 Mirally UI 的设备配对实现。

运行中补充验收先启动等待外部释放的真实后台进程，在工作期间断开并重连客户端，通过原生 `chat.send(queueMode: "steer")` 改变最终产物要求，再独立核对产物和用户消息历史。停止验收核对特定 Agent 回合的 `aborted` 事件和空闲状态，不能据此宣称所有后台子进程都已退出。审批和问题列表可读取也不等于实际审批、提问交互已全部验收；这些交互继续使用完整原生界面。

活动记忆可在「记忆 → 记忆列表」及主 Agent 的记忆页查看详情，支持来源、日期筛选和正文分页；「活动与记忆」设置提供同一入口。模型通过当前问题的相关上下文及原生 `memory_search(corpus: "all")` / `memory_get(corpus: "wiki")` 读取这些持久记忆。此检索使用关键词、平台别名和日期过滤，详见 [Activity Memory](activity-memory.md)。

验证结果只说明本机对应版本和该模型线路的实际表现。网页读取、浏览器访问与联网搜索是不同能力；搜索服务和其他可选插件需要各自的原生配置。当前尚未配置 embedding 服务，不能宣称向量语义检索已验收。其他渠道、多 Agent、自动化和扩展入口均保留，但不因为原生安装完成就宣称这些服务全部配置可用。
