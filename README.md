# Mirally

本分支默认运行自有 personal agent 框架。前端以固定 OpenClaw Control UI `2026.9.6` 源码为来源，沿用原 Mirally 的布局习惯、视觉、主题、动画与伙伴体验；导航、设置和内容页改为 Mirally 自有产品，见[前端内容与导航](docs/mirally-frontend.md)。数据与动作接到 Mirally HTTP/SSE 宿主，同仓 DSH 承载唯一会话与模型主链。OpenClaw Gateway 不参与默认运行；旧后端维护基线保留在 `feat/mirally-openclaw-desktop` 的 `2eeb778a`。

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-framework.txt
.\.venv\Scripts\python.exe tools\setup_framework.py
.\run.ps1
```

需要 Windows、Node 24+（含 npm）和 WebView2；模型服务由你配置。活动采集、后台模型与一般主动联系默认关闭。安装、模块关系、生态兼容、迁移回退和验证边界见[第一版使用与开发说明](docs/framework-v1.md)，长期方向见[产品框架方案](docs/personal-agent-framework.md)。

已接通原生流式对话与历史、停止和重启恢复、记忆与明确偏好、提醒和持久收件箱、可选主动检查、Skill/MCP/可信插件。本阶段暂不接入 UFO，已有源码与维护适配留作 Windows 适配参考。外部服务差异与长期成本仍需持续验收；未交付签名安装包、跨设备推送或完整长期陪伴能力。

原前端源码位于 [frontend/control-ui](frontend/control-ui/README.md)，包括之前的真实对话、历史、设置页面与主题；原 Mirally 开屏、品牌、工作页、海獭／飞狐、三形态伙伴、窗口裁剪和菜单继续复用。安装脚本分别按 DSH 与前端锁文件准备依赖，并构建原页面；前端构建失败会明确报错，不替换成简化页面。页面复用不代表 OpenClaw 的渠道、后台服务和专有管理能力已由 DSH 实现。接入关系、材料限制与实际交互验收项目见 [体验迁移对照](docs/framework-experience-migration.md)。

## 当前范围

会话支持重命名、固定和归档恢复；历史材料与工具详情来自唯一 DSH 记录。模型连接可按对话/后台分配；网页与文件能力在设置中明确开启或授权。旧会话通过显式迁移独立归档，在关于页只读查看。具体范围见[迁移收尾与维护切换](docs/framework-migration-closure.md)。

后续只维护新 Mirally/DSH 框架。旧 OpenClaw 分支、显式 `--openclaw` 回退入口和用户原数据保留，不再双线开发；旧版运行说明可在已推送的 [历史基线](https://github.com/lyingloong/openassist/tree/2eeb778a) 查看。迁移前先停止旧 Gateway、采集和记忆维护，不自动导入或删除私有数据。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
.\.venv\Scripts\python.exe tools/verify_framework.py
```

[实际验收与未覆盖边界](docs/framework-v1-validation.md) · [文档导航](docs/README.md) · [更新记录](CHANGELOG.md) · [产品思考记录](docs/product-notes.md)

产品与愿景展示网页见 [Everyday Ally 展示站](showcase/README.md)。DSH、前端固定来源及 UFO 的许可证、来源说明和第三方声明继续保留；UFO 本阶段不接入。
