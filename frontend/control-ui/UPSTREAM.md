# Mirally Control UI 的来源与改动

本文件记录 Mirally 前端复用的组件、样式、图标和本地化素材的确切来源。产品页面与接口见[前端架构](../../docs/architecture/frontend.md)。

- 上游：[OpenClaw](https://github.com/openclaw/openclaw)
- 对照发行版本：`2026.9.6`
- 精确提交：`eb377ac59e6c9fd6c7705028034812becf00271b`
- 许可：保留本目录的 `LICENSE` 和 `THIRD_PARTY_NOTICES.md`。
- `upstream-files.json` 记录每个导入文件的 Git blob SHA-1 与大小；导入时先核对固定提交的完整 Git tree。本地缓存只有逐字节哈希吻合时才可复用，不能以相近版本代替。
- 浏览器共享依赖按编译后的实际模块图保留。未引入后端 owner、Gateway server、Agent loop、工作区配置或用户数据。

原组件、样式与固定来源保留；经用户确认，导航与页面内容现按 Mirally 自有产品维护。有产品行为改动的源文件及原因列在 `local-changes.json`。仅改动 Mirally 标识的源文件在清单内同时记录原始 Git blob 和本地 `localGitBlob`，继续逐字节核验。迁移的文件通过 `upstreamPath` 保留原始来源路径。重新导入时保留本地维护文件，以 Mirally Git 为恢复来源。早期接缝包括：

1. `ui/src/app/bootstrap.ts` 向原 `createApplicationGateway` 工厂注入 `createMirallyBrowserClient`，保留原 Context、路由、会话组件和界面状态。客户端通过 Mirally 的本机 HTTP/SSE 接口访问产品状态和会话。
2. `ui/src/main.ts` 禁用上游 Gateway 的 service worker 安装。Mirally 的回环来源与构建由自身宿主管理，避免旧缓存和 Gateway 构建身份参与生命周期。
3. 原发送队列的五个类型、编解码与投递模块增加 `mirallyAllowFileWrite` 字段：工作页明确选择保存成果时，授权随同一原生 outbox 条目与发送身份保留，直到重试/重放完成。它不从模型输出或提示文本推断，也不授权其他会话/输入；不改变原页面布局和排队行为。

当前内容层位于 `ui/src/pages/mirally/`，复用原 settings/workspace 组件与样式。路由装配、导航、菜单、命令面板与搜索改为 Mirally 实际能力，保留原对话、新建对话和外观控件；默认不加载旧后台管理页面。上游版本是来源标识，不作为 Mirally 产品版本显示。

新对话和已有对话的权限模式控件仅在宿主明确宣告 `sessions.patch` 能力时显示；Mirally 默认未实现该模式，客户端也会在创建、材料上传或发送前拒绝恢复草稿中显式保存的权限模式，不将“只读”等选择静默降级为普通会话。工作成果仍使用原有按输入持久化的明确文件写入授权。

默认产品身份现覆盖 JavaScript 启动前的 HTML/失败恢复、安装清单及 SVG/PNG/ICO，运行时 favicon、未配置模型页和重连页也使用 Mirally。聊天与空状态使用统一的 `mirally-mascot` 向量 M 组件；页面来访伙伴复用既有海獭资源，原生三形态与动画不变。翻译在打包前统一产品名称，特定产品文案由翻译覆盖层维护；用户正文及显式链接按原内容显示。工具卡片的共享显示数据迁到 `ui/src/lib/chat/tool-display.json`，原始及本地版本由来源清单校验。

`ui/src/app/mirally-client.ts`、`ui/src/app/mirally-read-models.ts` 和 `ui/src/lib/mirally-browser-path.ts` 是 Mirally 自有代码。后者只提供上游材料展示所需的文件名操作，构建器拒绝其余 Node 内建模块进入浏览器。原 Mirally 品牌、工作、材料与伙伴资源仍由产品层提供，并没有另画一套近似页面。

20 种翻译以精确发行包导出的静态 catalog 保存在 `locales/`；`locales/upstream.json` 记录相同提交、源 chunk、原始与本地导出内容 SHA-256。这样保留原中文与其他语言，无须带入约 84 MB 的上游翻译生成缓存。维护工具只导入纯数据模块，不启动 Gateway 或读取用户配置。

构建按本目录的 npm 锁文件安装依赖。第三方链接保留准确的原始地址。路由和终端组件使用固定版本的外部库，工作区通过 npm 别名引用。
