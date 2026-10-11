# Activity 开发者浏览器

Activity 开发者浏览器用于回放本地历史数据，不启动采集器，也不操作桌面。它把同一份数据按层展示：

- Raw Observation：原始窗口、输入、UIA、截图和 OCR 记录；默认最新优先，可按时间、应用、类型和正文搜索。
- Activity Items：投影后的动作、窗口状态、正文摘要、来源 Observation 和资产引用。
- Episodes：按窗口活动片段聚合的时间范围、应用、摘要、缺口和成员 Item。
- Evidence：处理后的完整 OCR/UIA/结构正文，可回跳原始 Observation；资产在明确显示正文后预览。
- Events / Timeline：事件分组、用户输入信号、窗口状态、后台证据、未知项和来源链。
- Agent Context / Summary：模型实际可按预算读取的上下文、应用统计和摘要片段。
- Memory Candidates / Facts：待审核记忆候选和已接受事实。
- Diagnostics：原始库、派生索引、采集覆盖和投影积压状态。

## 启动历史浏览

使用已有 Activity 配置并允许启动时追赶派生索引：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli browse --config path\to\activity.yaml
```

也可以直接指定历史库：

```powershell
.\.venv\Scripts\python.exe -m openassist.runtime.activity.cli browse `
  --database .artifacts\activity-monitor\observations.sqlite3 `
  --knowledge-database .artifacts\activity-monitor\activity-knowledge.sqlite3 `
  --assets .artifacts\activity-monitor\assets --no-open
```

命令会打印 loopback URL 和一次性 token；`--port 0` 使用空闲端口，`--duration` 到时自动退出。`browse --test` 使用隔离的 `.artifacts/activity-monitor` 测试目录。不要把带 token 的 URL 写入提交或共享日志。

默认启动会读取原始库并把未投影记录追赶到可重建的知识库。原始 Observation SQLite 和资产文件始终以只读方式打开；这不会启动传感器或写入采集状态。需要完全不写派生库时，使用已经存在且版本匹配的知识库并添加 `--no-sync`，此时知识库也以 SQLite `mode=ro` 打开；缺库或旧 schema 会直接提示先运行 `knowledge-process`。

所有页面请求都受 loopback token 保护。浏览器中的内容开关只影响响应和资产下载，不改变数据库。历史数据页面明确保留完整 Evidence 正文；Summary、Timeline、Events 和 Agent Context 仍遵守各自的查询预算，并在返回值中标记 `truncated`、`next_cursor`、`data_status` 或缺口。

HTTP 页面由现有 Activity monitor handler 提供，主要只读接口包括 `/api/browser/observations`、`/api/knowledge/items`、`/api/knowledge/episodes`、`/api/knowledge/evidence`、`/api/knowledge/events`、`/api/knowledge/timeline`、`/api/knowledge/context`、`/api/knowledge/summary`、`/api/knowledge/memory/candidates`、`/api/knowledge/memory/facts` 和 `/api/status`。浏览器模式下 `/api/control/*` 返回 `405`，不会暂停、恢复或停止采集器。
