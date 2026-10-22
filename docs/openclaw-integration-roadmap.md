# OpenClaw 接入路线

当前产品只有 Mirally 的 OpenClaw Control UI 桌面入口：`python -m mirally`、默认 `run.ps1` 或 `run-openclaw.ps1 mirally`。旧 UFO 专用界面与独立 Tk 客户端已移除，历史设计素材仅作参考。

## 当前职责

OpenClaw 管理对话、模型、会话、工具调度、权限和审批。Mirally 提供 WebView2 桌面宿主、品牌、工作页面、侧边伙伴以及产品设置，不另建第二套会话核心。

主 Agent 优先使用文件、命令、API 和独立浏览器处理后台任务。必须操作 Windows 桌面时，通过 `sessions_spawn(agentId="ufo")` 委派内部 UFO 子代理；插件 Harness 与 Python worker 承载原生执行器。UFO 不作为用户直接对话的独立助手，也未进一步拆成逐个 SDK 桌面工具。

Activity 随保存的采集偏好启动，Memory 在后台做本地持久整理；模型语义整理独立选择。完整边界见 [Activity Memory](activity-memory.md)。模型设置和服务认证仍通过原生定向补丁维护，保留已有配置。

## 后续工作与验证

- 持续核对模型配置、文件与网页任务、运行中补充、停止及审批的原生行为。
- 在实际授权任务下验证 UFO 桌面执行、截图隔离、长调用收尾与主会话结果汇总。
- 上游升级时检查 Control UI 内部组件适配和插件接口，避免把展示层测试当作完整业务验收。
- 如需将 UFO 拆成更细的插件工具，另行设计授权、状态和执行生命周期；当前子代理适配不代表该工作已实现。

本机历史验证只证明当时版本与配置，不保证所有服务、显示环境或用户应用。安装见 [原生运行与验收](openclaw-native-setup.md)，窗口及后台生命周期见 [桌面套壳](openclaw-desktop-shell.md)，UFO 实现见 [子代理接入](ufo-openclaw-agent.md)。
