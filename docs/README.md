# Mirally 文档导航

Mirally 是面向 Windows 的对话与桌面助手，名称取自 MIRA + ally。当前正式图标为蓝青渐变的「相伴 M」。Python 包、配置目录和环境变量继续使用 `openassist`，与产品显示名称不同。

## 按目的阅读

| 需要了解什么 | 文档 |
| --- | --- |
| 安装、配置模型、启动与日常使用 | [项目 README](../README.md) |
| UFO 各目录、原生 Session、状态机、工具链、Galaxy 与 AIP | [UFO 仓库架构与源码导读](ufo-architecture.md) |
| Mirally 如何接入 UFO、配置和回调边界、已知缺口 | [OpenAssist 与 UFO 接入详解](ufo-integration.md) |
| 原生执行协议、控制边界与验收记录 | [UFO 原生执行链路补齐方案](native-runtime-plan.md) |
| 子模块只读约束、配置归属和开发验证要求 | [Agent 协作约定](../AGENTS.md) |
| 浮窗、历史、快捷键和最小化 | [会话与浮窗操作](../README.md#会话与浮窗操作) |
| 当前记忆范围与上下文限制 | [记忆范围](../README.md#记忆范围) |
| 当前实现、输入协调、截图隔离和验证脚本 | [桌面体验实现与验证](desktop-experience-implementation.md) |
| 产品目标、设计原则和后续能力 | [前端体验目标与规划](frontend-experience-goals.md) |
| 当前品牌与历次图标探索 | [品牌与图标索引](design-concepts/mirally/README.md) |
| 正式图标来源和生成提示词 | [相伴 M](design-concepts/mirally/companion-m/README.md) |
| PNG、ICO 规格与重新导出方法 | [运行图标资源](../openassist/assets/README.md) |
| 测试命令与界面验证方法 | [开发与验证](../README.md#开发与验证) |

## 文档范围

项目 README 说明如何使用当前产品；实现文档说明代码已经具备的行为及其验证边界；体验目标文档保留设计原则和规划，不能把其中的建议视为已实现功能。历史设计目录记录当时的选择，当前正式方案以「相伴 M」与生产资源为准。

架构文档按文首标注的源码 commit 核对。判断默认 UFO 原生路径是否接通某项能力时，请查看 [当前接入范围与限制](ufo-integration.md#当前接入范围与限制)；界面组件和离线演示不等于真实模型质量保证。

[原生执行链路补齐方案](native-runtime-plan.md) 记录本轮已经落实的协议、控制边界、验证结果和剩余限制；OpenClaw 接入暂不在本轮范围。后续设计可以覆盖其中的建议，但要同步更新实际语义和验收结果。

`output/` 和 `.artifacts/` 下的图像对比页、截图、GIF 和验证结果是本地生成产物，不随 Git 上传。设计文档中的这类链接需要在生成它们的机器上打开；GitHub 或新克隆的仓库中可能不存在。正式图标、源码、提示词和说明文档已经纳入仓库，运行不依赖这些本地预览。

普通演示和 GUI 验证不调用真实模型；实际模型验证脚本会使用已配置服务，应按需要单独运行。历史验证结果只是对应版本和本机环境的记录，不代表所有显示器、DPI、捕获驱动或真实桌面任务都已覆盖。
