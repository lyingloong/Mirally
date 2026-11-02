# dev-0024 · 长期事件与记事存储（Event Store）方案提案

> 状态：**Draft Proposal（阶段 A，第 2 轮修订）**。本文只写方案，不含任何实现代码；实现须在用户/Codex 明确 `proposal approved` 之后另开 Implementation PR。
> 修订说明：第 1 轮把本点写成“项目报销台账”，用户澄清**报销只是例子**，核心是**像记事本一样长期记录事件/小想法/见闻，模型可按需建目录**。本版据此重写；**报销作为 `ledger` 类型示例**保留在 §6，用于说明“当记录需要可加总时，类型可以带不变量”。

---

## 0. 用户原始需求

### 0.1 原始表述

> **整理**（第 1 轮，`user-request`）：项目 XXX 报销额度 3000，已报 160、1800 两笔；希望 Mirally 记住并在被问到时回答还剩多少；隔很久再问也要有理有据。
>
> **逐字要点**（用户明确约束）：**「不能用传统记忆/向量检索」**；应由**模型维护设计好的结构化存储**，并**规定协议**。

> **整理**（第 2 轮澄清，`user-request`）：**报销只是例子**。真正想要的是**像记事本一样的长期存储**：随手记下事件、小想法、见闻；**模型可以按需建目录**来组织；隔很久再问仍然找得回、说得清。同一约束仍然成立：**不用传统记忆/向量检索**，由模型维护结构化存储并遵守协议。

### 0.2 从表述中提炼的产品意图（需求师归纳，非原话）

1. **长期**：内容要活得过会话、活得过记忆预算与语义整理；一周、一年后仍能取回。
2. **随手**：用户不会先建表结构再说事；记录必须是**一句话就能存**。
3. **可组织**：内容变多以后需要目录/分类，但目录应当**由模型在需要时创建**，而不是强迫用户预先设计。
4. **可信**：隔久再问要“有理有据”——能说明依据是哪条记录、什么时候写的、有没有被改过。
5. **可计算**：某些记录（报销、预算、计数）需要**确定性汇总**，不能靠模型心算（第 1 轮例子）。
6. **不用记忆/向量检索做主路径**：记忆用于“人”，记事本用于“事与物”，两者职责不同（§9 论证）。

---

## 1. 背景与现状

### 1.1 相关入口（现行，`main` = `ad2f1d82`）

| 面 | 位置 | 现状与本点关系 |
| --- | --- | --- |
| 持久记忆 `MemoryStore` | `mirally/core/memory/store.py`：`kind ∈ {working, episodic, task, semantic}`，`memory_fts`（unicode61）+ `memory_literal_fts`（trigram），FTS + 规则打分 | **面向“关于用户的经历与主张”**；写入路径是维护流水线（活动投影→本地规则→可选语义整理），**不是用户随手记** |
| 记忆召回 | `mirally/runtime/memory/recall.py`（`plan/eligible/retrieve/excerpt`），预算 `context_token_budget`，`working_ttl_seconds=86400` | 按**当前问题**取前 k 条；预算截断与 TTL 决定它不适合当“记事本主查询” |
| 语义整理预算 | `mirally/runtime/memory/config.py`：`max_calls_per_hour=24`、`max_tokens_per_day=60000` | 会**改写**记忆正文；记事本不应被后台改写 |
| 偏好 | `mirally/framework/service.py:980` `preference.set(key, value, scope)`，JSON ≤16 KiB | 只适合单值设置，不承载记录 |
| 产品 SQLite | `mirally/framework/service.py`（`product.sqlite3`，schema 11） | 记事本表应与其同库同事务 |
| 工具注册 | `kernel/dsh/packages/mirally/mirally-product/src/calendar-tools.mjs:10` `host.ctx.tools.register({name, description, parameters, output})`，带 `checkCommandScope`/`guard`/`request_id` | 协议参照 |
| 审批 | `action-gate.mjs`（ActionGate 绑定会话/回合/动作内容） | 写入须走同一套审批与幂等 |

### 1.2 已实现 / 部分 / 未实现

| 能力 | 状态 |
| --- | --- |
| 记事本式用户记录（模型可写、可分类、可长期取回） | **未实现** |
| 用户可定义/模型可创建的分类目录 | **未实现** |
| 记录层面的纠正、冲正、删除与审计 | **未实现** |
| 可加总类型（额度/支出/余额） | **未实现**（第 1 轮已论证不能靠记忆） |

### 1.3 为什么现在需要决策

记忆系统已经存在且默认开启，若记事本再做一套，最可能的结果是**两个真相**：用户问“我上次买的那本书”时，记忆里有一条被整理过的版本，记事本里有一条原始记录，模型时而对时而不对。因此必须先定**边界与协议**，再决定实现形态。

---

## 2. 目标与非目标

### 目标

1. **随手可记**：一句“记一下：今天在梅坞老巷看到一只三花猫，很亲人”即可落库，不需要用户先设计 schema。
2. **模型按需建目录**：内容变多、出现明显主题时，模型可以**主动建议或直接创建 collection**，并把后续记录归入；用户可以改名、合并、拆出。
3. **长期可查**：标签 + 时间 + 全文 + 确定性计算四条检索路径；不依赖召回相似度。
4. **可追溯**：每条记录有来源（会话/渠道/回合/原话摘要）、时间（事件时间与记录时间分开）、创建者、版本链。
5. **可外加结构**：某一类记录可以升级为**带不变量与工具的类型**（如 `ledger`），计算由工具完成，不由模型心算。
6. **不与记忆冲突**：明确“什么进记事本、什么进记忆”，并在回答中可区分。

### 非目标

- **不以记忆/向量检索作为记事本的主查询路径**（§9）；也不改造现有记忆系统。
- 不做通用笔记应用（不做富文本、附件管理、协作编辑、Markdown 渲染、导出为知识库）。
- 不做自动扫描桌面/浏览历史写入记事本（活动采集是独立能力）。
- 不做跨设备同步（Q6）。
- 不自动把每一句话都写进记事本（写入触发见 Q7）。

---

## 3. 可选方案

| 方案 | 做法 | 优点 | 代价/风险 | 适用条件 |
| --- | --- | --- | --- | --- |
| **A. 通用事件存储 + 按需类型（推荐）** | 统一 `entries` 表（自由文本 + 时间 + 标签 + collection），`types`/`schema` 描述某类记录的可选结构；特定类型（ledger）附带不变量与工具 | 一条写入路径覆盖想法/见闻/事件/账目；目录可演进；计算型交给工具 | 设计一个“足够通用又不万能”的模型是本提案最大风险 | 需要长期、可查、可算的用户记录 |
| B. 全部塞进记忆 | 用 `MemoryStore` 的 `user_confirmed` 记录存 | 零新表 | 会被语义整理改写、被 TTL/预算影响、无目录/无冲正/不可审计 | 不可用（用户已排除） |
| C. 偏好 JSON 存“记事本” | `preference.set("notes", [...])` | 复用 API | 16 KiB 上限、无分页/索引/审计/并发语义 | 只够几十条 |
| D. 每条一个 Markdown 文件 + 目录 | 文件系统即目录 | 用户可直接看/备份 | 索引、并发、原子性、审计都要自己做；Windows 路径与编码问题；与产品库不一致 | 已有的个人知识库工作流 |
| E. 通用 key-value/表格抽象 | 万能表格给所有场景 | 复用性 | 与 [产品方向](../../../docs/product/vision.md) 原则 1“不建立万能 PersonalState”冲突 | 出现 3+ 真实场景后再考虑 |

**结论**：A。B 被用户排除；C 只用于设置；D/E 见 Q9/Q10。

---

## 4. 推荐方案总览

```text
用户："记一下：老周说下周工位搬到三号楼，记得带工牌。"
   ↓
模型：notebook.entry.add(text=..., occurred_at=..., tags=["工作","搬迁"], proposed_collection="工作/搬迁")
   ↓
工具：校验（长度、时间、敏感标记）→ 写入 entries（可撤销窗口内）→ 返回 entry_id + 归类建议回执
   ↓
用户："上次老周说的搬工位那事是哪周？"
   ↓
模型：notebook.search(query="老周 搬工位", time_hint=...)  → 命中条目（含 event_time、collection、tags）
   ↓
模型：按工具返回逐条转述，不自行推断时间或补写内容
```

**职责切分（协议核心）**

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| 模型 | 判断“是否值得记”、抽取事件时间与标签、建议目录、把查询结果转述成人话、发现歧义时追问 | 不做汇总算术、不编造内容、不擅自改写已有记录 |
| 工具 | 校验、写入、幂等、版本、审计、检索、计算（仅计算型类型） | 不做语义理解、不决定是否记录 |
| 存储 | 唯一事实 | 不做跨设备合并 |

---

## 5. 数据、接口与协议

### 5.1 数据模型（自由记录优先，结构按需附加）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `notebook_collections` | `id`、`path`（如 `工作/搬迁`，**模型可建**）、`title`、`parent_id NULL`、`kind`（`user_defined`/`auto_created`）、`created_by`、`status`（`active`/`archived`）、`created_at` | 目录树；`path` 唯一；`auto_created` 表示模型建议创建（Q1 决定是否默认创建） |
| `notebook_types`（可选） | `name`（`note`/`event`/`idea`/`ledger`…）、`schema_json`、`strictness`（`free`/`soft`/`strict`）、`tools[]` | **类型只在需要时出现**；`note` 为默认类型，零约束 |
| `notebook_entries` | `id`、`collection_id NULL`、`type`（默认 `note`）、`title NULL`、`body TEXT`、`tags_json`、`event_time`（事件发生时间，可空）、`recorded_at`（写入时间）、`time_quality`（`exact`/`day`/`approx`/`unknown`）、`props_json`（类型附加结构）、`status`（`active`/`superseded`/`reversed`/`deleted`）、`supersedes_id NULL`、`source_json`、`idempotency_key UNIQUE`、`sensitivity`、`created_by`、`revision` | 一条记录 = 一句话/一个事件/一笔账 |
| `notebook_revisions` | `entry_id`、`revision`、`before_json`、`after_json`、`at`、`actor`、`reason` | 只追加的版本链（改正文/换目录/合并都留痕） |
| `notebook_audit` | `seq`、`at`、`actor`、`channel`、`conversation_id`、`turn_id`、`tool`、`request_id`、`entry_id`、`action`、`detail_json` | 写入审计（与 revisions 互补：审计记录“谁用什么工具做了什么事”） |

**时间双轨**（关键）：`event_time` 是“事情发生的时间”（“上周搬的工位”），`recorded_at` 是“我知道这件事的时间”（今天）。两者必须分开，否则“隔久再问”会答错；`time_quality` 明确记录用户说的时间粒度（精确/仅日期/约略/不明）。

**目录与条目的关系**：一条记录**可以不属于任何目录**（收件箱式默认池），也可以属于一个 collection；同一主题可先散记、后归并（`collection.move`）。

### 5.2 目录（collection）与 manifest

- **manifest**：一个 collection 可带一份可选清单 `manifest_json`，描述“这里通常记什么、有哪些字段、推荐标签”；初始为空即自由记录。
- **目录创建的三种触发**（Q1）：①用户明确说“建个目录”；②模型发现同一主题出现 ≥N 条（建议 N=3）时**建议**创建并等确认；③模型直接创建 `auto_created` 目录，事后可归档。
- **目录约束**：`path` 层级 ≤ 4，单段 ≤ 24 字；重名冲突返回候选而非自动合并；归档目录不影响其下记录的可查性。
- **不预设分类体系**：不内置“工作/生活/财务”等强制目录；模型可以建议，但用户不使用也完全可用。

### 5.3 记录类型与 schema 严格度

| 类型 | 字段 | 严格度 | 用途 |
| --- | --- | --- | --- |
| `note`（默认） | `title?`、`body`、`tags`、`event_time?` | `free` | 小想法、见闻、一句话 |
| `event` | `title`、`body`、`event_time`（必填）、`people?`、`place?` | `soft` | 发生过的具体事情 |
| `idea` | `body`、`tags`、`status?`（未成型/在试/已放弃） | `free` | 待发展的小想法 |
| `ledger`（示例，见 §6） | `account_id`、`amount_minor`、`currency`、`kind`（`expense`/`reversal`）… | `strict` | 需要可加总的记录 |

**严格度语义**：`free` 任意附加字段进 `props_json`；`soft` 缺字段只警告不影响写入；`strict` 缺字段**拒绝写入**并要求澄清。类型不能凭空把自由记录“强行升级”——升级是显式动作（`notebook.entry.retype`），旧数据保留。

### 5.4 来源、时间与标签

| 字段 | 写入来源 | 用途 |
| --- | --- | --- |
| `source.conversation_id` / `channel` / `turn_id` | 当前会话上下文 | “这条是哪次说的” |
| `source.utterance`（≤200 字摘要） | 用户原话 | 人为核对；**不存全文** |
| `created_by` | `user` / `model_inferred` | 区分用户明确说 vs 模型抽取（见 Q7） |
| `tags` | 用户显式给的 + 模型建议（建议需在回执中回显） | 检索主路径之一 |
| `event_time` + `time_quality` | 用户表述（“上周三”“大概是 9 月”） | 时间检索；不明时存 `unknown` 而非猜今天 |

### 5.5 纠正、冲正与删除

| 用户意图 | 动作 | 结果 |
| --- | --- | --- |
| “这条写错了” | `notebook.entry.revise(entry_id, patch, reason)` | 写 revision，正文更新，`status` 仍 `active` |
| “这条记重复了” | `notebook.entry.merge(keep, drop)` | 被并条目 `superseded`，指向保留条目 |
| “我不记得说过这个” | `notebook.entry.reverse(entry_id, reason)` | `reversed`，仍在审计中可查，默认检索不返回 |
| “删掉这条” | `notebook.entry.delete(entry_id, reason)` | **软删除**（Q8 决定是否提供彻底删除）；默认检索不返回 |
| “这笔记错目录了” | `notebook.entry.move(entry_id, collection_id)` | 记录 revision |
| “目录建错了” | `notebook.collection.archive(id)` | 目录归档，条目回到未归类 |

**原则**：任何改动**留痕**；不物理覆盖正文；删除的出口在 Q8 由用户决定。

### 5.6 查询

| 查询意图 | 工具 | 说明 |
| --- | --- | --- |
| 关键词/主题 | `notebook.search(query, collection?, tags?, type?, time_range?)` | 全文 + 标签过滤（SQL `LIKE`/FTS 组合，见 Q4 的严格度） |
| 时间窗口 | `notebook.timeline(from, to, collection?, tags?)` | “上个月我记过什么” |
| 某目录 | `notebook.collection.list(collection)` + 分页 | 目录浏览 |
| 单条 | `notebook.entry.get(entry_id)` | 含来源、版本链、敏感标记 |
| 可加总类型 | 类型的专属工具（如 `ledger.balance`，§6） | **只允许工具计算**，模型不得对 `entries` 自行加总 |
| “为什么这么答” | 上述工具返回的 `evidence[]`（entry_id、event_time、collection、excerpt） | 模型据此逐条引用 |

### 5.7 幂等

- `idempotency_key` = `hash(entry 语义：type|text 规范化|event_time|collection|source.conversation_id|source.turn_id)`。
- 同 key 同内容 → `{replayed: true, entry_id}`；同 key 不同内容 → `IDEMPOTENCY_MISMATCH`，不覆盖。
- 同一回合重试幂等；**不同回合说同一句话**（“今天很开心”）应各记一条——因此 key 必须含 `turn_id`，不能只按文本去重。
- 模型批量写入（一次说三件事）时，每条单独一个 key，部分失败不回滚已成功条目，并在回执里逐条标注。

### 5.8 敏感内容

| 项 | 方案 |
| --- | --- |
| 识别 | 写入时按规则打标（密码/证件号/银行卡/密钥形态）+ 模型可显式标 `sensitivity=private`（Q5） |
| 存放 | 默认与普通记录同库，但带 `sensitivity` 标记；**不进入记忆系统**的自动召回 |
| 展示 | 桌面可见；微信/手机端默认**不回显** `private` 记录的正文，只显示“有 1 条私密记录”（Q5 决定是否允许渠道查看） |
| 检索 | 默认检索不含 `private`，需用户当次明确要求 |
| 备份/导出 | 导出时可选“是否包含私密记录”，默认排除 |

### 5.9 持久化、备份与迁移

| 项 | 方案 |
| --- | --- |
| 位置 | `product.sqlite3` 新增表（Q4/Q6 之外的 Q11 决定；备选独立 `notebook.sqlite3`） |
| 事务 | 单条写入一事务；跨表（条目+revision+audit）同事务 |
| 索引 | `(collection_id, event_time)`、`(status, recorded_at)`、`UNIQUE(idempotency_key)`、`tags` 的关联表或 `json_each` 过滤 |
| 备份 | 产品库既有备份策略；可选 `notebook.export`（JSON/CSV，Q12） |
| 迁移 | 新增表用 `CREATE TABLE IF NOT EXISTS` + schema 版本递增；既有表零改动；回滚 = 保留表不读 |
| 与记忆的迁移 | **不做双向迁移**：记忆里的经历不回写进记事本，记事本里的记录也不进记忆（§9） |

### 5.10 权限与隐私

- 写入有副作用：与 `ActionGate` 一致，逐次审批；是否允许“本会话自动记录”（Q7）。
- 微信/手机：读取跟随渠道现有权限；`private` 记录默认不外显；写入需逐次确认。
- 注入：工具只返回本次查询需要的字段与 ≤300 字摘要，**不整表注入**。

### 5.11 工具 API（协议面）

| 工具 | 入参 | 出参 |
| --- | --- | --- |
| `notebook.entry.add` | `body`、`type?`、`title?`、`tags?`、`event_time?`、`time_quality?`、`collection?`、`props?`、`idempotency_key`、`source` | `{entry_id, collection, suggested_tags, warnings[], sensitivity}` |
| `notebook.entry.get` | `entry_id` | `{entry, revisions[], source, sensitivity}` |
| `notebook.entry.revise` | `entry_id`、`patch`、`reason` | `{entry_id, revision}` |
| `notebook.entry.reverse` / `delete` / `merge` / `move` | 见 §5.5 | `{entry_id, status, revision}` |
| `notebook.search` | `query?`、`tags?`、`collection?`、`type?`、`time_range?`、`limit` | `{entries[], evidence[], total}` |
| `notebook.timeline` | `from`、`to`、过滤条件 | 同上（按 `event_time` 排序） |
| `notebook.collection.create` / `list` / `archive` / `rename` | `path`、`manifest?` | `{collection_id, path, created_by}` |
| 类型专属 | 例：`ledger.balance`（§6） | 由类型定义 |

**写给模型的硬规则**

1. 回答“上次/之前/某天”的问题必须先 `notebook.search`/`timeline`，**不得**凭印象作答。
2. 引用记录时给出 `entry_id` 或可读摘要（“3 月 12 日记的”）。
3. 涉及汇总数字（金额、次数、天数）只有类型提供工具时才可回答，且必须用工具结果。
4. 不确定是否值得记录时，先回执“要记吗”而不是默认写入（Q7）。
5. 目录建议可以由模型提出，但不得在用户不知情时改变已有记录的归属。

---

## 6. 示例类型：`ledger`（报销只是它的一个用法）

> 本节说明“自由记录之上如何附加可计算结构”。**方案主体不是报销系统**；`ledger` 只是第一个 strict 类型。

- **结构**：`ledger_accounts`（项目/账户、`limit_minor`、币种、额度版本 `effective_from/to`）+ `ledger` 类型的 entries（`kind=expense|reversal`、`amount_minor`、`currency`、`occurred_on`、`external_ref`）。
- **分单位**：金额一律整数分（`_minor`），禁止浮点。
- **不变量**：整数换算、冲正一对一、同币种、历史不可改金额、超额度不拒写但如实显示（沿用第 1 轮 INV-1…INV-10 中与记录层相关的条目）。
- **工具**：`ledger.balance(account_id, as_of?)` → `{limit, spent, reversed, remaining, basis, entries_considered[]}`。
- **示例场景**：额度 3000（`300000` 分），已报 160（`16000`）+1800（`180000`）→ 余额 `104000`；**隔 90 天再问，答案相同且能列出这两条 entry**（若记事本被清空记忆/记忆被改写，答案不受影响）。
- **与本提案的关系**：`ledger` 证明“需要可加总时可以加结构”，不是要求所有记录都带 schema。

---

## 7. 验证与验收

### 7.1 用户场景（必须逐条通过）

| # | 场景 | 期望 |
| --- | --- | --- |
| S1 | “记一下：今天在梅坞老巷看到一只三花猫，很亲人” | 1 条 `note`，`event_time=今天`，tags 建议回显，未归属目录 |
| S2 | 连续记 4 条关于“搬迁”的事 | 第 3 条起模型建议建 `工作/搬迁` 目录（或按 Q1 自动建并回执） |
| S3 | 隔 90 天问“上次老周说搬工位是哪周？” | `notebook.search` 命中，返回 `event_time` 与依据，**不依赖记忆是否被改写** |
| S4 | “这条写错了，是 3 号楼” | `revise` 生成 revision，旧值可查 |
| S5 | “这条删掉” | 软删除，默认检索不返回，审计可查 |
| S6 | 同一回合重复提交 | `replayed: true`，只 1 条；不同回合说同一句话 → 2 条 |
| S7 | 记一条私密内容 | 标记 `sensitivity`，微信侧不外显正文（Q5 口径） |
| S8 | 报销示例：3000 额度 − 160 − 1800 | `ledger.balance` 返回 104000 分 |
| S9 | 问“我记过多少条搬迁相关的” | 计数由工具给出，不靠模型数 |

### 7.2 目标测试（实现阶段）

- 不变量：`strict` 类型缺字段拒绝、`time_quality=unknown` 不被填成今天、`revision` 链完整。
- 幂等：同 key 同内容/不同内容两条路径。
- 检索：标签、时间窗、collection、关键词四路各有正例与**负例**（不得返回其他记录）。
- 负控制：故意把 `private` 过滤去掉，S7 必须失败。
- 端到端：隔离 `--test-mode` 宿主 + 确定性模型跑 S1–S9。
- **“不依赖记忆”控制**：清空 `memory.sqlite3` 后跑 S3/S8，结果不变。

### 7.3 人工验收

临时 `<home>` 里真实对话三次（随手记 / 隔天追问 / 纠正），检查回执、依据、目录建议是否符合直觉。

### 7.4 未覆盖

多端同步、附件、富文本、自动从活动采集生成记录、历史笔记迁移（Q6/Q9/Q10/Q12）。

---

## 8. 分阶段实现

| 阶段 | 内容 | 门禁 |
| --- | --- | --- |
| P0 | 表结构 + `entry.add`（`note` 自由类型）+ `search`/`timeline` | Q1–Q4 定稿 |
| P1 | collection（建/改/归档）+ 模型建议目录 + revise/reverse/delete | Q1/Q8 定稿 |
| P2 | `event`/`idea` 类型与 schema 严格度 + tags 索引 | Q2 定稿 |
| P3 | `ledger` 类型示例（额度、冲正、余额） | Q3 定稿 |
| P4 | 敏感内容策略、渠道外显、导出 | Q5/Q12 |
| P5（可选） | 多端、导入、富文本 | Q6/Q9/Q10 |

**建议先 P0+P1**：先把“随手记 + 目录 + 找得回”跑通，再谈结构化的账。

---

## 9. 为什么不以记忆/向量检索为主路径

| 维度 | 记忆 + 召回（含向量） | 事件存储 |
| --- | --- | --- |
| 写入时机 | 维护流水线（活动投影 → 规则 → 可选语义整理），用户一句话不一定落库 | 用户/模型显式写入，立即持久 |
| 内容稳定性 | 语义整理会**改写**正文（`max_calls_per_hour=24`、`max_tokens_per_day=60000` 预算内） | 正文只经显式 revise 变更，历史保存在 revisions |
| 生命周期 | `working` 有 TTL（默认 86400 秒）；召回按预算截断 | 长期保存，无 TTL |
| 检索方式 | 按当前问题相似度取前 k，**结果不稳定** | 标签/时间/目录/全文，结果稳定可复现 |
| 可审计 | 难以回答“参与了哪些条目” | 每条记录、每次修改都有 id 与审计 |
| 可计算 | 模型心算不可靠 | 类型工具确定性计算 |
| 失效模式 | 静默错误（答得像、依据不存在） | 显式错误（找不到就追问） |

**结论**：用户“不能用传统记忆/向量检索”的判断成立。两者是**分工**而非替代——
- **记忆**：关于“我”的经历、偏好、约定；需要随时间被模型整理与推断。
- **事件存储**：关于“事与物”的原始记录；需要原样保留、可查、可算、可纠。
- 允许**弱连接**：查询记事本时可以把“相关记忆”作为补充提示，但**不能**作为金额/事实的唯一依据。本提案不改变现有记忆系统的任何行为。

---

## 10. 待用户/Codex 决策

> 每题给「选项 / 推荐默认 / 影响」。P0 开工前只需 **Q1–Q4**。**推荐默认不代替用户决定。**

**Q1 目录自由度**
- 选项：A. 模型发现同主题 ≥3 条时**建议**创建，用户确认后建（推荐）；B. 模型可直接建 `auto_created` 目录，事后可归档；C. 只能用用户明确要求的目录。
- 影响：目录会自己长出来，还是始终由用户掌控；B 最省事但可能产生杂乱目录。

**Q2 schema 严格度**
- 选项：A. 默认全部 `free`（`props_json` 兜底），仅少数类型 `strict`（推荐）；B. 每种记录都必须选类型；C. 完全无类型，一律自由文本。
- 影响：决定“记账必须带金额”这类约束是否存在；C 会让 `ledger` 无法保证可计算。

**Q3 ledger 类型的边界**
- 选项：A. 只做最小子集：账户+额度+支出+冲正+余额（推荐）；B. 加报销状态（claimed/pending/reimbursed）；C. 加多币种与汇率。
- 影响：决定“还剩多少”的语义（额度余额 vs 还能再报多少）；B/C 可后置。

**Q4 数据位置与检索实现**
- 选项：A. `product.sqlite3` 新增表，检索用标签/时间/目录 + `LIKE`/FTS 组合（推荐）；B. 独立 `notebook.sqlite3`；C. 引入 FTS5 独立索引表（含中文 trigram）。
- 影响：备份粒度、跨库一致性、中文检索质量（C 与 dev-0023 的中文评测结论相关）。

**Q5 敏感内容**
- 选项：A. 规则+模型打标，默认不进记忆召回、渠道不外显（推荐）；B. 只靠用户手动标 `private`；C. 整个记事本默认只在本机可见、渠道一律不能查。
- 影响：隐私强度与可用性的取舍；A 需要一套规则（密码/证件/银行卡）。

**Q6 并发/多端**
- 选项：A. 只保证同一 `<home>` 内一致（推荐）；B. 支持多设备同步。
- 影响：B 需要冲突合并与离线队列，属独立大点。

**Q7 写入触发**
- 选项：A. 用户明确说“记一下/记下来”才写（推荐）；B. 模型判断值得记 → 先回执“要记吗”再写；C. 自动写入，事后可删。
- 影响：会直接改变用户的被记录感受（“它在偷偷记我”）。

**Q8 删除与保留**
- 选项：A. 软删除 + 可彻底删除（二次确认，推荐）；B. 只软删除，永不物理删除；C. 按时间自动清理。
- 影响：审计链完整性 vs 用户“真的删掉”的期待；C 与“长期存储”目标冲突。

**Q9 迁移与导入**
- 选项：A. 不做（推荐）；B. 支持从 Markdown/CSV 一次性导入；C. 从现有记忆反向导入。
- 影响：C 会造成两个真相，需非常谨慎。

**Q10 通用抽象范围**
- 选项：A. 先只做事件存储一域（推荐）；B. 同时抽出可复用的“结构化事实”API。
- 影响：与“万能 PersonalState”的边界。

**Q11 与记忆的联动**
- 选项：A. 完全分离（推荐）；B. 记事本记录可作为记忆来源之一；C. 记忆召回时自动附带相关记事本条目。
- 影响：B/C 会让“用户随手写的私密想法”进入记忆整理预算与上下文，隐私与噪声都要重新评估。

**Q12 导出**
- 选项：A. 只依赖产品库备份（A）；B. 提供 `notebook.export`（JSON/CSV/Markdown，推荐放 P4）。
- 影响：导出默认是否包含私密记录。

---

## 11. 风险与未决项

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| R1 “通用”变“万能” | 抽象过宽会和 PersonalState 一样难维护 | 只做 entries/collections/types 三件套；类型按需增加 |
| R2 目录自动膨胀 | 模型自建目录过多 | Q1 倾向“建议后再建”；提供归档 |
| R3 与记忆两个真相 | 同一件事两处都有、两处都不全 | §9 分工 + Q11 默认分离 |
| R4 敏感内容外泄 | 私密想法进上下文或渠道 | Q5 标记 + 渠道默认不回显 + 导出默认排除 |
| R5 模型编造记录 | 转述时补细节 | 工具返回 excerpt，模型只可引用；写入 `created_by=model_inferred` 必须回执 |
| R6 未决项过多 | Q1–Q12 | P0 只需 Q1–Q4 |

---

## 12. 最小验证（Proposal 阶段即可完成）

1. **场景走查**：用 §7.1 的 S1–S9 对照 §5 的工具与不变量，确认每条都有对应能力。
2. **决策完备性**：Q1–Q4 有答复即可开 P0；Q5–Q12 延后到对应阶段。
3. **反例推演**：①“同一天说两次今天很开心”必须是两条；②“上周三”在 90 天后仍解出同一日期；③私密记录不出现在微信回显中。三条均已在 §5/§7 覆盖，需用户确认是否符合预期。
4. **冲突检查**：新增表不触碰 `preferences`（16 KiB）、`memories`（语义整理改写）、`calendar_events`（时间语义）的既有语义。

---

## 13. 与现有开发点的关系

| 对象 | 关系 |
| --- | --- |
| dev-0013/0014（记忆系统） | 不改记忆；§9 明确分工 |
| dev-0015（记忆/活动/日历融合） | 无关：记事本不参与融合层 |
| dev-0023（中文检索评测） | 若 Q4 选 C（FTS/trigram），复用其结论 |
| PN-0079（移除“工作”实体） | 一致：collection 是**记录分类**，不是任务/工作实体 |
| 第 1 轮报销提案 | 已被本版取代；报销降为 §6 的 `ledger` 示例 |
