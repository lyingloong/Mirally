# dev-0024 · 项目报销台账（结构化账本 + 工具查询）方案提案

> 状态：**Draft Proposal（阶段 A）**。本文只写方案，不含任何实现代码；实现须在用户/Codex 明确 `proposal approved` 之后另开 Implementation PR。
> 本文按 `docs/development/proposal-template.md`（PR `docs/proposal-process`）的章节组织，并补齐模板要求之外、本点特有的「为什么不用 RAG」「金额不变量」「幂等」「歧义澄清」「持久化与迁移」等小节。

---

## 0. 用户原始需求

> **整理**（不是逐字）：项目 XXX 的报销额度是 3000；已经报了 160 和 1800 两笔；希望 Mirally **记住**这些，并在被问到时回答**还剩多少**；即使**隔很久再问**，回答也要**有理有据**（能指明依据的是哪几笔、额度来自哪里）。
>
> **逐字要点**（用户明确约束）：**「不能用传统记忆/向量检索」**；应由**模型维护设计好的结构化存储**，并**规定协议**。
>
> 来源：`user-request`（用户直接提出的需求，本会话 2026-11-02）。整理人：需求师。上文斜体部分为需求师归纳，§0 与 §8 的决策问题不得改写用户原话含义。

**“有理有据”在本提案中的定义**：任何余额回答都必须能追溯到 (a) 额度的来源条目、(b) 参与计算的每一笔支出条目、(c) 计算口径（币种、状态过滤、舍入），以及 (d) 最后一次修改是谁在什么时候做的。

---

## 1. 背景与现状

### 1.1 相关架构与代码入口（现行，`main` = `ad2f1d82`）

| 面 | 位置 | 与本案的关系 |
| --- | --- | --- |
| 持久记忆 `MemoryStore` | `mirally/core/memory/store.py`（`memories` 的 `kind ∈ {working, episodic, task, semantic}`，`provenance ∈ {observed, model_inference, user_confirmed}`） | **本提案明确不使用**：`kind` 是自由文本记忆，不是可加总的账目 |
| 记忆召回 | `mirally/runtime/memory/recall.py`（`plan/eligible/retrieve/excerpt`，FTS + 规则） | 不参与余额计算；可作为“我记得你在跟这个项目”的**引导线索**，但不能作为金额依据 |
| 偏好 | `mirally/framework/service.py:980` `preference.set(key, value, scope)`，JSON ≤16 KiB | 只适合存“默认报销状态口径”这类**单值设置**，不适合账目流水 |
| 产品 SQLite | `mirally/framework/service.py`（`product.sqlite3`，schema 11）：`conversations`、`calendar_events`、`deliveries`、`preferences`… | 新的账本表应与其**同库同事务**，避免第二份产品事实 |
| 工具注册 | `kernel/dsh/packages/mirally/mirally-product/src/calendar-tools.mjs:10` `host.ctx.tools.register({name, description, parameters, output, …})`，带 `checkCommandScope`、`guard`、`request_id` 幂等 | 工具层协议参照此处 |
| 源校验 | `ActionGate`（`action-gate.mjs`）绑定会话/回合/动作内容 | 账本写操作须走同一套审批与幂等 |

### 1.2 已实现 / 部分 / 未实现

| 能力 | 状态 |
| --- | --- |
| 结构化账本（额度、支出、冲正） | **未实现**（无任何对应表/工具） |
| 金额可加总的持久事实 | **未实现**（`preferences` 只能存 JSON 值，且没有并发/流水语义） |
| “隔很久再问仍正确” | **未实现**（记忆会被新内容挤占、预算截断、语义整理改写） |

### 1.3 为什么现在需要决策

用户提出的是一个**可计算的账目问题**，而不是“记住一段话”。用记忆回答会得到一个**看起来像回答、实际没有依据**的数字；这直接违反 [产品方向](../../../docs/product/vision.md) “有来源、范围、时间与纠正”的记忆承诺，以及 `docs/reference/capabilities.md` “未实现能力不得由模型口头承诺代替”的边界。因此需要先定协议与数据模型，再决定是否实现。

---

## 2. 目标与非目标

### 目标

1. **可加总的权威账本**：额度与支出是结构化行，余额是**确定性计算**的结果，不是模型生成的陈述。
2. **跨时间稳定**：隔 1 天、3 个月、1 年再问，只要账本没被改，答案相同；账本被改，回答必须带上改动与时间。
3. **有理有据**：每次回答附带 `as_of`、参与计算的条目 id、口径与来源；冲突/待定项显式列出。
4. **模型维护但受协议约束**：模型只能通过**固定工具**读写；工具负责校验不变量、幂等与审计，模型负责识别用户意图与澄清歧义。
5. **人类可审计、可纠正**：每次写入有来源（会话/渠道/时间/原话摘要）与操作者；纠正用冲正而非改历史。

### 非目标

- **不使用记忆召回/向量检索**回答金额问题（见 §10 的论证）。
- 不做财务系统：不做预算审批流、发票真伪校验、与公司财务系统对接、税金计算。
- 不做多币种换算（币种作为**标签**存在，见 Q3；跨币种求和不做）。
- 不做 OCR 自动记账（材料提取可作为未来输入，但不在本提案）。
- 不自动把**每一次**对话里的“花了 X 元”都写进账本（需明确记账意图或用户确认；见 Q6）。
- 不改动现有记忆、偏好、日历、投递的任何语义。

---

## 3. 可选方案

| 方案 | 做法 | 优点 | 代价/风险 | 适用条件 |
| --- | --- | --- | --- | --- |
| **A. 结构化账本 + 工具查询（推荐）** | 新增 `ledger` 领域表（项目、额度、条目）与 6 个工具；余额由 SQL 求和；模型通过工具维护 | 计算确定、可审计、可纠正、跨时间稳定；与现有 `product.sqlite3` 同事务；失败可回滚 | 需新增 owner、迁移与工具面；不变量与幂等要设计好 | 金额、额度、累计类事实 |
| B. 记忆 + 模型心算 | 把“额度 3000、报了 160/1800”写进 `MemoryStore`，回答时模型自己加总 | 零新表、零新工具 | 召回可能漏条目、预算截断、语义整理会改写；**无法证明依据**；数字会随记忆变化而漂移 | **不可用**（用户已明确排除） |
| C. 偏好 JSON 存整个账本 | `preference.set("ledger.XXX", {...})` | 复用现有 API | 16 KiB 上限、无并发/流水/审计语义、无幂等键；纠正只能整块覆盖，无法追溯 | 只适合“默认状态口径”这类单值 |
| D. 外部账本（表格/云端） | 让用户维护 Excel/在线表格，Mirally 读取 | 财务口径成熟 | 需要文件授权、格式脆弱、离线不可用；本机仍是唯一事实 | 用户已有表格工作流时 |
| E. 通用“结构化事实”抽象 | 做一个万能 key-value/表格抽象给所有场景用 | 复用性高 | 与 [产品方向](../../../docs/product/vision.md) 原则 1“不建立万能 PersonalState”冲突；抽象未经验证 | 出现 2–3 个真实场景后再考虑 |

**结论**：A 为推荐方案；B 被用户排除；C 只用于 §5.3 的“口径设置”；D/E 见 §8 的 Q7/Q8。

---

## 4. 推荐方案（A）总览

```text
用户："项目 XXX 还剩多少没报？"
   ↓
模型：ledger.project.resolve("XXX")  → 项目 id（有歧义则追问，不猜）
   ↓
模型：ledger.balance(project_id, as_of=now) → {limit, spent, remaining, entries[], basis, open_questions[]}
   ↓
模型：用工具返回值组织回答（逐条引用条目 id、时间、来源），不自行加总
```

**职责切分（协议核心）**

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| 模型 | 识别项目与金额意图、向用户澄清歧义、调用工具、把结果转述成人话 | 不做算术、不猜项目、不改历史、不补写缺字段 |
| 工具 | 校验不变量、幂等、事务、审计、金额换算为整数分、汇总 | 不做语义理解、不决定是否记账 |
| 账本表 | 唯一金额事实 | 不做跨币种换算 |

---

## 5. 数据、接口与协议

### 5.1 数据模型（金额一律为整数分）

> 约定：所有金额字段以**最小货币单位的整数**存储（人民币即“分”），字段名以 `_minor` 结尾。**禁止**以浮点数存储金额。

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `ledger_projects` | `id TEXT PK`、`name TEXT`、`aliases_json`、`status TEXT`（`active`/`archived`）、`created_at`、`updated_at` | 项目身份；`aliases` 存用户可能说的别名（“XXX”“那个搬迁项目”） |
| `ledger_accounts` | `project_id`、`currency TEXT`（ISO 4217，如 `CNY`）、`minor_unit INTEGER`（人民币=2）、`limit_minor INTEGER`、`effective_from`、`effective_to NULL`、`revision` | **额度可版本化**：额度调整不覆盖历史，旧额度保留 `effective_to` |
| `ledger_entries` | `id TEXT PK`、`project_id`、`kind TEXT`（`expense`/`reversal`/`adjustment`）、`amount_minor INTEGER`、`currency`、`occurred_on DATE`、`status TEXT`（见 Q4）、`category TEXT NULL`、`note TEXT NULL`、`external_ref TEXT NULL`（票据号）、`source_json`、`idempotency_key TEXT UNIQUE`、`created_by`、`created_at`、`reverses_id TEXT NULL` | 支出与冲正都是条目；**不提供 UPDATE 金额** |
| `ledger_audit` | `seq INTEGER PK AUTOINCREMENT`、`at`、`actor`、`channel`、`conversation_id`、`tool`、`request_id`、`entry_id`、`action`、`before_json`、`after_json`、`reason` | 只追加（append-only） |
| `ledger_conflicts`（可选，见 Q9） | `id`、`project_id`、`kind`、`payload_json`、`state`（`open`/`dismissed`） | “同一笔可能重复录入”等待澄清项 |

**为什么额度单独一张表**：额度会变（涨到 5000、或者分阶段拨付），若直接写在项目行上，历史回答里的“还剩多少”会失去当时的口径。版本化额度让 `as_of` 查询有确切依据。

### 5.2 金额不变量（工具必须强制执行）

| # | 不变量 | 违反时 |
| --- | --- | --- |
| INV-1 | 所有金额为整数（分）；入库前把用户说的小数按 `minor_unit` 精确换算，**禁止**用 `float` 乘 100 | `INVALID_AMOUNT`，不写库 |
| INV-2 | `amount_minor > 0`；方向由 `kind` 表达（`expense` 为正、`reversal` 为负向冲正） | `INVALID_AMOUNT` |
| INV-3 | 一条 `reversal` 必须 `reverses_id` 指向同项目、同币种、未被冲正过的条目，且金额相等 | `INVALID_REVERSAL` |
| INV-4 | 同一 `reverses_id` 最多被冲正一次 | `ALREADY_REVERSED` |
| INV-5 | 条目 `currency` 必须与项目账户币种一致（跨币种不做换算，见 Q3） | `CURRENCY_MISMATCH` |
| INV-6 | `idempotency_key` 唯一；相同 key 与相同内容 → 返回原结果（不算新写入） | 幂等重放 |
| INV-7 | 余额 = 期初额度 − Σ(有效支出) + Σ(冲正)，**全部在 SQL 中以整数求和**；不允许应用层浮点 | — |
| INV-8 | 超额度**不拒绝**写入，但查询必须返回 `over_limit: true` 与超额数值（用户可能先垫后补） | 只在查询中体现 |
| INV-9 | 每条写入必须有 `source_json`（会话 id、渠道、用户原话摘要）与 `created_by` | `SOURCE_REQUIRED` |
| INV-10 | 历史条目不可修改金额/项目/发生日期；只能追加 `reversal` 或 `adjustment` | `IMMUTABLE` |

### 5.3 工具 API（协议面）

命名遵循现有工具风格（`host.ctx.tools.register`），全部经 `ActionGate` + `request_id` 幂等。

| 工具 | 入参 | 出参 | 说明 |
| --- | --- | --- | --- |
| `ledger.project.resolve` | `query`（用户原话里的项目名/别名）、`create_if_missing=false` | `{project_id, name, matched_by, candidates[]}` | **只读**；多个候选时返回 `candidates` 让模型追问，**不自动选** |
| `ledger.project.create` | `name`、`aliases[]`、`currency`、`limit_minor`、`effective_from` | `{project_id, revision}` | 写操作；`idempotency_key` 必填 |
| `ledger.entry.add` | `project_id`、`kind`、`amount_minor`、`currency`、`occurred_on`、`status`、`category?`、`note?`、`external_ref?`、`idempotency_key`、`source` | `{entry_id, balance_after, audit_seq}` | 单条写入，返回**写入后的余额**便于模型直接引用 |
| `ledger.entry.reverse` | `entry_id`、`reason`、`idempotency_key`、`source` | `{reversal_id, balance_after}` | 冲正，不改原条目 |
| `ledger.entries.list` | `project_id`、时间范围、`status[]`、`include_reversed=true`、分页 | `{entries[], total_minor, page}` | 明细与审计核对 |
| `ledger.balance` | `project_id`、`as_of?`、`status_filter[]`、`currency` | `{limit_minor, spent_minor, reversed_minor, remaining_minor, over_limit, as_of, currency, entries_considered[], basis, open_questions[]}` | **余额唯一入口**；`basis` 说明口径，`entries_considered` 给出参与计算的条目 id |

**协议约束（写给模型看的硬规则）**

1. 回答任何“还剩多少/报了多少”必须先调用 `ledger.balance`，**不得**自行对 `entries.list` 的结果心算。
2. 引用金额时必须同时给出 `as_of` 与 `entries_considered` 中的至少一次幂等引用（条目 id 或用户可读的“9 月 12 日报的 1800”）。
3. 项目名歧义（`candidates.length > 1`）时**必须追问**，不得按名称相似度自选。
4. 用户给出的新支出缺少金额或日期时，先补问缺失字段，再写入（见 INV-9 的 `source`）。
5. 写入前若 `idempotency_key` 已存在，工具返回原结果，模型应说明“这笔之前已经记过”。

### 5.4 幂等与重复提交

- `idempotency_key` 由调用方生成，**语义为“同一次用户意图”**：建议格式 `sha256(project_id|kind|amount_minor|occurred_on|external_ref|source.conversation_id|source.turn_id)`；`source.turn_id` 取当前回合 id，使**同一回合重试**幂等、**不同回合相同金额**仍可分别记账。
- 工具层：`UNIQUE(idempotency_key)`；冲突且内容一致 → 返回 `{replayed: true, entry_id, balance_after}`；冲突但内容不一致 → `IDEMPOTENCY_MISMATCH`，不覆盖。
- 网络/宿主重启/用户在微信与桌面同时提交（见 Q5）：以 `idempotency_key` 去重，**不**以“金额相同”去重。
- 疑似重复（金额相同、日期相近、不同 key）不自动合并，登记到 `ledger_conflicts`，下次余额查询以 `open_questions` 返回。

### 5.5 来源与审计

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| `source.conversation_id` / `channel` / `turn_id` | 当前会话上下文 | 回答“这笔记在哪说的” |
| `source.utterance`（≤200 字摘要） | 用户原话 | 人为核对 |
| `created_by` | `user` / `model_inferred` / `import` | 区分用户明确说 vs 模型推断（推断必须待确认，见 Q6） |
| `ledger_audit` 每次写入一行 | 工具 | 审计链：谁、何时、通过哪个渠道、改了什么 |

**审计可核对性**：任一余额回答都应能在 `ledger_audit` 中找到对应条目与额度版本的写入记录；`ledger.entries.list(include_reversed=true)` 能完整复现历史。

### 5.6 用户纠正与撤销

| 用户意图 | 工具动作 | 结果 |
| --- | --- | --- |
| “那笔 1800 记错了，其实是 800” | `reverse(1800)` + `add(800)`（同一 `idempotency_key` 家族，`source` 注明纠正） | 历史保留两条冲正/新增，余额正确 |
| “那笔根本没报，删掉” | `reverse(entry_id, reason="user_says_not_reported")` | 原条目仍在，状态显示“已冲正” |
| “额度不是 3000，是 5000” | 新额度版本（`effective_from=今天`），旧版本保留 | `as_of` 在旧日期仍算 3000 |
| “刚才那笔记到别的项目去了” | `reverse` + 在新项目 `add` | 两项目都有审计记录 |

**不允许**：直接改 `amount_minor`、直接删行、`UPDATE` 项目归属。原因：审计与“隔很久再问”的可追溯性依赖不可变历史。

### 5.7 查询余额与明细

- **余额**：`ledger.balance`，默认口径 = 全部 `status` 参与（口径见 Q4），返回 `limit/spent/reversed/remaining/over_limit/as_of/basis`。
- **“为什么是这个数”**：同一响应的 `entries_considered` 列出每条参与计算的条目（id、日期、金额、状态、来源摘要），模型据此逐条复述。
- **明细与对账**：`ledger.entries.list` 支持按时间、状态、类别过滤，`total_minor` 由 SQL 汇总；用于“把这几个月的都列一下”。
- **`as_of`**：允许问“上个月底还剩多少”，用当时的额度版本与截至该日的条目计算。

### 5.8 歧义澄清协议

| 歧义类型 | 工具返回 | 模型行为 |
| --- | --- | --- |
| 项目名不唯一 | `candidates[]`（含最近使用时间、别名命中方式） | 列出候选并追问，**不选默认** |
| 项目不存在 | `{found: false}` | 询问是否新建，并在用户确认后调用 `ledger.project.create` |
| 金额单位不明（“一千八”） | 工具不做推断 | 模型按用户语言习惯理解，但在写入回执中明确回显“1800.00 元”让用户确认 |
| 币种不明 | `currency` 必填 | 缺省用项目账户币种，并在回执回显 |
| 同一笔可能重复 | `open_questions[]` | 主动提示“之前好像记过一笔 1800，是同一笔吗？” |
| 日期不明 | `occurred_on` 必填 | 追问；**不得**默认今天 |

**硬规则**：任何写入回执都必须回显“项目、金额（带币种）、日期、口径”，用户可在看到回执后立即纠正。

### 5.9 持久化、备份与迁移

| 项 | 方案 |
| --- | --- |
| 位置 | 与产品事实同库：`<home>/framework/product.sqlite3`（与 `conversations`/`calendar_events` 同事务），**不**写入 `memory.sqlite3` 或 `data/activity/` |
| 事务 | 单条写入一个事务；冲正与新增的“纠正对”在同一事务内完成 |
| 索引 | `ledger_entries(project_id, occurred_on)`、`(project_id, status)`、`UNIQUE(idempotency_key)` |
| 备份 | 导出 `ledger.export`（可选，见 Q11）输出 JSON/CSV，字段只含账本与审计，不含对话正文；产品库本身按现有 home 备份策略 |
| 迁移 | 新增表用 `CREATE TABLE IF NOT EXISTS` + schema 版本号递增；不动的既有表零改动；回滚 = 保留表不读（数据可删，属开发阶段授权范围，见 AGENTS.md） |
| 删除项目 | `archived` 状态而非物理删除；物理删除单独工具 + 二次确认（见 Q12） |
| 跨设备 | 本期不做同步；多端看到同一本账的前提是同一 `<home>`（见 Q5） |

### 5.10 权限与隐私

- 账本写入属**有副作用**动作：与现有 `ActionGate` 一致，逐次审批；用户可在会话内选择“本会话允许自动记账”后再写（是否需要该开关见 Q13）。
- **微信/手机**：账本读取跟随渠道现有权限；写入在微信侧需逐次确认（与 `docs/architecture/channels.md` 的“微信与桌面共用动作审批”一致）。
- **隐私**：账本不存对话全文，只存 ≤200 字原话摘要；导出文件不含凭据与 memo 正文以外的内容。
- 模型提示中注入的账本内容仅限本次查询需要的字段（项目、金额、日期、状态），**不整表注入**。

---

## 6. 验证与验收

### 6.1 用户场景（必须逐条通过）

| # | 场景 | 期望 |
| --- | --- | --- |
| S1 | 建项目 XXX，额度 3000 元 | 返回 `project_id`，`limit_minor=300000` |
| S2 | 记两笔：160、1800 | `balance` 返回 `spent_minor=196000`、`remaining_minor=104000` |
| S3 | **隔很久**（模拟 90 天后、记忆库被大量新内容挤占）再问“还剩多少” | 返回**同一个** `104000`，且 `entries_considered` 含那两笔 |
| S4 | 记忆里的相关内容被删除/撤回 | 余额不受影响（账本不依赖记忆） |
| S5 | 用户说“1800 是 800” | 冲正+新增，余额 300000−16000−80000=204000 |
| S6 | 同一笔重复提交（双击/网络重试） | 只记一笔，第二次返回 `replayed: true` |
| S7 | 用户问“XXX 还剩多少”，存在两个同名项目 | 返回候选并要求澄清，不自动选 |
| S8 | 超额度（额度 3000，已报 3200） | 返回 `over_limit: true`、`remaining_minor=-20000`，并如实说明 |

### 6.2 目标测试（实现阶段的验收口径，不在本提案写代码）

| 类型 | 内容 |
| --- | --- |
| 不变量单测 | INV-1…INV-10 各一条正例 + 一条反例（非法金额、跨币种、二次冲正、修改历史） |
| 幂等测试 | 同 key 同内容 → `replayed`；同 key 不同内容 → `IDEMPOTENCY_MISMATCH` |
| 汇总测试 | 整数求和、`as_of` 额度版本、冲正后余额 |
| 端到端 | 现有 `--test-mode` 隔离宿主 + 确定性模型，走一遍 S1–S8 |
| 负控制 | 故意把 `status` 过滤口径改错，S3/S5 必须失败（证明测试不是空跑） |
| “不用记忆”的控制 | 清空 `memory.sqlite3` 后跑 S3，结果不变 |

### 6.3 人工验收

在一个临时 `<home>` 里真实对话三次（首次记账、当日追问、模拟隔月追问），检查回执是否回显正确、审计能否复现、微信侧是否能完成一次写入与一次查询。

### 6.4 未覆盖

真实公司财务口径（审批、发票）、多币种、跨设备同步、历史数据导入（见 Q10/Q11）。

---

## 7. 分阶段实现计划（每阶段独立可回滚）

| 阶段 | 内容 | 产出 | 门禁 |
| --- | --- | --- | --- |
| P0 | 表结构 + `ledger.balance`/`entries.list` 只读工具 | 能对合成数据算出余额 | 只需 §8 中 Q1–Q4 定稿 |
| P1 | 写入工具 `project.create`/`entry.add` + 幂等 + 审计 | S1/S2/S6 通过 | Q5/Q6 定稿 |
| P2 | 冲正与纠正 + 额度版本 + `as_of` | S5 通过 | Q4 定稿 |
| P3 | 歧义澄清、`open_questions`、项目归档 | S7/S8 通过 | Q12 定稿 |
| P4（可选） | 导出/备份、导入旧账 | `ledger.export` | Q10/Q11 |
| P5（可选） | 微信/手机写入体验 | 渠道验收 | Q5/Q13 |

**不建议**一次性做完 P0–P3：账本一旦写入真实数据，后续改数据模型的成本远高于改工具。

---

## 8. 待用户/Codex 决策

> 每个问题都给「选项 / 推荐默认 / 影响」。**推荐默认不代替用户决定**；未回答的问题在实现阶段前保持 `open`。

**Q1 项目识别**
- 选项：A. 用户首次说项目名时创建，别名由用户补充；B. 自动从名称相似度匹配已有项目；C. 每次都必须显式选择。
- 推荐默认：A（歧义时返回候选并要求澄清，不自动匹配）。
- 影响：决定 `ledger.project.resolve` 的匹配强度；B 有“记到错项目”的风险。

**Q2 项目与“工作/会话”的关系**
- 选项：A. 账本项目是独立实体，可关联会话但不依赖；B. 复用会话作为项目。
- 推荐默认：A（会话是协作单位，不是账目容器；与 PN-0079 移除“工作实体”的方向也不冲突）。
- 影响：是否需要会话↔项目关联表。

**Q3 币种**
- 选项：A. 项目级单一币种（推荐），跨币种禁止混算；B. 条目级多币种 + 汇率换算；C. 只支持人民币（`minor_unit=2`）。
- 推荐默认：A（多币种可在 P4 之后加，不阻塞）。
- 影响：`ledger_accounts.currency` 的约束强度与查询复杂度。

**Q4 报销状态口径**
- 选项：A. 单一状态 `spent`；B. `claimed`（已提交）/`approved`（已批准）/`reimbursed`（已到账）/`pending`/`rejected`；C. 用户自定义状态集。
- 推荐默认：B 的最小集 `claimed|pending|reimbursed`；余额默认只计入“不计 rejected 的已发生支出”，并把“待审批”单独列示。
- 影响：**直接决定“还剩多少”的语义**——是“额度余额”还是“还能再报多少”。这是本提案最需要用户拍板的一项。

**Q5 并发与多端**
- 选项：A. 只保证同一 `<home>` 内一致性（桌面 + 微信共用产品库）；B. 支持多设备同步。
- 推荐默认：A（与现有产品结构一致：微信/手机与桌面共用同一逻辑会话与产品库）。
- 影响：B 需要同步协议、冲突合并与离线队列，属独立大点。

**Q6 记账触发**
- 选项：A. 用户明确说“记一笔”才写；B. 模型从对话中推断并**先回执再写入**（用户可撤销）；C. 自动写入，事后可纠正。
- 推荐默认：A + B 的“先回执”，不做 C。
- 影响：决定 `created_by=model_inferred` 是否允许，以及 INV-9 的确认流程。

**Q7 是否允许从材料（票据/表格）导入**
- 选项：A. 本期不做；B. 支持 CSV 导入；C. 支持票据图片 OCR。
- 推荐默认：A。
- 影响：材料提取与 OCR 属既有能力边界，需另点。

**Q8 通用“结构化事实”抽象**
- 选项：A. 先只做账本这一域；B. 抽出通用表格/事实 API 供多场景复用。
- 推荐默认：A（出现第 2–3 个真实场景后再抽象，避免万能 PersonalState）。
- 影响：决定本点是否要顺带设计通用接口。

**Q9 疑似重复的处理方式**
- 选项：A. 写入时登记 `ledger_conflicts`，查询时提示；B. 直接拒绝写入并追问；C. 自动合并。
- 推荐默认：A。
- 影响：需要一张可选的冲突表与提示协议。

**Q10 历史数据导入**
- 选项：A. 不做；B. 提供一次性 JSON/CSV 导入并保留 `provenance=import`。
- 推荐默认：A。
- 影响：导入的审计与幂等边界。

**Q11 导出与备份**
- 选项：A. 只依赖产品库备份；B. 额外提供 `ledger.export`（JSON/CSV）。
- 推荐默认：B（低成本、便于对账），但仅在 P4。
- 影响：导出内容是否含原话摘要（隐私）。

**Q12 删除项目**
- 选项：A. 仅归档；B. 归档 + 物理删除（二次确认）。
- 推荐默认：A。
- 影响：审计链是否可能断裂。

**Q13 自动记账开关**
- 选项：A. 始终逐次审批；B. 提供“本会话自动记账”临时授权。
- 推荐默认：A（与 PN-0054 的逐次/临时/持续三档一致，可在 P5 再评估 B）。
- 影响：`ActionGate` 接入方式。

**Q14 数据位置**
- 选项：A. `product.sqlite3` 新增表（推荐，同事务、同备份）；B. 独立 `ledger.sqlite3`（隔离风险，但跨库无事务）。
- 推荐默认：A。
- 影响：备份粒度与跨库一致性；若用户担心账本与聊天同库，选 B 需要额外一致性设计。

---

## 9. 风险与未决项

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| R1 口径错导致“错误但自信”的余额 | 报销状态口径未定（Q4） | 余额响应必须带 `basis`；未决口径在回答中显式标注 |
| R2 模型绕过工具自算 | 模型可能直接对明细加总 | 协议硬规则 + 提示词 + 端到端测试（S3 人工核对 `entries_considered`） |
| R3 重复记账 | 重试/双击/多端 | `idempotency_key` + `ledger_conflicts` |
| R4 额度变更污染历史 | 直接覆盖额度 | 额度版本化 + `as_of` |
| R5 隐私 | 备注/原话摘要入库 | 摘要 ≤200 字；导出不含凭据；工具不整表注入 |
| R6 与记忆并存造成“两个真相” | 用户可能既让 Mirally “记住”又让它记账 | 回答金额只用账本；记忆最多作为“可以帮你记账吗”的引导（需在实现时明确文案） |
| R7 未决项过多导致实现停滞 | Q1–Q14 | 分阶段：P0 只需 Q1–Q4 |

---

## 10. 为什么不用记忆 / 向量检索（用户已明确排除，这里给出论证）

| 维度 | 记忆 + 模型心算 / RAG | 结构化账本 |
| --- | --- | --- |
| **可加总性** | 召回是“相似度取前 k”，漏一条就错；预算内截断（现有 `context_token_budget`）会静默丢条目 | SQL 精确求和，全量参与 |
| **确定性** | 同一问题隔天问，召回集合会随新记忆、预算、语义整理变化 | 账本不变则答案不变 |
| **可审计** | 无法给出“参与了哪些条目”的完整集合；改动后无历史 | `entries_considered` + 只追加审计 |
| **可纠正** | 只能改记忆正文或撤回来源，难以表达“这笔冲正” | 冲正记录，历史保留 |
| **金额安全** | 模型算术不可靠、浮点误差、单位混淆（元/分） | 整数分 + 工具强制换算 |
| **成本** | 每次问答要召回 + 大上下文 | 一次点查 + 小响应 |
| **失效模式** | 静默错误：数字看着对、依据不存在 | 显式错误：找不到项目就追问 |

**结论**：用户“不能用传统记忆/向量检索”的判断成立。记忆可以承担“我在跟一个叫 XXX 的项目打交道”这类**线索**，但任何金额都必须来自账本。

**边界**：本提案不反对记忆在其他场景的价值，也不改变现有记忆系统的任何行为。

---

## 11. 最小验证（Proposal 阶段即可完成，零实现）

1. **口径走查**：用本文 §6.1 的 S1–S8 逐条对照 §5 的工具与不变量，确认每条场景都有对应工具能力与返回字段（需求师已完成初查，见下方“已知缺口”）。
2. **决策完备性**：§8 的 Q1–Q4（P0 必需项）全部有用户答复后，P0 可以开工；Q5–Q14 可延后到对应阶段。
3. **反例推演**：用“1800 记错为 800”“额度涨到 5000”“同一笔重复提交”三个反例，检查 §5.6/§5.4 是否覆盖（本提案已覆盖，需用户确认是否符合预期）。
4. **与现有系统的冲突检查**：确认新增表不触碰 `preferences`（16 KiB）、`memories`、`calendar_events` 的语义；`product.sqlite3` schema 版本递增。

**已知缺口（需要用户确认）**：余额语义（Q4）未定时，S2 的“还剩 1040”只在“全部已报金额都算作占用额度”的口径下成立；若用户认为“已报销到账后才占用额度”，则口径不同、数字也不同。**这一点必须在实现前定。**

---

## 12. 与现有开发点的关系

| 对象 | 关系 |
| --- | --- |
| dev-0013/0014（记忆系统） | 本提案明确**不依赖**记忆；不改 `MemoryStore` |
| dev-0015（记忆/活动/日历融合） | 无关：账本不参与融合层 |
| PN-0079（移除“工作”实体） | 一致：本项目是**账目容器**，不是任务/工作实体 |
| 现有 `preference.set` | 只用于 Q4 的口径设置，不承载账目 |
| open PR（核对时） | 无 PR 触碰 `product.sqlite3` schema 或工具注册以外的账本领域（实现时需再核对一次） |
