# Mirally Control UI 的来源与改动

此目录保留重构前实际使用的 OpenClaw Control UI 页面、Lit 组件、布局、CSS、主题、图标及本地化内容，作为 Mirally 自行维护的前端源码。它不运行 OpenClaw Gateway、Agent、模型路由或数据库。

- 上游：[OpenClaw](https://github.com/openclaw/openclaw)
- 对照发行版本：`2026.9.6`
- 精确提交：`eb377ac59e6c9fd6c7705028034812becf00271b`
- 许可：保留本目录的 `LICENSE` 和 `THIRD_PARTY_NOTICES.md`。
- `upstream-files.json` 记录每个导入文件的 Git blob SHA-1 与大小；导入时先核对固定提交的完整 Git tree。本地缓存只有逐字节哈希吻合时才可复用，不能以相近版本代替。
- 浏览器共享依赖按编译后的实际模块图保留。未引入后端 owner、Gateway server、Agent loop、工作区配置或用户数据。

原组件、样式与固定来源保留；经用户确认，导航与页面内容现按 Mirally 自有产品维护。所有本地修改的上游文件及原因列在 `local-changes.json`，未列出的文件继续逐字节核验。早期接缝包括：

1. `ui/src/app/bootstrap.ts` 向原 `createApplicationGateway` 工厂注入 `createMirallyBrowserClient`，保留原 Context、路由、会话组件和界面状态。新客户端直接映射 Mirally 的 HTTP/SSE 接口，不建立 OpenClaw WebSocket 或第二条推理链。
2. `ui/src/main.ts` 禁用上游 Gateway 的 service worker 安装。Mirally 的回环来源与构建由自身宿主管理，避免旧缓存和 Gateway 构建身份参与生命周期。
3. 原发送队列的五个类型、编解码与投递模块增加 `mirallyAllowFileWrite` 字段：工作页明确选择保存成果时，授权随同一原生 outbox 条目与发送身份保留，直到重试/重放完成。它不从模型输出或提示文本推断，也不授权其他会话/输入；不改变原页面布局和排队行为。

当前内容层位于 `ui/src/pages/mirally/`，复用原 settings/workspace 组件与样式。路由装配、导航、菜单、命令面板与搜索改为 Mirally 实际能力，保留原对话、新建对话和外观控件；默认不加载旧后台管理页面。上游版本是来源标识，不作为 Mirally 产品版本显示。

新对话和已有对话的权限模式控件仅在宿主明确宣告 `sessions.patch` 能力时显示；Mirally 默认未实现该模式，客户端也会在创建、材料上传或发送前拒绝恢复草稿中显式保存的权限模式，不将“只读”等选择静默降级为普通会话。工作成果仍使用原有按输入持久化的明确文件写入授权。

默认产品身份现覆盖 JavaScript 启动前的 HTML/失败恢复、安装清单及 SVG/PNG/ICO，运行时 favicon、未配置模型页和重连页也使用 Mirally。原 mascot 标签只作调用兼容，由自有向量 M 组件实现；页面来访伙伴复用既有海獭资源，原生三形态与动画不变。翻译通过插值前的产品文案覆盖层维护，原 catalog 保持校验，用户正文及显式链接不作品牌文本替换。全部导入文件的本地修改理由仍在 `local-changes.json`。

`ui/src/app/mirally-client.ts`、`ui/src/app/mirally-read-models.ts` 和 `ui/src/lib/mirally-browser-path.ts` 是 Mirally 自有代码。后者只提供上游材料展示所需的文件名操作，构建器拒绝其余 Node 内建模块进入浏览器。原 Mirally 品牌、工作、材料与伙伴资源仍由产品层提供，并没有另画一套近似页面。

20 种翻译以精确发行包导出的静态 catalog 保存在 `locales/`；`locales/upstream.json` 记录相同提交、源 chunk 和导出内容 SHA-256。这样保留原中文与其他语言，无须带入约 84 MB 的上游翻译生成缓存。维护工具只导入纯数据模块，不启动 Gateway 或读取用户配置。

构建使用本目录独立、固定版本的 npm 依赖与锁文件，不使用仓库中 ignored 的 `openclaw/`、其 node_modules 或已安装发行包作为产品运行时。
