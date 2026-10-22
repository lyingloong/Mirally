# Mirally personal agent 架构调研与取舍

整理日期：2026-10-22。资料核对与研究发生于 2026-10-21 至 22 日。下文 §1–9 保留当时对应推荐案 v0.4 的研究结论与证据；后续用户已改变源码取得和开发路线，当前决定见 §0、[主案 v0.9](../personal-agent-framework.md)与[实施计划](../framework-refactor-plan.md)。历史上的“优先正式扩展/官方发行包、必要时才 fork”不再是当前开发限制。

后续用户明确扩展性与 Skill/MCP/插件兼容为核心要求，并询问模块耦合与整体运行，主案已更新为 v0.7，补充[扩展接口与生态兼容设计](../framework-extensions.md)及交互归属/依赖方向。本报告保留原选型证据与反例；新接口草案尚未经过运行验证，不据此扩大 DSH 已验证能力。

实施状态补充：用户先授权建立完整重构分支，随后明确要求复制 DSH 为同仓内核并允许侵入式修改。此前官方依赖建议被这一明确选择推翻，不删除原记录来伪装研究从未有过取舍。源码导入、构建和端到端运行仍是不同状态，本报告不证明新宿主或迁移已实现。

本次比较了当前接入、DSH 固定源码、不同宿主结构，以及记忆和主动交互的一手研究；分别进行运行时可实现性、架构备选、产品反例研究，再对推荐案复核。研究只读源码、使用临时数据库和离线模型；没有启动 DSH、调用真实模型、操作桌面或修改现有运行配置。

## 0. 用户后续决定：同仓源码内核（当前路线）

采用 `kernel/dsh/` 纳入主仓库源码的方式，基线为 tag `dsh-v0.2.1-rc.2`、commit `687f282e8625c6b8d11ff58211a90524be2e4317`。来源与本地修改记录见[内核说明](../../kernel/README.md)及[UPSTREAM](../../kernel/dsh/UPSTREAM.md)。研究缓存仍不是产品依赖；导入文件本身也不能作为构建或运行成功证据。

用户已授权直接修改内核 API、loop、Session、持久化、宿主、UI 与扩展机制，不再优先使用官方包或通过外部插件绕接缝。Profile、Bundle、Preset、Slots 可按需要复用，不足时直接整改内核；正式接口不足、需要内核变更不再自动触发保留 OpenClaw 或改 SDK 外宿主的选择。

保持的设计原则是领域状态归属、单一会话权威、明确扩展契约、受控实际效果以及真实取消/恢复。侵入式权限不表示所有业务进入 loop，也不表示包兼容、遗忘或外部效果恰好一次已经得到保证。Session 格式和插件 API 变更需要本地版本、迁移与调用方测试；维护成本转为主动承担内核构建、回归和选择性吸收上游，不能仅保留上游版本号就冒充未修改发行版。

下一步门槛是从同仓固定源码复现构建并实际跑最小端到端：独立 home、客户端、fake 模型、受控工具和临时 Python worker，验证输入、流式回答、持久化、取消、重启与投影。控制缺口直接修改内核并补回归；后续再验收产品领域、生态、完整体验、状态迁移与移除 OpenClaw。没有完整测量前仍不宣称更轻、更快；真实模型、桌面与私有数据验证不由源码导入自动完成。UFO 子模块继续只读。

## 1. 原研究的推荐与最强反对理由（历史判断）

推荐先建立 **Mirally 自有领域模块 + DSH 正式组合机制 + 既有 Python worker**。产品长期状态不依附于某条会话；普通模块负责产品规则，DSH 负责原生会话和模型—工具循环。下一阶段只做一个隔离的可行性切片。

最强的反对理由是：DSH 仍处于 developer preview，自有 Profile 中的产品后台与它共享 Node 故障和版本边界；新插件体系也会带来维护成本。假如最终仍需私有 UI/控制补丁，或者没有功能对等的资源与维护收益，迁移就只是更换依赖。此时应保留 OpenClaw，在现有宿主上建立自有产品层，而不是为了迁移而迁移。

这是研究时带推翻条件的推荐，不宣称存在脱离产品阶段、维护者与成本的唯一“最好架构”。当前已采用 §0 的同仓源码路线，以下仍保留该判断的依据和代价。

## 2. 证据基线

| 对象 | 核对基线 | 能说明什么，不能说明什么 |
| --- | --- | --- |
| 当前 Mirally | 本工作区源码；OpenClaw / gateway-client 均锁定 2026.9.6 | 能说明当前职责和适配；工作区有大量既有未提交变更，不等于已发布版本 |
| DSH | tag `dsh-v0.2.1-rc.2`，commit `687f282e8625c6b8d11ff58211a90524be2e4317`，2026-10-17 预发布；22 日再次核对 release 列表 | 文档与源码支持接口存在；没有本机运行、完整性能与兼容验收 |
| DSH 默认分支 | 21 日下载的 master archive，关键控制/SDK/后台文件与 tag 相同 | 仅补充对照；未取得可靠 commit SHA，不以可变分支证明发布能力 |
| 外部框架与 HCI 研究 | 当日官方文档、论文作者的公开论文/仓库 | 提供机制、反例与评价方法；不能证明 Mirally 的真实用户收益 |

固定 tag 源码 archive SHA-256 为 `dec9f36f9b25028ca8de2a7520e16029ad36e8328078fb33b85b1b9b338d5690`。本机临时研究源和独立笔记位于 `.artifacts/framework-research/`，不是安装依赖或正式产品组成。下文引用固定官方路径，阅读不依赖本机临时产物。

## 3. 当前接入意味着什么

当前链路是 `run.ps1 → python -m mirally → WebView2 中的 OpenClaw Control UI → 原生 WebSocket → Gateway → 模型/工具`。普通聊天不是经过 Python 自写模型代理。Python 宿主连接已有 Gateway 或启动它；关闭窗口不等于 Gateway 退出。[产品宿主](../../mirally/openclaw_host.py)、[窗口壳](../../mirally/control_ui_shell.py)

Activity 与 Memory 已有独立 Python 数据责任。Activity 通过本地 MCP 暴露读取，发现/查询不应启动采集；当前采集生命周期仍由桌面入口管理。Memory 插件监督 worker、提供宿主模型路由和相关问题的自动召回；已有墓碑、版本、租约和用量机制值得保留。UFO 代码作为内部执行器存在，是否实际加载以配置为准，不能把代码存在写成用户已获得该能力。[Memory 插件](../../integrations/openclaw/memory/index.mjs)、[数据 store](../../mirally/core/memory/store.py)、[现有接入](../ufo-openclaw-agent.md)

更换宿主既不会自动提升活动质量，也不会自动修复模型服务迟缓。既有[响应耗时记录](../response-latency.md)同时涉及准备阶段、工作目录、数据库排队和模型服务行为；缺少完整同功能 DSH 进程树对照，不能从其中推出 DSH 必然更轻、更快。

## 4. 真正不同的架构路线

以下是原研究时的结构判断，不是任意数字评分；当时未选择同仓侵入式内核，不能把表中选择视为后续用户决定的替代。

| 路线 | 获得什么 | 增加什么责任 | 研究时判断 |
| --- | --- | --- | --- |
| O：继续 OpenClaw，采用官方 Feature UI 扩展整理产品层 | 复用已运行的模型、会话、MCP、Memory 与 UFO 接入，迁移面最小 | 继续承受该宿主 UI/API/配置约束；扩展仍实验性 | 必须保留的现实对照，不需等换内核才建立领域边界 |
| A：自有 DSH Profile/Bundle，少量组合插件与 Python worker | 完整原生控制、会话投影和公开插件装配，避免第二套控制协议 | 共享宿主故障/升级，需解决产品后台、打包与适配 | 下一阶段原型首选，尚未成为生产选择 |
| B：独立 Mirally Host + DSH SDK 子进程 + 补充协议 | 产品入口和记录可独立存活，执行器可按需退出 | SDK 控制不足；补取消、历史、回执、审批与双端监督 | 仅在隔离/资源收益被证明时重开 |
| C：耐久 Workflow / Actor 统领产品 | 对明确长步骤有持久执行和串行所有权 | 长寿命执行兼容、重放/副作用约束，以及额外平台或图状态 | 借局部机制；不用它表示个人生命史或全部对话 |
| D：自研小 loop | 可以完全控制执行基础 | 模型流、工具/MCP、压缩、输入、取消、恢复均自负 | 暂不承担，差异价值主要在产品领域 |

OpenClaw v2026.9.6 已有 typed operations、页面、panel 和 composer/transcript/workspace replacement。当前 DOM 注入的局限不能全部归因于缺少正式扩展；但 API experimental、同源和原生状态 owner 等约束仍需验收。[官方 Feature plugins](https://github.com/openclaw/openclaw/blob/v2026.9.6/docs/plugins/feature-plugins.md)

LangGraph 区分线程 checkpoint 与跨线程 store，恢复时未完成 task 仍可能重执行；Temporal 的可重放编排仍要求 Activity 幂等与代码版本兼容；Actor 的串行请求也不等于外部效果耐久。这些机制有价值，但不能替代个人知识与承诺本身的模型。[LangGraph Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)、[Temporal Activities](https://docs.temporal.io/activities)、[Temporal workflow versioning](https://docs.temporal.io/develop/python/workflows/versioning)、[Orleans persistence](https://learn.microsoft.com/en-us/dotnet/orleans/grains/grain-persistence/)

## 5. 改变选型的 DSH 源码事实

### 5.1 官方扩展可以承载自有框架，但仍需产品验证

Profile 是进程组合，Bundle 分发配置与插件，Preset 配置单个 Agent；可从 web 派生 named profile 并加入外部包，无需手工摘取内部 loop 或先 fork。配置 patch 替换的是整行 config，不能假定递归合并。[组合架构](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/docs/architecture.md)、[app-boot](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/boot/app-boot/README.md)

正式客户端分层为 Host/Remote、React-free ClientSessions、会话 UI 与 Slots。自有 UI 可使用同一个 SessionBinding 和 Conversation Component Factory；不必复制 transcript 或打开另一条历史流。不过同 Session 两个可编辑 root composer 不是现成承诺，客户端模块打包与粗粒度 slot 替换也有约束。[Web client](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/docs/subsystems/web-client.md)、[ui-conversation](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/client/ui-conversation/README.md)、[Client modules](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/docs/subsystems/client-modules.md)

### 5.2 SDK 与 Web 控制面不等价

| 产品需要 | rc.2 SDK | 原生 Web Session Controller |
| --- | --- | --- |
| 客户端 prompt 重试身份 | 没有客户端 requestId | requestId 进入 user source rpcId，检查 inbox/原生日志 |
| 补充和逐会话取消 | 普通 prompt；无 cancel/session-close | queue/steer、队列操作、cancel |
| 历史与断线补洞 | resume 不重播旧通知 | page/follow、序列与重连恢复 |
| 审批请求 | server→client request 尚未使用 | 原生产品交互体系可复用，具体能力仍需验收 |
| 返回值语义 | run 等整个 Agent idle，最后根回复不等于某 prompt 的响应 | 直接读取原生会话/工具状态，不应另造单 prompt 因果假象 |

依据：[SDK 协议](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/sdk/protocol/README.md#known-limitations-and-deferred-work)、[SDK client](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/sdk/client/README.md)、[Session Controller](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/api/session-controller/README.md)。因此不能把 B 当作与 A 等能力、只多一层函数调用的结构。

### 5.3 accepted、flush、取消与恢复

Controller prompt 在 followup/steer 后返回 accepted，没有等待 flush；持久化的完成 flush 才是崩溃耐久屏障。requestId 去重有原生依据，但不自动给出跨产品数据库的事务。其检查发生在异步附件 admission 之前且未再次检查；并发同 ID 可能重复入队，清除尚未持久进入日志的 inbox 后也可能失去身份。因此产品门面还需按 Conversation+命令 ID 唯一接纳，首次绑定目标代与内容摘要，并保留取消/拒绝终态；重试不能跨代重新绑定。不是只加 flush 即可。Controller cancel 请求取消并保留 inbox，立即返回 accepted，也不证明外部 worker 停止。[输入源码](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/api/session-controller/src/commands.ts#L321)、[取消源码](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/api/session-controller/src/commands.ts#L497)、[持久化契约](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/session/session-persistence/README.md)

checkpoint policy 在重要模型/工具边界 flush；崩溃恢复仍可产生 TOOL_OUTCOME_UNKNOWN。Session resume 恢复日志并修补中断尾部，不恢复外部进程。Mirally 应复用这些事实，再定义自己的持久接收、停止范围和效果核对。[checkpoint policy](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/session/session-checkpoint-policy/README.md)、[Agent API](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/core/agent/README.md)

### 5.4 后台不是另开一条模型聊天

Host 插件可直接使用正式 LLM stream/prepareCall 服务，选择路由、超时和预算；无需为一次小整理建立 Task 或完整 Agent。业务作业仍负责版本、用量、重试与输入/输出记录。已有 Python MCP worker 可保留，宿主模型桥接尤其要验证多模态、reasoning、取消和错误传播，不能假定现有 OpenAI 兼容桥自动适配所有 DSH provider。[LLM 服务](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/llm/llm/README.md)、[MCP](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/mcp/mcp-client/README.md)

原生 schedule 是持久提醒与向原 Session 入队的机制，依赖 Web Controller/持久化/活着的 Host；不是跨入口的主动联系服务。jobs-local 则随 owner/Host 退出丢失活作业，不能承担业务恢复。[schedule](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/schedule/schedule/README.md)、[jobs-local](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/jobs/jobs-local/README.md)

### 5.5 原生历史不能被简单等同于可遗忘记忆

DSH surface replacement 能改变未来模型输入，原始日志仍保留；tool/result replacement 的范围也受限。被采用的记忆还可能进入 assistant 回答、压缩摘要和子会话，不能靠遮蔽一个节点保证所有副本停止使用。推荐保留产品 Conversation 身份，以必要的 Session 代际交接处理严格停止使用；普通纠正不每次重建。[原生 Session](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/core/session/README.md)、[surface 实现](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/core/session/src/surface.ts#L455)

新 content-changing event 还涉及已知事件目录和脱离插件后的读取，不是任意第三方注册后就能无条件恢复。产品来源元数据应优先利用已知消息格式与引用，不顺手扩张原生持久格式。[持久化来源测试](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/session/session-persistence-jsonl/tests/native-source-admission.spec.ts)

### 5.6 体量、数据路径与 Windows 边界

Web profile 仍组合较完整的 base；minimal Agent preset 主要缩减模型工具，不等于精简 Host。sdk-minimal 更小，但缺 Web/设置/凭据等产品基础，不能拿它与完整 Mirally 对比。[base](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/bundle/base/README.md)、[sdk-minimal](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/bundle/sdk-minimal/README.md)

Session OTel feedback、官方 DeepSeek API 的 session-log 以及可选 Desktop 统计是不同路径，关闭一个不表示全部关闭。Windows ACL sandbox 对写入与删除的限制也不能当读文件或网络隔离。这里只将其列入打包/迁移的具体核验项，没有改现有设置，也没有宣称本次完成安全验收。[OTel](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/session/session-telemetry-otel/README.md)、[session-log](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/session/session-log-deepseek/README.md)、[Windows ACL](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/sandbox/sandbox-windows-acl/README.md)

## 6. 记忆与主动交互研究带来的取舍

| 一手依据 | 可借鉴什么 | 不能外推什么 |
| --- | --- | --- |
| [Generative Agents，2023](https://arxiv.org/html/2304.03442v2) | 检索、计划与保留证据指针的反思；同时报告检索失败和记忆 embellishment | 模拟人物的可信度不等于真实用户事实准确、长期陪伴有效；不据此引入递归反思树 |
| [MemGPT，2024 版本](https://arxiv.org/html/2310.08560v2) | 有限上下文与外部存储分层、按需读取 | 自编辑内存不等于用户纠正/遗忘治理，也不证明越多检索轮次越好 |
| [LongMemEval，作者仓库](https://github.com/xiaowu0162/LongMemEval) | 信息提取、跨会话、知识更新、时间推理与弃答的分项评测 | 聊天问答基准不测活动误归因、主动时机或遗忘污染，不用榜单替代产品回放 |
| [Human-AI Interaction Guidelines，CHI 2019](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/)、[HAX 设计库](https://www.microsoft.com/en-us/haxtoolkit/library/) | 情境时机、便捷调用/结束/纠正、谨慎适应和全局控制；论文经 49 位从业者、20 个产品评估 | 通用交互指导不能给出 Mirally 的通知频率或证明其体验收益 |
| [Mixed-Initiative UI，CHI 1999](https://erichorvitz.com/chi99horvitz.pdf) | 目标不确定性、打扰与延迟成本、低成本纠错 | 不能从键鼠空闲直接可靠推断愿意聊天或心理状态 |
| [Developer Interaction Patterns with Proactive AI，IUI 2026](https://arxiv.org/html/2601.10253v1) | 15 人、5 天、229 次干预中，工作边界比任务中介入更易接纳；控制与上下文重要 | 样本小且限定开发工作，不能给跨生活场景统一阈值 |
| [“Having Lunch Now”，CHI 2026](https://arxiv.org/html/2509.24073v2) | 12 人、两周部署中，固执议程、抢答和假称记忆/提醒能力会破坏相处 | 不证明长期幸福感或普遍陪伴效果，不采纳增加多 Agent 的推测为必要结构 |

结合本仓库，最值得保留的是经历/主张的来源、时间与纠正，而非更大的“人格”。现有 Memory 已有 candidate、supersede、墓碑、租约和词法召回；同 ID / conflict_key 并不是自然语言全局矛盾检测，当前候选确认也不能冒充完善的产品体验。

高主动用户、低打扰用户与低资源用户可共用底座，但必须允许不同来源、联系策略与预算。明确“以后这样做”“某时提醒”在已有权限内直接落实并可撤销；低价值推断允许丢弃，不建立待用户逐条确认的画像或建议收件箱。若陪伴无法相对同模型普通聊天减少重复说明、改善接续与兑现约定，应减少后台自主闲聊。

## 7. 持久化与故障反例

本地持久状态不需要全局事件溯源。普通事务记录加必要版本/引用更容易迁移和清理；事件通知已发生事实，回放视图不重新产生外部动作。CQRS 与 Event Sourcing 的原作者资料也强调适用范围和外部效果重放成本，不能仅因需要历史就采用整套模式。[CQRS](https://martinfowler.com/bliki/CQRS.html)、[Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html)

SQLite WAL 允许读写并行，但同一文件仍只有一个 writer；多个 attached 数据库不是一组原子提交，网络文件系统也不是合适的 WAL 共享方式。因此不把模型调用放在写事务中，不通过跨数据库锁模拟全局快照。备份使用数据库支持的机制，不在运行时只复制主文件而丢失 WAL。[SQLite WAL](https://sqlite.org/wal.html)

| 反例 | 容易出错的设计 | 推荐规则 |
| --- | --- | --- |
| 入队后用户停止，worker 随后执行 | 只在接纳时检查一次 | 实际效果 owner 在执行前检查有效代际；停止确认覆盖相应排队和 worker |
| 模型生成中用户撤回来源 | 只在读取时检查，迟到结果又写回 | 提交、再次引用与投递前检查来源版本；不复活撤回资料 |
| 外部动作成功，收据尚未本地提交就崩溃 | 一见本地 pending 就自动重试 | 提供方幂等键或外部核对；不能核对则未知，禁止盲重做 |
| 跨库清理途中崩溃 | 只有一个“已删除”布尔值 | 先持久阻断未来使用，再按范围清理并恢复未完操作 |
| 睡眠后积压很多触发 | 补跑每个 tick、逐条模型分析 | 按承诺补送/过期规则与候选合并策略恢复 |
| 低成本模式关闭来源 | 核心假定一定有完整 Activity | 缺口显式、来源可缺省；明确无法提供的主动范围 |

### 本次实际运行的验证

- 根 `.venv` 运行现有 `test_memory_store.py`、`test_memory_semantic_lifecycle.py`、`test_memory_retention.py`，**79 项通过**。它们使用临时数据库与模拟模型结果；支持现有存储/租约/墓碑/清理资产的可复用判断，不证明真实模型整理质量或 DSH 集成。
- 本地 `continuity_probe.py` 枚举四种合法 `prepare/admit/effect/stop` 顺序：只检查 admission 出现一个晚到效果反例；效果边界再次检查在这个抽象模型中无该反例。另四种 `read/generate/commit/revoke` 顺序中，只在读取时检查出现两个复活反例，提交版本检查排除它们。
- 临时 SQLite outbox 加独立模拟外部效果，注入“动作成功、回执前崩溃”：盲重试产生两次效果；无幂等支持时保留 unknown 避免重复，但牺牲自动完成；模拟提供方支持幂等键时同键重试只产生一次效果。

后两项仅是小型设计模型，假设效果 owner 串行检查、来源版本单调变化、幂等提供方确实守约；它们**没有验证**跨进程取消、真实幂等、并发 requestId、DSH flush、Windows 退出树或语义遗忘。通过抽象模型不能宣传功能已实现。

## 8. 原研究的验证顺序与选择门槛（历史判断）

以下记录当时正式扩展路线的次序和放弃条件，现由 §0 与[重构计划](../framework-refactor-plan.md)的源码构建、直接整改和端到端验收替代；不作为当前“禁止改内核”的依据。

当时建议不并行开发两套完整产品。先 A 的一个切片，再针对关键缺口用 O 对照，B 只在出现明确隔离需求时补协议研究。

产品对照与技术切片同时准备：①给同一段经历和缺失信息，比较同模型普通聊天与产品能否跨会话正确回答/弃答，减少用户重述；②“仅今晚安静”与“以后晚上别主动聊工作”分别在当晚、次日和重启后检验，允许结束且不制造审核负担；③明确提醒必须有真实持久 ID、取消与补送规则，不能只凭模型答应。前两项的实际有用性需要模型和用户评估，fake 协议测试不能代替。领域体验有价值而 DSH 无额外收益时，保留 O 仍是成功路线。

首次使用也有可验收基线：未来新安装默认仅使用用户提供内容，Activity 和一般主动话题关闭，背景模型预算为零；明确偏好/提醒可直接落实。关窗保留托盘 Host，明确退出停止后台，不默认承诺自启或唤醒。已有安装不被覆盖；固定宿主成本与用户是否理解该行为仍须验证，避免以复杂设置问卷替代产品选择。

| 门槛 | 必须得到的证据 | 不通过时怎么做 |
| --- | --- | --- |
| 正式 UI 与会话控制 | 外部客户端包、共享会话投影、审批/补充/取消/断线恢复不依赖私有 DOM | 保留 O；或证明 B 的窄协议确实更简单 |
| 回执、停止、撤回 | fake 模型/worker 下，重复 ID、丢回执、flush 失败、慢停、旧代唤醒与迟到结果的故障注入 | 缩小承诺与控制范围，先补适配；不迁移后再修语义 |
| Python 与 Windows | 原存储/配置可复用；被动发现、模型桥、退出树、不同目录与路径可运行 | 不为共享宿主重写大量已验证资产；必要时转 B |
| 体验边际收益 | 生活回忆、偏好、提醒/主动和报告等多场景，相对普通聊天/规则提醒有可解释收益 | 减少无收益自动化，不因“像 Agent”保留功能 |
| 完整资源与维护 | 相同采集/记忆/工具/窗口、相同模型下的整个进程树、冷/热交互、空闲 CPU/RSS、后台用量与升级工作量 | 无实际收益则留 O；若主要是常驻执行器成本，再评估 B |
| 迁移与回退 | 配置/数据备份、旧历史只读、唯一采集与维护者、固定配套发布和回退步骤 | 不切正式入口，不做双生产写入 |

测量先记录版本、数据量、后台开关、工具数、缓存/冷暖状态和硬件；重复多轮报告中位数及尾部，不把模型网络波动归给宿主。目标值应基于当前基线、实际设备与用户可感知差异制定，本次没有虚构百分比收益或给出已测性能排名。

以下情况推翻 A：公开扩展不足而持续需要私有补丁；必须让产品后台跨 DSH 故障独立存活；固定宿主成本不满足目标且 B 实测更优；为共享宿主重写 Python 的成本大于补充协议。若 O 官方 Feature 扩展已经足够且完整成本可接受，则先在 O 建设 Mirally 领域。只有真实长任务的恢复复杂度反复超出简单状态机时才重开 C；只有执行基础持续阻塞必要语义且自维护范围可控时才重开 D。

本次交付的是可被证伪的推荐与实施前门槛，没有将原型、规划或模拟测试写成已实现产品能力。

## 9. 反例复核后的修订

独立架构审查和产品审查共提出八项有效问题，已并入 v0.4：命令并发/终态身份、旧代冻结的持久禁令与启动屏障、通用 shell/read/PTC 的旁路、活跃会话中的旧事实纠正、Session 代级来源使用回执、真实建立提醒的收据、提前开展产品对照，以及单一首次使用默认。

这些修订不增加一套通用内核或语言因果图。必要元数据只服务于已承诺的去重、撤回和恢复；无法追踪的派生关系不假装精确，必要时扩大冻结范围。受限 Preset 必须覆盖子 Agent、原生入口和自动上下文，不能只让产品自定义工具守规则而保留不受限工具旁路，也不能只从 schema 隐藏工具；实际执行 guard 与能力内部都要落实范围。[DSH tools guard](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/core/tools/README.md)

产品约定保持单一调度归属；原生 schedule 如参与，只作为同一门控下的触发源，不另建可以自主恢复的执行副本。旧代禁令与命令接纳需覆盖原生写入口，不能假定官方 composer 自动经过 Mirally 门面。这些是 v0.4 形成的控制契约，当时计划通过公开扩展实现；当前可以直接修改内核落实，仍需故障注入验证，没有以文档或源码导入代替验收。
