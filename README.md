# Mirally

![Mirally 相伴 M 图标](openassist/assets/mirally-128.png)

Mirally 是来自 MIRA + ally 的 Windows 助手。当前版本以完整的 [OpenClaw](https://github.com/openclaw/openclaw) 为核心，在独立桌面窗口中承载官方 Control UI，并应用 Mirally 品牌、晴空蓝主题、启动动画和相伴 M 图标。

**原生功能和配置管理方式保留。** 对话、历史、模型、权限、记忆、工具、自动化和插件继续使用 OpenClaw 的实现。外壳统一产品文案与图标、隐藏上游推广卡，品牌替换不会改写消息或服务配置。

现有智能体选择器也可以选择「桌面助手（UFO）」。它通过产品维护的 OpenClaw Agent Harness 直接使用 UFO 原生 HostAgent/AppAgent 执行 Windows 桌面任务；OpenClaw 仍管理会话、模型选择和审批。普通聊天请使用默认 OpenClaw 智能体；默认智能体也可以使用 `sessions_spawn(agentId="ufo")` 把一个明确的桌面子任务委派给 UFO，自己继续负责整体规划和结果汇总。

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

窗口使用现有的相伴 M 图标，支持任务栏、最小化、最大化、文字选择、上传和下载。外部链接交给默认浏览器。顶部「⋯」菜单提供重新连接、重启 Gateway、在浏览器中打开和关闭窗口。系统设置仍在原生页面中。

更新执行器插件后，可点「⋯ → 重启 Gateway…」，确认后在当前窗口自动重启并重新连接，不必关闭 Mirally。请先停止任务、保存未发送草稿；重启会中断所有连接到该 Gateway 的客户端。按钮只处理已核实属于本项目的本机 `gateway run`；远程服务和系统托管服务仍按原部署方式重启。「重新连接」只刷新连接，不重启服务。

## 与你并肩工作

点击原生侧栏底部或桌面「Mirally」菜单中的「侧边伙伴」，让 Mirally 留在屏幕边缘，你可以继续使用其他应用。鼠标靠近时连续展开，离开后立即开始收缩；输入框有焦点或留有草稿时保持展开。最小化后仍可从任务栏恢复，完整工作台随时可返回。

- **随时补充**：工作中可以发送新信息，通过原生补充机制调整当前任务。草稿、附件和会话与完整工作台共用；文件选择及拖入沿用原生上传流程。
- **办公起步**：「整理会议」「准备汇报」「比较方案」准备可编辑草稿，保留已有输入与附件；由你确认发送。
- **看见进展与交付**：展示实际任务和工具状态，完成后留下简短回复与工作区入口。文件预览、已更改文件和结构化产物沿用原生 Files 面板；不会把回答中的任意路径都当成已生成文件。
- **接着上次**：查看当前会话的原生摘要及最近回复，切换近期会话。「接着推进」先准备可编辑草稿，由你决定怎样继续，不建立另一份项目记忆。

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

已有 OpenClaw 配置不会被覆盖。全新环境通过 `.\run-openclaw.ps1 onboard` 使用官方引导。后续模型、服务、权限和插件配置均按 OpenClaw 的原生方式管理。

## 启用 UFO 桌面智能体

UFO 是可选执行器，其锁定依赖建议使用 Python 3.10（64 位）。初始化 main 使用的同一子模块版本，并将依赖安装到根目录环境：

```powershell
git submodule update --init UFO
.\.venv\Scripts\python.exe -m pip install -r requirements-ufo.txt
.\.venv\Scripts\python.exe tools\configure_ufo_agent.py --model "provider/vision-model"
.\.venv\Scripts\python.exe tools\configure_ufo_agent.py --model "provider/vision-model" --apply
```

将 `provider/vision-model` 换成已经在 OpenClaw 配好的视觉模型引用。脚本先验证；加 `--apply` 后通过官方配置补丁添加 `ufo` 智能体与插件，不改变默认智能体、模型凭据或已有插件路径。若同名智能体已有其他配置，使用 `--agent-id` 指定新名称。

Gateway 空闲时通过顶部「⋯ → 重启 Gateway…」重启，然后在现有智能体选择器中选择「桌面助手（UFO）」。当前支持 API Key 认证的 OpenAI-compatible Chat Completions；自定义请求头、代理、其他 API 协议和图片附件会明确拒绝。运行中可停止，普通文字补充会使旧计划失效；具有可独立撤销来源权限的补充暂不支持。

等待时可在 `.artifacts/ufo-runs/<runId>.jsonl` 查看具体初始化阶段、模型/工具等待耗时、重试与收尾；实时查看方法见 [UFO 诊断说明](docs/ufo-openclaw-agent.md#等待时查看具体阶段)。界面的通用“等待响应”文案暂未细分 UFO 阶段。修改执行器插件后需重启 Gateway，单独关闭再打开窗口不会加载插件更新。

UFO 操作当前交互桌面，会占用鼠标、键盘和窗口。Mirally 桌面窗口登记后从 UFO 截图和控件列表中排除；外部浏览器控制台不具备此窗口隔离。普通问答请使用默认 OpenClaw 智能体；选择 UFO 会直接进入桌面执行链路。详细链路、设置和验证边界见 [UFO 智能体接入](docs/ufo-openclaw-agent.md)。

## 原生入口与后台运行

完整浏览器界面和 CLI 始终可用：

```powershell
.\run-openclaw.ps1 dashboard
.\run-openclaw.ps1 --help
.\run-openclaw.ps1 gateway status
```

也可以自行在终端运行 `.\run-openclaw.ps1 gateway run`，随后打开 Mirally；套壳会复用该进程。终端启动的 Gateway 可用 Ctrl+C 停止。套壳自动启动的 Gateway 是独立后台进程，本地日志与启动 PID 记录在私有状态目录 `mirally-host/`；关闭桌面窗口不会结束它，也不会安装开机启动服务。

默认智能体仍由 OpenClaw 执行。文件、命令和独立浏览器已通过真实后台任务验证，用户实测没有干扰鼠标。选择 UFO 时才使用前台桌面观察和执行链路。

## 配置与数据

| 位置 | 内容 |
| --- | --- |
| `config/openassist/openclaw/openclaw.json` | 私有原生配置、模型凭据和 Gateway 认证 |
| `config/openassist/openclaw/` | 原生会话、记忆、设备与工作目录 |
| `config/openassist/openclaw/mirally-webview/` | 桌面窗口的浏览器数据、原生设备配对和界面偏好 |
| `config/openassist/openclaw/mirally-host/` | 本地后台启动日志与进程记录 |
| `config/openassist/openclaw.template.json` | 可提交的无凭据配置模板 |
| `config/openassist/activity.yaml.template` | 活动观察数据层模板，默认关闭；启用后数据保存在用户本地目录 |
| `integrations/openclaw/package-lock.json` | 完整原生运行依赖版本 |
| `openassist/assets/` | 正式矢量源与统一导出的 PNG、ICO |

私有状态目录、`.venv`、`.artifacts/`、`node_modules/` 和 `output/` 已被 Git 忽略。配对使用原生的一次性浏览器流程；套壳不会把模型密钥复制进页面，也不会打印认证 URL。

活动观察数据层目前作为独立、默认关闭的产品能力提供基础实现。复制
`config/openassist/activity.yaml.template` 为 `activity.yaml`，在确认本地隐私策略后设置
`enabled: true`；诊断和只读查询使用：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli doctor
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli status
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli query --limit 20
```

需要观察采集器本身时，可以直接启动独立的本地监控界面。它使用隔离的测试目录，不读取模型凭据，也不会修改正式 Activity 配置：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli monitor --test
```

命令会绑定到 `127.0.0.1`、打印一次性访问地址并打开浏览器。测试模式默认完整采集：持续多显示器截图与 OCR、UIA 正文和属性、已提交输入文本变化、剪贴板更新及复制/剪切/粘贴事件，截图保留在 `.artifacts/activity-monitor/`。界面提供最新优先的事件时间线、截图预览、来源和调度诊断，以及暂停/恢复/停止控制。勾选「显示内容」查看正文和图片；这只改变显示，不改变采集。需要不打开浏览器时使用 `--no-open`，退出监控使用终端 `Ctrl+C`。监控与正式传感器共用单实例锁。

不需要手动配置即可测试；只测基础元数据和 UIA 结构时添加 `--metadata-only`。旧 `--content`、`--persist-images` 参数保留兼容，但现在已经是测试默认行为。已有正式 `activity.yaml` 不会自动覆盖，旧配置仍按原设置运行；要使用丰富内容配置，参照最新模板调整并重启采集器。

窗口标题和浏览器地址栏 URL 已作为 `window_context` 事件采集：标题使用 Win32 前台窗口 API，浏览器 URL 使用有界 UI Automation worker；同一浏览器窗口导航后会更新上下文。模板与 `monitor --test` 默认启用，可在 `activity.yaml` 的 `collection.window_titles`、`collection.browser_urls` 中分别关闭；已有监控进程需要重新启动以加载改动。当前数据层与 Screenpipe 的功能差距、完整对齐目标和已知限制见 [Activity 与 Screenpipe 对齐情况](docs/activity-screenpipe-gap.md)。

第一版已实现版本化 Observation、单写者 SQLite、资产生命周期、隐私策略、游标和
agent 控制租约。完整启用后，基础观察契约要求同时具备前台基础元数据、输入元数据和受限
UIA 结构；Raw Input helper 聚合计数并通知剪贴板变化，有界 UIA worker 返回结构和获准正文。
旧配置缺少其中一项时不会静默当作完整采集，状态会显示 `degraded`。
采集器整体仍默认关闭；启用最新模板后，截图、UIA 正文、输入文本、剪贴板和 OCR 默认一起开启，音频未接入。后台有界队列合并周期检查、输入停顿、点击和滚动触发，未变化的图像不重复保存；OCR 成功后不重复识别，失败时最多退避补试两次，以 `ocr_result` 关联原始帧。截图遮罩按窗口遮挡关系计算，避免后台 Mirally 窗口遮黑其他应用；UIA 内容默认深度 16，优先读取文档和编辑区。图片配额、保留时间、低磁盘保护和暂停均生效。实现与限制见 [丰富内容采集](docs/activity-rich-capture.md)。

WGC 现在复用常驻会话，避免周期截图反复开关捕获描边，并尽力请求关闭边框；Windows 权限限制下仍可能显示边框。默认过滤显示器黑色超过 90%、窗口范围黑色超过 80% 的帧，只保留过滤诊断，不执行 OCR 或保存图片；阈值按实际捕获区域计算。暂停/停止会释放会话，恢复后按需重建。更新此实现后重启 Activity 采集器即可，不需要重启 Gateway；其他机器需要重新构建 helper。监控页 `wgc_session` 可查看实际会话与边框请求状态。

截图和 OCR 需要本机 helper（本开发机已构建；新环境需构建一次）：

```powershell
.\tools\build-activity-wgc.ps1
$env:OPENASSIST_WGC_HELPER = "$PWD\tools\bin\desktop_wgc.exe"
```

然后在自有 `activity.yaml` 中同时显式授权 `collection.default_level: content` 和
`visual.enabled: true`，最新模板已设置。`visual.scope: monitors` 显式捕获所有显示器，改为 `foreground` 可只捕获前台窗口；两者不互相 fallback。正文与边界框保存在 Observation，无法读取、超时或语言包缺失保留诊断，不伪造内容。

## 当前范围

完整 Control UI 直接加载，Mirally 通过独立的展示层统一品牌与默认主题，不替换其设置或业务交互。默认外观是「晴空蓝」，深色模式对应蓝灰配色；原生其他主题、颜色模式和自定义选项仍可使用。Discord 推广卡被隐藏，Discord 渠道功能保留。默认龙虾标记换为相伴 M，用户自己设置的 Agent 头像不覆盖。

启动标语是「与你并肩，让想法向前。」：环绕光带、相伴 M 入场、粒子与柔光在等待期间循环，不显示引擎连接名，也不虚报进度。会话上下文、压缩、长期记忆和运行中补充由 OpenClaw 与模型配置决定，没有旧版固定交流轮数的限制。当前未配置 embedding 或专门搜索服务；可选服务仍需要按原生方式配置。

当前侧边伙伴属于主入口 `.\run-openclaw.ps1 mirally`，与完整 Control UI 共用原生状态。之前的 Tk OpenClaw 客户端保留为实验入口 `.\run-openclaw.ps1 companion`，与这个新视图不同。迁移前 UFO 版本可通过 `.\run.ps1 --ufo` 打开，其配置和会话独立；原版本基线保存在标签 `mirally-pre-openclaw-2026-10-03`，使用方式见 [历史版本说明](docs/ufo-version-guide.md)。

## 开发与验证

桌面外壳实现见 `openassist/control_ui_shell.py`，原生进程连接见 `openassist/openclaw_host.py`。页面由 WebView2 直接从 Gateway 加载；侧边伙伴复用该页面的原生会话状态和交互，没有新模型调用、记忆数据库或运行依赖。适配固定的 OpenClaw `2026.9.6` 内部 Control UI 结构，升级后需要核对。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_openclaw_host -v
```

生命周期检查不调用模型。实际原生功能与桌面验收范围见 [套壳说明](docs/openclaw-desktop-shell.md) 和 [原生验证记录](docs/openclaw-native-validation.md)。保留全部原生入口不代表所有外部服务、渠道和平台交互都已配置或测试。

[文档导航](docs/README.md) · [迁移路线](docs/openclaw-integration-roadmap.md) · [图标资源](openassist/assets/README.md)

OpenClaw 与 UFO 的上游许可证和版权声明保留。当前使用未修改的官方 OpenClaw 发行包；UFO 作为只读 submodule 使用主仓库锁定的版本，产品适配位于 `openassist/runtime/ufo/`。
