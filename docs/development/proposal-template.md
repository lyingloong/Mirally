# 方案提案（Proposal PR）模板

> 用于**需要用户/Codex 做产品或技术决策**、不能直接进入实现的高风险点。  
> Proposal PR 只写方案，不写实现代码；决策完成后再开 Implementation PR。

## 1. 何时使用

- 用户点但存在未决产品取舍（范围、隐私、跨来源合成、保留/删除、日历读写等）
- 高风险：数据模型、权限、安全、协议、外部服务、凭据
- 会改变共享接口或 owner 的重构
- 删除类改动（如旧代码清理）需要先确认保留边界

低风险、纯文档、无产品取舍的点不需要 Proposal，直接走普通规格/实现。

## 2. 两阶段流程

### 阶段 A：Proposal PR

- 分支：`proposal/<point-id>-<slug>`
- 只改 `docs/development/proposals/<point-id>.md`（可加索引）
- PR 标题：`[Proposal] <point-id> <主题>`
- 标签：`type:proposal`、`phase:proposal`、`source:*`（按仓库现有标签；没有就只用标题/正文标记）
- 状态：Draft PR 或普通 PR 均可；**不合并、不实现**
- 审查：需求师/审查师检查来源、选项、风险、验证；Codex 可提出意见
- 问题：在 PR comment 用 `Q1`、`Q2` 编号提问；用户/Codex 直接回复
- 决策记录：队长把回复摘要写回 proposal 文档（新 commit），并在 PR 评论确认“已记录”

### 阶段 B：Decision Gate

进入实现必须同时满足：

- 用户/Codex 在 PR comment 明确写下针对**当前 proposal commit/方案**的 `proposal approved` 或“同意按推荐方案”；用户直接指示“按这个方案实现”也算
- 所有影响实施的 Q 已标为 `resolved`，或明确写成“延后到实现阶段”并说明理由
- 队长已把决定摘要写回 proposal 文档并提交；若方案有实质变化，需要重新确认

`proposal:accepted` 标签只是可视标记，**不得单独作为批准依据**；标签只有在上述明确批准后才可添加。Implementation PR 必须引用批准评论和 proposal commit。

未满足时，Implementation PR 不应创建；不得用“先写代码再让用户确认”替代。

### 阶段 C：Implementation PR

- 分支：`agent/<point-id>-<slug>` 或 `feat/<point-id>-<slug>`
- 标题/正文引用 Proposal PR 链接和已确认决策
- 正常走实现 → 验证 → 审查 → 待合并池
- 实现中若发现方案需要改：回到 Proposal PR 更新并重新确认，不在实现 PR 里静默改产品取舍

## 3. Proposal 文档模板

```markdown
# <point-id> · <主题> 方案提案

## 0. 用户原始需求

> <逐字/摘录；标明逐字、摘录或整理；不要冒充完整原话>

## 1. 背景与现状

- 相关架构/文档/代码入口：
- 已实现 / 部分实现 / 未实现：
- 为什么现在需要决策：

## 2. 目标与非目标

### 目标
- ...

### 非目标
- ...

## 3. 可选方案

| 方案 | 做法 | 优点 | 代价/风险 | 适用条件 |
| --- | --- | --- | --- | --- |
| A | ... | ... | ... | ... |
| B | ... | ... | ... | ... |

## 4. 推荐方案

- 推荐：
- 理由：
- 关键取舍：
- 回滚/兼容：

## 5. 数据、接口与权限

- 数据 owner / schema：
- 接口/扩展点：
- 权限/隐私：
- 迁移/回滚：

## 6. 验证与验收

- 目标测试：
- 负控制：
- 人工验收：
- 未覆盖：

## 7. 分阶段实现计划

1. ...
2. ...

## 8. 待用户/Codex 决策

- Q1：<问题>
  - 选项：
  - 推荐默认：
  - 影响：
- Q2：...

## 9. 风险与未决项

- ...
```

## 4. PR 评论问题格式

```text
Q1（范围）：记忆是否只保留“值得长期记住”的内容，还是主动记录日常？
- A. 只保留显式/高置信内容（推荐）
- B. 主动记录日常，再按保留策略清理
请回复 A/B 或具体范围。
```

- 一个问题一个 comment，避免大段混问
- 用户回复后，队长在文档 §8 记录结论并回评论确认
- 未回答的 Q 标为 `open`；已回答标 `resolved`

## 5. 与 AgentTeams 状态的关系

- Proposal 对应点状态仍是 `specifying`（或 `blocked` 等用户决策）
- Proposal 通过后，点进入 `implementing`，创建 implementation 任务
- Proposal 被拒绝或撤回：当前状态机用 `cancelled`（`specifying -> cancelled` 是允许的），不要写成 `rejected`/`proposed`；若点处于 `blocked`，先用 `resume` 回到原状态再 `cancelled`，释放容量。提案记录保留在文档/Git 历史，PR 关闭，不进入待合并池
- Proposal PR 本身不进入待合并池；它是决策载体，不是产品实现

## 6. 不作为

- 不把 Proposal 写成完整实现规格；实现细节留给后续 spec
- 不在 Proposal PR 改产品代码/测试/配置
- 不用 Proposal 绕过正常 verify/review
- 不自动把 Proposal 标为 accepted；必须用户/Codex 明确回复
