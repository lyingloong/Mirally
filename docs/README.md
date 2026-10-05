# Mirally 文档导航

Mirally 是面向 Windows 的对话与桌面助手，名称取自 MIRA + ally。当前正式图标为蓝青渐变的「相伴 M」。Python 包、配置目录和环境变量继续使用 `openassist`，与产品显示名称不同。

## 按目的阅读

| 需要了解什么 | 文档 |
| --- | --- |
| 安装、配置模型、启动与日常使用 | [项目 README](../README.md) |
| 对照检查累计功能、界面表现与待修复问题 | [Mirally 功能与体验验收清单](mirally-feature-checklist.md) |
| 官方界面桌面套壳、启动页、窗口与后台生命周期 | [OpenClaw 桌面套壳](openclaw-desktop-shell.md) |
| 打开程序慢的定位、启动优化与复测 | [桌面启动耗时](desktop-startup-performance.md) |
| 两份合成材料的办公演示与明天验收清单 | [办公协作演示](demo/README.md) |
| 模型思考参数、慢请求原因与响应耗时复测 | [响应耗时定位](response-latency.md) |
| 当前记忆范围与上下文限制 | [当前范围](../README.md#当前范围) |
| 旧 UFO 的输入协调、截图隔离和验证脚本 | [历史桌面体验实现与验证](desktop-experience-implementation.md) |
| 之前的 Tk OpenClaw 浮窗客户端 | [实验客户端说明](openclaw-desktop-client.md) |
| UFO 各目录、原生 Session、状态机、工具链、Galaxy 与 AIP | [UFO 仓库架构与源码导读](ufo-architecture.md) |
| Mirally 如何接入 UFO、配置和回调边界 | [OpenAssist 与 UFO 接入详解](ufo-integration.md) |
| 在现有智能体选择器中使用 UFO，了解 Gateway/worker 与实际限制 | [UFO 作为 OpenClaw 智能体](ufo-openclaw-agent.md) |
| 原生执行协议、控制边界与验收记录 | [UFO 原生执行链路补齐方案](native-runtime-plan.md) |
| 子模块只读约束、配置归属和开发验证要求 | [Agent 协作约定](../AGENTS.md) |
| 产品目标、设计原则和后续能力 | [前端体验目标与规划](frontend-experience-goals.md) |
| 保留 OpenClaw 原生完整功能、逐步适配 Mirally 的路线 | [OpenClaw 接入路线（实施中）](openclaw-integration-roadmap.md) |
| 启动完整 OpenClaw、模型配置、独立浏览器及验证边界 | [OpenClaw 原生运行与验收](openclaw-native-setup.md) |
| 已验证的后台任务、上下文、补充、中止及尚未覆盖范围 | [OpenClaw 首批原生验收记录](openclaw-native-validation.md) |
| 当前品牌与历次图标探索 | [品牌与图标索引](design-concepts/mirally/README.md) |
| 正式图标来源和生成提示词 | [相伴 M](design-concepts/mirally/companion-m/README.md) |
| PNG、ICO 规格与重新导出方法 | [运行图标资源](../openassist/assets/README.md) |
| 测试命令与界面验证方法 | [开发与验证](../README.md#开发与验证) |

## 文档范围

项目 README 和套壳文档说明当前的完整 OpenClaw 入口。桌面体验实现文档说明迁移前 UFO 的行为；Tk 客户端文档只描述实验入口。体验目标保留历史设计原则和规划，不能把其中的建议视为新系统的规则或已实现功能。历史设计目录记录当时的选择，当前正式图标以「相伴 M」与生产资源为准。

架构文档按文首标注的源码 commit 核对。当前主入口通过已有智能体选择器接入 UFO，见 [UFO 智能体接入](ufo-openclaw-agent.md)；独立 `--ufo` 原生路径见 [接入详解](ufo-integration.md)。界面组件和离线演示不等于真实模型质量保证。

[原生执行链路补齐方案](native-runtime-plan.md) 记录本轮已经落实的协议、控制边界、验证结果和剩余限制；OpenClaw 接入暂不在本轮范围。后续设计可以覆盖其中的建议，但要同步更新实际语义和验收结果。

`output/` 和 `.artifacts/` 下的图像对比页、截图、GIF 和验证结果是本地生成产物，不随 Git 上传。设计文档中的这类链接需要在生成它们的机器上打开；GitHub 或新克隆的仓库中可能不存在。正式图标、源码、提示词和说明文档已经纳入仓库，运行不依赖这些本地预览。

普通演示和 GUI 验证不调用真实模型；实际模型验证脚本会使用已配置服务，应按需要单独运行。历史验证结果只是对应版本和本机环境的记录，不代表所有显示器、DPI、捕获驱动或真实桌面任务都已覆盖。
