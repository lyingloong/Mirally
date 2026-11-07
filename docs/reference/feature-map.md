# Feature map：按功能定位代码

更新：2026-11-06，Asia/Shanghai。状态：现行代码导航。

从要修改的用户任务找到入口、状态 owner 和代表测试，再沿调用链读代码。能力是否开放、限制及验收范围见[能力矩阵](capabilities.md)与[验证](validation.md)；模块关系见[系统概览](../architecture/overview.md)。下表中的测试是定位和起步验证的线索，仍需按实际改动选择检查。

## 先认清四个位置

| 位置 | 修改时关注什么 |
| --- | --- |
| [产品页面](../../frontend/control-ui/ui/src/pages/mirally/)与[客户端适配](../../frontend/control-ui/ui/src/app/mirally-client.ts) | 用户操作、草稿、加载/错误状态，以及 HTTP/SSE 映射 |
| [Node Host](../../kernel/dsh/packages/mirally/mirally-product/src/index.mjs) | 接口鉴权、命令准入、DSH 组合、事件、工具和渠道生命周期 |
| [Python 产品 owner](../../mirally/framework/service.py)及同目录领域模块 | SQLite 中的产品事实、会话引用、版本/来源校验和持久回执 |
| [同仓 DSH](../../kernel/dsh/) | 唯一 Session/transcript、Agent loop、原生模型适配与执行器；从[组合入口](../../kernel/dsh/packages/mirally/mirally-product/composition.mjs)追踪已挂载能力 |

下表的“归属”指状态或执行责任，不指某位开发者。涉及多个位置时，先确认事实由谁保存、请求由谁批准、动作由谁执行。

产品展示站独立于这四个运行时位置，见[产品展示站](#产品展示站)；开发工具见[开发工作流与工具](#开发工作流与工具)。

## 对话、模型与行动

| 要修改的功能 | 从哪里开始 | 状态与执行归属 | 代表测试 |
| --- | --- | --- | --- |
| [发送、历史、停止、会话管理与分叉](../architecture/conversations.md) | [客户端适配](../../frontend/control-ui/ui/src/app/mirally-client.ts)、[Host](../../kernel/dsh/packages/mirally/mirally-product/src/index.mjs) | [service.py](../../mirally/framework/service.py)保存会话元数据、代次和回执；[native-runtime.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/native-runtime.mjs)连接 DSH；正文来自原生 Session | [会话生命周期](../../tests/test_framework_session_lifecycle.py)、[原生会话往返](../../tests/test_framework_native_session_roundtrip.mjs) |
| 会话摘要与自动标题 | [recaps.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/recaps.mjs) | [recaps.py](../../mirally/framework/recaps.py)管理摘要状态与过期校验；手动标题及会话引用仍归产品 owner | [摘要状态](../../tests/test_framework_recaps.py)、[端到端摘要](../../tests/test_framework_recaps_e2e.mjs) |
| [模型连接、列表、会话选型与能力](../architecture/models.md) | [model-settings.ts](../../frontend/control-ui/ui/src/pages/mirally/model-settings.ts)、[models.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/models.mjs) | [model_connections.py](../../mirally/framework/model_connections.py)保存连接与路由；[model-probe.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/model-probe.mjs)探测；实际推理走 DSH | [连接与引用](../../tests/test_framework_model_connections.py)、[会话模型绑定](../../tests/test_framework_session_models_host.mjs) |
| 原生计划、提问、子代理、压缩等 | [composition.mjs](../../kernel/dsh/packages/mirally/mirally-product/composition.mjs)、[native-interactions.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/native-interactions.mjs) | DSH 原生能力；[页面交互桥](../../frontend/control-ui/ui/src/pages/mirally/native-interactions.ts)呈现问题/计划等交互。挂载范围见[DSH 运行时](../architecture/native-dsh.md) | [完整 base 组合](../../tests/test_framework_native_base_e2e.mjs)、[原生交互](../../tests/test_framework_native_interactions.mjs) |
| 命令、后台作业、状态与停止 | [shell-capabilities.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/shell-capabilities.mjs)、[native-runtime.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/native-runtime.mjs) | Mirally 受管理命令和 DSH 原生作业分别持有进程/输出；[job-notifications.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/job-notifications.mjs)登记结束通知。停止回复与停止作业分别处理 | [会话作业](../../tests/test_framework_session_shell_jobs.mjs)、[作业通知](../../tests/test_framework_job_notifications.mjs) |
| 动作审批、临时信任与持续信任 | [action-approvals.ts](../../frontend/control-ui/ui/src/pages/mirally/action-approvals.ts)、[action-gate.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/action-gate.mjs) | Host 准入与 ActionGate 绑定会话、命令及动作；DSH 原生沙箱/审批是另一执行机制，改动时核对[权限边界](../architecture/overview.md#会话作业与权限) | [ActionGate](../../tests/test_framework_action_gate.mjs)、[权限往返](../../tests/test_framework_action_permissions_roundtrip.mjs) |
| 文件、材料、成果与网页搜索 | [capabilities-settings.ts](../../frontend/control-ui/ui/src/pages/mirally/capabilities-settings.ts)、[basic-capabilities.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/basic-capabilities.mjs)、[materials.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/materials.mjs) | [capabilities.py](../../mirally/framework/capabilities.py)负责授权根与开关；[materials.py](../../mirally/framework/materials.py)负责有限材料提取；原生文件工具仍走 DSH | [能力边界](../../tests/test_framework_capabilities.py)、[材料链路](../../tests/test_framework_materials.mjs)、[能力设置草稿与失败处理](../../tests/test_framework_capabilities_settings_ui.mjs) |
| 独立浏览器、桌面观察 | [browser-capabilities.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/browser-capabilities.mjs)、[desktop-observation.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/desktop-observation.mjs) | 浏览器执行器与[桌面观察适配](../../mirally/framework/desktop_observation.py)持有实际观察/动作；能力开放范围以[能力矩阵](capabilities.md)为准 | [浏览器生命周期](../../tests/test_framework_browser_lifecycle.mjs)、[桌面观察](../../tests/test_framework_desktop_observation.mjs) |

## 渠道、提醒与扩展

| 要修改的功能 | 从哪里开始 | 状态与执行归属 | 代表测试 |
| --- | --- | --- | --- |
| [微信绑定、收发、回执与数字命令](../architecture/channels.md) | [channels-page.ts](../../frontend/control-ui/ui/src/pages/mirally/channels-page.ts)、[Node 渠道适配](../../kernel/dsh/packages/mirally/mirally-product/src/channels/) | [channels.py](../../mirally/framework/channels.py)保存身份、绑定、路由和收发回执；渠道输入进入同一会话 | [渠道 owner](../../tests/test_framework_channels.py)、[收发与回执](../../tests/test_framework_weixin_channel.mjs) |
| 微信助理、查询/委托原会话 | [assistant-tools.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/assistant-tools.mjs)、[渠道适配](../../kernel/dsh/packages/mirally/mirally-product/src/channels/) | 产品 owner 校验归属；工具委托目标原会话；微信与桌面共用动作权限，保留来源核验 | [助理工具](../../tests/test_framework_weixin_assistant_tools.mjs)、[助理端到端](../../tests/test_framework_weixin_assistant_e2e.mjs) |
| 语音转写与微信媒体 | [voice-input.ts](../../frontend/control-ui/ui/src/pages/mirally/voice-input.ts)、[speech-service.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/speech-service.mjs)、[渠道适配](../../kernel/dsh/packages/mirally/mirally-product/src/channels/) | 录音/转写、平台媒体和材料提取各有结果；材料仍归原会话 | [桌面录音](../../tests/test_framework_voice_input.mjs)、[微信媒体](../../tests/test_framework_weixin_media.mjs) |
| 可选手机网页与配对 | [手机 UI](../../frontend/control-ui/ui/src/mobile/)、[mobile.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/mobile.mjs) | 独立受限服务和设备身份；访问同一产品事实，权限范围与桌面管理接口不同 | [手机服务](../../tests/test_framework_mobile.mjs)、[手机 UI](../../tests/test_framework_mobile_ui.mjs) |
| [日程、提醒与多端投递](../architecture/scheduling.md) | [calendar-panel.ts](../../frontend/control-ui/ui/src/pages/mirally/calendar-panel.ts)、[calendar-tools.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/calendar-tools.mjs) | [calendar.py](../../mirally/framework/calendar.py)与产品 owner 保存日程/提醒；[messages-page.ts](../../frontend/control-ui/ui/src/pages/mirally/messages-page.ts)展示收件箱，渠道记录独立投递回执 | [日历状态](../../tests/test_framework_calendar.py)、[日历链路](../../tests/test_framework_calendar_integration_e2e.mjs) |
| 会话定时执行 | [scheduled-actions.mjs](../../kernel/dsh/packages/mirally/mirally-product/src/scheduled-actions.mjs) | 复用 DSH 时间规则；产品定时 owner 持久化，到期经过原会话准入与动作权限；与普通提醒分开 | [调度逻辑](../../tests/test_framework_scheduled_actions.mjs)、[微信调度来源](../../tests/test_framework_scheduled_weixin_authority.mjs) |
| [Skill、MCP 与可信插件](../architecture/extensions.md) | [extensions-page.ts](../../frontend/control-ui/ui/src/pages/mirally/extensions-page.ts)、[扩展组合入口](../../kernel/dsh/packages/mirally/mirally-extensions/src/index.mjs) | [configuration.mjs](../../kernel/dsh/packages/mirally/mirally-extensions/src/configuration.mjs)处理配置；[script.mjs](../../kernel/dsh/packages/mirally/mirally-extensions/src/script.mjs)处理脚本；DSH 提供原生扩展服务 | [扩展包测试](../../kernel/dsh/packages/mirally/mirally-extensions/tests/)、[脚本执行与确认](../../kernel/dsh/packages/mirally/mirally-extensions/tests/script.test.mjs) |

## 持久上下文与桌面体验

| 要修改的功能 | 从哪里开始 | 状态与执行归属 | 代表测试 |
| --- | --- | --- | --- |
| [活动采集、投影与查询](../architecture/activity.md) | [activity_experience.py](../../mirally/framework/activity_experience.py)、[runtime/activity](../../mirally/runtime/activity/) | [core/activity](../../mirally/core/activity/)保存观察与知识投影；[platform/activity](../../mirally/platform/activity/)适配 Windows；[framework_workers.py](../../mirally/framework_workers.py)管理后台生命周期 | [产品活动体验](../../tests/test_framework_activity_experience.py)、[采集生命周期](../../tests/test_activity_monitor.py) |
| [记忆、个性化与上下文读取](../architecture/memory.md) | [memory-page.ts](../../frontend/control-ui/ui/src/pages/mirally/memory-page.ts)、[context.py](../../mirally/framework/context.py)、[runtime/memory](../../mirally/runtime/memory/) | [core/memory](../../mirally/core/memory/)持有记忆；Context 按需只读组合来源；显式偏好和主动联系设置归产品 owner | [记忆 owner](../../tests/test_memory_service.py)、[记忆与会话回放](../../tests/test_framework_memory_replay.py) |
| 后台模型、主动候选与收件箱 | [settings-page.ts](../../frontend/control-ui/ui/src/pages/mirally/settings-page.ts)、[messages-page.ts](../../frontend/control-ui/ui/src/pages/mirally/messages-page.ts)、[framework_workers.py](../../mirally/framework_workers.py) | [service.py](../../mirally/framework/service.py)管理预算相关产品设置、候选、约定及收件箱；后台请求复用原模型桥 | [产品状态](../../tests/test_framework_product.py)、[后台 worker](../../tests/test_framework_workers.py)、[消息与提醒刷新状态](../../tests/test_framework_messages_ui.mjs) |
| [窗口、托盘、伙伴三形态与关闭](../architecture/desktop.md) | [framework_host.py](../../mirally/framework_host.py)、[framework_shell.py](../../mirally/framework_shell.py)、[desktop_surface.py](../../mirally/desktop_surface.py) | Python 壳持有原生窗口/托盘与退出流程；公共 surface 保留伙伴、裁剪和主动截图行为 | [窗口壳与托盘](../../tests/test_framework_shell.py)、[伙伴还原](../../tests/test_framework_companion_restore.py)；原生 GUI 按[验证文档](validation.md)顺序验收 |
| [页面、导航、主题、品牌与快捷开始](../architecture/frontend.md) | [route.ts](../../frontend/control-ui/ui/src/pages/mirally/route.ts)、[侧栏](../../frontend/control-ui/ui/src/components/app-sidebar-render.ts)、[我的 Mirally 菜单（设置入口）](../../frontend/control-ui/ui/src/components/app-sidebar-identity-menu.ts)、[产品页面目录](../../frontend/control-ui/ui/src/pages/mirally/)、[客户端适配](../../frontend/control-ui/ui/src/app/mirally-client.ts) | 复用原前端组件；品牌/伙伴资源见[资源说明](../../mirally/assets/README.md)。修改固定来源文件前核对[来源维护](../architecture/frontend.md#来源维护) | [当前客户端](../../tests/test_framework_original_client.mjs)、[请求边界](../../tests/test_framework_ui_races.mjs)、[首次引导、建议草稿与新对话入口](../../tests/test_framework_model_onboarding_ui.mjs)；同时遵守[UI 规范](../standards/ui-guidelines.md) |

## 产品展示站

| 要修改的功能 | 从哪里开始 | 状态与执行归属 | 代表验证 |
| --- | --- | --- | --- |
| 产品首页、开发版截图、伙伴与预设演示 | [网站说明](../../website/README.md)、[App.jsx](../../website/src/App.jsx)、[DayExperience.jsx](../../website/src/DayExperience.jsx)、[素材来源](../../website/public/CREDITS.md) | 两栏首屏展示中文价值主张与真实截图节选，弹窗保留完整原图；唯一浅灰演示舞台、两个平面能力栏、独立白底伙伴章节及扩展/源码安装 FAQ；截图取自隔离开发版，三场景演示使用预设资料，不访问产品宿主、数据库或模型服务。当前能力以[能力矩阵](capabilities.md)为准 | 网站 `build`、`export:html`；实际桌面/窄窗口、截图放大、标签键盘操作、摘要下载、FAQ 与外链 |
| 对外更新内容与日期页面 | [更新 Markdown](../../website/content/updates/)、[页面生成脚本](../../website/scripts/sync-update-pages.mjs)、[Vite 配置](../../website/vite.config.js) | 人工整理的 Markdown 是正文来源，构建生成日期页；不自动发布全部产品 CHANGELOG | 网站构建；`/updates/`、日期深链接与图片路径验收 |
| 网站 CI 与 Pages 发布 | [网站工作流](../../.github/workflows/website.yml)、[范围选择器](../../tools/check_ci_scope.py) | 网站相关 PR 独立构建；`main` 网站变更或手动运行发布 Pages | [CI 范围测试](../../tests/development/test_ci_scope.py)、网站工作流及部署后检查；见[网站验证](validation.md#网站独立验证) |

## 开发工作流与工具

开发工作流不改变产品事实；它由仓库内的状态助手和流程文档驱动，队长推进状态，合并仍由用户批准。

| 要修改的功能 | 从哪里开始 | 状态与执行归属 | 代表测试 |
| --- | --- | --- | --- |
| 开发点状态、并发/配额闸门与待合并池 | [状态助手](../../tools/dev_workflow/agent_workflow.py)、[AgentTeams 工作流](../development/agent-teams-workflow.md) | 队长用状态助手推进 `proposed → … → awaiting_merge`；审查通过仍需用户批准，不自动合并；本机状态与配置在 `output/agent-workflow/`（gitignore，不是产品数据） | [状态机与闸门测试](../../tests/development/test_agent_workflow.py) |
| 审查经验与可复用准则 | [审查经验库](../development/review-experience.md) | 审查师维护；候选先写入 `output/agent-workflow/experience-candidates.md`，整理走独立 docs PR | 无自动化测试；实现/审查前人工核对相关条目 |
| 仓库文档链接守卫 | [链接检查器](../../tools/check_docs_links.py) | 文档改动后由改动者运行；入口是否仍正确、测试是否覆盖目标行为仍需审查核对 | `python tools/check_docs_links.py` 输出 0 broken |
| CI 影响范围与依赖缓存 | [PR 工作流](../../.github/workflows/mirally-pr.yml)、[范围选择器](../../tools/check_ci_scope.py) | 明确的文档/开发工具改动只运行轻量检查，网站改动另走网站工作流；未知或无法比较时运行完整产品检查，规则见[验证](validation.md#github-actions-检查范围) | [范围与 Git 差异测试](../../tests/development/test_ci_scope.py) |

## 找到入口后的检查顺序

1. 从上表选对应功能，再读专题文档和实际调用方。Host 的跨层入口常集中在 `src/index.mjs`，用具体 API、工具名或事件名继续搜索。
2. 确认变更会触及的 owner、持久状态、命令/来源校验及生命周期；涉及不同执行器时分别检查真实状态和停止语义。
3. 从代表测试扩展到受影响的交互与失败路径。测试命令、构建准备及真实 GUI/API 的边界见[验证](validation.md)；新 worktree 的数据与依赖隔离见[协作指南](../../CONTRIBUTING.md)。

找不到条目时，先查[能力矩阵](capabilities.md)、[DSH 组合入口](../../kernel/dsh/packages/mirally/mirally-product/composition.mjs)和对应领域目录。`mirally/runtime/ufo/` 是保留的参考/冻结适配（UFO 子模块源码已移除），当前默认框架不加载；拟议桌面能力见[研究记录](../product/research-2026-10-31-ufo.md)，不能当作已开放功能入口。

## 维护约定

- 新增或移除功能、迁移主要入口/owner、改变代表验证入口时，在同一 PR 更新对应行；局部重构不需要扩写成文件清单。
- 改动 `tools/dev_workflow/`、`docs/development/` 或 `tests/development/` 时，同步更新上面「开发工作流与工具」的入口与代表验证。
- 链接使用仓库内相对路径，不依赖开发机目录；优先维护少量稳定入口，避免复制全部 RPC、工具目录、状态说明或架构正文。
- 代码与地图不一致时先核对当前实现并修正地图；不要为了符合旧索引保留错误职责或过时模块。
- 改动后运行 `python tools/check_docs_links.py`；它检查目标是否存在，入口是否仍正确、测试是否覆盖目标行为需要审查者核对。
