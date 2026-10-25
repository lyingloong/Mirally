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
