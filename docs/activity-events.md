# Activity 事件视图

日期：2026-10-11。当前实现是确定性的本地数据视图，不调用模型，也不启动采集器。它将同一窗口活动片段中的动作、窗口状态和文字证据放在一起，让开发者能够检查一段时间内实际观察到了什么。

## 产品目标与当前边界

用户的使用轨迹需要同时回答三个问题：发生了哪些动作、动作对应哪个窗口或资源、当时有哪些可读取的内容。截图、焦点控件、剪贴板和时间信息提供辅助证据；它们不需要全部出现才能形成事件，缺失时要说明缺失。

当前 `window_activity` 事件复用已有 Episode 边界，不增加原始存储表，也不重写原始记录。同一窗口的一段连续观察可以包含窗口切换记录、标题和 URL 状态、输入计数、控件文字变化、UIA 和 OCR。切到其他窗口后再切回，会形成新的片段；明确的 URL 导航、采集 epoch 变化、资源缺口和空闲边界也会分段。多个窗口是否属于同一个用户任务，仍需要后续应用语义或更强的动作证据。

事件摘要只描述观察事实。前台窗口出现不能证明用户阅读了内容；点击计数不能证明点击了哪个按钮；复制、剪切和粘贴信号不能证明操作成功；控件文字变化不能单独证明用户保存、发送或提交了内容。事件的目的和动作结果保持 `unconfirmed`。

## 事件中的证据

输出协议为 `activity.event.v1`，底层投影仍为 `activity.v2`。每个事件保留稳定的 `event_id`，其值等于 `episode_id`。

| 字段 | 当前表达的事实 |
| --- | --- |
| `actions` | 非零输入计数、已知基线的完整文字变化、复制/剪切/粘贴操作信号；保留 actor、归因依据和未确认结果 |
| `window_states` | 应用、进程和窗口身份，以及观察或继承的标题、URL；继承字段保留上下文来源，身份置信度单独表达 |
| `window_text` | UIA 或与原帧关联的 OCR 摘录、完整文字长度和哈希、来源质量及采集范围 |
| `input_states` | 初次读取、基线未知或不完整的控件文字状态；这些状态不计为新输入动作 |
| `clipboard_states` | 剪贴板更新或内容读取状态；不推定粘贴成功 |
| `asset_refs` | 显式请求后返回的截图等资产引用，不包含图片二进制 |
| `unknowns` / `missing_reasons` / `data_gaps` | 动作缺失、窗口文字未进入视图、来源读取问题、采集缺口及片段不完整等限制 |
| `source_observation_ids` / `context_observation_ids` / `evidence_ids` | 区分事件自身来源和继承上下文，可追溯完整 Observation 与 Evidence |

`actions` 可以包含归因未确认的动作，只有明确标记为 USER 的动作才使 `user_actions_observed` 为 true。AGENT 和 SYSTEM 的动作保留各自归因。没有动作证据的窗口片段仍可输出，但会标记 `action_evidence_missing`。

显示器截图和 OCR 进入顶层 `background_evidence`，不会归给当时的前台应用。无法关联窗口或原帧的记录进入 `unattributed_evidence`，采集状态进入 `collection_status`。迟到 OCR 依据原始帧关系回到原 Episode，不能因为处理时窗口已经变化而加入新的窗口事件。

## 时间与完整性

`observed_start_utc_ms` 和 `observed_end_utc_ms` 表示本次返回片段中首尾证据的时间；`episode_observed_start_utc_ms` 和 `episode_observed_end_utc_ms` 表示组织层中的整个 Episode 范围。它们不是用户活跃时长，也不表示用户一直在该窗口操作。

输入 helper 提供的原始单调时间范围和发出消息时的 UTC 时间会随输入批次保存并投影。它们是 helper 报告的批次时间，尚不足以恢复每个动作的精确 UTC 时间；动作输出使用 `time_quality` 和 `time_semantics` 表达这一限制。快速切换窗口后迟到的输入批次仍可能无法安全归属：采集器拒绝将其归给当前窗口，并记录未归属诊断。当前实现尚不能完整重建这些动作。

原始文字和 UIA 结构在声明的隐私、可见性范围内完整保存，不按字符、节点、深度或 JSON 大小裁剪。事件视图属于处理阶段：UIA 摘录上限为 600 字符，OCR 摘录上限为 24,000 字符；超出 OCR 上限时保留首尾并插入中间省略标记。两者都返回 `excerpt_truncated`、`excerpt_limit_chars`、`original_char_count` 和完整文字哈希；窗口和变化中的字符串也会标注裁剪字段。完整内容仍可通过 Evidence 引用检索。Agent Evidence 接口受响应预算约束，预算不足时 OCR 也保留首尾而不是只取文本开头。旧数据在采集时已经截断的内容不能由新视图恢复，`source_truncated` 与来源质量会保留这一限制。

## 本地查询

在仓库根目录使用正式配置查询已组织的数据：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli knowledge-events
```

`monitor --test` 的隔离库需要通过 `--config` 指定指向该库的 Activity 配置。配置选项放在子命令前：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli --config path\to\activity.yaml knowledge-events --limit 100 --episodes 30 --tokens 8000
```

命令默认包含 UIA 和 OCR 文字，使用 `--no-uia` 或 `--no-ocr` 排除对应文字证据，使用 `--include-assets` 请求资产引用。这些选项只控制查询视图，不改变采集设置。

过滤参数包括 `--start`、`--end`（UTC Unix 毫秒）、`--query`、可重复的 `--app`、`--window`、`--kind`，以及 `--episode`。`--limit` 限制参与本页的 ActivityItem 数量，范围 1–500；`--episodes` 限制本页事件数，范围 1–100；`--tokens` 范围 128–100000，按序列化 UTF-8 字节数不超过预算的三倍估算，并非模型 tokenizer 的精确计数。

默认查询只读取已组织数据。需要先追赶原始库时添加 `--sync`，可用 `--sync-limit`、`--sync-batches` 限制处理量；这会更新派生库，不启动采集器、不操作桌面，也不调用模型。输出中的索引水位、来源最新序号、投影错误数量和 `data_status` 帮助区分没有数据、索引未追上、投影不完整及预算不足。预算连第一条记录也容纳不下时会返回 `budget_exceeded`，需要增加预算。

## 分页与片段合并

数据页按最新 ActivityItem 优先选取，单页中的事件按观察时间排列。`truncated: true` 时，将返回的 `next_cursor` 原样传给下一次查询的 `--after-item`，其余过滤条件保持一致。游标属于 ActivityItem，不是 event_id。

一个 Episode 可能跨页出现，也可能因为时间、正文或来源类型过滤只返回部分成员。`partial_event`、`emitted_item_ids` 和 `episode_item_count` 表达这一点；不能将一页片段当成整个事件。调用方应按 `event_id` 合并，按 `emitted_item_ids` 去重，并结合 `revision` 判断组织层是否收到迟到证据。来源引用和各项证据也要去重；片段摘要和观察起止时间不能直接当成整个 Episode 的最终摘要和范围。

Python 调用复用 `ActivityOrganizer.activity_events(ContextQuery(...))`。`ContextQuery` 的通用默认值仍是 `include_uia=False`，需要窗口文字时显式设置为 true；CLI `knowledge-events` 为此视图单独启用了 UIA 默认值。

```python
from openassist.core.activity.knowledge import ContextQuery

result = organizer.activity_events(ContextQuery(
    purpose="diagnostics",
    max_items=100,
    max_episodes=30,
    token_budget=8000,
    include_uia=True,
    include_ocr=True,
))
```

当前交付是数据层 API 和 CLI。既有 Agent 接口的接入边界未因此改变。真实动作覆盖、窗口身份和应用语义仍需用可核对的新采集轨迹验收；能组织出事件并不等于已经理解用户任务。
