# Activity 与 Screenpipe 的对齐情况

本文记录 2026-10-08 的产品实现状态，目标是尽量对齐 Screenpipe 的数据能力，服务于理解用户上下文的陪伴产品，而不止于活动计数。参照本机 `tmp/screenpipe` 的 `ac204c0b8e3499225f5d26bd4b4bef48ef818674` 快照；不复制其源码、数据库迁移或受限许可代码，实现仍由 Mirally 的 Python/Windows 适配层负责。

## 当前已经具备

- 前台窗口身份：HWND、进程实例、窗口实例、应用名、时间和切换事实。
- 窗口上下文：通过 Win32 `GetWindowText` 采集窗口标题；浏览器前台时通过受限 UI Automation worker 读取可见地址栏 URL。两者写入 `window_context` 事件，同一个浏览器窗口导航后会重新记录。
- 输入：键盘、点击、滚动、移动的聚合计数，UIA 已提交文本变化，剪贴板 Unicode 正文和复制/剪切/粘贴事件；不记录原始逐键字符流。
- UIA：有界结构、Name/Value/Text、控件属性、父子 ID 与焦点编辑内容，明确密码过滤、超时和截断。
- 持续内容：周期与输入/滚动合并调度，显式多显示器或前台窗口 WGC、Windows 原生 OCR、完整图像 SHA-256 去重、配额和保留清理、监控截图预览。
- SQLite 单写入观察存储、游标、事件关联、保留清理、采集器健康状态和本地监控界面。
- Activity Knowledge 派生层：基于 `ingest_seq` 增量投影 ActivityItem/Episode，提供独立 Knowledge SQLite、FTS5、摘要、上下文预算和带证据的 MemoryCandidate/MemoryFact 生命周期。
- 面向 Agent 的本地查询：CLI 已支持 `knowledge-process`、`knowledge-query`、`knowledge-summary`、`knowledge-status`、`knowledge-candidates`、`knowledge-facts`；监控服务提供 `/api/knowledge/context`、`/api/knowledge/summary`、`/api/knowledge/health`、Episode/Evidence 只读查询。
- Agent 控制租约原语：可标记 agent 控制和混合输入，避免把输入计数直接解释成用户事实；尚未自动贯穿默认宿主和 UFO worker。

标题和 URL 是陪伴层的上下文元数据，模板和 `monitor --test` 默认启用；部署方可以在 `config/mirally/activity.yaml` 的 `collection.window_titles` 和 `collection.browser_urls` 中分别关闭。URL 接受明确的协议，也保留浏览器隐藏协议时的域名地址原值，不猜测 HTTPS；不把普通搜索词当作 URL。Chrome、Edge、Firefox、Brave、Opera 和 Vivaldi 的常见进程名已纳入候选，但不表示每个版本/语言都经过实机验证。只读可见浏览器地址栏，不遍历网页正文；常规每 3 秒采样一次，窗口/标题变化触发更新，失败后退避。无法读取时保留 `not_found`、`timeout` 或 `unavailable` 状态，而不是伪造地址。尚未确认导航时输入框里的值仍只是地址栏观察，不是已加载网页的证明。

## 与 Screenpipe 的差距

Screenpipe 的能力包括持续屏幕和音频捕获、OCR、无障碍树、输入事件、可检索数据库、REST API/SDK、MCP、时间线、插件/pipe，以及按应用、窗口、URL、时间过滤的搜索。依据：[项目说明](https://github.com/screenpipe/screenpipe/blob/ac204c0b8e3499225f5d26bd4b4bef48ef818674/README.md)、[API 数据模型与接口](https://github.com/screenpipe/screenpipe/blob/ac204c0b8e3499225f5d26bd4b4bef48ef818674/crates/screenpipe-core/assets/skills/screenpipe-api/SKILL.md)、[MCP 接口](https://github.com/screenpipe/screenpipe/blob/ac204c0b8e3499225f5d26bd4b4bef48ef818674/packages/screenpipe-mcp/README.md)。这些包含 `frames`、`elements`、`audio_transcriptions`、`ui_events`、`meetings`、`memories` 等数据面；模块或接口存在不代表各个平台表现完全一致。

| 能力 | Mirally 当前 | 与 Screenpipe 的差距 | 优先级 |
| --- | --- | --- | --- |
| 标题/浏览器 URL | 已接入 `window_context` | 还没有跨浏览器标签页历史的专门索引和 URL 查询 API | 高 |
| 连续屏幕帧 | 周期+输入/滚动触发，多显示器变化帧，常驻 WGC 会话、按需 CPU 读回、无边框尽力请求，启用模板默认开启 | 没有视频导出；系统无边框授权、长期真实多屏/RDP 稳定性未验收 | 高 |
| OCR | Windows 原生 OCR，变化帧触发，有界与指纹去重 | 没有全文索引和跨帧文本检索 | 高 |
| UIA / 元素 | 前台有界 Name/Value/Text 和控件历史 | 没有独立 `elements` 索引/查询、后台窗口正文和应用解析器 | 高 |
| 输入与剪贴板 | 已提交 UIA 文本变化、Unicode 剪贴板、copy/cut/paste、元数据 | 不支持所有非 UIA 编辑器，瞬时文本可能遗漏；没有富文本/图片剪贴板解码 | 中高 |
| 音频 | 只有独立语音命令 Provider，没有常驻采集 | 没有麦克风/系统音频、VAD、分段、说话人、会议和转写历史 | 高 |
| 搜索 | Knowledge Store 已支持时间、应用、窗口、类型、标题/URL/OCR/UIA/input 的 FTS5 检索 | 还没有自然语言重写和跨设备索引 | 高 |
| 外部接口 | CLI、监控 loopback HTTP、`ActivityContextSource` 和本地 stdio MCP 已提供只读 context/summary/health 契约 | 远程认证和跨设备 MCP 服务尚未接入 | 中 |
| 插件与自动化 | 没有 pipe/插件生命周期 | 没有 Screenpipe 式扩展、触发器和结果回写 | 中 |
| 多设备/记忆 | 没有跨设备同步和长期 memories 投影 | 只保存本机 Observation，尚未做语义记忆和跨设备聚合 | 中 |
| 宿主生命周期 | 独立 CLI/监控页可运行 | 没有在 Mirally 中启动/停止、开机恢复、UFO 控制租约贯穿和默认 agent 自动消费 | 高 |
| 资源与恢复 | worker deadline、预算、SQLite 保留和健康状态已存在 | 还缺长时间运行、多显示器、锁屏/UAC、设备重置和高负载稳定性验证 | 高 |

## 完整对齐目标

以下是完整数据能力的验收范围，不把几个半成品阶段作为最终交付：

1. 连续内容：事件/周期合并调度，多显示器、同窗口滚动/输入/内容变化、UIA 名称/值/文本、OCR、帧与证据配对、回看导出。
2. 可检索：Knowledge Store 已提供标题/URL/UIA/OCR/input 的 FTS5、时间线、Episode、近期上下文和活动摘要；OpenClaw 通过本地 stdio MCP 按需调用这些能力。
3. 音频历史：麦克风/系统音频、VAD、片段、云端 STT、说话人/会议，以及与屏幕事件的时间关联。现有语音命令 Provider 可以复用，但不能替代常驻采集。
4. 产品闭环：宿主启停/恢复、资源上限和故障恢复，默认 agent 消费、UFO 控制归因、插件和语义 enrichment。推断引用原始 Observation，不覆盖原始事实。

当前已按陪伴定位使用丰富内容默认配置：采集器整体需显式启动，启用模板或 `monitor --test` 后默认开启标题、URL、持续屏幕、UIA 正文、输入文本和剪贴板；保留独立关闭、排除、密码过滤和资源边界。音频未接入。详细链路、测试入口和覆盖限制见 [丰富内容采集](activity-rich-capture.md)。
