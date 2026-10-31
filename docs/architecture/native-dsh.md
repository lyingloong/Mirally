# DSH 运行时

更新：2026-10-31，Asia/Shanghai。状态：现行。验收边界见[验证](../reference/validation.md)。

默认 Host 由同仓 DSH 的 `dsh-app-boot` 启动。`kernel/dsh/packages/mirally/mirally-product/composition.mjs` 通过原生 `loadOverlayPatches` 与 `composeEntries` 加载完整 `@deepseek-ai/dsh-base`，再应用 Mirally 产品覆盖层。上游 base 的声明整体继承，上游新增组件会随之进入；产品不维护一份手工挑选的最小插件清单。

Mirally 的界面、窗口、微信、模型设置和个人数据仍由产品层管理；Agent loop、Session、模型协议、工具执行与下表能力使用实际 DSH 实现。产品层不运行第二套模型循环或会话正文库。

## 继承的能力

| 能力 | 实际实现与产品入口 | 当前边界 |
| --- | --- | --- |
| 会话、停止与续接 | 同一逻辑会话绑定原生 Session；停止调用原生取消并取消待处理输入，不换 Session、不丢工具或图片历史；重启按原生持久化恢复 | 不自动重放未完成动作；旧的已分裂代次不自动合并 |
| 用户会话分叉 | 原会话菜单使用已完成回合前缀创建独立 Session，记录 `parentSession`/`isSeeded` 与继承边界 | 产品库只保存引用和重试身份，不复制正文 |
| 长上下文 | 原生 token meter、compaction、请求失败恢复、工具结果裁剪、图片 offload 与 spill | 压缩质量与具体模型窗口仍需按实际供应商验证 |
| 文件与执行 | 原生沙箱文件 read/write/edit/glob/grep/read_image、Windows pwsh、jobs 与工作目录工具 | 沙箱预设、文件根和审批按 DSH 原生机制执行；产品自有执行器另受 ActionGate 约束 |
| 权限 | 原生 sandbox-policy、approval、permission-presets 保留 | 默认 workspace-write + ask；DSH `never` 不等于自动批准，Mirally 不把它解释为授权 |
| 子代理与工作流 | 原生 spawn/fork、`list_agents`、`send_message`、`interrupt_agent` 与 workflow | 子代理继承父请求模型与沙箱；停止主会话取消后代 |
| 计划、提问、todo、goal | 原生计划状态/审核、结构化问题、todo、goal 与人类命令注册；聊天输入区选择执行/计划 | 挂载本身不等于长期任务质量已经验收 |
| 微信同会话 | 原生工具在微信渠道受同一输入身份和会话边界约束；问题/计划可投递后用 `/answer` 回答 | 复杂多选或过长计划仍提示电脑端处理 |
| Skill、MCP、插件 | 原生 Skill 发现与文件系统能力；Mirally 受监督扩展继续组合 | 只加载明确配置的目录；不读取其他产品凭据 |
| 原生观测与查询 | session-query、持久化投影、storage、token、命令与子代理目录；鉴权接口 `/api/sessions/{id}/native` | 不把能力名称当成已有独立 UI |

## 产品覆盖与关闭项

- 关闭上游账号、默认模型提供方、环境凭据读取、会话日志上传、遥测和厂商包目录。模型连接与凭据只由 Mirally 设置管理。
- 原生会话标题服务保留，但自动标题写入由产品摘要规则替代，避免两个 owner 互相改名。标题与摘要见[会话与作业](conversations.md)。
- 保留 Mirally 界面与扩展管理；不加载上游 Web/CLI 产品界面，不恢复 OpenClaw 或 UFO 推理链。
- 原生公开网页 fetch 可用；搜索只使用用户已配置的产品服务。未配置时不自动采用上游厂商搜索凭据。
- 原生 Windows 沙箱依赖同仓构建的 runner；其他机器须按准备步骤生成，不能引用开发机绝对路径。
- 撤回数据来源、变更扩展等边界仍会隔离旧会话代次；普通停止不再产生新代次。早期已分裂的历史保留原档，只对仍有效来源提供保守文字续接，不能称旧工具和附件已经迁移到当前上下文。
- Session 与投影跨重启保存；运行中的操作、后台进程和子代理不会因恢复历史而自动重跑。旧日志里的 `running` 不能直接解释为仍在运行。

## 源码入口

- 产品组合：`kernel/dsh/packages/mirally/mirally-product/composition.mjs`
- 产品覆盖：`kernel/dsh/packages/mirally/mirally-product/cordis.patch.yml`
- 上游 base：`kernel/dsh/packages/bundle/base/`
- 上游基线与本地改动：`kernel/dsh/UPSTREAM.md`
