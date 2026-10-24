# Mirally 自有体验迁移对照

本轮按 `feat/mirally-openclaw-desktop` 的 `2eeb778a` 核对之前的页面和产品成果。原布局习惯、视觉与已实现体验继续保留；依据用户在 PN-0022 的确认，导航与页面内容现按 Mirally 自有能力调整，见[前端内容与导航](mirally-frontend.md)。此前简化页面即使接回桌宠和工作入口，也不等同于恢复原界面。

当前默认入口以固定 OpenClaw Control UI `2026.9.6`（`eb377ac59e6c9fd6c7705028034812becf00271b`）的真实源码与原 Mirally 皮肤为基础，自行维护导航和产品内容；源码、许可证、校验清单和独立构建位于 [frontend/control-ui](../frontend/control-ui/README.md)。原客户端工厂改接 Mirally HTTP/SSE 接口，DSH 保持唯一 Session/Agent/模型主链；默认不启动 OpenClaw Gateway。旧后端维护基线与私有配置继续保留，不自动导入。

## 页面与产品成果对照

| 原有成果 | 保留实现与接入位置 | 验收重点及边界 |
| --- | --- | --- |
| 原主界面、侧栏导航、新会话、对话、历史、设置和外观布局 | `frontend/control-ui/ui/src/` 的原页面与 Lit 组件，继续应用原 `control-ui-mirally.css/js` | 逐项检查真实页面、滚动、输入焦点、会话切换和布局；不能用资源存在或入口按钮代替交互验收 |
| 相伴 M、双光带开屏、呼吸粒子、失败提示、首绘门控 | `framework_shell.py` 复用 `startup_html()`，后台启动 Host，首绘后就绪即进入工作台 | Windows WebView2；无需等待额外动画周期 |
| 晴空蓝、深色与跟随系统 | 原外观页面、原主题与 Mirally 皮肤；原 M/宠物 SVG | 保留原外观操作，界面偏好由 Mirally 产品 home 持久化；核对随机端口变化后的恢复 |
| 侧栏、48px 图标、海獭和飞狐伙伴 | 原 `control-ui-companion.js/css`，侧栏底部/原生菜单进入 | 保留呼吸/眨眼/挥手/爱心、工作/等待/完成态及新回复提示 |
| 原生透明裁剪、非矩形点击区、置顶、拖动、悬停及右键 | 共享 `ControlUIShell` 的窗口呈现代码；新 `FrameworkShell` 拥有启动和消息入口 | Windows 真窗口、同 HWND 返回工作台；浏览器仅有页面形态 |
| 十个工作起点、四种交付方式 | 「工作」；复用原 `control-ui-office.js`、`control-ui-work.js/css` | 准备阶段不发送，点击开始才提交到当前原生会话 |
| 文件选择、文件/文字/链接拖入、图片预览、目标窗口截图 | 原 intake/context + 新材料接口 | 截图只由明确手柄操作发起，先预览再发送；浏览器提示添加图片文件 |
| 对话、工作、伙伴共用草稿和材料 | 原 Chat pane/Context 继续管理草稿与材料，`mirally-client.ts` 只映射请求和事件 | 按逻辑会话隔离；核对切换不误发、迟到截图不进入其他会话、发送失败保留材料与重试身份 |
| 当前任务、工具步骤、问题、停止、最近会话 | 复用伙伴呈现；真实 Host 事件；本阶段不接入 UFO 问题回执 | 不用草稿代替已发任务；工具结果和失败来自原生事件 |
| 三段交接、接着上次、成果查看 | 复用原 Office 交接投影；接着上次先生成可编辑草稿 | 只有成功保存的成果回执进入成果列表，不把回答里的路径当文件 |
| 窗口图标菜单、重连、浏览器、托盘和显式退出 | 新 Shell 复用成熟窗口菜单并连接自有 Host | 退出清理自己拥有的 Host；无旧 Gateway 重启操作 |
| 活动捕获范围、开始确认、暂停/恢复/停止 | 原「活动与记忆」设置；`legacy-native-bridge.js` 映射产品消息 | 开始/恢复复核当前指纹；暂停保留启用偏好，先退出旧进程再恢复；范围改变不复用旧确认 |
| 原始历史、诊断与监测 | 同页明确点击打开既有只读 Activity 浏览器 | 不启动第二采集器，不因查看重写索引；无 knowledge 库也可查看 |
| 记忆来源/类型/候选筛选、正文分页、语义预算 | 原记忆页面与活动/记忆设置，经产品 bridge 读取现有 owner | 查看候选不晋升；来源撤回继续生效。后台默认关闭，预算默认 4 次/小时、24000 tokens/日、1500 输出 tokens |

模块关系：原前端的 Context、Chat pane 与页面组件继续管理界面状态；`ui/src/app/mirally-client.ts` 向原客户端工厂提供 Mirally HTTP/SSE 传输，转换已实现操作与事件，不建立新的推理链或聊天历史。`mirally/framework_ui/legacy-native-bridge.js` 适配活动、记忆、成果等原产品消息；`mirally-product/src/presentation.mjs` 同源提供原品牌、companion/work/intake/context 等资源。`FrameworkShell` 继续复用旧 Shell 的窗口算法，不创建 OpenClawHost。DSH Session 保留唯一聊天事实；Activity/Memory 由现有 Python owner 管理。原 Gateway service worker 已停用。

这张表记录源码与接入对应关系。页面恢复还需核对真正渲染后的布局及各条操作链；本轮实际结果见[验收记录](framework-v1-validation.md)，先前简化 UI 的单测和截图不能充当本轮原页面的验收结论。

## 材料与成果

单份材料上限 8 MiB，每次最多 12 份，提取文字每份最多 100000 字符，组合上下文另有上限。原始材料由 DSH 附件服务保存，清单属于明确的逻辑会话；上传本身不调用模型，明确发送后才传递给所配置的服务。

- 支持常用 UTF-8、带 BOM 的 UTF-16、GB18030 文本；DOCX/PPTX 做有限 XML 文字提取。Office 展开数量/体积有界，拒绝 DTD/实体。
- XLSX 仅部分提取文字和值，尚未重建共享字符串和公式关系，不保证表格语义或计算；PDF 使用 `requirements-framework.txt` 中的 pypdf，最多前 100 页文字，不包含扫描 OCR/完整图表理解。
- 图片交给实际支持图片且已启用能力的模型。音视频可保存，但没有实现录音/播放/转写工作流，界面明确显示未解析；旧代码中的 speech 接口不代表这些体验已交付。
- 在原工作页明确选择保存交付后，产品接缝才为该次提交授权原生 `mirally_create_file` 工具写入会话成果目录；普通对话默认不获得文件写入授权。支持 Markdown、文本、CSV、JSON、HTML、CSS、SVG，UTF-8 内容最多 1 MiB。不能借此覆盖任意本机文件；保存授权随该次原生 outbox 输入持久化，重连和重试不丢失，不传给其他输入；运行中补充不能扩大原有文件授权，需等当前工作结束后授权。
- 成果以确定性 ID 去重，返回真实回执与下载链接；预览为文字，不执行 HTML/SVG。独立 home 移动后成果仍可读取。此能力不等同于通用文件编辑器、Word/PPTX 生成或网页抓取；这些仍可由明确配置的 Skill/MCP/可信插件提供。

## 迁移边界与验收

原页面源码的复用与后端能力移植分别验收。OpenClaw 的渠道、Gateway 管理、通用 Files 工作区、服务商账号与宿主插件等专有能力不会因页面保留而自动在 DSH 中实现；未映射操作明确提示尚未支持，不能以空列表或虚假成功冒充已实现。原 Files 呈现当前只接入明确登记的 Mirally 成果，并非任意本机目录浏览器；模型设置当前映射单组明确服务和模型连接，不等同于全部 OpenClaw 账号与路由功能。旧会话按显式迁移只建只读索引，不伪造为 DSH transcript。原私有配置、活动/记忆库的复制与回退继续使用 [框架第一版](framework-v1.md) 的流程。

“相伴精灵”等尚未实现的概念、完整语音陪伴、跨设备联系和长期行为品质仍是后续方向。已存在的海獭/飞狐桌宠与动画属于本轮实际保留项，不能再归为未来设想。

离线验收命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_*.mjs integrations/openclaw/*.test.mjs kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools/verify_framework.py
.\.venv\Scripts\python.exe tools/verify_framework_shell.py

# 核对固定原页面来源与已记录集成接缝：
npm --prefix frontend/control-ui run verify:source
```

浏览器验收使用可配置的 Playwright Core 和本机 Edge；Shell 工具仅操作自己创建的 WebView2 窗口，GUI 验证顺序运行。`verify_framework_browser.mjs` / `verify_framework_experience.mjs` 现核对真实原页面，`verify_framework.py --browser-files` 验证原 Files/成果侧栏和工作任务的失败重试；模型回环检查只证明对应原生链路，不代表外部服务或用户应用效果。源码校验和单测通过也不能替代这些页面交互及实际使用者的体验验收。
