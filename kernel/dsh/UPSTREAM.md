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

## 验证范围

固定源码来源与许可证保留。第一版已完成 9 项完整源码构建检查、MCP 协议单测、原生模型/会话和产品领域的回环端到端联调，自有 UI 经离线 Edge 验证。没有验证全部上游 workspace 的所有测试，未调用真实外部模型或执行真实桌面动作。当前范围与命令见 [框架第一版](../../docs/framework-v1.md)和[重构实施计划](../../docs/framework-refactor-plan.md)。
