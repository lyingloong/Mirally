# UFO 框架复盘与 Mirally 借鉴建议

状态：研究/建议。本文不是现行能力说明，也不表示已经接入 UFO。
核对基线：UFO 子模块 `a795552d976c4c019d7c2f778a0effb5cef7de6b`（2026-09-29）；Mirally `main` `913b2262`。
方法：阅读 UFO²/UFO³ 文档、关键源码和现有 Mirally UFO 适配；静态核对，没有启动真实桌面任务、没有调用外部模型、没有操作用户应用。

## 0. 结论摘要

UFO 值得借的不是“再做一个 Agent 框架”，而是三样东西：

1. **桌面能力底座**：UIA 控件树、稳定的控件快照、带编号的标注截图、目标校验、GUI/API 混合执行、应用级 API 工具。
2. **执行纪律**：先观察再动作、动作后重新观察验证、目标 ID 过期即拒绝、敏感动作确认、停止后不再准入、失败不盲目重放。
3. **应用知识与工具组织**：按应用组织工具命名空间、按需加载帮助文档/示例、优先 API 再 GUI。

Mirally 不应该借的是：

- UFO 的 HostAgent/AppAgent 双层模型循环、Blackboard 记忆库、完整 JSON 提示词协议；
- Galaxy 的跨设备 DAG 调度；
- FAISS/sentence-transformers/OmniParser 这类重依赖默认化；
- “Picture-in-Picture 桌面”这类 README 声明但当前源码未证明的能力。

**建议的第一刀**：在 Mirally 产品层做一个**只读的 UIA 快照工具**（窗口 + 紧凑控件列表 + 标注截图），验证控制覆盖率、延迟和 token 成本；通过后再加受 ActionGate 约束的单步动作工具。不启动 UFO，不引入第二个模型循环。

## 1. UFO 的两代结构

| 层 | UFO²（单设备） | UFO³（Galaxy） |
| --- | --- | --- |
| 任务入口 | Session/Round/Step | GalaxyClient + ConstellationAgent |
| 规划 | HostAgent 选择应用、拆子任务 | TaskConstellation DAG，动态编辑 |
| 执行 | AppAgent 面向单应用 | 设备 Agent 分布在多设备 |
| 工具 | FastMCP 本地/HTTP/stdio 服务器 | 同一 MCP 工具 + AIP 远程命令 |
| 通信 | 本地 CommandDispatcher | AIP WebSocket、心跳、重连 |
| 观测 | UIA + 可选 OmniParser | 各设备自己的观测能力 |

本地 Windows 任务走 UFO² 即可，不要求 Galaxy。Galaxy 解决的是跨设备编排，不是单机桌面质量问题；对当前 Mirally 不是优先项。

## 2. UFO 的关键机制

### 2.1 双层 Agent 与 FSM

- HostAgent：看桌面截图和窗口列表，拆子任务，选择应用，创建/复用 AppAgent。
- AppAgent：面向一个应用，执行“观察 → 思考 → 动作”循环，完成子任务后交还 Host。
- 状态机：`CONTINUE / ASSIGN / FINISH / FAIL / ERROR / PENDING / CONFIRM`（Host 7 态）与 `CONTINUE / SCREENSHOT / FINISH / FAIL / PENDING / CONFIRM / ERROR`（App 7 态）。
- 每个状态挂一个 Processor，按 `DATA_COLLECTION → LLM_INTERACTION → ACTION_EXECUTION → MEMORY_UPDATE` 四阶段执行。

**判断**：这套分层的价值在“应用级执行上下文”和“显式状态”，不在两个模型循环。Mirally 已有 DSH 主循环，照搬会变成第二条推理链。

一次本地 Windows 任务的链路：

```text
用户请求
  → Session / Round
  → HostAgent.CONTINUE：桌面截图 + 窗口列表 → 选择应用
  → HostAgent.ASSIGN：创建/复用 AppAgent
  → AppAgent.CONTINUE：应用截图 + UIA 控件 → LLM 选动作
  → Command → CommandRouter/Computer → MCP server（UIA/COM/CLI）
  → Result → 下一步观察
  → AppAgent.FINISH/FAIL → HostAgent 继续或结束
```

Session 管共享 Context、Round、日志和结果；Round 管一次用户请求；Step 是一次状态处理。三者不是同一个粒度，读源码时不要混用。

### 2.2 观察：UIA + 标注 + 紧凑控件列表

UFO 的观察不是只发一张截图，而是：

1. 取当前应用窗口的 UIA 控件树；可选 OmniParser 视觉检测补自定义控件。
2. 过滤/合并控件，分配当前 step 的稳定编号，写入 `TargetRegistry`。
3. 生成带编号标注的截图（SoM，Set-of-Mark），同时保留无标注截图。
4. 给模型的控件列表只保留 `{id, name, type}`，完整坐标和真实控件对象留在本地 `annotation_dict` / `control_dict`。
5. 动作时用 `id + name` 找回控件，校验名称、可见、可用；名称不匹配会提示，控件不可用则要求重新观察。
6. 每次动作后保存“选中控件”的标注截图，供下一步对比。

关键源码：

- `UFO/ufo/agents/processors/schemas/target.py`：`TargetInfo(kind,name,id,type,rect)` 与 `TargetRegistry`。
- `UFO/ufo/agents/processors/schemas/log_schema.py`：`ControlInfoRecorder` 记录 `control_text/control_type/control_rect/source`。
- `UFO/ufo/agents/processors/strategies/app_agent_processing_strategy.py`：控件收集、标注、紧凑列表、动作执行。
- `UFO/ufo/client/mcp/local_servers/ui_mcp_server.py`：`get_app_window_controls_target_info`、`click_input`、`set_edit_text` 等工具及 `_verify_id`。
- `UFO/ufo/automator/action_execution.py`：控件可用性校验与执行。
- `UFO/ufo/automator/ui_control/control_filter.py`、`inspector.py`：控件过滤与字段读取。

**可借点**：紧凑列表 + 标注截图 + 本地完整目标表，是同时控制 token 和提升点击准确率的实用组合。Mirally 当前只有窗口级观察，没有控件级目标。

### 2.3 执行：MCP 工具命名空间与命令分发

UFO 把工具分成两类：

| 类型 | 用途 | 模型是否可选 | 例子 |
| --- | --- | --- | --- |
| `data_collection` | 读状态、截图、控件树 | 否，框架自动调用 | `get_desktop_app_info`、`get_app_window_controls_target_info` |
| `action` | 修改状态 | 是，模型选择 | `click_input`、`set_edit_text`、`save_as` |

- `Computer` 按 agent/应用/root 组装工具环境；每个 MCP server 有 namespace，工具键为 `namespace.tool_name`。
- `CommandRouter` 把 `Command(tool_name, parameters, tool_type, call_id)` 路由到对应 MCP server；结果统一为 `Result(status,result,error,call_id,namespace)`。
- 支持 local FastMCP、HTTP、stdio 三种部署；MCP server 可懒注册、可删除、可重置。
- Office/PDF/CLI 通过本地 MCP server 暴露为应用 API 工具，优先于 GUI。

关键源码：`UFO/ufo/client/computer.py`、`UFO/ufo/module/dispatcher.py`、`UFO/ufo/client/mcp/mcp_server_manager.py`、`UFO/ufo/client/mcp/local_servers/`。

**判断**：Mirally 已有 MCP 扩展和命名空间，不需要 UFO 的 `Computer/CommandRouter`。但“数据收集工具自动调用、动作工具模型选择、按应用命名空间”这个划分值得保留到 Mirally 的工具元数据和审批策略里。

### 2.4 动作安全与结果语义

UFO 的几个细节比“点一下”更重要：

- **目标绑定当前 step**：控件 ID 只在当前观察有效；新观察会替换旧 ID。
- **动作前校验**：控件存在、可见、可用；名称不匹配会告警但仍按 ID 执行（Mirally 应改为拒绝，除非用户明确覆盖）。
- **敏感动作确认**：模型可把单个动作标为 `CONFIRM`；执行前把整批动作快照下来，用户批准的是这批具体动作，不是后续新生成的响应。
- **重复检测**：记录动作历史与重复次数，提示模型不要重复无效动作。
- **动作后的核对材料**：保存动作结果和“选中控件”的标注截图，供下一步观察/模型判断；UFO 自己并不保证每次都自动验证效果，`FINISH` 仍由模型判断。这正是 Mirally 应补成明确 `verified` 语义的地方。
- **停止/取消**：动作批次有 `action_execution_started` 消费标记；取消后不自动重试。

**判断**：Mirally 的 ActionGate 在“会话/命令/来源绑定”和微信确认码上更强；UFO 在目标快照与控件级定位上更细。两者应组合，而不是互相替代。

### 2.5 应用 API：Office/PDF/CLI

UFO 为 Excel、Word、PowerPoint、PDF、命令行提供本地 MCP 工具，典型动作包括：

- Excel：表格转 Markdown、写入表格、选择区域、读取区域、重排列、另存为。
- Word：读写正文/表格、插入内容、另存为。
- PowerPoint：新建/编辑幻灯片、插入文本/图片、另存为。
- PDF：提取文本/页面。
- CLI：执行命令行。

这些工具通过 COM/本地库直接操作文档，不激活窗口、不需要截图和模型循环；对批量、结构化办公任务比 GUI 稳定。

关键源码：`UFO/ufo/automator/app_apis/`、`UFO/ufo/client/mcp/local_servers/{excel,word,ppt,pdf}_*.py`。

**判断**：这是 Mirally 当前最缺、ROI 最高的一块。Mirally 有 PowerShell 和文件工具，但没有 Office/PDF 结构化能力；GUI 点击不是完成表格/文档任务的正确抽象。

### 2.6 知识层

UFO 支持四类检索：

- 离线帮助文档（`learner/` 建索引）；
- 在线搜索（Bing）；
- 自身成功经验（`experience/`）；
- 用户演示轨迹（`record_processor/`）。

通过 FAISS + sentence-transformers 检索，增强 AppAgent 提示词；经验和演示可以保存/复用。

**判断**：方向有价值，但实现太重，且自动经验学习会带来隐私、成本和错误固化。Mirally 应先用现有 Skill/MCP 做“应用知识包”，按需加载，不默认建向量库、不自动保存轨迹。

### 2.7 AIP 与 Galaxy

AIP 是 UFO³ 的设备通信层：持久 WebSocket、类型化消息、能力注册、命令/结果、心跳、重连、中间件。Galaxy 在此之上做任务 DAG、设备分配、并行调度和动态编辑。

**判断**：Mirally 当前是单机产品，手机是消息渠道而不是远程执行器。AIP 的“能力声明 + 命令/结果 + 心跳/重连”可以在未来接远程 Windows 执行器时借鉴；Galaxy 的 DAG/调度对当前产品过重，不借。

### 2.8 README 声明与源码证据

UFO README 提到 Picture-in-Picture 桌面、跨设备 Galaxy 等。本文核对的是 Python 源码快照：

- Galaxy、AIP、MCP、UIA、OmniParser、Office API 都有对应代码。
- **没有找到**独立虚拟桌面/独立 Windows session 的 PiP 实现；`UFO/documents/docs/faq.md` 还明确写“Picture-in-Picture mode is planned for future releases”，与 README/迁移文档中“已支持”的表述矛盾。PiP 不能当作现成能力。
- 上游的成功率、延迟和“20+ 应用”覆盖是论文/项目声明，不是 Mirally 环境的验收结果。

## 3. Mirally 现状与差距

| 能力 | Mirally 当前 | UFO 参考 | 差距 |
| --- | --- | --- | --- |
| 窗口观察 | 前台窗口、窗口列表、全屏截图；需审批 | 窗口选择、截图、控件树、标注 | 无控件级观察 |
| 桌面动作 | 单步坐标点击/文字输入，已有实现但未向模型开放、未通过实机验收 | UIA 控件动作 + 坐标回退 + 目标校验 | 无控件目标、无验证语义 |
| 浏览器 | 独立 Edge、页面文本/元素选择器、快照校验 | 非重点 | 已有，不必借 |
| 文件/命令 | PowerShell、文件读取/写入、成果 | CLI/Office API | 无 Office/PDF 结构化 API |
| 审批 | ActionGate：会话/命令/来源绑定，逐次/临时/持续 | 每步 CONFIRM + 批次快照 | 目标级详情和动作后验证不足 |
| 执行循环 | DSH 单一 Agent/Session | HostAgent/AppAgent 双层 | 不借；映射为产品能力层 |
| 扩展 | Skill/MCP/可信插件，命名空间 | 按应用 MCP server | 可补应用能力包 |
| 知识 | Skill/MCP/记忆 | 文档/经验/演示 RAG | 可补应用知识包，不建向量库 |
| 远程/多设备 | 微信/手机 HTTP | AIP/Galaxy | 当前不需要 |

补充：Mirally 已有 `mirally/runtime/ufo/` 这套冻结适配，包含任务预算、停止/暂停/补充、动作准入、重新观察、截图隔离和结果归并；它证明了这些控制语义在真实 UFO 链路上可行，但默认产品不加载，也不应把默认入口重新接回 UFO。新桌面能力应借鉴这些语义，而不是复制 UFO 的 Agent 循环。

DSH 已有 `computer-use` 注册服务和实验性 Cua Driver provider，但 **Windows provider 尚未实现**（`cua` 的 README 明确说 Windows 是计划中的独立 provider）。因此 UFO 的 UIA 实现正好可以作为 Windows provider/产品工具的设计参考，但不能直接当作 DSH 现成能力。

## 4. 借鉴矩阵

### 直接借

| 机制 | 在 Mirally 的落点 | 理由 |
| --- | --- | --- |
| UIA 控件树 + 紧凑 `{id,name,type}` 列表 | 桌面快照工具 | 降低模型猜测，控制 token |
| 带编号标注截图（SoM） | 快照工具返回附件 | 视觉定位更稳，保留无标注图对照 |
| 目标 ID 只在当前快照有效 | 快照注册表 + 动作校验 | 防止旧坐标/旧控件误操作 |
| 动作后重新观察/验证 | 动作结果 `dispatched/verified` | 不把“已发送”当成功 |
| 数据收集 vs 动作工具划分 | 工具元数据与审批策略 | 读操作与副作用分开 |
| 按应用组织工具命名空间 | MCP/扩展配置 | 避免工具目录膨胀 |
| Office/PDF API 工具 | 可选 MCP/插件包 | 结构化任务比 GUI 稳 |
| 路径校验（禁止系统目录、目录穿越） | Office/保存类工具 | 防止模型写出授权范围 |

### 改造后借

| UFO 机制 | 改造方式 |
| --- | --- |
| HostAgent/AppAgent | DSH 主 Agent 负责规划；产品桌面 provider 只提供工具和状态，不另起模型循环 |
| 7 态 FSM | 不复制为 Agent 状态机；只在单次桌面动作/运行内做 `snapshot → validate → execute → verify` 的确定性状态 |
| Processor 四阶段 | DSH 工具调用天然有“观察/推理/动作/结果”；不引入新 Processor 框架 |
| Blackboard | 不建第二份共享记忆；使用 DSH Session 事件、产品 Context 和工具结果 |
| MCP 管理器 | 使用 Mirally 现有 MCP 扩展生命周期；只借命名空间与工具分类 |
| 知识 RAG | 先用 Skill `SKILL.md` + 文档/示例按需加载；需要检索时用 SQLite FTS，不默认 FAISS |
| AIP | 未来远程执行器可借 `Command/Result`、能力声明、心跳/重连；当前不建 |

### 不借/推迟

| 机制 | 原因 |
| --- | --- |
| UFO Agent loop、Host/App 双层模型循环 | 与 DSH 单一 Session/模型主链冲突，形成第二套历史与权限 |
| UFO 的 JSON 状态提示词 | DSH 工具调用/结构化输出更直接；UFO 提示词冗长且有“$200/毁掉电脑”等不适用话术 |
| Galaxy DAG/Constellation 调度 | 单机产品不需要；DSH plan/workflow/子代理已覆盖多步 |
| FAISS/sentence-transformers/OmniParser 默认化 | 重依赖、额外模型服务、成本与许可/维护负担；UIA 先覆盖大部分标准控件 |
| 推测式多动作批执行 | 减少调用但增加误操作/回滚复杂度；先保证逐步验证 |
| 自动经验学习/Blackboard 持久化 | 隐私、错误固化、第二份记忆；先做显式、可审阅的经验保存 |
| Picture-in-Picture/独立桌面 | 当前源码未证明；需要独立 session/虚拟桌面，属于单独研究 |
| 评估 Agent | Mirally 用现有验证与诊断即可；不把评估器做成产品运行时 |

## 5. 建议的目标结构

```text
DSH 主 Agent / 唯一 Session
  ├─ 工具：文件、PowerShell、Web、浏览器、记忆/活动
  ├─ 工具：mirally_desktop_snapshot / mirally_desktop_action
  │    └─ 产品桌面 provider（Node）→ Python UIA helper
  │         ├─ 窗口/控件树/标注截图
  │         ├─ 快照注册表（短期、按 session）
  │         └─ 动作执行 + 验证
  ├─ 工具：Office/PDF MCP（可选）
  └─ ActionGate：统一审批、来源绑定、停止/过期
```

原则：

1. **一个模型循环**：桌面能力是工具，不是另一个 Agent。
2. **一个 transcript**：快照/控件表是执行期状态，不是聊天历史；工具调用和结果仍进 DSH Session。
3. **一个执行 owner**：同一窗口/动作链只有一个 provider；不并行运行 UFO worker 和产品 provider。
4. **策略在动作边界**：ActionGate 管“是否允许”，provider 管“目标是否有效、动作是否真的生效”。
5. **先读后写**：快照工具可以先上线；动作工具默认关闭，逐步开放。

## 6. P0：UIA 快照与单步动作

### 6.1 工具

```text
mirally_desktop_snapshot
  window?: {hwnd, pid, title}
  include_screenshot?: boolean = true
  max_controls?: integer = 120 (range 1..200)
  → {
      snapshot_id, captured_at_utc_ms,
      window: {hwnd, pid, title, process, bounds, active},
      controls: [{id, name, type, enabled, focused}],
      image?: attachment,
      control_count, truncated, notes
    }

mirally_desktop_action
  snapshot_id, target_id, name
  action: "invoke" | "set_value" | "select" | "toggle" | "expand" | "click" | "type" | "keys"
  value? / button? / double? / keys?
  → {
      action_id, snapshot_id, target: {id,name,type},
      dispatched: true, verified: boolean,
      needs_observation: boolean,
      window_changed: boolean,
      notes
    }
```

- 快照结果给模型的是紧凑控件列表，不是整棵 UIA 树；完整坐标/AutomationId/类名/模式留在本机快照注册表。
- 标注截图用编号叠加在控件矩形上；只生成一张，按窗口/截图尺寸缩放；可关闭以省 token。
- 控件过滤：可见、可用、非零尺寸、非密码、非 Mirally 自身窗口；按树顺序编号；上限 120，超出标记 `truncated`。
- 模型必须先用快照，再用 `snapshot_id + target_id + name` 动作；新快照使旧 ID 失效。

### 6.2 动作解析顺序

1. 校验 `snapshot_id` 存在、未过期、属于当前会话/命令。
2. 校验窗口身份（hwnd/pid/title/bounds）未变；变化则 `STALE_SNAPSHOT`。
3. 校验目标存在、名称匹配、可见、可用；密码控件拒绝。
4. 优先 UIA Pattern：
   - `InvokePattern`：按钮、菜单项；
   - `ValuePattern`：输入框；
   - `SelectionItemPattern`：列表/标签；
   - `TogglePattern`、`ExpandCollapsePattern`；
   - `SetFocus` + 坐标点击仅作回退。
5. 需要前台输入时，先明确激活目标窗口，再执行；动作详情显示“会激活窗口/可能影响其他应用”。
6. 执行后重新读取目标/窗口状态；返回 `verified` 或 `needs_observation`。
7. 任何超时、取消、未知结果都不自动重试；要求重新观察或由用户决定。

### 6.3 审批

- 复用 `ActionGate`，`kind:'desktop_action'`；审批详情包含应用、窗口标题、控件名称/类型/位置、动作和值。
- 快照本身是读操作，但仍可能包含屏幕敏感信息；是否每次审批沿用当前桌面观察策略（截图需审批），控制树读取可单独放宽但默认同一审批。
- 微信来源默认不开放桌面动作；如未来开放，必须复用现有确认码/来源绑定，不新建渠道策略。
- 停止/归档/撤权/命令变更后，快照与待审批动作立即失效。

### 6.4 验证与结果语义

- `dispatched` 只表示输入已发出；`verified` 必须有可观察依据（值变化、选中/展开状态变化、窗口标题/结构变化、新窗口出现）。
- 无法观察变化时返回 `verified:false, needs_observation:true`，不宣称成功。
- 失败码明确：`STALE_SNAPSHOT`、`TARGET_NOT_FOUND`、`TARGET_CHANGED`、`UNSUPPORTED_PATTERN`、`DENIED`、`EXECUTION_UNKNOWN`、`VERIFY_FAILED`。
- 不用自然语言“应该已经完成”掩盖未知。

### 6.5 依赖与实现位置

- Python helper：`mirally/framework/desktop_control.py`（或 `mirally/platform/windows/desktop_control.py`），JSON stdio，使用现有 `uiautomation` 与 Pillow；不需要引入 UFO。
- Node 工具：`kernel/dsh/packages/mirally/mirally-product/src/desktop-control.mjs`，注册工具、快照注册表、ActionGate 调用；由 `installProductTools()` 安装。
- 默认关闭：设置项 `desktop_control_enabled`（读）与 `desktop_control_actions_enabled`（写）分开；动作还需要 ActionGate。
- 若未来 DSH Windows computer-use provider 落地，可把 provider 内部实现替换/迁移，但产品工具名、审批和验证语义保持稳定。

### 6.6 验收

先做**只读快照**，不做动作：

1. 在 Notepad、Calculator、Explorer、Word/Excel（只读）上取快照，记录控件数、耗时、标注图大小、token 估算。
2. 对比“纯截图”和“截图 + 控件列表”在 10 个定位问题上的模型选择准确率。
3. 校验密码控件不出现、Mirally 窗口被排除、无权限时不采集。
4. 再开 `invoke/set_value` 两个动作，在测试文档/记事本上验证：成功、拒绝、过期快照、窗口移动、目标禁用、进程退出、取消。
5. 最后才评估 `click/type/keys` 回退；真实桌面验收必须在隔离测试窗口/测试账号进行，不操作用户当前应用。

## 7. P1：Office/PDF 应用 API 包

优先做“无 GUI”的结构化能力，而不是先做通用桌面点击：

- Excel：读取区域/表格转 Markdown、写入二维表、选择区域、另存为；遵守授权目录与文件大小上限。
- Word：读取正文/表格、追加段落、插入表格、另存为。
- PowerPoint：新建/复制幻灯片、插入文本/图片、另存为。
- PDF：提取文字/页数；扫描件 OCR 另计。
- 通过 Mirally 现有 MCP/可信插件机制接入，默认不启用；工具命名空间按应用划分。
- COM 调用固定 CLSID/ProgID，不执行宏、不接受任意 COM 对象；保存路径走现有授权根和路径校验（可借鉴 `UFO/ufo/automator/path_validator.py` 的目录穿越/系统目录拒绝思路）。
- 依赖：`uiautomation` 已带 `comtypes`；Office COM 可用 `comtypes`/`win32com`，若新增 `pywin32` 应作为 Windows 可选依赖并说明安装。
- 验收：临时目录生成测试文档，覆盖读写、另存、越权路径、Office 未安装/文档未打开、并发/占用、失败恢复。

## 8. P2：应用知识包与诊断

### 应用知识包

- 每个应用一个 Skill：`SKILL.md`（何时用 API、何时用 UIA、失败如何重试）、`references/`（快捷键、常见控件）、`examples/`（成功操作示例）。
- 默认按需加载，不把所有正文塞进每轮请求；不自动建 FAISS 索引。
- 知识来源要可追溯；官方文档链接与本地摘要分开，不把模型推测写成应用事实。
- 未来若要“从成功任务保存经验”，必须显式触发、可审阅、可删除，不能默认收集。

### 诊断

- 每次桌面运行记录：`snapshot_id`、窗口身份、目标、动作、审批、结果、验证、耗时；写入工具结果/现有诊断，不建第二份聊天历史。
- 保留标注截图和选中控件截图作为附件引用，便于复盘；不把原始控件树长期持久化。
- 页面只在需要时展示诊断，不增加常驻 UI。

## 9. 暂不采用

- **UFO 作为默认执行器**：已有 `mirally/runtime/ufo` 冻结适配可作参考；默认产品不加载，避免第二套循环、依赖和权限。
- **Galaxy/AIP 立即接入**：等出现“远程 Windows 执行器/多设备”真实需求，再借 AIP 的消息/心跳/重连；不建 DAG 调度。
- **PiP/独立桌面**：当前源码未证明；若产品要求“不打断用户”，需要单独研究 Windows 虚拟桌面/独立 session、输入注入和权限，不与本次 P0 混做。
- **推测式多动作**：先保证单步可验证；未来可在稳定任务上做“批量预览 + 逐步验证”，但不作为第一阶段。

## 10. 风险与开放问题

1. **UIA 覆盖率**：部分 Electron/自绘/游戏窗口控件树贫乏；需要视觉 grounding 或坐标回退。先测量，再决定是否接 OmniParser/自建视觉服务。
2. **前台激活**：UIA Pattern 可后台执行的比例未知；需要真实应用矩阵测试。坐标点击会抢焦点，必须审批并明确告知。
3. **快照一致性**：UIA 树读取期间窗口可能变化；用窗口身份 + 时间戳 + 动作前复核，不做“原子快照”承诺。
4. **权限模型**：产品工具走 ActionGate，DSH 原生沙箱/审批是另一套；不要出现“UI 显示关闭、原生工具仍可执行”的矛盾。新增 provider 前先确定唯一执行 owner。
5. **依赖与许可**：UFO 是 MIT；若复制代码需保留版权/许可证并更新第三方声明。优先按接口重写，避免整段搬运。
6. **远程渠道**：微信默认不开放桌面动作；如果开放，必须复用现有确认码、来源绑定和过期语义，不能只加一个开关。
7. **PiP 真实性**：README 声明与源码能力要分开；不要在文档/UI 中承诺未验证的独立桌面。

## 11. 建议的下一步

1. 先做 **只读快照原型**（不接动作），在 3–5 个应用上测量控件覆盖率、延迟、标注图/token 成本。
2. 如果覆盖率可接受，做 `invoke/set_value` 两个动作 + ActionGate + 快照过期/验证测试。
3. 并行做 **Office/PDF API 包**的可行性验证（独立 MCP server，临时文档，不接默认产品）。
4. 通过真实任务对比：同一“整理表格并生成文档”的任务，GUI 点击 vs Office API，记录步骤数、耗时、失败率和 token。
5. 根据结果决定是否扩展到 `click/type`、视觉 grounding、应用知识包；AIP/Galaxy/PiP 保持推迟。

## 12. 证据索引

UFO：

- `UFO/ufo/agents/processors/schemas/target.py`
- `UFO/ufo/agents/processors/schemas/log_schema.py`
- `UFO/ufo/agents/processors/strategies/app_agent_processing_strategy.py`
- `UFO/ufo/client/mcp/local_servers/ui_mcp_server.py`
- `UFO/ufo/automator/action_execution.py`
- `UFO/ufo/automator/ui_control/{control_filter,inspector}.py`
- `UFO/ufo/client/computer.py`
- `UFO/ufo/module/dispatcher.py`
- `UFO/ufo/client/mcp/mcp_server_manager.py`
- `UFO/ufo/automator/app_apis/`
- `UFO/ufo/automator/path_validator.py`
- `UFO/aip/messages.py`、`UFO/aip/protocol/`
- `UFO/documents/docs/ufo2/`、`UFO/documents/docs/mcp/`、`UFO/documents/docs/aip/`

Mirally：

- `mirally/framework/desktop_observation.py`
- `kernel/dsh/packages/mirally/mirally-product/src/desktop-observation.mjs`
- `kernel/dsh/packages/mirally/mirally-product/src/action-gate.mjs`
- `kernel/dsh/packages/mirally/mirally-product/src/browser-capabilities.mjs`
- `kernel/dsh/packages/mirally/mirally-product/src/basic-capabilities.mjs`
- `kernel/dsh/packages/computer-use/`（注册服务；Windows provider 尚未实现）
- `mirally/runtime/ufo/`（冻结适配，控制/预算/观察语义参考）
