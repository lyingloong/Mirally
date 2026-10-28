# DSH 最小组合的历史能力审计

下文保留本轮改造之前的审计依据，已由 [继承标准 DSH base 的实现](framework-native-dsh.md) 替代，不能当作当前装配状态。

日期：2026-10-28。本文根据本仓库当前源码核对，不代表 DSH 全部插件都适合直接启用，也不把源码存在视为已在 Mirally 验收。

Mirally 当前 `kernel/dsh/packages/mirally/mirally-product/cordis.patch.yml` 自行组合 DSH 的 Agent loop、Session、持久化、LLM、工具注册、Skill 与附件；它没有加载 DSH 的标准 base bundle。产品自身组合文件、进程、浏览器、工作和渠道。这带来了明确的功能缺口，不能以“使用 DSH 内核”暗示标准 DSH 能力均已继承。

| 能力 | DSH 标准 base | Mirally 当前默认入口 | 差距 |
| --- | --- | --- | --- |
| 会话与模型 | 原生 Agent、Session、持久化、模型路由 | 原生 DSH loop 和 Session，产品逻辑会话与模型连接 | 同一原生代次内沿用历史；停止后跨代次仅恢复有界、安全的文字上下文，不恢复工具结果或附件 |
| 长对话 | token meter、自动压缩、溢出恢复、工具输出裁剪 | 未挂载这些 DSH 组件 | 长会话的自动压缩与溢出恢复未实现；不能声称任意长度可持续 |
| PowerShell 与进程 | Windows 原生 PowerShell、作业和沙箱组合 | 自有 PowerShell 和内存中受管作业 | 可查询和取消，但非 DSH 沙箱，作业不跨重启恢复，工具接口与权限模式不同 |
| 文件 | 原生读取、写入、编辑、搜索及沙箱 | 有界授权目录读取/搜索、工作区文字写入和会话成果；命令可按用户权限访问文件 | 无等价的原生编辑工具与沙箱；通过 shell 访问不等同于完整文件编辑体验 |
| 权限 | sandbox-policy、user-approval、permission-presets | Mirally ActionGate 的逐次、临时会话和持续信任；微信独立授权 | 两者不是同一执行器，DSH `never` 不代表自动批准；若接入原生工具需统一映射和端到端核验 |
| 多代理与任务 | subagent / fork / 控制、todo、goal | 工作领域及产品页面，默认未组合原生子代理、todo、goal | 页面或长期方案不等于模型可调用这些内核能力 |
| 交互与计划 | 用户问题、plan-mode、命令和状态投影 | 普通对话与 Mirally 自有工作流程 | 未组合完整原生结构化提问和计划模式 |
| Skill 与 MCP | Skill、MCP tools/resources/prompts | 通过 Mirally 扩展组合 DSH Skill 和受监督 MCP；脚本单独授权 | 已有明确范围和协议适配，不能推断所有上游插件或界面都兼容 |
| 网页 | 标准搜索和公开网页抓取 | 受配置约束的网页服务和独立浏览器 | provider、网络边界、登录态与界面不同，不应视为逐项等价 |
| 插件和运行时 | 标准插件管理、受管理执行环境、工作流等 | 受信任的 DSH 原生插件和 Mirally 扩展入口 | 并非已继承完整插件管理、工作流、所有插件的版本/生命周期行为 |

建议先补齐长会话与可追溯上下文，随后按真实产品接口整合 DSH 的文件、进程和权限机制，再考虑结构化提问、计划、子代理和工具输出管理。每项应核对 Windows 执行能力、唯一数据归属、微信权限与停止行为，不把遥测、上游默认网络服务或第二套产品状态无差别引入。

上游直接依据：[DSH base 配置](../kernel/dsh/packages/bundle/base/cordis.patch.yml)、[DSH base 说明](../kernel/dsh/packages/bundle/base/README.md)、[DSH 压缩说明](../kernel/dsh/packages/compaction/compaction-basic/README.md)、[Mirally 宿主组合](../kernel/dsh/packages/mirally/mirally-product/cordis.patch.yml)、[Mirally 扩展](../kernel/dsh/packages/mirally/mirally-extensions/src/index.mjs)。
