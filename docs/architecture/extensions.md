# 扩展接口与生态兼容

更新：2026-10-31，Asia/Shanghai。状态：现行。相关实现：`kernel/dsh/packages/mirally/mirally-extensions/`、`mirally/framework/service.py` 的扩展配置。

Mirally 通过明确接口接入新来源、服务、执行、投递和交互能力，同时保留 Skill、MCP 与插件的原有格式和协议。内置实现与外部扩展使用同一套生命周期和权限边界；产品不另建第二个通用插件宿主。

## 三种边界

| 概念 | 职责 | 示例 |
| --- | --- | --- |
| 普通模块 | 领域内部的规则与数据操作 | 记忆冲突判断、候选去重、报告格式整理 |
| 扩展点 | 由责任领域定义输入、输出、时机和保证 | 来源读取、投递渠道、执行器、界面位置 |
| Extension | 可配置、声明依赖与兼容范围、管理资源生命周期的集成单元 | 日历集成同时提供来源、提醒触发和卡片 |

一个 Extension 可以跨多个领域，但各领域的权威状态仍由原 owner 管理。Extension 不等于一个模型 Agent、进程或商店安装包。

## 扩展面

| 扩展面 | 主要契约 | 权威归属 |
| --- | --- | --- |
| 来源 | 来源身份、发现/启停、变化或观察、游标、时间/质量/缺口、撤销 | 多来源并存；Activity 或相应领域接纳 |
| 情境与记忆 | 有预算的查询、带来源/版本/时效的结果、候选提交 | Context 选择与裁剪；Memory owner 决定写入与撤回 |
| Skill | 目录与元数据、按需正文、相对资源、依赖能力 | 通过宿主 Skill 机制加载，不成为长期记忆 |
| 能力与执行器 | 请求/结果 schema、真实效果、操作身份、状态/回执、超时和取消 | 注册工具或委派适配；效果由能力内部核验 |
| 投递渠道 | 可用性、内容与附件支持、提交身份、送达/回复回执 | 产品选择投递策略；渠道回调不等于用户同意 |
| 界面 | 产品视图、可用位置、输入命令与交互状态 | 共享权威会话和业务状态 |
| 触发与策略 | 变化触发、机会候选、时机建议、理由与有效期 | 领域定义合并/优先级，不能绕过设置与预算 |
| 模型 Provider | 路由与能力声明、输入/输出、用量和错误 | 复用或修改内核 provider 机制 |

接口的组合方式也属于契约：来源和渠道可以多选；检索结果按预算合并；有权威状态的后端必须显式选择一个实现；安全和授权限制只能收紧。

## 生命周期

1. **装配前检查**：检查接口版本、必需服务、配置和能力；未知依赖、重复身份、独占冲突时拒绝激活并说明原因。
2. **受控启用**：装配成功后开放贡献；配置声明访问范围不自动授予权限，后台模型仍受统一预算。
3. **停用先关准入**：阻止新调用，按依赖顺序排空在途工作，释放连接和资源；迟到回执保留，迟到计划不能继续执行。
4. **状态有归属**：扩展自有游标和作业使用命名空间存储；已进入 Memory、承诺或原生 Session 的内容仍由原 owner 管理。
5. **替换与升级显式发生**：固定版本，检查依赖方与数据迁移；有权威状态的实现先停写、迁移，再绑定新实现。
6. **重启是允许的选择**：首版不承诺任意热插拔；重复启停不能遗留 timer、writer 或隐形订阅。

同进程插件是受信代码，注册时的权限声明不构成安全沙箱。需要接入不受信代码时，应选择受控进程或远端适配，不把“已安装”当成“已授予全部本机访问”。

## Skill 兼容

支持标准 `SKILL.md`（name/description 元数据）、按需正文、`scripts/`、`references/` 和 `assets/`；保留原目录结构和相对引用，不把全部技能正文放入每轮请求。

- 先索引元数据，选用后加载正文，所需资源再读取；同名技能可明确绑定或选择，不因扫描顺序静默覆盖。
- 正文提到 Python、shell、外部服务或工具，不代表这些能力已经可用。脚本必须通过显式配置的脚本能力接入。
- 脚本条目声明解释器（Python 或 Node）、路径和内容指纹，必须位于已配置的 Skill 根目录；每次执行在 UI 明确确认，命令 ID 去重，运行前复核路径和 `sha256`。修改脚本后需重新审阅并配置新指纹。
- 脚本使用现有范围、预算、超时、输出限制与取消语义；同用户进程不能承诺读取/网络隔离。

```json
{"id":"local-skills","type":"skill","path":"./my-skills"}
```

```json
{"id":"utilities","type":"bundle","config":{"apiVersion":1,"skills":["./my-skills"],"scripts":[{"id":"export","path":"./my-skills/helper/export.py","interpreter":"python"}]}}
```

## MCP 兼容

支持本地 stdio 与 Streamable HTTP。同一受监督连接提供 tools、resources、resource templates 和 prompts，包括取消、重连和停用。

- 工具保留 server-qualified 身份、schema 和原始结果；资源和模板按 URI/名称按需读取，prompts 是可发现/显式调用的模板，不自动注入系统指令。
- 配置中的 `readOnly:true` 是用户对该服务的信任声明，不能仅凭工具名称推断只读；可能写入的工具通过扩展页明确确认后复用原生工具执行管线。
- 协议发现可能启动本地 server，不能冒充纯读文件；连接重建不等于可以重做未确认的远端动作。
- Mirally 自有 Activity/Memory server 的启动和发现不启动采集；扩大导出范围属于后续显式设计。

```json
{"id":"my-mcp","type":"mcp","config":{"serverName":"local","transport":"stdio","command":"python","args":["./my_server.py"],"readOnly":true}}
```

`command:"python"` 使用仓库 `.venv`；`node` 使用宿主 Node。相对路径以仓库为基准，外部目录可用绝对路径，迁移机器后需重新绑定。

## 插件兼容

```json
{"id":"my-plugin","type":"plugin","path":"./my-plugin.mjs","config":{"apiVersion":1}}
```

模块可导出 `mirally={apiVersion:1}` 与原生 Cordis 插件；不兼容版本拒绝激活，`dependsOn` 声明其他扩展依赖。插件通过 `ctx.mirallyProduct`、`ctx.tools`、`ctx.skills` 等现有服务接入，外部效果必须遵循产品 guard、停止和回执契约。OpenClaw 插件不能不经适配直接加载；其他宿主的同名“插件”也不保证兼容。

## 当前范围与验证

- 已实现纯说明 Skill、资源 Skill、显式脚本、MCP 工具/资源/模板/prompts、可信原生插件及停用撤销。
- 扩展变更会使后续会话能力变化；已加载进原生 Session 的说明或模板不会随目录卸载消失，按来源失效/必要会话交接处理。
- 插件市场和任意热更新未实现；扩展兼容范围以实际测试为准，不把源码可改或上游声明当成已兼容。

```powershell
node --test kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools\verify_framework.py
```

`verify_framework.py` 覆盖真实 MCP 协议、Skill 与插件生命周期；扩展页面与脚本确认另经浏览器验收。

相关：[DSH 运行时](native-dsh.md)、[能力矩阵](../reference/capabilities.md)。
