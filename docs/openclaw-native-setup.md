# OpenClaw 原生运行与验收

Mirally 已改用完整的 OpenClaw。`run.ps1` 默认启动[完整原生界面的桌面套壳](openclaw-desktop-shell.md)；旧 UFO 版本使用 `run.ps1 --ufo`。原生 Control UI 与 CLI 直接使用 OpenClaw 的会话、工具、记忆、审批和插件，不经过旧 Mirally 的任务分类或控制器。界面适配参见 [接入路线](openclaw-integration-roadmap.md)。

## 环境与安装

运行环境为 Windows x64、项目内便携式 Node `24.21.0`。Gateway 和官方客户端固定为 `2026.9.6`，依赖由 `integrations/openclaw/package-lock.json` 锁定。没有安装系统服务，也没有修改系统 Node 或全局 npm 包。

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe tools\setup_openclaw.py
.\.venv\Scripts\python.exe tools\configure_openclaw.py
.\run-openclaw.ps1 config validate
```

安装脚本下载官方 Node 压缩包并核对 SHA256，安装完整 `openclaw` 与 `@openclaw/gateway-client`。初始化脚本从已有的本地 `APP_AGENT` 配置复制模型线路，生成本地 Gateway 认证令牌；已有 OpenClaw 配置会保留，后续改动通过原生命令或界面完成。

当前 Qwen3.8-flash 中转模板保留已验证的思考参数映射，默认关闭思考，需要时仍可在原生界面选择其他档位。仅设置界面的 `off` 而缺少模型元数据，会使中转继续按自己的默认方式思考，可能拖慢工具任务。具体配置、复测结果与适用范围见[响应耗时定位](response-latency.md)；其他模型不能直接套用此兼容参数。

图片能力也需要在原生模型条目中声明。当前同一中转、同一 `qwen/qwen3.8-flash` 已通过原生界面发送自绘图片，正确识别数字、颜色与几何形状，公开模板因此声明 `input: ["text", "image"]`。缺少这个字段时，原生模型可能按纯文字处理，即使前端成功添加和发送图片，助手也无法看到画面。当前本机由原生 CLI 修正这一字段，其他配置保留，Gateway 热加载后进程未重启，6 项实际识图检查通过；证据在 `output/openclaw-native-verification/vision/verification.json`。初始化脚本仍保留已有配置；已有环境应通过原生配置界面或命令修正对应模型条目，不覆盖整个配置或更换密钥。其他模型的图片能力应单独核对，录音和视频的转写也不是这个字段所保证的能力。

默认 Agent 的原生显示名称为 Mirally，头像使用当前正式矢量图标导出的 PNG，复制到工作目录的 `avatars/` 下。原生界面已验证可以正确显示名称、头像和验收会话；这属于官方身份配置，未改写原生界面源码。

模型对自己的认识来自工作目录中的 `IDENTITY.md`，仅设置界面的显示名称并不足够。初始化脚本会用 `config/openassist/openclaw-workspace/IDENTITY.md` 预置 Mirally 的身份：名称来自 MIRA 与 ally，定位为与你并肩工作的 AI 伙伴。底层模型、引擎和能力仍须按实际配置如实说明。OpenClaw 检测到已填写的身份后，会自行完成首次出生流程，不再要求给助手起名。

对已经运行过的环境，或通过原生 `onboard` 创建的工作目录，可以单独补齐身份：

```powershell
.\.venv\Scripts\python.exe tools\configure_openclaw.py --identity-workspace .\config\openassist\openclaw\workspace
.\run-openclaw.ps1 agents set-identity --agent main --from-identity
```

若使用了其他工作目录或 Agent，请替换为实际路径与 ID。第一步只填充缺失、空白或与当前官方模板一致的身份文件，保留已经自行编写的身份及已有头像文件；第二步使用原生命令将身份同步到界面和渠道。`AGENTS.md`、`SOUL.md`、`USER.md` 与记忆文件不由此脚本改写。身份文件是可持续编辑的原生工作区内容，不会在每次启动时重置；后续普通对话将读取更新的身份，无须清空会话或重启 Gateway。

2026-10-04 已在两个独立原生会话中实际询问「你是谁？」与「你好，怎么称呼你？」：当前配置的模型均自行介绍为 Mirally，没有再次要求起名。原生流程自行清理了 `BOOTSTRAP.md`；核对配置时仅身份及原生修改时间发生变化，其他设置保留，`AGENTS.md`、`SOUL.md`、`USER.md` 未变，Gateway 持续运行。脱敏结果保存在本机 `output/openclaw-native-verification/identity/verification.json`。

上述初始化面向已有 `config/openassist/ufo/agents.yaml` 的迁移环境。全新环境可在安装运行依赖后使用 `.\run-openclaw.ps1 onboard` 完成官方引导。下载需要网络；npm 沿用进程的标准代理配置，不写入仓库或修改全局配置。

`2026.9.8` 在本机可以执行文件任务，但 `sessions.create` 反复报 `Session creation publication owner is no longer current`，无法作为完整会话界面的基线。上游已有相同的 Windows 回归报告及修复 PR，因此暂时固定为此前版本，待修复发布后重新验收升级：

- [2026.9.8 的同类报告](https://github.com/openclaw/openclaw/issues/164310)
- [Windows 路径与会话创建的修复 PR](https://github.com/openclaw/openclaw/pull/162033)

没有修改 OpenClaw 源码或绕开原生会话机制。较新版本的状态留在本机忽略目录 `.artifacts/openclaw-2026.9.8-state/`，此前版本使用新状态目录，避免较新数据库直接被旧版本打开。

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

## 文件与配置

| 位置 | 用途 |
| --- | --- |
| `integrations/openclaw/package.json` 和 lockfile | 可提交的完整原生运行依赖 |
| `.artifacts/node/` | 本机便携式 Node |
| `.artifacts/openclaw-runtime.json` | 本机启动路径和版本 |
| `config/openassist/openclaw.template.json` | 可提交的配置模板，不含真实凭据 |
| `config/openassist/openclaw/openclaw.json` | 私有模型凭据、Gateway 认证和原生配置 |
| `config/openassist/openclaw/workspace/` | 默认 Agent 工作目录和原生身份、记忆文件 |
| `config/openassist/openclaw/` 的其他内容 | 原生数据库、会话、设备、浏览器和插件状态 |
| `output/openclaw-native-verification/` | 本机验收输出与脱敏诊断 |

`config/openassist/openclaw/`、`.artifacts/`、`output/` 都已被 Git 忽略。私有状态备份包含凭据和会话，应继续只保存在本机。

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

验证结果只说明本机对应版本和该模型线路的实际表现。网页读取、浏览器访问与联网搜索是不同能力；搜索服务和其他可选插件需要各自的原生配置。当前尚未配置 embedding 服务，不能宣称语义长期记忆已验收。其他渠道、多 Agent、自动化和扩展入口均保留，但不因为原生安装完成就宣称这些服务全部配置可用。
