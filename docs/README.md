# Mirally 文档导航

Mirally 是面向 Windows 的 personal agent 应用，名称取自 MIRA + ally。Python 包和配置目录使用 `mirally`，产品环境变量使用 `MIRALLY_` 前缀。

本目录只保留描述**当前实现、当前规范和当前验证边界**的文档。已完成的计划、旧宿主路线、历史验收和设计探索由 Git 历史与[产品思考记录](product/product-notes.md)保留，不再单独存档。

## 按目的阅读

| 我想了解 | 文档 |
| --- | --- |
| 安装、启动、日常使用 | [项目 README](../README.md) |
| 当前系统由哪些模块组成、数据归谁、默认行为是什么 | [系统概览](architecture/overview.md) |
| 产品长期方向、设计原则和暂不实现的范围 | [产品方向](product/vision.md) |
| 按时间查看产品决策、取舍与演进 | [产品思考记录](product/product-notes.md) |
| 为什么选择同仓 DSH、哪些原生能力已继承 | [DSH 运行时](architecture/native-dsh.md) |
| 会话、停止、分代、后台作业和提醒 | [会话与作业](architecture/conversations.md) |
| 微信、语音和手机网页 | [消息渠道](architecture/channels.md) |
| 日历、提醒和定时执行 | [日历与定时](architecture/scheduling.md) |
| 电脑活动采集、知识投影和浏览 | [活动数据层](architecture/activity.md) |
| 记忆维护、纠正和召回 | [记忆](architecture/memory.md) |
| 窗口、托盘、开屏和侧边伙伴 | [桌面与伙伴](architecture/desktop.md) |
| 模型连接、路由、推理和视觉能力 | [模型](architecture/models.md) |
| Skill、MCP、脚本和可信插件 | [扩展接口](architecture/extensions.md) |
| 页面、导航和前端来源维护 | [前端](architecture/frontend.md) |
| UI 交互规则和文字层级 | [UI 规范](standards/ui-guidelines.md) / [视觉基线](standards/ui-visual-system.md) |
| 已实现、部分实现和未实现的能力 | [能力矩阵](reference/capabilities.md) |
| 测试命令、已验证范围和未覆盖边界 | [验证](reference/validation.md) |
| 品牌、图标和资源位置 | [品牌](design/brand.md) |

## 目录结构

```text
docs/
├─ product/       长期产品方向与决策记录
├─ architecture/  当前运行结构、数据归属和各专题
├─ standards/     UI 交互与视觉规范
├─ reference/     能力矩阵与验证边界
└─ design/        当前品牌与资源说明
```

## 文档约定

- **只写已实现行为。** 现行文档中的能力、默认值和限制应能在代码、配置或测试中找到依据。未实现的方向写入[产品方向](product/vision.md)；有讨论价值但尚未确定的判断写入[产品思考记录](product/product-notes.md)。
- **一个主题一篇。** 专题文档之间只链接，不复制长段。安装与启动只在[项目 README](../README.md)和[系统概览](architecture/overview.md)各保留必要部分。
- **写清状态。** 每篇文首标注更新日期、状态和主要依据。行为改变时同步修改对应文档，不把旧结论留在正文里。
- **历史不进正文。** 已完成的计划、旧入口和历史验收不再保留副本；需要时通过 Git 历史或产品记录追溯。
- **链接可检查。** 移动或重命名文档后运行 `python tools/check_docs_links.py`，确认相对链接仍可解析。

## 相关入口

- [协作指南](../CONTRIBUTING.md)
- [Agent 协作约定](../AGENTS.md)
- [更新记录](../CHANGELOG.md)
- [内核说明](../kernel/README.md)
- [模块职责](../mirally/ARCHITECTURE.md)
- [前端来源](../frontend/control-ui/UPSTREAM.md)
- [品牌资源](../mirally/assets/README.md)
