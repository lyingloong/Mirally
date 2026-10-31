# 活动数据层

更新：2026-10-31，Asia/Shanghai。状态：现行。相关实现：`mirally/core/activity/`、`mirally/runtime/activity/`、`mirally/framework/activity_experience.py`。

活动数据层记录 Mirally 在获准范围内实际观察到的 Windows 前台状态、输入、界面结构、屏幕内容和剪贴板，并把原始观察投影为可查询的活动知识。它回答“观察到了什么”，不把窗口出现、输入计数或 OCR 成功直接解释成用户意图、任务完成或行为结论。

## 默认与启用

活动采集默认关闭。启用前必须在设置中确认采集范围，产品保存范围指纹；范围改变后不能复用旧确认。

- 启用后默认采集前台窗口与标题、浏览器地址栏、输入与剪贴板内容、UIA 结构/正文、多显示器变化帧和 Windows OCR。
- 可在设置中收窄采集级别（`metadata` / `structure` / `content`）、切换前台窗口或全显示器范围，并单独关闭截图、OCR、输入、剪贴板、UIA 正文、标题或 URL。
- 可配置排除应用、排除窗口标题和允许应用列表；Mirally 自身窗口默认排除。密码控件、只读控件和明确排除的子树不采集正文。
- **暂停**只释放当前采集会话，保留启用偏好；**恢复**重新确认同一范围；**停止**关闭采集。
- 采集线程跟随产品 Host 生命周期。关闭主窗口到托盘时 Host 仍在运行，采集按偏好继续；明确退出 Mirally 会停止采集。
- 查询历史、搜索记忆或打开诊断页面不会启动采集器。

配置模板位于 `config/mirally/activity.yaml.template`；产品实际配置在 `<home>/config/activity.yaml` 生成，也可用设置中的显式配置路径覆盖。

## 采集面

| 数据 | 内容 | 触发与限制 |
| --- | --- | --- |
| 前台与窗口 | 进程、窗口实例、标题、可读浏览器地址栏 URL、切换时间 | 前台变化与同窗口标题/URL 更新；URL 只读可见地址栏，不证明页面已加载 |
| 输入 | 键盘、点击、滚动、移动的聚合计数和静默时间 | Raw Input 元数据；不记录逐键字符流，快速切换窗口时可能拒绝归属 |
| 输入文本 | 已提交的控件文本插入/删除/替换、位置和基线状态 | 依赖 UIA Value/Text；支持中文 IME 提交和粘贴，不把功能键翻译成字符 |
| 界面结构 | 控件角色、层级、坐标、Name、AutomationId、ClassName、Value/Text、焦点与密码状态 | 完整遍历声明范围内的树；UIA 超时或 provider 失败明确返回不可用，不把成功前缀当完整树 |
| 屏幕与 OCR | 多显示器或前台窗口的变化帧、图像指纹、黑帧比例、遮罩、OCR 文本与框 | 常驻 WGC 会话按需读回；变化帧才 OCR；遮罩排除产品窗口；黑帧过滤可配置 |
| 剪贴板 | Unicode 文本、序号、owner、复制/剪切/粘贴操作 | 只处理文本；读取失败、序号过期或内容被替换时保留状态，不猜测旧内容 |

音频、视频录制、后台窗口全文和富文本/图片剪贴板不在当前范围。

## 处理链

```text
Windows 采集器
  → ObservationStore（append-only 原始观察 + 资产）
  → 确定性知识投影（activity.v2）
  → ActivityItem / Episode / 事件视图 / 查询预算
  → ContextReader / CLI / 历史浏览器
```

- 原始观察是唯一事实源，派生结果可删除并从原始记录重建。
- 投影按 `ingest_seq` 增量处理；失败保留有界重试队列，不阻塞持续采集。
- 原始文字、UIA 结构和 OCR 正文在获准范围内完整保存，不按字符数、节点数或 JSON 大小裁剪。摘录上限只作用于派生摘要和查询结果，并标注 `excerpt_truncated`、原始长度和哈希。
- `activity.event.v1` 把同一窗口片段中的动作、窗口状态和文字证据组织为可追溯事件；动作结果保持 `unconfirmed`，只有明确标记为用户动作的输入才计入用户动作证据。
- 显示器帧与前台窗口 UIA 是顺序采集，不伪造同步原子快照；后台画面不归给当时的前台应用。

## 存储与清理

| 数据 | 位置 |
| --- | --- |
| 原始观察 | `<home>/data/activity/observations.sqlite3` |
| 截图资产 | `<home>/data/activity/assets/` |
| 知识投影 | `<home>/data/activity/knowledge.sqlite3`（可重建） |

图片和观察年龄参数存在，但当前采集器保护原始资料，不按年龄或配额直接淘汰。清理需要稳定窗口的模型总结和匹配的持久回执；本地规则摘要、失败或预算等待不授权删除。模型整理默认关闭，因此原始资料可能继续占用配额，需要用户明确处理旧积压或调大配额。

磁盘不足、图片配额超限或采集源不可用时，采集器进入明确的暂停/降级状态并保留原因，不删除旧图片来腾出空间。

## 查询与浏览

- 产品 Context 按需只读组合 Activity 与 Memory，返回带来源、时间、预算和缺口的有限上下文；查询不启动采集、不执行维护。
- 本地 CLI 可检查状态、投影、事件与上下文，例如：

```powershell
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli status
.\.venv\Scripts\python.exe -m mirally.runtime.activity.cli knowledge-events --limit 100
```

- 「设置 → 活动与记忆 → 查看活动历史」或窗口菜单可打开只读开发者浏览器，按原始观察、Items、Episodes、Evidence、Events、Agent Context、诊断分层查看。页面服务只绑定回环地址并使用一次性 token，不启动采集器。
- 知识库支持标题、URL、UIA、OCR 和输入文本的 FTS5 检索；Agent 证据接口受响应预算约束，完整正文仍可按 Evidence 引用读取。

## 已知限制

- 这是变化帧与证据时间线，不是视频录制；不保证每次显示变化、每个按键、瞬间清空的输入框或每次滚动中间状态都完整保留。
- 缺少 UIA Value/Text 的应用不能保证输入原文；OCR 只能补屏幕可见文字，不能还原已消失的输入。
- 多显示器、锁屏/UAC、混合 DPI、RDP、应用 provider 崩溃和长时间压力仍需真实环境验证；短时 fixture 不能当作全应用覆盖。
- 浏览器 URL 是地址栏观察，不证明导航完成、页面内容或用户已阅读。
- 无边框截图请求是尽力而为；Windows 可能仍显示捕获边框。黑帧阈值是工程过滤，不等于全黑画面一定无信息。
- 当前没有音频历史、后台应用全文、视频导出或跨设备聚合。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_activity*.py" -v
.\.venv\Scripts\python.exe tools\verify_activity_content.py
.\.venv\Scripts\python.exe tools\verify_activity_monitor.py
.\.venv\Scripts\python.exe tools\verify_activity_browser.py
```

真实桌面内容、不同显示器和长期运行仍需单独验收。相关：[记忆](memory.md)、[系统概览](overview.md)。
