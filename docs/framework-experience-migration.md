# Mirally 自有体验迁移对照

本轮按 `feat/mirally-openclaw-desktop` 的 `2eeb778a` 核对自有成果。此前新框架的简化页面遗漏了原桌面体验；现已复用既有资源和交互，将数据与动作接到自有 Host。OpenClaw 维护基线与私有配置继续保留，不自动导入。

## 已迁移的产品成果

| 原有成果 | 当前实现与入口 | 验证及边界 |
| --- | --- | --- |
| 相伴 M、双光带开屏、呼吸粒子、失败提示、首绘门控 | `framework_shell.py` 复用 `startup_html()`，后台启动 Host，首绘后就绪即进入工作台 | Windows WebView2；无需等待额外动画周期 |
| 晴空蓝、深色与跟随系统 | 设置中的外观；原 M/宠物 SVG | 偏好保存到独立 home，随机端口变化不丢失 |
| 侧栏、48px 图标、海獭和飞狐伙伴 | 原 `control-ui-companion.js/css`，侧栏底部/原生菜单进入 | 保留呼吸/眨眼/挥手/爱心、工作/等待/完成态及新回复提示 |
| 原生透明裁剪、非矩形点击区、置顶、拖动、悬停及右键 | 共享 `ControlUIShell` 的窗口呈现代码；新 `FrameworkShell` 拥有启动和消息入口 | Windows 真窗口、同 HWND 返回工作台；浏览器仅有页面形态 |
| 十个工作起点、四种交付方式 | 「工作」；复用原 `control-ui-office.js`、`control-ui-work.js/css` | 准备阶段不发送，点击开始才提交到当前原生会话 |
| 文件选择、文件/文字/链接拖入、图片预览、目标窗口截图 | 原 intake/context + 新材料接口 | 截图只由明确手柄操作发起，先预览再发送；浏览器提示添加图片文件 |
| 对话、工作、伙伴共用草稿和材料 | `experience-adapter.js` 投影当前框架状态 | 按逻辑会话隔离；切换不误发，旧会话/内核代次的迟到截图被拒绝 |
| 当前任务、工具步骤、问题、停止、最近会话 | 复用伙伴呈现；真实 Host 事件与 UFO 问题回执 | 不用草稿代替已发任务；工具结果和失败来自原生事件 |
| 三段交接、接着上次、成果查看 | 复用原 Office 交接投影；接着上次先生成可编辑草稿 | 只有成功保存的成果回执进入成果列表，不把回答里的路径当文件 |
| 窗口图标菜单、重连、浏览器、托盘和显式退出 | 新 Shell 复用成熟窗口菜单并连接自有 Host | 退出清理自己拥有的 Host；无旧 Gateway 重启操作 |
| 活动捕获范围、开始确认、暂停/恢复/停止 | 「活动 → 活动范围、采集控制与原始记录」 | 保存范围先停止旧采集；开始/恢复复核当前指纹；暂停保留启用偏好，先退出旧进程再恢复 |
| 原始历史、诊断与监测 | 同页明确点击打开既有只读 Activity 浏览器 | 不启动第二采集器，不因查看重写索引；无 knowledge 库也可查看 |
| 记忆来源/类型/候选筛选、正文分页、语义预算 | 「记忆」高级浏览与「设置」预算面板 | 查看候选不晋升；来源撤回继续生效。后台默认关闭，预算默认 4 次/小时、24000 tokens/日、1500 输出 tokens |

模块关系：`framework_ui/app.js` 拥有状态和唯一传输，`experience-adapter.js` 只投影这些状态；原 companion/work/intake/context 负责呈现与手势。`FrameworkShell` 复用旧 Shell 的窗口算法，但不创建 OpenClawHost，不调用品牌注入脚本。DSH 原生 Session 保留唯一聊天事实；Activity/Memory 继续由原有 Python owner 管理。原自有资源没有复制成另一套分叉。

## 材料与成果

单份材料上限 8 MiB，每次最多 12 份，提取文字每份最多 100000 字符，组合上下文另有上限。原始材料由 DSH 附件服务保存，清单属于明确的逻辑会话；上传本身不调用模型，明确发送后才传递给所配置的服务。

- 支持常用 UTF-8、带 BOM 的 UTF-16、GB18030 文本；DOCX/PPTX 做有限 XML 文字提取。Office 展开数量/体积有界，拒绝 DTD/实体。
- XLSX 仅部分提取文字和值，尚未重建共享字符串和公式关系，不保证表格语义或计算；PDF 使用 `requirements-framework.txt` 中的 pypdf，最多前 100 页文字，不包含扫描 OCR/完整图表理解。
- 图片交给实际支持图片且已启用能力的模型。音视频可保存，但没有实现录音/播放/转写工作流，界面明确显示未解析；旧代码中的 speech 接口不代表这些体验已交付。
- 在工作页选择保存交付，或对话勾选「本轮保存文件」，才允许原生 `mirally_create_file` 工具写入会话成果目录。支持 Markdown、文本、CSV、JSON、HTML、CSS、SVG，UTF-8 内容最多 1 MiB。不能借此覆盖任意本机文件；运行中补充不能扩大原有文件授权，需等当前工作结束后授权。
- 成果以确定性 ID 去重，返回真实回执与下载链接；预览为文字，不执行 HTML/SVG。独立 home 移动后成果仍可读取。此能力不等同于通用文件编辑器、Word/PPTX 生成或网页抓取；这些仍可由明确配置的 Skill/MCP/可信插件提供。

## 迁移边界与验收

OpenClaw 自带的管理页、渠道和 Files 并非 Mirally 自有界面，未搬入新引擎；对应能力以新框架当前实际接口为准。旧会话按显式迁移只建只读索引，不伪造为 DSH transcript。原私有配置、活动/记忆库的复制与回退继续使用 [框架第一版](framework-v1.md) 的流程。

“相伴精灵”等尚未实现的概念、完整语音陪伴、跨设备联系和长期行为品质仍是后续方向。已存在的海獭/飞狐桌宠与动画属于本轮实际保留项，不能再归为未来设想。

离线验收命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_*.mjs integrations/openclaw/*.test.mjs kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools/verify_framework.py
.\.venv\Scripts\python.exe tools/verify_framework_shell.py
# 对显式 --test-mode --home 运行中的隔离 Host：
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
```

浏览器工具使用可配置的 Playwright Core 和本机 Edge；Shell 工具仅操作自己创建的 WebView2 窗口，GUI 验证顺序运行。回环模型验证覆盖真实材料/图片进入主会话、文件授权拒绝、真实成果和重放，未调用外部服务；未使用用户真实桌面截图或执行用户应用任务。机器验证不代替实际使用者的体验验收。
