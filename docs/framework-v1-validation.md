# 微信提问、日历与定时能力验收

> 以下为各阶段的历史验收记录，不代表当前能力清单。旧工作 owner、工作页面及其专用验收脚本已移除；当前入口和边界见[框架第一版](framework-v1.md)与[手机会话说明](mobile-work-guide.md)。

日期：2026-10-29，Asia/Shanghai（+08:00）。对应 PN-0063、PN-0064。

- 微信及原生问题定向 Node 回归 112 项通过；真实 DSH、Python owner、本地模型和隔离 iLink 覆盖逐题直接回答、数字选择、桌面发起问题接手及权限/送达失效。没有发送真实微信消息。
- 隔离真实宿主与本地模型验证：北京时间明天 11 点会议产生 10 点提醒；桌面与微信写入同一会话、日程取消联动提醒；微信通过编号查看日程；到期提醒经过真实微信 outbox 的模拟服务并留下回执。桌面通道独立记录，headless Edge 注入模拟原生通知桥后验证实际 UI 触发与回执；未验证真实 Windows 通知展示或真实微信平台的长期主动投递。
- 正式构建页面在 headless Edge 中验证确认时区、日程展示和编辑、手工提醒按所设时区计算、480px 窄窗及渠道设置；时区/DST、修改冲突、旧数据库 schema 迁移、渠道绑定与取消栅栏有 Python 定向测试。
- 定时执行、保留页面和来源回归 51 项通过；真实 DSH 会话中验证逐次确认拒绝、持续信任执行、来源标记和回复结束后的消息提醒。定时任务能否完成取决于模型、权限及目标环境，回复结束不等于任务目标成功。微信尚不能创建定时执行任务。
- Framework Python 最终 263 项完成，1 项跳过；前端来源校验与正式构建通过。根 Python 全量此前运行 1150 项，3 项跳过、1 项旧 UFO worker 的 pythonw 启动诊断超时；该诊断单独复跑 4 项通过。最终时区冲突保护加入后只重跑相关 Framework 测试，不声称重新跑完全量；本轮没有修改或接入 UFO。
- 根据用户明确要求，确认默认产品 home 没有运行宿主后，先备份本地数据库再通过产品 owner 升级并保存 `Asia/Shanghai`；未修改其他私人设置或启动生产宿主。日志与截图保存在 ignored 的 `.artifacts/` 中。

---

# 工作会话与微信编号验收

日期：2026-10-29，Asia/Shanghai（+08:00）。对应 PN-0061、PN-0062。

- 隔离 Host、真实 DSH 和本地模型/iLink fixture 验证：已有对话上下文进入模型，由模型建立或更新同一会话的工作项，重复调用不产生重复工作；原有工作准备、材料和会话历史保留。微信能按编号进入原会话和工作并继续对话，远程权限及绑定失效后不能继续修改工作。
- 正式构建的页面在隔离环境和 headless Edge 验证从会话菜单加入工作、保留草稿、打开工作详情及返回原对话；手机页面另在 320、390 和 736px 完成操作检查。旧工作与多项关联不自动合并；加入列表不代表监测已经启动。
- 微信、工作和界面定向 Node 回归 139 项通过，原有伙伴与工作体验回归 37 项通过；Framework Python 252 项完成，其中 1 项因 Windows 符号链接权限跳过。前端来源校验及正式构建通过。
- 根 Python 全量运行 1139 项，3 项跳过，1 项旧 UFO worker 的 pythonw 启动诊断超时；该诊断模块单独复跑 4 项通过。本轮未重新运行最终全量，不将其记为全通过。本轮未修改或接入 UFO。
- 未发送真实微信消息、调用外部模型、修改用户配置或重启当前生产宿主；微信平台与实际模型的使用体验仍需在重启后验证。隔离测试日志和截图保存在 ignored 的 `.artifacts/work-conversation-check/` 与 `.artifacts/work-conversation-ui/`。

---

# 微信接收反馈、会话入口与设置排版补查

日期：2026-10-29，Asia/Shanghai（+08:00）。对应 PN-0060。

- 微信定向 Node 回归 90 项通过，覆盖实际接入后反馈、慢模型/慢投递、未知发送不重试、重复入站、结束与重启取消过期处理中提示，以及控制命令、审批与工作通知。接收反馈不写入模型历史，不改变任务权限。
- 真实 DSH、Python sidecar、本地模型与 iLink HTTP fixture 的端到端通过：桌面后台实验经微信查询和审批控制；模型响应被阻塞时仍先收到接收反馈；中文标题切换、新命名会话、原生问答、历史隔离及切回桌面原会话通过。新会话问答最初因测试前段已关闭远程控制而被真实权限拒绝，测试显式恢复该设置后验证，不放宽产品权限；文件结果读取等待真实命令结束，避免 Windows 写句柄竞态。
- 会话标题与短编号合并候选，由产品 owner 单次 SQLite 查询判断，避免动态分页漏掉同名项；内部接口未加入网页公开 RPC。Framework Python 242 项完成，1 项因 Windows 符号链接权限跳过，其余通过。
- 前端来源校验 2672 文件、正式构建 716 browser modules 通过。七个设置页完成默认 100% 检查，并在模型、外观、个性化和消息渠道补查 125% 文字大小、明暗主题、480px 窄窗、字体菜单和中英文混排：桌面控件默认 13px/32px、125% 为 16.25px/40px；隐藏标签不留额外间距，未见文字裁切、横向溢出或页面异常。两项既有浏览器/命令 UI 回归顺序运行通过。
- 本轮根 Node 非 GUI 全量初次 423 项中 421 通过，另外两项分别为上述新增问答测试权限不符和旧维护 UFO 的计时诊断超时；前者修正测试前置状态后真实端到端通过，后者独立 20 项复跑通过。没有宣称最终重新跑过根 Node 全量。日志和截图留在 ignored 的 `.artifacts/weixin-feedback/` 与 `.artifacts/settings-typography-review/`，不提交测试 home 或凭据。
- 未发送真实微信消息、修改用户模型配置/绑定或重启当前生产宿主。微信平台原生界面和资料定制的实际边界见[手机使用说明](mobile-work-guide.md)；本轮为文本交互增强，未实现媒体或输入状态协议。

---

# 标准 DSH base 与完整原生会话验收

日期：2026-10-28，Asia/Shanghai（+08:00）。对应 PN-0056；取代下方 PN-0055 的最小组合续接范围。

- 原生组合测试证明全部 96 个 base 声明由 DSH patch 引擎继承，产品仅覆盖有明确归属的设置并新增三项；实际挂载与关闭理由见 [原生 DSH 运行时](framework-native-dsh.md)。
- 真实 Host、DSH 与本地 HTTP 模型：普通停止后 SessionId/代次不变；此前的用户文字、工具结果、工具回合助手文字和 PNG 图片进入后续实际请求。重启后保留相同 Session，不自动重跑。用户分叉使用原生已完成前缀和继承标记，新会话真实模型请求包含原历史；重复请求只创建一次，撤回来源同时隔离相关分叉。
- 实际原生 write/read/edit 落盘并修改文件，原生 Windows pwsh 返回真实输出，原生 subagent 使用父模型配置调用本地模型并进入原生子代理目录；原生 write 拒绝审批时未产生文件。实际 token/压缩/工具结果裁剪生成压缩模型请求和 compaction/end，下一请求收到裁剪/压缩后的内容，小窗口配置亦通过。
- 隔离正常宿主与 headless Edge 使用正式构建的原页面：原生问题选择/提交进入模型工具续轮，计划开关与 exit_plan_mode 审核生效，手动原生压缩按钮完成，无页面错误。微信回环另验证完整问题投递、`/answer` 和同一模型回合继续；不是实体手机或真实腾讯服务验收。
- 根 Node 全部 382 项通过；Framework Python 236 项，其中 1 项因 Windows 符号链接权限跳过。根 Python 全量运行 1124 项，3 项跳过、1 项旧 UFO worker 启动诊断超时；对应诊断模块单独复跑 4 项通过。本轮未修改或重新接入 UFO。前端来源校验和正式构建通过；不把未运行的上游 workspace 测试当作通过。
- 用户私有配置、当前宿主和实验未修改或重启；没有调用真实外部模型。早期已分裂的历史代次不自动重写合并，保守文字续接不能恢复全部旧工具和附件。todo/goal/workflow 的原生挂载不等于已验收所有长期任务行为。

复现：

```powershell
node --test --test-concurrency=2 tests/test_*.mjs
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tools/verify_framework_native_interactions.mjs
# frontend/control-ui：
node scripts/verify-source.mjs
node scripts/build.mjs
```

---

# 会话上下文与 DSH 能力核对（历史阶段）

日期：2026-10-28，Asia/Shanghai（+08:00）。对应 PN-0055。

- 使用隔离 Host、真实 DSH 和本地模拟模型复现停止后换代，再检查实际模型请求：先前用户任务文字与已授权产品上下文到达模型；撤回来源后，相关历史文字和产品上下文均不再发送。历史界面仍只有一份原消息。
- 微信回环实测模型请求包含与当前远程控制设置一致的提示；源权限检查、私有接口边界和历史投影使用自动测试验证。Framework Python 230 项，其中 1 项因 Windows 符号链接权限跳过。
- 根 Node 测试共 370 项：并行与顺序两次全量各有一项隔离 Host 启动超时（分别为历史恢复和推理流），其余通过；两项分别独立复跑通过，超时原因未确定。上下文续接、来源撤回和微信远程控制的定向端到端测试通过。
- 跨代续接有意限于有界用户文字和无工具回合的助手文字，不携带附件、工具输出及工具回合的助手文字；尚不等同完整续接或长会话自动压缩。没有访问真实模型服务或重跑用户实验。实际内核能力与缺口见 [DSH 能力对照](dsh-capability-audit.md)。

---

# 会话操作权限验收

日期：2026-10-28，Asia/Shanghai（+08:00）。对应 PN-0054。

- 真实 Host、DSH 和本地模拟模型测试逐次确认、单会话信任、会话隔离、撤销、持续信任与重启后临时权限失效；微信回环另外验证继承关闭时确认、开启时自动执行以及关闭后恢复确认。
- 实际构建页面在隔离 Host 和 headless Edge 中验证会话开关、全局状态提示、会话切换和重启；无页面错误。未使用真实微信、外部模型或用户数据。
- 根 Node 回归 367 项通过；Framework Python 运行 229 项，其中 1 项因 Windows 符号链接权限跳过；前端来源校验及构建通过。设置不改变工作专用审批和其他未接入的能力边界。

---

# 微信同会话远程指挥验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0052 / PN-0053。

- 隔离真实 Host、DSH、SQLite、PowerShell、本地模型与回环 iLink：电脑会话启动后台实验，微信在同一会话以自然语言查询真实运行输出；微信远程控制关闭时拒绝停止，开启后原实验继续运行，微信可审阅并拒绝或批准停止、新命令。待审批时关闭开关后旧确认不能执行，两端消息均归属同一会话。
- 逐次审批测试覆盖绑定账号、身份和会话切换、完整分段、部分失败与未知回执；后台进程测试覆盖会话隔离、近期输出截断和撤销权限。远程浏览器使用独立本地 Edge 与测试网页；截图观察覆盖模拟提供方和权限检查。
- 微信、审批、浏览器、桌面观察及命令相关 Node 定向回归 96 项通过；Framework Python 运行 228 项，其中 1 项因 Windows 符号链接权限跳过。前端固定来源校验和正式构建通过。
- 本轮使用独立临时 home 与测试内核模型注入，不访问真实微信账号或外部模型，因此不将回环结果称为真实微信实收验收。配置界面经源码组件测试和正式构建；实体手机、微信实际网络以及多平台桌面动作未验证。

---

# 模型行动能力验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0051。

- 命令、进程、审批、浏览器生命周期与桌面观察的 Node 定向测试通过；真实 Host、DSH 和本地假模型验证了命令执行、错误会话不能批准、拒绝和取消，以及执行中停止后延迟写入不发生。
- 隔离 Host 与 headless Edge 验证实际聊天审批卡片展示完整命令，允许和拒绝分别产生预期结果，无页面脚本错误。浏览器工具在本机回环页面验证打开、读取、填写、点击和状态变化时拒绝动作。
- 在本机明确授权的测试中，截图经实际 Host 与视觉模型工具续轮进入本地假模型的图片负载；未向外部服务发送，也未保留截图。自动回归中的真实截图测试默认关闭，仅显式 opt-in 执行。
- 根 Python 全量运行 1115 项，3 项跳过、1 项旧 UFO 启动诊断超时；单独复跑对应诊断模块 4 项通过。用户当前产品未接入 UFO。Windows 键鼠单步操作的真实交互式桌面验收结果另行标明，不将模拟测试视为实机通过。
- 前端固定来源校验和构建通过。未调用真实外部模型、修改用户配置或发送微信消息；进程树停止不保证终止已脱离的进程。

---

# 多模型连接与会话选择验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0048。

- Node 全量 334 项通过；Framework Python 227 项运行，226 通过、1 项跳过；覆盖 schema 6 升级、会话模型选择、首次创建、重试、连接与模型引用保护，以及在途请求保护。
- 真实 Host、DSH 与回环假模型服务端到端测试，检查 HTTP 请求的实际模型：两个会话分别选择、回复期间切换并排队、重启恢复、图像支持与拒绝、创建幂等重试及在途配置保护。
- 使用真实构建页面与隔离本机 Host，验证先保存服务和凭据，再发现或手动添加首个模型；验证多模型增删改、刷新与凭据不回显。另验证两个会话选择不同连接的模型，切换会话和刷新后仍正确，手机及工作页面通过原有回归。
- 验收仅连接本地假服务，不调用外部模型或使用用户配置；服务商提供的模型能力是声明，实际视觉、推理与工具表现仍需对应服务验证。

---

# 跨端消息来源与会话命名验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0047。

- 完整 Node 331 项通过；前端源码校验与构建通过。完整 Python 1106 项运行中有 3 项跳过、1 项 UFO `pythonw.exe` 导入在 20 秒限时内未完成；该诊断单独复跑通过。会话元数据测试覆盖 schema 5 迁移、失败或未知提交、重启及手动改名竞争。
- 隔离 Host 与本机微信协议 fixture 的 12 组验收通过，覆盖同一 DSH 记录的电脑/微信来源、冷启动和微信新会话日期与首条文字自动命名；没有使用真实微信账号或发送外部消息。
- 隔离 Host 与 Edge 的手机/电脑页面验收通过，实际手机渠道消息实时进入打开的电脑会话、显示来源并在刷新后保留；同时核对了工作和配对的原有路径及页面截图。未验证实体手机、真实外部模型或真实微信端交互。
- 自动命名目前是首条文字摘录，并非调用模型生成语义标题；无法确认来源的旧消息不显示标签。

---

# 工作列表与原工作准备入口验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0046。

- 完整 Node **325 项通过**，Framework Python **211 项、210 通过、1 跳过**（Windows 符号链接权限）；覆盖工作及运行历史分页、多个审批、跨页去重、来源轮询、会话状态批量投影及权限边界。完整根 Python 本轮未重跑。
- 实际 Host、DSH、Python 和 Edge：**11 组链路、6 组页面通过**，包括真实正在回复的会话活动、实际来源 42/100、审批、执行/取消、成果、列表搜索/筛选/进入会话、手机偏好、480px 桌面与 320/390/736px 手机布局。工作准备按钮验证了跳转到保留的原工作页面；首次测试发现未切换到对话导致按钮无效，修复后复验通过。
- 实际 Windows WebView2 和 FrameworkShell **24 组通过、无失败**，包括从新工作列表进入原工作准备页的十个工作入口及原生菜单、伙伴窗口等既有回归。未发送真实微信消息、调用外部模型、采集桌面或使用生产数据。
- 前端源码校验通过，构建 715 个浏览器模块。搜索与统计针对已加载范围，列表和历史按页加载；观察轮询每轮有界，不对任意规模承诺固定刷新延迟。

```powershell
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
node --test --test-concurrency=4 tests/test_*.mjs
.\.venv\Scripts\python.exe -m unittest discover -s tests -p '*framework*.py' -v
.\.venv\Scripts\python.exe tools/verify_framework_mobile_work.py --browser
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
```

GUI 验证顺序运行；截图和日志位于忽略的 `.artifacts/`。未覆盖多屏 DPI、实体手机及真实外部模型质量。

---

# 微信作为当前手机主入口的验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0042。网页仍保留可选，使用微信不要求自建云服务器或网页可达。

- 完整 Node **316 项通过、0 跳过**，含真实 Python sidecar 的脚本日志、微信通知开关、跨路由拒绝、正文历史分页与旧 `main` 会话切换；日志 `.artifacts/weixin-primary-node.log`。
- `tools/verify_framework_weixin.py` **12 组通过**：启动真实构建 Host/DSH/Python，明确关闭可选网页服务，经本机 iLink fixture 登录/绑定、继续同一历史、文字菜单/历史、微信新建工作、审阅并批准实际脚本、查询实际日志、查询/开关通知且保留其他设置、停止/重连/重启/解绑。没有外部微信消息或模型调用；报告 `.artifacts/weixin-primary-acceptance.json`。
- 真实 Edge **9 组通过、无 pageerror**：实际原前端，微信协议 RPC 为 fixture；增加无需云/网页、可选网页、历史/工作命令文案验证，保留扫码、确认、会话、断线和 480px 布局。报告 `.artifacts/weixin-primary-browser/report.json`。源码核对与 715 模块构建通过。
- 本轮不修改 Python 产品行为或 schema，未重复执行根 Python 全量；下方上一轮 1095 项结果保留。当前用户账号的新命令尚未实发验收，不将本机协议 fixture 当作真实微信实收证明。

```powershell
node --test --test-concurrency=4 tests/test_*.mjs
.\.venv\Scripts\python.exe tools/verify_framework_weixin.py
# 对独立 --test-mode --home 宿主，顺序运行 GUI：
node tools/verify_framework_weixin_browser.mjs <隔离home/runtime.json>
```

---

# 手机工作与会话第一版验收

日期：2026-10-27，Asia/Shanghai（+08:00）。对应 PN-0040、PN-0041；下方记录保留为历史。实际使用与未覆盖边界见[手机工作说明](mobile-work-guide.md)。

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 完整 Python | 1095 项，1092 通过、3 跳过 | schema 1–4 升级到 5、真实受监督脚本、固定审批字节、进程树取消、产物、来源撤权与通知归属；跳过包括当前 Windows 符号链接权限，另有真实 Junction 防越权验证通过 |
| 完整 Node | 305 项通过，0 跳过 | `--test-concurrency=4`；手机隔离服务、配对/撤销、呈现偏好、同一会话、微信完整审批、绑定换代去重与侧栏支持列表 |
| 真实 Host / DSH / Python | 10 组通过 | 独立回环手机入口、工作来源、偏好分层、会话接续、桌面准备后手机批准并执行实际脚本、日志/成果、进程取消、收件箱、冷重启与撤销 |
| 真实浏览器 | 6 组通过，无 pageerror | 桌面配对、手机工作/状态/脚本/下载、会话发送/改名/归档/恢复、用户与设备设置、320/390/736px 布局与深色主题；实际构建和真实隔离后端，无页面 API mock |
| 原功能回归 | 18 组通过，无 pageerror | 原聊天、真实材料与成果、历史刷新、取消/重启、Skill、MCP、可信插件、模型桥及本机收件箱；含原 Files 与历史的真实浏览器验收 |

真实脚本使用隔离 home 内的临时文件。未调用外部模型、登录或发送真实微信消息；新微信命令由实际 Python sidecar 与本机协议 fixture 验证，既有账号文字往返仍以下方用户验收为依据。私人 HTTPS 转发、实体手机、移动网络和微信内置浏览器未验收；本机预览不能代替这些结果。QQ、媒体与桌面接管没有接入。

离线与浏览器验收后，确认原正常 home 没有活动 owner，先用 SQLite backup 留存 schema 4，再启动新版正常桌面宿主。实际升级到 schema 5，已有会话数与微信绑定/启用/目标会话均保持，独立手机服务就绪；未新增演示工作、改动已有模型连接或发送微信测试消息。备份只保留在本机忽略的 `.artifacts/`。

完整 Python 本轮通过了下方曾超时的既有 UFO 导入测试，未改动该测试或接入 UFO。Node 首次无限制文件并发有一次旧会话组织测试的 sidecar 启动失败；原断言单项复验通过，限制 4 文件并发后完整 305 项通过，未放宽断言。最终日志为 `.artifacts/mobile-work-python-final.log`、`.artifacts/mobile-work-node-final.log`。

```powershell
.\.venv\Scripts\python.exe tools/verify_framework_mobile_work.py --browser
.\.venv\Scripts\python.exe tools/verify_framework.py --browser-files --browser-history
node --test --test-concurrency=4 tests/test_*.mjs
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

GUI 顺序运行，报告和截图留在忽略的 `.artifacts/`，不提交配对码、凭据或用户数据。

---

# 微信文字入口第一版验收

日期：2026-10-26，Asia/Shanghai。对应 PN-0034；以下同日记录保留为历史。

- 新增微信 API 12、渠道生命周期 19、实际工具边界 6、UI 类行为 9，共 46 项 Node 定向测试通过；全仓 `test_*.mjs` 252 项通过，其中 framework 228 项。Python 微信持久化 17 项通过，含 schema 1/2/3 升级、当前 Windows 用户 DPAPI、密文损坏/跨机器解密失败降级、去重和原子投递。
- `tools/verify_framework_weixin.py` 启动真实构建 Host、Python sidecar、原生 DSH 与显式离线模型，经本机 HTTP iLink fixture 完成 9 组验收：扫码与本机确认、同一 transcript、陌生人/重复输入、会话管理、停止、重连、冷重启、解绑。报告 `.artifacts/framework-weixin.json`。该模型仅生成文字；强制工具写操作拒绝由真实注册的工具 hook 单测覆盖，不称作真实模型对抗测试。
- `tools/verify_framework_weixin_browser.mjs` 在独立 test-mode Host 上以 RPC fixture 驱动真实原前端与 Edge，8 组场景通过：本机二维码 Canvas、扫码/验证码/显式确认、切换与新建会话、暂停/重连/解绑、失效与未知发送、重复点击、陈旧响应、轮询/离页及 480px 窄屏。报告 `.artifacts/framework-weixin-browser/report.json`，截图已检查，无 pageerror。前端构建 709 模块与源码验证通过。
- 原 `tools/verify_framework.py --browser-files --browser-history` 18 组回环与真实浏览器回归通过，覆盖原生工具写入、材料成果、历史、取消/重启、Skill、脚本、MCP、可信插件和本机收件箱；微信只读边界没有改变桌面原有工具能力。
- 全产品 Python：1069 项，1066 通过、2 跳过、1 失败。唯一失败为既有 `test_ufo_diagnostics.StartupDiagnosticsTests.test_real_worker_imports_with_idle_control_pipe_and_receives_cancel` 的 `pythonw.exe` session_import 20 秒超时，未修改或放宽该测试。日志 `.artifacts/weixin-python-tests.log`。

前述自动化验收未登录真实微信账号或发送微信消息。随后在当前用户正常 Mirally home 启动新版，真实请求二维码；用户手机扫码并明确确认后完成本机绑定。用户发送测试文字并确认收到回复，产品库对应 1 条 completed 入站与 1 条 sent 出站，使用已有模型连接；未打印或提交私人消息及凭据。该结果证明本账号一次文字往返，不证明其他账号、长期推送窗口或全部工具/媒体能力。

用户反馈微信授权页显示 OpenClaw。核对固定官方协议与登录请求后，未发现可用的应用显示名参数；观测字段 `bot_agent` 不控制授权文案。Mirally 扫码页增加说明，保留平台名称，不改变当前 DSH 主链。功能使用与当前边界见[框架第一版](framework-v1.md#微信文字私聊第一版)。

# 侧边返回主窗口黑色底板定位与验收

日期：2026-10-26，Asia/Shanghai。对应 PN-0030；以下同日记录保留为历史。

真实 WebView2（Runtime `155.0.4283.62`）对原生代码基线`e981daf2`与修复版分别记录恢复过程：旧版在释放Region时，Form为黑色、WebView背景alpha为0、全窗口glass仍开启；随后恢复的`#0d1520`又与实际页面背景不一致。修复版在释放Region前，浅色`#f7f9fc`与深色`#111923`均已经是Form/WebView一致的opaque底色，glass关闭，Bounds也已恢复完整工作空间。

这是实际窗口的逐阶段状态证据。基线只替换原生方法，前端保持当前构建，因此不能当作完整旧版逐帧回放；Python逐行trace会改变耗时，也不能拿时间差当作黑闪持续时间。自身HWND像素校准未通过：两种受控DOM色块都只读到宿主底色，说明这一DC读法没有观测到WebView合成内容。已拒绝将该读数作为黑帧或视觉消除证明，未截取用户桌面。

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| Framework Python | 164项通过 | 含实际JS函数的页面先切模式再发消息、9组RGB/rgba/透明回退、原生菜单共享转换及脚本不可用后备 |
| Companion Python | 10项通过 | 6项原生裁剪及4项新增底板测试：任意frame/resize/unclip前opaque、合法RGB校验、浅/深颜色、缺字段使用dock缓存 |
| Node定向 | 61项通过，0跳过 | 原生/浏览器桥、原客户端和隔离Host往返、材料与会话归属 |
| 真实WebView2 | 25组通过，0失败 | 原24组窗口/焦点/提示/截图偏好/完整退出，以及新增浅/深背景时序基线对照 |

本轮不使用固定sleep、隐藏窗口或双rAF作为绘制修复；导航仍保留原rAF回调，但不将其称为GPU帧确认。没有修改用户配置、外部模型或UFO。未做实际桌面录屏、逐帧DWM画面测量、多屏/DPI切换或全新安装；根Python与Node全量本轮未重跑，上一节结果保持原记录。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p '*framework*.py' -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_companion*.py' -q
node --test tests/test_framework_legacy_native_bridge.mjs tests/test_framework_original_client.mjs
.\.venv\Scripts\python.exe tools/verify_framework_shell.py --workspace-paint-probe --workspace-paint-baseline-ref e981daf2
```

GUI顺序执行；基线对照需要Git和对应历史提交，不修改工作树。源码与设计见[伙伴交互](companion-interaction.md)。

---

# 提示越界与截图显示开关验收

日期：2026-10-26，Asia/Shanghai。对应 PN-0029，后面的同日侧边交互验收保留为历史。

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 原前端源码核对/构建 | 通过 | 2672上游文件、55接缝、20locale；708 browser modules，无未捆绑import |
| Node 完整回归 | 443项通过，0跳过 | `--test-concurrency=4`，含布尔偏好保存/拒绝非法值、Host通知、真实浏览器shim和原生桥包装、连接恢复及唯一SSE |
| Framework Python + 裁剪 | 162 + 6项通过 | 截图偏好读取/同源刷新、共享壳与原生恢复；第三提示part独立Union，隐藏后释放，不填充透明间隙 |
| 伙伴真实DOM | 13组通过，无pageerror | 原12组及body提示、aria-describedby、移入阅读/返入、首Esc关闭提示、次Esc收起、400/267/235px视口 |
| 实际产品页面 | 通过，无pageerror | 设置开关缺省开启→关闭→API落盘→完整刷新仍关闭→重新开启；原消息、提醒、记忆、Activity确认、Skill/MCP及窄布局回归 |
| 实际 WebView2 | 24组通过，0失败 | 原21组及pet compact两part/快速面板三part提示、9个可见Region采样、窄视口不遮触点、间隙穿透、隐藏/切view释放、固定Bounds；真实系统affinity即时切换 |

原生捕获排除通过独立HTTP调用保存偏好，再由Host SSE→现有客户端→同源原生桥自动生效，工具没有直接调用刷新方法。`GetWindowDisplayAffinity`观测到：缺省伙伴为`0x11`，关闭后已展开伙伴为`0`，重新进入仍为`0`，开启后为`0x11`，主窗口为`0`。没有实际截取桌面或运行外部录屏软件，不能将系统标志通过当成所有采集软件已实测。

默认并发Node首轮有1项原会话组织用例在第二次Host启动时sidecar退出；保持原断言单项复验通过后，采用4文件并发完整重跑443项通过。没有改超时、跳过用例或放宽该断言。根Python全套本轮未重跑，旧UFO路线的历史超时保持原记录。

GUI顺序运行，只用隔离test-mode Host、自身窗口、合成数据与本机API，未改用户配置或采集桌面。物理屏幕/多DPI切换与外部采集软件矩阵未覆盖。复现沿用下节命令，Node完整命令增加`--test-concurrency=4`；产品设置页由`tools/verify_framework_product_pages.mjs <隔离home/runtime.json>`验证。设计及官方依据见[伙伴交互](companion-interaction.md)。

---

# 侧边伙伴交互与窗口性能验收

日期：2026-10-26，Asia/Shanghai。对应 PN-0028 与[伙伴交互设计及官方调研来源](companion-interaction.md)。本节为本轮最终源码结果，下方迁移验收保留为历史。

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| Node 完整回归 | 440 项通过，0 跳过 | 原会话、材料、工具、模型与产品模块；全部离线或本机回环 |
| Framework Python | 159 项通过 | 原153项及新增6项最小化恢复、迟到回调与owner隔离；另有5项物理像素裁剪/资源释放测试通过 |
| 伙伴真实 DOM | 12 组通过，无 pageerror | 停留/掠过/离开宽限、固定/选择/键盘、草稿材料、桌宠点击/拖动、更多、触屏、减少动态效果；实际本仓 JS/CSS/资源，native bridge与会话状态为fixture |
| 原菜单与进展展示 | 10 + 6 组通过，无 pageerror | 三形态右键、编辑选择的原生菜单归属、几何变化后的误收起、235/267/400px布局、同会话当前轮状态与唯一回复通知 |
| 实际 WebView2 | 21 组通过，0 失败 | 真Host/原前端/FrameworkShell、真实Region命中、三形态SC_MINIMIZE→SC_RESTORE与托盘恢复、normal/maximized主窗口恢复及完整退出；选择阅读保护与真实hasFocus失焦收起 |
| 完整产品页面 | 通过，无 pageerror | 隔离test-mode Host：原水獭、十工作入口/四输出、真实材料与本机会话、聊天草稿、三形态和造型刷新、Activity/Memory、原深浅主题；无桌面采集或外部API |

悬浮展开的 DOM 与实际 WebView2 均观测到 **1 条 shape 消息**；静止重复同步为0，8次目标切换为8条。验证目标布局稳定和消息有界，不承诺机器固定FPS。原生工具的rAF记录包含故意最小化后的后台节流，最大间隔不能当成展开动画长帧。

触屏检查实际确认按钮高度至少40px，400/267/235px宽、最短350px高下的三形态detail/quick/menu无横向滚动需求、关键按钮可命中、裁剪在视口内。极窄或短面板允许纵向滚动。真实窗口检查还记录恢复后0/100/300/600ms的Bounds/ClientSize，防止只在瞬间通过；修复前稳定复现的边框重复计入尺寸已消除。

原生hover使用同一WebView2的CDP可信输入，按钮/右键和任务栏restore使用自己的HWND消息，托盘用真实NotifyIcon回调；未移动系统指针。GUI顺序执行，只操作隔离数据和测试窗口。未覆盖物理通知区命中、多屏DPI切换、全新安装与长期实际使用。根Python全套本轮未重跑，上一轮旧UFO导入超时记录保持不变；本轮未修改或接入UFO。

复现（GUI顺序执行）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p '*framework*.py' -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_companion_geometry.py' -v
node tools/verify_companion_interaction.mjs
node tools/verify_companion_menu.mjs
node tools/verify_companion_status.mjs
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
# 对自己创建并已配置本机fixture模型的 --test-mode --home 隔离Host：
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
```

完整产品页面使用临时home和显式本机fixture连接；test-mode不替用户自动配置服务商。临时日志、截图、配置与构建产物不提交。

---

# 迁移收尾与单一框架验收

日期：2026-10-26，Asia/Shanghai。对应 PN-0027 和[迁移收尾](framework-migration-closure.md)。此前记录保留为历史；下表为本轮最终源码的验证范围。

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 固定来源 / 正式前端构建 | 2672 文件、55 个登记维护文件、20 catalog；708 浏览器模块通过 | 保留原组件和视觉，无未打包 import；未宣称全量 TypeScript 类型检查 |
| Node 完整回归 | 440 项通过，0 跳过 | 含 CJS 原体验、原客户端、真实模型路由/冷启/工具、历史、授权范围与取消；全部为本机回环或离线 |
| Framework Python 定向 | 153 项通过 | Schema 1/2→3、元数据并发、旧记录迁移独立归档、文件授权/联接拒绝、模型连接/路由、共享 Shell |
| 根 Python 全套 | 本轮中途 1021 项：1018 通过、2 跳过、1 失败 | 失败仍为历史 UFO 路线 pythonw.exe 的原生导入超过 20 秒，与此前已记录问题一致；未改断言、跳过或修改 UFO 内核。之后新增的能力测试由上述最终 Framework 定向覆盖 |
| 会话组织真实页面 | 通过，无 pageerror | 原侧栏与标题改名、固定与刷新、并发改名冲突保留草稿、归档→Undo、筛选→恢复、native身份不变 |
| 历史与真实附件页面 | 通过，无 pageerror | 普通 Host / PiAI / 本机provider，实际图片像素、文件字节与工具参数/结果；刷新和全新浏览器保持同一回执、一张工具卡。完整冷启与多代次在 Node 实际 Host 验收通过 |
| 模型连接真实页面与路由 | 通过，无 pageerror | 新建连接/独立key/选中连接probe、background改派、使用中禁删/确认删除、保存重载；双本机provider验证主Agent与后台桥各走选定连接，图像不支持在入队和网络前拒绝 |
| 旧档案与访问范围页面 | 通过，无 pageerror | 实际索引、30条分页、文字转义、复制归档、移走原文件后刷新仍可读、不创建新会话；目录授权、独立开关、保存/刷新及480px布局 |
| 原聊天/工作/伙伴/产品页 | 通过，无 pageerror | Markdown/代码、两轮/刷新、材料/纯附件/草稿、十工作起点/四输出、三形态/造型/主题、Activity/Memory、偏好/提醒/收件箱/扩展和丢响应重试 |
| 原生端到端与 Files | 通过 | 实际DSH/PiAI/SQLite/MCP/脚本/插件、停止、冷启、失败owner、真实成果预览/字节下载及文件授权重试；默认不加载 UFO |
| 实际 WebView2 | 17 组通过，0 失败 | 开屏首绘、同HWND、三形态裁剪/返回尺寸和位置、最大化/最小化/托盘与明确退出；未采集桌面 |
| DeepSeek 网页服务实际请求 | Search / Fetch 各1次通过 | 所存连接的凭据副本，只发公开查询和官方文档URL；搜索返回真实来源，正文4250字符。为实际服务适配器验证，未宣称全部网页/供应商或长期成本通过 |

回归发现并修复：新会话页不能因恢复 sessions.patch 元数据操作就展示未实现的旧权限模式；旧组件会将斜杠附件URL误解为旧宿主本机文件，需要在自有客户端把确切原生回执路由映射为当前Host同源HTTP资源。地址每次读取重建，冷启换端口不保留旧连接地址。没有放宽媒体路径或开放任意旧文件读取。

本轮 GUI 顺序运行，只操作隔离 Host、自身 WebView2 和 Edge。没有更改用户原配置或采集桌面。真实网页验证只有两次公开请求，临时凭据副本已删除，日志/截图/私有测试home不提交。根全套的旧 UFO 失败保留；当前新框架不加载该执行器。未覆盖全新Windows安装、签名发布、多屏/DPI物理切换、全部外部供应商、语音/跨设备与长期陪伴质量。

复现（GUI顺序执行）：

```powershell
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
.\.venv\Scripts\python.exe -m unittest discover -s tests -p '*framework*.py' -q
node --test tests/test_*.mjs tests/test_*.cjs integrations/openclaw/*.test.mjs kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools/verify_framework.py --browser-files --browser-history
.\.venv\Scripts\python.exe tools/verify_framework_model_connections.py
node tools/verify_framework_model_settings.mjs
node tools/verify_framework_archive_browser.mjs
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
# 对自己创建的 --test-mode --home 隔离宿主再顺序运行：
node tools/verify_framework_session_organization.mjs <隔离home/runtime.json>
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
node tools/verify_framework_product_pages.mjs <隔离home/runtime.json>
```

`verify_framework.py --browser-history` 使用本机合成provider驱动真实原生链和界面；直接对已配置外部模型运行 history_browser 的 `--live` 才会消耗该服务用量。完整历史读取本身不调用模型或重执行工具。

---

# 伙伴还原、模型能力与体验迁移验收

日期：2026-10-25，Asia/Shanghai。对应用户的侧边返回、DeepSeek 连接能力和旧功能迁移反馈。本节为当前结果，下方同日品牌与退出结果作为上一轮记录保留。

| 验证 | 本次结果 | 范围 |
| --- | --- | --- |
| 固定来源与正式构建 | 2672 文件 / 45 个登记维护文件 / 20 个 catalog；708 个浏览器模块通过 | 无未打包 import，原视觉与组件继续复用 |
| Node 完整回归 | 269 项通过，0 跳过 | 包含实际 PiAI HTTP 推理字段/工具回放、模型发现响应体中断/超限、真实 Host 的推理 SSE/历史与工具投影 |
| Framework Python 定向 | 127 项通过 | 包含 Shell 38 项与模型设置字段验证；共享壳旧维护路径另有 68 项通过 |
| 实际 WebView2 | 17 组通过 | 真实伙伴返回按钮，rail/badge/pet：自定义正常大小/位置、移动、最小化和托盘后返回；最大化进入后移动，再返回及还原正常 RestoreBounds；显式退出清理 |
| 模型设置真实 Edge | 通过，无 pageerror | 实际普通 Host / PiAI + 本机服务，预设/保存/密钥清空、列表与能力采用、同项采用、dirty禁检查、off/default/low重载、刷新/保存清旧探测状态、文字成功/503失败/401列表失败、窄屏 |
| DeepSeek 真实服务与产品链路 | 直连 7 个、Mirally 14 个场景通过 | Flash 两协议的文字、图片、推理档位和工具续轮；实际成果下载核对字节。细节与供应商范围见[实测记录](deepseek-compatibility.md) |
| DeepSeek 实际页面 | 通过，无 pageerror | 用户菜单开启推理，流式期间实际绘制 9 次，最终与刷新后可见且无重复消息；工具卡展开显示实际空提醒列表 |
| 原聊天、工作与伙伴回归 | 通过，无 pageerror | 原 Markdown/代码复制、材料批量/移除/纯附件、两轮/刷新/草稿/窄屏；十个工作起点/四种交付、水獭来访、三形态与造型持久、Activity/Memory、原深浅色切换 |

原生几何问题的根因为提前恢复 WS_MAXIMIZE 样式位，导致窗口状态已标最大化但实际仍是伙伴 Bounds；现在由 WindowState 执行真实状态转换。验收同时检查状态、实际 Bounds 与 RestoreBounds，不能只检查 DOM 或同 HWND。

真实 API 由本次用户明确授权，使用已保存凭据的独立模型配置副本；只发送测试文字和自制几何 PNG，文件与会话仅进入隔离 home，没有改写用户原模型配置或发送个人历史/桌面/记忆。GUI 顺序运行，本机 fixture 与真实供应商验证分别记载。临时数据、日志、截图、凭据与构建产物不提交。

尚待迁移的会话组织、完整历史材料/工具块、旧索引入口、多模型与基础工具见[体验对照](framework-experience-migration.md)。根 Python 全套及全新 Windows 安装本轮未重跑；完整 TypeScript 检查仍不作通过项，未通过调整上游断言隐藏旧 UFO 超时或声明缺失。窗口未覆盖多显示器/DPI切换及通知区物理指针命中；供应商独立 Files/Search/FIM 等不属于上述对话链路通过范围。

关键复现：

```powershell
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
node tools/verify_framework_model_settings.mjs
node --test tests/test_framework_model_routing.mjs tests/test_framework_reasoning_stream.mjs
# 仅对自己准备、已配置推理模型的独立测试 home，真实服务会产生 API 用量：
node tools/verify_framework_reasoning_browser.mjs <隔离home/runtime.json> --live
```

---

# Mirally 窗口生命周期与首次品牌验收

日期：2026-10-25，Asia/Shanghai。对应 PN-0023 / PN-0024：关闭主窗口可选择托盘或退出，明确退出收尾真实后台；默认产品的空配置、重连和失败状态使用 Mirally 标识。下方 2026-10-24 及更早章节保留历史结果。

| 验证 | 本次结果 | 范围 |
| --- | --- | --- |
| 固定来源校验 | 2672 文件、45 个有理由登记的维护文件、20 个原语言 catalog 通过 | 静态资源和源组件直接维护；保留来源与许可 |
| 正式构建 | 708 个浏览器模块通过 | 无未打包 import；包含本轮权限菜单补漏 |
| Node 完整回归 | 233 项通过，0 跳过 | 新增旧权限模式在网络、材料上传和会话创建前拒绝；普通会话与真实逐条文件授权保留 |
| Framework Python 定向 | 125 项通过，其中 Shell 37 项 | 关闭偏好、托盘恢复、幂等清理、启动/重试迟到 owner、复用进程退出屏障与句柄释放 |
| 首次品牌真实浏览器 | 通过 | 禁用 JavaScript 时的标题/HTML、manifest/图标；空配置 M 插画、真实模型连接入口；关闭偏好默认/保存/刷新/非法值拒绝；深浅色与运行时图标；无效凭据重连、实际模块加载失败回退 |
| 原聊天真实浏览器 | 通过，无 pageerror | 模型保存/密钥清空、新旧会话不暴露未实现的权限模式、Markdown/代码复制、流式回执、刷新/两轮、批量附件/移除/纯附件发送、草稿和窄屏 |
| 原工作与伙伴真实浏览器 | 通过，无 pageerror | 新对话水獭实际资源和来访；十个工作起点/四种输出、真实材料与草稿保留、三形态、造型/形态持久化、Activity/Memory 页面、原深浅色切换与刷新 |
| 实际 WebView2：默认托盘 | 11 组通过 | 同 HWND，三种伙伴形态及最小化的单击恢复，托盘明确退出 |
| 实际 WebView2：关闭即退出 | 10 组通过 | 真实 WM_CLOSE、窗口/托盘清理、进程退出、日志/句柄释放与运行记录退役 |
| 实际 WebView2：复用后台后页面退出 | 11 组通过 | 真实关于页按钮；提前保留只读进程句柄，验证具体后台进程已退出，不能仅以运行记录消失代替 |

复用后台验收发现运行记录先于 Node 进程最终结束，已修复为等待该具体进程的只读同步句柄。上述退出结果均在验收器的应急清理之前断言；未以强制清理掩盖失败。启动中关闭与迟到复用 owner 的边界本轮由针对性线程单测验证。

GUI 顺序运行，只使用独立测试 home、本机确定性模型、自身 WebView2 HWND 和隔离 Edge。托盘恢复调用真实 NotifyIcon MouseClick 事件，尚未覆盖通知区物理指针命中、跨屏或 DPI 变化。没有桌面采集、外部模型调用或私有配置写入；测试数据、凭据、日志、截图和构建产物不提交。

本轮根 Python 全套未重跑；下方历史记录中的旧 UFO 导入超时仍保留。正式构建与实际交互通过不等于完整 TypeScript 检查通过，上游 workspace/生成声明边界未在本轮解决。未做全新 Windows 安装、签名打包或真实供应商在线验证。

复现本轮窗口和品牌检查（顺序执行，品牌脚本需尚未配置模型的独立测试 home）：

```powershell
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
.\.venv\Scripts\python.exe -m unittest discover -s tests -p '*framework*.py' -q
node --test tests/test_*.mjs integrations/openclaw/*.test.mjs kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
.\.venv\Scripts\python.exe tools/verify_framework_shell.py --close-exit
.\.venv\Scripts\python.exe tools/verify_framework_shell.py --page-quit --reuse-host
node tools/verify_framework_branding.mjs <空配置隔离home/runtime.json>
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
```

---

# Mirally 内容与导航重构验收

日期：2026-10-24，Asia/Shanghai。依据用户在 PN-0022 的确认，保留原视觉、布局习惯、组件、工作和伙伴，导航及页面内容改为 Mirally 自有产品。实现映射见[前端说明](mirally-frontend.md)。

| 验证 | 本次结果 | 范围 |
| --- | --- | --- |
| 固定来源校验 | 2672 文件、19 个有理由登记的维护文件、20 个原语言 catalog 通过 | 原样式及未修改文件继续核验；本地修改理由在 `local-changes.json` |
| 正式构建 | 713 个浏览器模块检查通过 | 无未打包 import，原子发布；旧后台页面不再装配到默认导航 |
| Node 完整回归 | 232 项全部通过，0 跳过 | 传输、原对话、材料、扩展与维护路线；分叉/错误伙伴请求在写入前拒绝 |
| Framework Python 定向 | 107 项通过 | 包含新增 6 项真实 MemoryStore 创建重放/冲突/撤回/重启回归 |
| 原聊天浏览器回归 | 通过，无 pageerror | 自有模型连接保存/密钥清空、原 Markdown/代码复制、刷新/两轮、附件首发/移除、会话草稿和窄屏 |
| 新产品页面 | 通过，无 pageerror | 真实导航/设置、偏好保存/撤回/同名重新启用、提醒响应丢失后刷新重试去重/取消、入箱/已读/接回原会话、记忆响应丢失后刷新去重及即时列表刷新 |
| 扩展页面 | 通过，无 pageerror | 实际 Skill 配置/读取/停用；真实 MCP stdio 服务通过页面配置、目录、资源读取、提示参数/正文、停用；未调用外部服务 |
| 原工作与伙伴 | 通过，无 pageerror | 十个工作起点/四种输出、材料与草稿、三形态/造型持久化、原 Activity/Memory 浏览、原深浅色切换及刷新 |
| 实际 WebView2 | 11 组通过 | 开屏、同 HWND、裁剪/置顶/菜单、导航到新产品路径、桌宠、托盘与退出 |
| 原生端到端及文件浏览器 | 18 组通过 | 实际 DSH/PiAI、工具、文件成果预览下载、附件、取消/重启、Skill/MCP/插件、主动候选、sidecar 故障与清理 |

GUI 顺序运行，只使用测试 home、回环模型/stdio fixture 和自身窗口；没有采集用户桌面、调用外部模型或改写私有配置。截图、日志、测试数据和凭据未提交。

全量 TypeScript 仍受固定上游 workspace/生成声明缺失影响，不能称全量类型检查通过；新 `pages/mirally/` 文件在检查中没有诊断。根 Python 全套本轮未重跑；下方上一轮的 979 项记录包含 1 项旧 UFO 导入超时，仍保留为未解决边界。新默认链路不接入 UFO。

本次没有插件市场、独立脚本执行页、跨设备/系统通知、语音或长期陪伴质量验收。扩展页展示真实协议目录并读取说明/资源/提示，工具行动继续走现有会话与授权；模型连接保存不等同于供应商在线验证。新内容页正文当前为中文。

---

# 原前端恢复验收（本次内容重构前）

日期：2026-10-24，Asia/Shanghai。分支：`codex/mirally-framework-refactor`。本节保留前一轮恢复原页面的历史结果，不代替上方当前导航与内容重构验收；下方 2026-10-23 内容为初版历史。

| 验证 | 当前结果 | 范围 |
| --- | --- | --- |
| 原前端来源 | 2672 个固定提交文件、7 处注明的非视觉接缝、20 种原语言 catalog 校验通过 | `2026.9.6` / `eb377ac59e6c9fd6c7705028034812becf00271b`；保留许可证和来源清单 |
| 原前端构建 | 878 个浏览器 JS 模块检查通过 | 无未打包裸 import；独立锁文件与原子发布，非全量 TypeScript 类型检查 |
| Node 完整回归 | 231 项全部通过，0 跳过 | 原客户端、收据和运行归属、网络丢响应、原 UI 工具归约、扩展/材料/维护路线 |
| Framework Python 定向 | 101 项通过 | 产品持久化、配置、路径、安装、宿主与原生 Shell |
| 根 Python 全套 | 979 项：976 通过、2 跳过、1 失败 | 唯一失败为旧维护路线 `pythonw.exe` UFO 导入超过 20 秒；没有放宽断言或修改 UFO 内核来隐藏失败。新默认框架不加载 UFO |
| 原生产品端到端 | 17 组通过 | 实际 DSH、PiAI、工具/材料/图片/文件、取消、冷重启、真实 MCP stdio、Skill/脚本、插件、主动入箱、sidecar 故障与退出 |
| 原浏览器页面 | 通过，无 pageerror | 从空配置首次保存模型即可发消息；原聊天布局、Markdown/代码及复制、两轮/刷新、首轮附件批量添加与移除、纯附件发送、草稿归属、480px 布局 |
| 原工作、伙伴与设置页 | 通过，无 pageerror | 十个工作起点/四种输出，真实材料和同一 native 会话、草稿保留、三形态、造型持久、Activity/Memory、深色切换及刷新 |
| 原 Files 与成果 | 通过，无 pageerror | 真正工具产生的文件回执、原侧栏文件/成果两类预览、有界原始字节下载；发送失败后刷新重试保留同一命令 ID 和文件授权；localhost 来源也能挂载原呈现 |
| 实际 WebView2/Shell | 11 组通过 | 开屏首绘、同 HWND、图标、原生菜单、三形态 Region/置顶、右键、返回工作台、托盘/退出；确认不登记 UFO 输入桥 |
| 无 UFO 默认链路 | 3 项通过 | Node/Python 导入拦截下真实启动、对话、关闭；相关 RPC 拒绝、专用旧模型别名 404、无 actions 目录、无相关模型工具 |

本轮 GUI 顺序运行，只操作隔离 Host 和自身 WebView2 窗口；没有采集开发者桌面、操作用户应用或调用外部模型。代码复制使用隔离浏览器内的 clipboard 写入接收器，不改系统剪贴板。所有私有配置、测试 home、日志、截图及凭据未纳入提交。

保留原页面与实现原宿主后台能力分别验收。当前模型设置仍是单组服务/模型连接，Files 只暴露该会话实际登记的成果；原渠道、Gateway 管理、通用工作区、账号、分支/标题/归档等未映射操作明确报尚未支持。无痕会话不支持时在创建前拒绝，不假装无痕但实际落盘。Skill/MCP/可信插件的原生链路通过，不等同于全部原宿主管理页均已接通。UFO 本阶段已移出默认执行链，仅保留源码与旧维护适配供后续 Windows 适配参考。

完整 TypeScript 检查仍受固定前端共享 workspace/生成声明缺失影响；正式构建通过，不能称全量类型检查通过。未做全新 Windows 安装演练、真实外部供应商或长期陪伴质量验收。

复现当前原页面与真实文件验收：

```powershell
.\.venv\Scripts\python.exe tools/verify_framework.py --browser-files
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
# 对显式 --test-mode --home 隔离宿主，顺序执行：
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
```

---

# 2026-10-23 初版验收历史

日期：2026-10-23（Asia/Shanghai）。分支：`codex/mirally-framework-refactor`。本记录对应[第一版范围](framework-v1.md)，长期产品方案继续演进。

## 已通过

| 验证 | 结果 | 说明 |
| --- | --- | --- |
| 根 Python 全套 | 965 项，963 通过、2 跳过 | 包含实际 UFO 隔离导入/初始化、产品 SQLite、来源与派生链撤回、命令、后台生命周期、WAL 迁移和回退；其后新增的修复及案例经全部 Framework 定向 92 项复验通过；两项真实桌面测试保持跳过 |
| 根与产品 Node | 152 项通过 | 包含旧维护入口、共享体验、真实材料/成果、发送竞态、流式 attempt 边界、精确重放、桌面协议和脚本进程控制 |
| 内核 MCP 协议 | 7 项通过 | 真实 Vitest，覆盖协议接缝与产品授权回调 |
| 完整内核构建 | 9 项通过 | Host/Client compiler、bundles、native addon、Desktop、Web 与 aggregate 检查；其后源码 MJS 改动经 Node 语法、单测及实际端到端验证 |
| 精确依赖与锁文件 | 通过 | Node 24.19.0、pnpm 11.28.5；368 workspace，最终 offline frozen install 成功 |
| 原生产品端到端 | 15 组通过 | 实际构建内核、PiAI provider、Agent loop、真实材料/图片/成果、MCP stdio 和子进程，合成服务仅提供模型/MCP 输入 |
| Edge Headless 自有 UI | 通过 | 流式对话、cookie 刷新、会话切换、记忆纠正/检索、安全文本、提醒与已读、独立桌面确认、窄屏布局 |
| Edge Headless 原有体验迁移 | 通过 | 十个工作起点、四种交付、真实材料入会话、草稿与归属、接着上次、三形态、造型持久、Activity/Memory 高级页面 |
| 实际 WebView2 与原窗口体验 | 通过 | 临时中文/空格 home；原开屏首绘/就绪切换、正式图标/原生菜单、三形态真实 Region/置顶/同 HWND、右键及 native 兜底、托盘回调和明确退出；修复双桥编辑注册并发 |

端到端包含：模型私有配置与默认关闭状态、回环鉴权/跨域拒绝、浏览器认证交接、单一原生历史和命令去重、真实偏好与提醒工具、停止与冷重启、原生 Skill 正文、实际脚本文件产物与取消、MCP 工具/资源/资源模板/提示模板、确认后的 MCP 调用、原生插件加载与撤下、辅助模型文字/有效 PNG 图像输入、主动候选入箱，以及 sidecar 故障后仍可鉴权退出并释放宿主锁。

源码检查包含 `git diff --check`、Python compileall 与产品 JS/MJS 的 Node 语法检查。未修改 UFO 内核、gitlink 或 `.gitmodules`。未覆盖现有私有配置，未把凭据、测试 home、日志或截图提交。

体验迁移的功能映射与格式限制见 [对照文档](framework-experience-migration.md)。真实桌面验收仅操作测试进程自己的 WebView2 HWND；没有全局键鼠、用户窗口截图、跨显示器/DPI 变化或通知区的物理指针命中测试。真实外部供应商与桌面任务效果仍不在本轮范围内。

## 复现

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_*.mjs integrations/openclaw/*.test.mjs kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools/verify_framework.py
Push-Location kernel/dsh
try {
    $env:LEFTHOOK = "0"
    node node_modules/pnpm/bin/pnpm.cjs exec vitest run packages/mcp/mcp-client/tests/protocol.spec.ts
} finally { Pop-Location }
```

浏览器脚本要求独立的 `--test-mode --home` 宿主，把其 `runtime.json` 路径交给 `tools/verify_framework_browser.mjs`；不要把私有 token 放在 shell 参数或提交中。准备工具与构建命令见[第一版说明](framework-v1.md)。

## 保留的实际边界

本轮没有调用真实外部模型供应商，也没有运行真实 UFO 桌面任务。回环服务证明的是实际适配与执行管线，不证明具体模型的质量、供应商计费、桌面成功率或长期陪伴品质。停止模型连接不能收回已发送数据或已经发生的计费。

没有全新机器安装演练、签名安装包、跨设备推送、系统通知或完整语音体验。采集器本体继续使用已有实现与回归测试，本轮新增监督接入以真实配置解析和受控生命周期 fixture 验证，未开启开发者桌面活动采集。旧会话仅索引，不做双向 transcript 转换。

首次全量 Python 运行曾在旧 UFO `pythonw.exe` 导入的 20 秒窗口超时；独立复测通过，后续两轮完整回归分别以 915 项、最终 930 项通过。未放宽该时间断言或修改 UFO 源码来掩盖超时。
