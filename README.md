# Mirally

Mirally 是运行在 Windows 上的个人 agent 产品：自有界面与桌面伙伴，同仓 DSH 承载唯一会话与模型主链。安装与启动见下文；[文档导航](docs/README.md) · [协作指南](CONTRIBUTING.md) · [验证与未覆盖边界](docs/reference/validation.md)。

## 环境要求

- Windows 10/11；Python 3.10；Node 24+（含 npm）；WebView2。
- 模型服务自备：安装后需在设置中配置连接、地址与凭据才能对话。

## 快速开始

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-framework.txt
.\.venv\Scripts\python.exe tools\setup_framework.py
.\run.ps1
```

首次启动后在「设置 → 模型」配置模型连接。

## 运行与默认行为

活动采集、后台模型与一般主动联系默认关闭，需要时在设置中开启。关闭窗口默认隐藏到托盘，Host、微信连接和后台作业继续；托盘不可用时关闭即退出。窗口、伙伴形态与明确退出的完整说明见[桌面与伙伴](docs/architecture/desktop.md#关闭托盘与退出)。

## 当前能力

已接通：原生流式对话与历史、停止和重启恢复、记忆与明确偏好、提醒和持久收件箱、可选主动检查、Skill/MCP/可信插件。

会话支持重命名、固定和归档恢复；历史材料与工具详情来自唯一 DSH 记录；模型连接可按对话/后台分配，网页与文件能力在设置中明确开启或授权。具体范围见[系统概览](docs/architecture/overview.md)。

未交付：签名安装包、跨设备推送与完整长期陪伴能力；当前边界见[验证与未覆盖边界](docs/reference/validation.md)。

## 开发与验证

并行开发、独立工作目录与 PR 流程见[协作指南](CONTRIBUTING.md)。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
.\.venv\Scripts\python.exe tools/verify_framework.py
```

全量测试需先完成上面的安装与前端构建；未准备可选依赖时整批失败，逐项命令与边界见[验证与未覆盖边界](docs/reference/validation.md)。

## 文档导航

[文档导航](docs/README.md) · [验证与未覆盖边界](docs/reference/validation.md) · [协作指南](CONTRIBUTING.md) · [更新记录](CHANGELOG.md) · [产品思考记录](docs/product/product-notes.md)

## 来源与许可

OpenClaw 后端已退役，旧实现通过 Git 提交 [`2eeb778a`](https://github.com/lyingloong/Mirally/tree/2eeb778a) 追溯，历史与迁移说明见[系统概览](docs/architecture/overview.md#已退役实现)。

前端以固定 OpenClaw Control UI `2026.9.6` 源码为来源，沿用原 Mirally 的布局、视觉、主题、动画与伙伴体验；DSH、前端固定来源及第三方组件的许可证、来源说明与第三方声明继续保留。

UFO 本阶段不接入：子模块与专用测试依赖已移除，源码不再随仓库提供；产品自有的 Windows 适配留作参考。产品与愿景展示站源码位于 [website/](website/README.md)，保留独立依赖、构建与发布流程，不参与产品启动；新站部署地址为 <https://lyingloong.github.io/Mirally/>。
