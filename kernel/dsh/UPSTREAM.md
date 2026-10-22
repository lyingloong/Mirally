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

## 验证范围

首次导入核验固定 archive、普通文件内容、workspace 本地依赖完整性、可执行文件模式及安装 hooks 的集成保护。尚未安装完整 Node 依赖、编译内核或启动新的 Agent；现有产品仍运行 OpenClaw。后续构建与运行步骤见 [内核维护说明](../README.md)和[重构实施计划](../../docs/framework-refactor-plan.md)。
