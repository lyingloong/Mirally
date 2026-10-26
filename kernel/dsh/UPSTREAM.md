# Mirally DSH 内核来源

本目录是用户授权直接修改的内核源码，随 Mirally 在同一个 Git 仓库维护。上游项目是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)。

| 项目 | 初始基线 |
| --- | --- |
| Tag | `dsh-v0.2.1-rc.2` |
| Commit | `687f282e8625c6b8d11ff58211a90524be2e4317` |
| 源码 archive | `https://github.com/deepseek-ai/deepseek-harness/archive/refs/tags/dsh-v0.2.1-rc.2.tar.gz` |
| Archive SHA-256 | `dec9f36f9b25028ca8de2a7520e16029ad36e8328078fb33b85b1b9b338d5690` |
| 上游普通文件数 | 16,283；导入前逐字节与固定 archive 核对 |
| 原始链接 | 14；路径、目标及本地物化方式记录于 [dsh-upstream.json](../dsh-upstream.json) |

初始导入保留完整 workspace、锁文件、配套 Cordis vendor、native、patches、测试、快照、文档及原版权声明。上游包名暂保留；不把整体改名混入首次导入。`LICENSE`、`THIRD_PARTY_NOTICES.md` 与各第三方目录的声明继续随源码保留。

## 初始集成处理

- 当前 Windows 环境没有创建符号链接的权限，原 archive 的 14 个相对链接物化为目标文件或目录的普通副本；包括测试预期文件和 Agent 指令别名。目标均在本内核内部，未写入本机绝对路径。修改原文件时应同步对应副本，或在支持链接的维护流程中显式恢复链接语义。会话指令 fixture 中涉及链接的场景仍需实际测试。
- `AGENTS.md` 加入 Mirally 源码所有权与侵入式修改授权，原上游文本完整保存在 `AGENTS.upstream.md`；`CLAUDE.md` 副本同步本地指令。
- `scripts/install-lefthook.mjs` 限定为只在内核本身是 Git 根时安装 hooks，避免作为子目录安装依赖时修改外层 Mirally Git 配置；对应测试 fixture 随同调整。独立安装保护的离线验证位于仓库根 `tests/test_dsh_kernel_hooks.mjs`。
- 上游可执行文件清单记录在元数据中，并在 Git index 恢复原始 executable mode，供 Linux/macOS checkout 使用。上游 `.gitattributes` 保留，文本使用 LF，Windows `.cmd` 按其规则 checkout。

初始导入提交是后续内核改动的本地对照。普通源码修改直接提交到 Mirally；显著的上游移植、构建或格式兼容变化在此补充记录。无需为本项目另建 DSH Git 仓库或维护自动跟随 master 的同步分支。

## 第一版源码变更

- 新增 `packages/mirally/mirally-product` 与 `mirally-extensions` 两个源码直接运行的 MJS workspace，组合已有 Agent/Session/LLM、持久化、Skill 与工具服务；产品 Host 和 UI 由 Mirally 拥有。
- `llm-pi-ai/src/index.ts` 导出已有配置解析与宿主凭据适配，产品使用原生 PiAI provider；没有另写模型主循环。
- MCP client 导出受监督连接，并补齐共享连接上的 prompts、取消/代次检查和工具调用前授权回调；资源、工具与提示模板使用同一传输，相关协议测试同步维护。
- `tsdown.config.ts` 按 `dsh.sourceOnly` 跳过源码 MJS workspace 的 TypeScript `lib/types` 输出假设。`pnpm-lock.yaml` 记录新增 workspace 接缝。
- `pnpm-workspace.yaml` 禁止 lefthook 依赖自身的构建/安装钩子，补足工作区自有 postinstall 保护，防止嵌入安装修改外层 Git hooks；准备脚本同时设置 `LEFTHOOK=0`。
- `mirally-product` 通过原生 attachments 服务接入显式材料和多模态消息，新增有产品授权约束的成果文件工具及本机预览/下载接口。
- 默认前端改为直接保留固定 OpenClaw Control UI `2026.9.6` 的原页面源码与原 Mirally 皮肤，源码位于仓库根 `frontend/control-ui/`，许可证、精确提交及逐文件来源另记于该目录的 [UPSTREAM.md](../../frontend/control-ui/UPSTREAM.md)。原 Lit 页面客户端工厂注入 Mirally HTTP/SSE 适配，产品活动、记忆与成果消息经 `legacy-native-bridge.js` 进入现有 owner；`mirally-product/src/presentation.mjs` 提供原品牌、工作与伙伴资源。原 Gateway、WebSocket owner 与 service worker 不加入默认运行链，DSH 仍是唯一 Agent loop/Session/模型链。
- 保留旧页面不移植 OpenClaw 后端专有能力；未实现的旧操作不广告为可用，并明确报告尚未支持。前端使用独立 npm 锁文件与 Node 24+ 构建，准备入口为仓库根 `tools/setup_framework.py`，不以 ignored 的 OpenClaw checkout 或发行包作为运行依赖。

- 2026-10-24：按用户范围暂不接入 UFO，移除新默认 Host 的执行器、专用任务 RPC/模型别名与输入桥，保留自有窗口/截图和原维护适配。

- 2026-10-24：经用户确认，前端沿用原视觉与组件，导航和内容页改为 Mirally 自有产品；默认呈现资源不再加载旧模型管理覆盖层，模型连接由自有设置页管理。相关源码维护记录位于根 `frontend/control-ui/local-changes.json`。

- 2026-10-25：产品呈现偏好接口增加有枚举校验的 `mirally.window.closeBehavior`（`tray` / `exit`），由原设置页保存、桌面壳读取；不增加会话或模型链。首次空配置与失败页品牌、图标和页面伙伴在根前端源码直接维护，保留上游来源及协议兼容标识。

- 2026-10-26：呈现偏好增加严格布尔值 `mirally.companion.hideFromCapture`，缺省保持浮动伙伴的 Windows 截图/录屏排除行为。保存后沿已有 SSE 通知当前客户端，桌面壳重读产品偏好并应用；普通浏览器设置同样可以更新已打开的伙伴。主窗口与 Mirally 主动拖动截图的临时隐藏流程保持各自语义，不进入会话或模型记录。

- 2026-10-25：模型设置提供显式 DeepSeek 预设、上下文窗口和推理强度，以及真实模型列表发现和文字连接检查。`mirally-product/src/model-probe.mjs` 有界读取已保存服务的 `/models` 元数据，禁止重定向；固定文字检查复用现有 LLM 调用，不建立测试会话，随断连和 Host 停止取消。设置保存继续由 Python 产品 owner 负责，能力声明与实测结果分开呈现。

- 2026-10-25：命名模型连接与 `chat` / `background` 用途路由由 Python 产品 owner 持久化，原 `settings.model` 保留为隐式 default 连接。一个 PiAI 适配器组合各连接和冻结凭据代次，后台记忆与主动联系通过原模型桥选择 background。模型列表与文字检查可明确指定保存的连接；新图片在所选连接未声明支持时由产品入口拒绝，避免内核通用文本降级被当作识图成功。

## 验证范围

2026-10-26 迁移收尾：Conversation 元数据由 Python owner 持久化，Host 提供带版本校验的 PATCH；历史材料和工具由 `history-presentation.mjs` 从原生所有代次派生，并以真实引用授权附件。`basic-capabilities.mjs` 在原工具管线组合文件授权根、工作区文字写入与可选网页 search/fetch；不新增终端、浏览器或模型主循环。旧档案由产品侧独立只读归档，不伪造原生会话。具体范围及新旧维护切换见根[迁移收尾](../../docs/framework-migration-closure.md)。

2026-10-25 模型与呈现补充：`mirally-product` 明确区分服务默认、关闭与推理档位，将 DeepSeek Chat 方言和 Responses 字段交给现有 PiAI 实现；修复旧 `off: ''` 的非法推理声明，保留协议回放和凭据代次。原生 reasoning 块与工具参数/结果投影给产品 UI，不增设聊天事实来源；辅助模型调用须检查原生终止错误，不能把失败当作空文字成功。实际供应商与离线 wire 验证范围另见根 [DeepSeek 实测](../../docs/deepseek-compatibility.md)。

固定源码来源与许可证保留。此前第一版的 9 项完整内核源码构建检查、MCP 协议单测与模型/会话及产品领域回环联调属于对应版本的记录；此前简化 UI 的离线 Edge 结果不能证明本轮原页面与交互已恢复。原页面接缝与真实交互须按本轮独立验收记录核对。没有验证全部上游 workspace 的所有测试；早期验收未调用真实外部模型或执行真实桌面动作，2026-10-25 的 DeepSeek 实测范围单独列于上方链接。当前范围与命令见 [框架第一版](../../docs/framework-v1.md)和[重构实施计划](../../docs/framework-refactor-plan.md)。
