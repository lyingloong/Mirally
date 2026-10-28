# Mirally 的 DSH 基础运行时

日期：2026-10-28。此文替代此前的最小组合能力对照。验收记录见 [框架验证](framework-v1-validation.md)。

默认宿主由同仓 DSH `dsh-app-boot` 启动；`composition.mjs` 使用原生 `loadOverlayPatches`、`composeEntries` 加载完整 `@deepseek-ai/dsh-base`，再应用 Mirally 产品覆盖层。上游 base 的 96 个声明全部继承，产品新增结构化提问工具及两个产品插件，共 99 个声明；平台条件和显式覆盖决定实际激活范围。组合测试逐项核对原 base，新增上游组件会被继承，不再人工挑选一组最小插件。

Mirally 的界面、窗口、微信、工作、模型设置和个人数据仍由产品层管理；Agent loop、Session、模型协议、工具执行与下述能力使用实际 DSH 实现。产品层不运行第二套模型循环或会话正文库。

| 能力 | 实际实现与产品入口 | 验证 |
| --- | --- | --- |
| 会话、停止与续接 | 同一逻辑会话绑定原生 Session；普通停止调用原生取消并取消待处理输入，不换 Session、不丢工具或图片历史；重启按原生 JSONL 恢复，不重放未完成动作 | 真实模型请求保留用户文字、工具结果、工具回合助手文字及原始图片；重启后 SessionId 一致 |
| 用户会话分叉 | 原会话菜单使用原生已完成回合前缀创建独立 Session；记录原生 parentSession/isSeeded 与继承边界。产品 schema 8 仅保存引用、重试身份及来源依赖，不复制正文到 SQLite | 分叉后实际模型收到原文字/工具/图片；重试只创建一次；撤回来源同时隔离受影响分叉 |
| 长上下文 | 原生 token meter、compaction-basic、请求失败恢复、工具结果裁剪、图片 offload、spill；小窗口模型使用比例阈值和零固定 headroom，避免上游 65k 固定余量吞掉可用窗口 | 本地模型协议验证实际压缩请求、裁剪后的下一请求及原生 compaction/end；聊天中可手动压缩 |
| 文件和执行 | 原生沙箱文件系统 read/write/edit/glob/grep/read_image、Windows pwsh、jobs、工作目录工具；原产品命令工具保留为已有作业兼容入口 | 原生文件落盘、读取后修改和真实 PowerShell 输出；拒绝审批时不产生文件；原生与产品作业不混为一条记录 |
| 权限 | 原生 sandbox-policy、approval 和 permission-presets 保留；默认 workspace-write + ask。Mirally 将原生权限申请和原生命令/写入/停止动作接到现有审批/信任设置 | 实际 native write 拒绝不执行；微信开关和独立信任继承继续校验；不把 DSH never 解释成自动批准 |
| 子代理和工作流 | 原生 spawn/fork、list_agents、send_message、interrupt_agent 与 workflow；模型配置由原生父请求继承。子代理绑定产品根会话、输入渠道及来源检查 | 真实子代理调用配置的本地模型并写入原生子会话目录；停止主会话取消后代。子代理使用继承沙箱的原生执行工具，不经另一条产品执行器扩大权限 |
| 计划、提问、todo、goal | 原生计划状态/审核、结构化问题、todo、goal 与原生人类命令注册；聊天保留原视觉并显示计划开关、问题卡片和压缩按钮 | 实际 Edge 页面的答案进入同一模型工具续轮，计划审核后退出计划模式；todo/goal/工作流已挂载，未将挂载本身视为长期任务质量验收 |
| 微信同会话 | 原生工具在已开启远程控制时受同一输入身份和渠道边界约束；问题/计划完整投递后，用 `/answer 问题码 [题号] 回答` 回答 | 真实 Host + 模型协议 + iLink 回环验证问答续轮，无需云服务器；复杂多选或过长计划提示电脑端回答 |
| Skill、MCP、插件 | 原生 Skill 与文件系统发现，Mirally 受监督 Skill/MCP/可信插件扩展继续组合；明确配置目录、版本和作用域 | 沿用现有协议验收；不自动读取其他产品的凭据或配置 |
| 原生观测与查询 | session-query、持久化投影、storage、原生 commands、token 等服务；鉴权 `/api/sessions/{id}/native` 返回真实命令、投影、工具和子代理目录 | 原生服务返回，不使用能力名称伪造可用状态；不将全部原生观测都宣称已有独立 UI |

产品覆盖和边界：

- 关闭上游账号、默认模型提供方、环境凭据读取、会话日志上传、遥测和厂商包目录；模型连接和凭据继续由 Mirally 设置明确管理。原生会话标题服务保留，模型自动标题写入器由现有产品标题规则替代，避免两个 owner 互相改名。
- 保留 Mirally 界面和扩展管理。原生 profile 设置/HMR/插件管理没有另开一套入口；不加载上游 Web/CLI 产品界面，也不恢复 OpenClaw 或 UFO 推理链。
- 原生公开网页 fetch 可用；搜索通过用户已配置的产品服务，未配置时不自动采用上游厂商搜索凭据。原生 Windows 沙箱依赖同仓构建的 runner，其他机器须按准备步骤生成，不引用开发机绝对路径。
- 普通停止不再产生新代次。撤回数据来源、变更扩展等边界仍会隔离旧代次。早期版本已经分裂的多代会话保留原档，不重写/合并旧日志；仅对仍有效来源提供保守文字续接，不能称其旧工具和附件已经自动迁移到当前模型上下文。
- Session 和投影跨重启保存；运行中的操作、后台进程和子代理不会因恢复历史而自动重跑。进程状态应据实际作业回执核对，不能把旧日志中的 running 直接解释成仍在运行。
- 本轮实际调用均为本地模型/iLink fixture；没有重跑用户实验、消费真实 API 或重启用户当前实例。声明已挂载与具体供应商/桌面长期效果分别验收。

依据：[原生 base](../kernel/dsh/packages/bundle/base/cordis.patch.yml)、[原生说明](../kernel/dsh/packages/bundle/base/README.md)、[组合器](../kernel/dsh/packages/mirally/mirally-product/composition.mjs)、[产品覆盖](../kernel/dsh/packages/mirally/mirally-product/cordis.patch.yml)。
