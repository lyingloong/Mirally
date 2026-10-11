# Activity 丰富内容采集

2026-10-08 实现记录。本轮完整接入持续屏幕内容、UIA 正文、输入内容三个数据面，不复制 Screenpipe 的受限许可源码，不修改 UFO 子模块。音频、全文检索、MCP 和长期记忆不在本轮范围。

## 启动与查看

本机已构建 WGC helper，直接运行：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli monitor --test
```

无需正式配置或模型凭据。默认保存多显示器变化帧、OCR、UIA 正文、输入文本和剪贴板内容到 `.artifacts/activity-monitor/`。运行几分钟浏览、滚动、编辑和复制粘贴，回监控页勾选「显示内容」；筛选 `content_snapshot`、`context_snapshot`、`input_text`、`clipboard_content`。选中内容帧可预览截图。显示开关不停止正文采集，暂停按钮才暂停采集。

`--metadata-only` 只采集元数据和 UIA 结构；旧 `--content`、`--persist-images` 仍接受，但完整内容已是默认。正式运行使用 `activity.yaml`，不覆盖已有配置；改配置后重启采集器。采集器不随 Mirally 默认启动。

编码、遮罩、去重和常驻 WGC 修复需要重启采集器，不需要重启 Gateway。本机 helper 已重新构建；其他机器更新代码后需重新运行构建脚本。已有事件保持不变，不自动回填 OCR；此前被遮黑的图片无法恢复原始画面，应重新采集。

新机器需先运行 `tools/build-activity-wgc.ps1`，安装项目依赖和 Windows OCR 语言包。helper 不存在时，元数据和 UIA 可继续，WGC 显示 `helper_missing`，不会改用其他截图方案。

## 数据与链路

| 事件 | 保存的信息 | 触发与关联 |
| --- | --- | --- |
| `foreground_changed` / `window_context` | 进程和窗口实例、标题、可读浏览器地址栏 URL、时间 | 前台切换；同窗口标题/URL 更新 |
| `context_snapshot` | 控件角色、父子 ID、坐标、Name、AutomationId、ClassName、Value、Text、焦点/可用/密码状态、截断 | 前台切换、周期、输入停顿、点击、滚动；结构未变化则去重 |
| `content_snapshot` | 图像指纹、黑色比例、遮罩面积、显示器 ID/边界或窗口 ID、图片引用、OCR 文本和框、采集起止时间、质量与缺口 | 默认每 3 秒检查；输入与滚动增加触发，完整 PNG SHA-256 变化后才 OCR/持久化 |
| `ocr_result` | OCR 重试结果、尝试次数、原始帧引用 | 未变化帧的 OCR 失败时，5 秒、10 秒退避后最多补试两次；复用原始图片，不再保存重复帧 |
| `input_batch` | 按键、点击、滚动计数，移动标记和静默时间 | 默认输入静默 1.5 秒或 burst 达 10 秒；窗口切换分开批次 |
| `input_text` | 已提交文本插入/删除/替换、位置、控件 ID、baseline 是否已知、provenance | UIA 焦点编辑控件 Value/Text 的变化；支持中文 IME 提交和粘贴，不把功能键翻译成字符 |
| `clipboard_changed` / `clipboard_operation` | 剪贴板序号、copy/cut/paste、观察窗口和时间 | `WM_CLIPBOARDUPDATE` 和 Ctrl+C/X/V 的 Raw Input 信号 |
| `clipboard_content` | Unicode 文本、序号、owner HWND、操作类别、截断或错误 | 剪贴板更新和粘贴；按指定序号读取，已被替换的内容标为 stale，不回填后来内容 |

周期采样也会发现没有键盘信号的 UIA 文本变化；其来源仍标注 `focused_uia` / `observed_committed_text`，不能据此断言一定是用户输入。没有已知基线时记录带 `initial_baseline_unknown` 的快照，不把已有文档全文宣称为新输入。密码、只读和非编辑控件不生成输入文本事件。

默认 `visual.scope: monitors` 是明确的全显示器模式，不是窗口捕获失败后的 fallback。显示器画面可能包含多个应用，因此 actor 为 `BACKGROUND`，不把整幅图归给前台 app。UIA 仍针对前台窗口，不枚举所有后台窗口正文。Mirally 和配置排除窗口在显示器图像中遮蔽后才进行指纹、OCR 和持久化；已允许的普通应用内容正常保留。指定 `foreground` 可收窄范围。

采集队列按目标合并，最多 32 项，15 秒过期；前台 poller 与 Raw Input pump 不执行慢 COM/GPU/剪贴板调用。UIA、WGC、OCR、地址栏和剪贴板均使用有期限的隔离进程。暂停/恢复产生新的 generation，旧任务和过期目标结果不写入；恢复清空内容基线并重新观察。停止时等待有界 worker 退出，再关闭数据库。内容记录保留实际触发标签（`foreground_settled`、`periodic_visual_check`、`typing_pause`、`click`、`scroll_stop`、`periodic_monitor`），不再把所有显示器帧都伪装成周期帧。

采集完成时间记录在截图读回和遮罩完成之后、OCR 之前；OCR 耗时不会把截图时间向后推。2026-10-11 起，OCR 返回的正文和全部行／词框完整保存，父子进程不再施加 48 KiB 或字符前缀预算。原始 Observation 的正文、UIA、OCR 字段不再受旧 64 KiB 拒绝限制；摘要和查询的限额由组织层处理，完整证据保持不变。

### 常驻 WGC 与屏幕描边

参考 Screenpipe 的持久会话和按需读回设计：一个隐藏的产品 helper 使用 JSONL 接收取帧请求，每个显示器复用一个 WGC 会话，前台窗口模式只保留当前请求窗口的会话。帧到达时只保留最新 GPU 帧，不持续编码 PNG 或执行 OCR；收到请求后才进行 CPU 读回。窗口/显示器尺寸变化使用 `FramePool.Recreate`，不重启会话，避免周期取图反复触发捕获描边。会话最多 16 个，显示器移除、目标关闭和设备丢失在后续请求中清理或重建。

在 `StartCapture` 前尝试 `IsBorderRequired(false)`，不支持或失败均保留诊断，不阻止正常采集，也不弹出系统授权窗口。**这是尽力请求，不是所有 Windows 环境下的静默保证**：Windows 官方说明，无边框需要系统授权/能力；请求可能成功但仍被系统忽略，其他应用对同一目标要求边框时也可能继续显示。当前不自动修改系统设置、不调用授权弹窗，也不悄悄切换其他截图方式。

监控页「内容采集调度」的 `wgc_session` 和 `/api/status` 的 `sources.visual` 显示进程、会话数量、会话复用、帧序号、尺寸重建次数、CPU 读回次数及边框状态。每个保存帧的 `visual_summary.runtime` 也保留这些诊断。`border_status` 为 `disable_requested` / `unsupported` / `request_failed`；第一种仅表示请求已提交，不代表已验证无边框。

暂停、停止、agent 控制租约要求暂停内容及检测到不可用桌面时，释放 helper 和采集会话；恢复后首次获准请求才重新启动。取帧超时会强制结束 helper，下一次请求按需重建，不在同次请求里无限重试。父进程退出导致 stdin EOF 时，原生 helper 关闭全部会话。旧 helper 返回 `helper_protocol_mismatch_rebuild_required`，应重建而不是回退到闪烁的一次性捕获。

依据：[Screenpipe WGC 实现](https://github.com/screenpipe/screenpipe/blob/ac204c0b8e3499225f5d26bd4b4bef48ef818674/crates/screenpipe-screen/src/wgc_capture.rs)、[Windows IsBorderRequired 限制](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession.isborderrequired)。这里只参考设计与公开 API，没有复制 Screenpipe 源码。

前台窗口模式的内容帧与同轮 UIA 通过 `capture_group_id` 关联，触发事件通过 `caused_by` 引用。显示器帧有独立目标和 capture group，通过触发引用/时间关联，不伪造同步原子快照：窗口 UIA 与各显示器帧是顺序采集，有各自时间戳。

## 配置与资源

模板整体 `enabled: false`，启用后 `collection.default_level: content`，`ui_structure.content`、`input.text`、`input.clipboard`、`visual.enabled/persist_images/continuous` 为 true。旧 `collection.input_content/clipboard` 作为兼容 fallback；新 `input` 字段优先。独立关闭任一源不关闭其他源。

- UIA：完整遍历目标窗口声明范围内的树，正文使用无长度限制的 `GetText(-1)`（见 [Microsoft API 契约](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-gettext)）。旧 depth/nodes/max_text_chars 配置仍可加载，原始采集不应用其裁剪。`capture_scope` 声明 offscreen、密码和未知可见性／密码状态子树的排除；只保留被排除根节点的结构元数据。读取失败、provider 循环或父进程超时返回明确不可用诊断，不将成功前缀当作完整树。
- UIA 结构模式：`include_content=False` 只保存声明的角色、深度、离屏状态、矩形宽高及排除原因，不保存正文或窗口内坐标；完整性针对该结构字段范围，不代表 UIA 的所有可用属性都已采集。正文模式保存完整获准控件属性，包括坐标。
- 截图遮罩：排除最小化和 DWM cloaked 窗口，按 Z-order 扣除上层不透明窗口的客户端内部区域，只遮黑排除窗口可能露出的部分。透明、点击穿透、非矩形和未知 no-redirection 窗口不用于消除下层遮罩；普通 Chromium 主窗口的 DirectComposition 样式单独识别，避免 Edge/VS Code 被误判为透明。非客户端边缘保持保守遮罩。捕获前后都检查遮罩，混合 DPI 坐标转换使用临时线程 DPI 上下文并恢复。剧烈窗口移动/重排不是原子快照，仍需真实场景验证。
- 黑帧过滤：遮罩完成后在实际截图区域内按像素计算黑色比例。显示器区域超过 90%、前台窗口区域超过 80% 时，不执行 OCR、不保存图片，只留带 `mostly_black` 的不可用 `content_snapshot` 和诊断比例；因此小窗口按自己的面积判断，不会因它只占桌面一小块而天然被过滤。黑色阈值默认 RGB 最大值不超过 8，可配置；关闭 `drop_black_frames` 才会保留这类帧。
- OCR：父子进程固定 UTF-8，不依赖 Windows 控制台代码页。图片去重只看 PNG SHA-256，不看遮罩计数；OCR 失败仍保留首帧并单独有界重试。`ocr_result.caused_by` 指向原始 `content_snapshot`，后续消费者应沿此关系读取最新 OCR 结果，不覆盖原始事件。
- 输入／剪贴板：完整保留获准的控件文本、编辑差分及 Unicode 剪贴板正文；旧 max_chars 不截原文。剪贴板继续检查序号、Unicode 和终止符，读取失败保留诊断。此前被截断的输入基线保持未知。窗口标题及合法地址栏值也不再按旧 2048／4096 字符裁剪或拒绝。
- Worker deadline：UIA 5 秒、WGC 2.5 秒、OCR 2.5 秒、地址栏 1.2 秒、剪贴板 1.5 秒。原生 fixture 的 UIA 约 4 秒，较原结构采集需要更宽的隔离预算。
- 图片：默认最长边 1920、每分钟最多 120 次采集检查，保留 24 小时、上限 1024 MiB、至少 2048 MiB 剩余空间。达到配额先通过资产 tombstone 删除最老图像；超大单图不保存，低磁盘跳过截图并保留原因。观察默认保留 14 天。
- 监控：loopback/token 访问，默认脱敏 OCR、UIA 属性、输入和剪贴板正文；授权查看后提供文字和图片。暂停与关闭内容查看含义不同。

## 验证与限制

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_activity*.py" -v
.\.venv\Scripts\python.exe tools/verify_activity_content.py
.\.venv\Scripts\python.exe tools/verify_activity_monitor.py
```

第二个脚本只创建不抢焦点的自有窗口，验证真实隔离 UIA、中文 Value、密码过滤、WGC 非空像素、文字变化和尺寸变化后的进程/会话复用、按需读回、暂停退出/恢复重建、EOF 退出、Windows OCR，以及后台排除窗口遮挡和最小化遮罩，关闭后销毁；不采集用户应用正文。第三个脚本需要开发验证用 `playwright` 和 Edge，仅用合成数据库/图像检查 1320×900、1680×1000、390×844 的布局、正文显示开关及截图预览；截图在 `.artifacts/activity-ui-verification/`。

这是变化帧/证据时间线，不是视频录制：不保证每次显示变化、每个按键、瞬间被清空的输入框或每次滚动中间状态都完整留存。队列合并和预算会降低高负载时采样频率。缺少可访问性 Value/Text 的应用不能保证输入原文，OCR 只能补屏幕可见文字，不能还原已经消失的输入。剪贴板读取忙、非文本、序号过期均有明确状态；复制/剪切操作不直接读取旧剪贴板，正文由随后更新事件采集。当前没有富文本/图片剪贴板解码。

多显示器、锁屏/UAC、混合 DPI、应用 provider 崩溃有防护代码和模拟回归，尚未完成真实多屏/长期压力验证；不能把短时 fixture 验证当作全应用覆盖。无边框请求的视觉效果仍需用户实际运行核对，系统权限限制不会通过诊断伪装成成功。黑帧阈值是工程过滤规则，不等于“全黑画面一定无信息”；极暗主题或视频黑场可按配置关闭过滤。资源预算不是实时 CPU/GPU 限额。尚无 FTS/元素级索引、视频导出、音频、后台应用全文和产品宿主自动启停。

Windows 接口依据：[剪贴板更新通知](https://learn.microsoft.com/en-us/windows/win32/dataxchg/wm-clipboardupdate)、[UIA ValuePattern](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationvaluepattern)、[GetWindow Z-order](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindow)、[DWM 窗口属性](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)。Screenpipe 对照与剩余差距见 [对齐情况](activity-screenpipe-gap.md)。

Chromium 不透明主窗口的 no-redirection 兼容依据：[窗口样式计算及透明 popup 条件](https://chromium.googlesource.com/chromium/src/+/HEAD/ui/views/widget/widget_hwnd_utils.cc)。这是针对现有 Chromium 窗口约定的适配，不是 Windows 为任意 DirectComposition 窗口提供的不透明性保证。

## Developer browser

The collector monitor is for live capture diagnostics. To inspect an existing run across every data layer without starting collection, use the historical developer browser described in [Activity developer browser](activity-browser.md):

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli browse --config path\to\activity.yaml
```
