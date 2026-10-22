# Mirally 文档导航

当前重构分支的目标和验收见[重构实施计划](framework-refactor-plan.md)。下列运行文档仍描述 OpenClaw 基线；产品框架与扩展文档描述待实现的目标，阶段通过后再更新对应运行说明。

Mirally 是面向 Windows 的对话与桌面助手，名称取自 MIRA + ally。当前正式图标为蓝青渐变的「相伴 M」。Python 包统一使用 `mirally`，产品配置位于 `config/mirally/`，产品环境变量使用 `MIRALLY_` 前缀。

## 按目的阅读

| 需要了解什么 | 文档 |
| --- | --- |
| 安装、配置模型、启动与日常使用 | [项目 README](../README.md) |
| 从旧包名迁移本机配置、会话和启动路径 | [Mirally 名称迁移](mirally-name-migration.md) |
| 用户可见功能与版本变化 | [更新记录](../CHANGELOG.md) |
| 按时间追踪产品发现、想法、设计原则、方向和文案 | [产品思考记录](product-notes.md) |
| 面向长期 personal agent 的目标、模块归属、依赖方向与整体运行 | [产品框架目标方案](personal-agent-framework.md) |
| 完整重构的基线、实施阶段、验收与回退条件 | [重构实施计划](framework-refactor-plan.md) |
| Skill、MCP、插件及新来源、服务、执行器和交互入口的接入与组合 | [扩展接口与生态兼容设计](framework-extensions.md) |
| 为什么这样选、其他架构、固定源码证据、反例与迁移门槛 | [架构调研与取舍](research/personal-agent-architecture-2026-10-22.md) |
| 对照检查累计功能、界面表现与待修复问题 | [Mirally 功能与体验验收清单](mirally-feature-checklist.md) |
| 官方界面桌面套壳、启动页、窗口与后台生命周期 | [OpenClaw 桌面套壳](openclaw-desktop-shell.md) |
| 打开程序慢的定位、启动优化与复测 | [桌面启动耗时](desktop-startup-performance.md) |
| 两份合成材料的办公演示与明天验收清单 | [办公协作演示](demo/README.md) |
| 模型思考参数、慢请求原因与响应耗时复测 | [响应耗时定位](response-latency.md) |
| 当前记忆范围与上下文限制 | [当前范围](../README.md#当前范围) |
| Windows 活动观察数据层、Screenpipe 源码参考和实施状态 | [活动观察数据层方案](activity-sensor-design.md) |
| 采集层之上的活动组织、上下文检索与记忆衔接设计 | [Activity Knowledge Layer](activity-knowledge-layer.md) |
| 将动作、窗口状态与文字组织为事件，查询使用轨迹和来源限制 | [Activity 事件视图](activity-events.md) |
| 按数据层回放历史 Observation、Evidence、事件、上下文、记忆和诊断 | [Activity 开发者浏览器](activity-browser.md) |
| 数据是否实际收集、组织是否正确，以及能否说明用户行为 | [Activity 数据层评估与验收](activity-data-acceptance.md) |
| 清理后新样本的检查、投影 v2 完善与事实时间线 | [Activity 新数据检查](activity-fresh-data-review.md) |
| OpenClaw agent 按需访问活动知识的统一接入 | [Activity Agent Access](activity-agent-access.md) |
| 无对话时整理 Activity、持久记忆和相关问题自动召回 | [Activity Memory](activity-memory.md) |
| Activity 与 Screenpipe 的能力差距和陪伴场景对齐目标 | [Screenpipe 对齐情况](activity-screenpipe-gap.md) |
| 持续屏幕、UIA 正文、输入文本与剪贴板的实现和测试 | [丰富内容采集](activity-rich-capture.md) |
| UFO 各目录、原生 Session、状态机、工具链、Galaxy 与 AIP | [UFO 仓库架构与源码导读](ufo-architecture.md) |
| Mirally 如何接入 UFO、配置和回调边界 | [Mirally 与 UFO 接入详解](ufo-integration.md) |
| 主 Agent 委派 UFO 桌面任务，了解 Gateway/worker 与实际限制 | [UFO 桌面任务子代理](ufo-openclaw-agent.md) |
| 原生执行协议、控制边界与验收记录 | [UFO 原生执行链路补齐方案](native-runtime-plan.md) |
| 子模块只读约束、配置归属和开发验证要求 | [Agent 协作约定](../AGENTS.md) |
| 产品目标、设计原则和后续能力 | [前端体验目标与规划](frontend-experience-goals.md) |
| 保留 OpenClaw 原生完整功能、逐步适配 Mirally 的路线 | [OpenClaw 接入路线（实施中）](openclaw-integration-roadmap.md) |
| 启动完整 OpenClaw、模型配置、独立浏览器及验证边界 | [OpenClaw 原生运行与验收](openclaw-native-setup.md) |
| 已验证的后台任务、上下文、补充、中止及尚未覆盖范围 | [OpenClaw 首批原生验收记录](openclaw-native-validation.md) |
| 当前品牌与历次图标探索 | [品牌与图标索引](design-concepts/mirally/README.md) |
| 正式图标来源和生成提示词 | [相伴 M](design-concepts/mirally/companion-m/README.md) |
| PNG、ICO 规格与重新导出方法 | [运行图标资源](../mirally/assets/README.md) |
| 测试命令与界面验证方法 | [开发与验证](../README.md#开发与验证) |

## 文档范围

项目 README 和套壳文档说明当前的完整 OpenClaw 入口。旧 UFO 专用界面及独立 Tk 客户端已删除，历史说明页只保留索引。体验目标保留历史设计原则和规划，不能把其中的建议视为新系统的规则或已实现功能。历史设计目录记录当时的选择，当前正式图标以「相伴 M」与生产资源为准。

架构文档按文首标注的源码 commit 核对。当前只提供 OpenClaw Control UI 对话入口，主 Agent 通过 `sessions_spawn` 委派内部 UFO 桌面子代理，见 [接入说明](ufo-openclaw-agent.md)。执行器适配见 [接入详解](ufo-integration.md)，界面组件与离线测试不等于真实模型质量保证。

[原生执行链路补齐方案](native-runtime-plan.md) 记录本轮已经落实的协议、控制边界、验证结果和剩余限制；其中旧界面相关内容仅作历史参考。后续设计可以覆盖其中的建议，但要同步更新实际语义和验收结果。

`output/` 和 `.artifacts/` 下的图像对比页、截图、GIF 和验证结果是本地生成产物，不随 Git 上传。设计文档中的这类链接需要在生成它们的机器上打开；GitHub 或新克隆的仓库中可能不存在。正式图标、源码、提示词和说明文档已经纳入仓库，运行不依赖这些本地预览。

普通演示和 GUI 验证不调用真实模型；实际模型验证脚本会使用已配置服务，应按需要单独运行。历史验证结果只是对应版本和本机环境的记录，不代表所有显示器、DPI、捕获驱动或真实桌面任务都已覆盖。
