# OpenClaw 首批原生验收记录

日期：2026-10-03。环境：Windows x64、Node `24.21.0`、OpenClaw 与官方 Gateway 客户端 `2026.9.6`、协议 v4。使用现有 OpenAI-compatible Qwen 线路，配置模型 ID 为 `qwen/qwen3.8-flash`。本记录不包含凭据。

## 实际结果

| 场景 | 结果与证据 |
| --- | --- |
| 安装与配置 | 完整依赖安装、原生配置校验和 Gateway readiness 通过；14 个插件保留，未修改核心源码。 |
| 原生界面与品牌入口 | Control UI 通过官方浏览器配对连接同一 Gateway，显示 Mirally 名称、正式头像与原生会话列表；已打开截图核对。 |
| 中文资料与后台文件处理 | 原生 Agent 读取唯一标记的中文纪要，生成包含负责人、截止日期和下次时间的摘要，并执行真实 SHA256 命令。独立文件核对的 8 项检查通过；原生历史包含 `read`、`write`、`exec`、`process`。 |
| 新建会话与订阅 | `sessions.create`、会话列表和消息订阅可用，解决了此前 `2026.9.8` 的 Windows 回归阻塞。 |
| 会话上下文与重连 | 临时项目代号、负责人及时间仅写在对话中；断开并重新连接后，不读文件、不使用工具的追问准确回答三项信息。 |
| 原生网页读取 | 真实模型调用 `web_fetch`，读取 `https://example.com/`，返回 HTTP 200 和 Example Domain 标题。 |
| 独立浏览器 | 真实模型调用 `browser`，启动独立 Edge 配置档并读取网页 snapshot；页面链接为 `https://iana.org/help/example-domains`，运行状态确认 `headless: true`。 |
| 工作期间断开客户端 | 后台进程等待外部释放时断开官方客户端，重连后原生历史仍报告原来的活跃 run ID。 |
| 运行中补充 | 通过 `chat.send(queueMode: "steer")` 改变最终文件要求；独立回读确认文件内容为补充中的 `STEER-…` 标记，补充消息进入原生用户历史。验收依据是产物，发送 ACK 不是采用证明。 |
| 停止回合 | 对指定 run ID 调用 `chat.abort`，收到 `chat` 的 `aborted` 事件；原生历史随后报告会话空闲。 |
| 原生交互入口 | 执行、插件及 OpenClaw 审批列表，以及问题列表 RPC 可读取。实际人工审批和问题回答流程仍由原生 Control UI 提供，尚未完成真人交互验收。 |

官方客户端验收脚本的 16 项检查全部通过。文件核对和 Gateway 事件、历史、请求结果分别保存在本机 `output/openclaw-native-verification/`，不随 Git 上传。复测方法和入口见 [原生运行与验收](openclaw-native-setup.md)。

## 版本处理

首先安装了最新 `2026.9.8`。文件任务可以完成，但原生 `sessions.create` 多次出现 `Session creation publication owner is no longer current`，无法进入正常的新建会话流程。上游同类 [Windows 报告](https://github.com/openclaw/openclaw/issues/164310) 指出 `2026.9.6` 可用；[修复 PR](https://github.com/openclaw/openclaw/pull/162033) 仍需随正式发行版验证。

本机使用 `2026.9.6` 后，新建会话和以上真实任务均通过。较新版本的私有状态保留在 `.artifacts/openclaw-2026.9.8-state/`，没有把旧版本直接打开较新的数据库。后续升级应先重跑原生验收，再切换用户入口，不能仅根据“有更新”提示直接升级。

## 未覆盖的范围

- 暂未配置 embedding 服务，日志提示向量回忆降级。会话内上下文已验证，语义长期记忆尚未验收；原生记忆文件和机制保留。
- 本轮验证网页读取与浏览器访问，没有配置或验证专门的搜索服务，也未验证登录网站、验证码和下载任务。
- 资料为合成 Markdown，DOCX、PDF、表格等实际业务流程待进一步验收。
- 使用了后台文件/命令工具和无窗口浏览器，但没有独立监测所有窗口焦点变化和鼠标事件，需要用户在真实使用中观察。
- 中止测试覆盖 Agent 回合；没有据此判定任意外部后台进程都已退出。
- 多 Agent、渠道、自动化、可选插件与扩展入口均保留，但没有逐一配置第三方凭据或验证每项功能。

## 下一步的最小接入

当前可直接使用完整 Control UI 与 CLI；旧 `run.ps1` 仍是 UFO 版本。先让用户用自己的问题和文件体验原生助手，确认任务能力和后台运行方式，再接 Mirally 的常用交互与视觉资产。

首个 Mirally 客户端应连接同一 Gateway，采用已验证的原生会话、历史、事件、补充和中止语义；UI 使用正式设备身份与配对。没有适配的功能继续进入原生界面。浮窗、主题和工作状态可以重新实现，不沿用旧控制器、重复注入上下文或旧暂停规则。
