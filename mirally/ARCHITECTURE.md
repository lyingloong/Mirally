# Mirally 运行结构

更新：2026-10-31，Asia/Shanghai。状态：现行。完整架构与验证边界见[系统概览](../docs/architecture/overview.md)。

默认入口：`python -m mirally` → `framework_host.py` → `kernel/dsh/packages/mirally/mirally-product/start.mjs`。产品主链不依赖 OpenClaw；`--openclaw` 只保留为冻结的显式回退入口。

## 产品层

| 模块 | 职责 |
| --- | --- |
| `framework_host.py` | 解析 home、Node/Python 工具链和端口；构建检查；启动 Node Host；打开桌面窗口或浏览器 |
| `framework/service.py` | 产品 SQLite owner：设置、逻辑会话/代次、命令回执、偏好、提醒/日历、投递、主动候选、摘要入口 |
| `framework/channels.py` | 微信安装身份、本人绑定、会话路由、收发去重与回执；凭据使用 DPAPI |
| `framework/calendar.py` | 日程、提醒与定时执行的产品事实 |
| `framework/model_connections.py` | 命名连接、模型目录、对话/后台路由与引用保护 |
| `framework/capabilities.py` | 文件授权根、工作区写入、网页开关与有界读取 |
| `framework/context.py` | 按需只读组合 Memory 与 Activity；查询不启动采集 |
| `framework/materials.py` | 材料接收、文字提取、会话成果与鉴权 |
| `framework/recaps.py` | 会话摘要与自动标题的产品状态 |
| `framework/desktop_observation.py` | 窗口列表、截图与桌面观察接口 |
| `framework/legacy_history.py` | 旧会话独立归档的只读分页 |
| `framework_workers.py` | Activity/Memory 子进程、语义任务、预算与停止 |
| `framework_ui/` | 原前端桥接、品牌与伙伴呈现资源 |
| `framework_shell.py` | WebView2 窗口、原生裁剪、托盘、伙伴与退出 |

产品库位于 `<home>/framework/product.sqlite3`，schema 11。它保存引用和业务状态，不保存第二份聊天正文。

## 内核层

`kernel/dsh/packages/mirally/mirally-product/` 是本机鉴权 Host 与 DSH 组合：

- `start.mjs` 启动 Host；`composition.mjs` 加载完整 `dsh-base` 并应用 Mirally 覆盖。
- 原生 Session/Agent loop、模型适配、工具执行、压缩、子代理、计划、提问、todo/goal 和 workflow 由 DSH 提供。
- `src/` 另有微信协议与生命周期、手机受限服务、日历工具、摘要、作业通知、模型探测和产品呈现。
- `kernel/dsh/packages/mirally/mirally-extensions/` 管理 Skill、脚本、MCP 与可信插件。

## 领域层

- `core/activity/`：产品中立的观察协议、单写入 SQLite、知识投影和查询预算。
- `core/memory/`：持久记忆、来源墓碑、纠正、召回与预算。
- `runtime/activity/`：Windows 采集器、CLI、历史浏览器和隔离 worker。
- `runtime/memory/`：后台维护、语义整理、召回、保留和 CLI。
- `platform/`：Windows 捕获隔离、Raw Input、焦点和任务栏适配。
- `runtime/ufo/` 与 `UFO/`：旧桌面执行适配和只读子模块，默认产品不加载。

## 前端层

`frontend/control-ui/` 以固定上游源码为视觉与组件基础；`ui/src/pages/mirally/` 维护自有页面，`ui/src/app/mirally-client.ts` 连接本机 HTTP/SSE。产品页面、导航和来源维护见[前端](../docs/architecture/frontend.md)。

## 数据流

1. 桌面或手机入口提交输入 → 产品 owner 校验会话、模型、来源和命令身份。
2. Host 把输入交给 DSH 原生会话；工具经 DSH 原生执行器或 Mirally 产品执行器运行。
3. Context 按需只读组合记忆、活动和来源缺口；不复制为第二份聊天历史。
4. 提醒、摘要和作业通知由产品 owner 持久化，经桌面或微信渠道投递；渠道回执不等于用户已读。
5. 原始活动与记忆分别由各自 SQLite owner 保存；DSH Session 是唯一模型历史。

## 历史回退

旧 OpenClaw 路线已冻结为历史与显式回退，历史基线为提交 `2eeb778a`。它不再维护、不参与默认启动、不自动消费新 home 的配置。两个入口不能同时采集或维护同一来源。迁移和回退流程见[系统概览](../docs/architecture/overview.md#数据迁移与历史回退)。
