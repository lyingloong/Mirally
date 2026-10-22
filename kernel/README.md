# Mirally 内核源码

`dsh/` 是本仓库直接维护、允许侵入式修改的 Agent 内核。初始代码来自 DeepSeek Harness `dsh-v0.2.1-rc.2`；来源、初始导入处理和本地改动见 [UPSTREAM.md](dsh/UPSTREAM.md)，精确提交与原始链接映射见 [dsh-upstream.json](dsh-upstream.json)。

本目录通过普通 Git 文件跟随 Mirally 提交；开发者 clone Mirally 即取得内核源码，无需额外 clone、初始化 DSH submodule 或安装另一份官方 DSH。包名暂保留上游命名，避免在首次导入时破坏 workspace 和依赖解析。

## 维护范围

可直接修改 `packages/`、`apps/`、模型/工具运行、会话和 UI 实现。Mirally 的产品领域职责、数据归属、扩展契约仍需清晰；可以复用 Profile/Bundle 和现有插件机制，也可以为产品直接调整内部接口。

后续上游变更按需要显式比较和移植，不自动更新或要求完整合并 master。本仓库的初始导入提交保留原始对照；维护上游来源和显著本地改动记录，保留 LICENSE、THIRD_PARTY_NOTICES 和第三方 vendor 声明。`UFO/` 的只读子模块规则保持有效。

## 构建准备

独立 workspace 位于 `kernel/dsh/`，保留原锁文件与配套 Cordis、native、patches、脚本和测试。Node 要求 `^22.19.0 || >=24.0.0`，pnpm 固定 `11.28.5`，按自己的安装方式准备对应工具链；不将开发机路径写入脚本或配置。

从仓库根目录 PowerShell：

```powershell
Push-Location kernel/dsh
try {
    pnpm --version  # 应为 11.28.5
    pnpm install --frozen-lockfile
    pnpm run build
} finally { Pop-Location }
```

安装会联网下载依赖并运行 workspace 已审查的依赖构建脚本；生成的 `node_modules/`、`lib/`、Web 构建、缓存和二进制不提交，换机器后重新安装和构建。首次导入仅验证源码完整性与本地集成改动，尚未执行完整依赖安装或构建；构建成功后仍需验证实际运行。

原上游 `postinstall` 的 hooks 安装行为已加以限制：只有内核本身是 Git 根时才可安装，嵌入 Mirally 时保持外层 hooks 配置。上游部分 Git gate 假定自己处于仓库根，后续应在需要时改为识别内核目录，不能将它们未适配视为已通过。

## 当前实施状态

源码内核已纳入开发结构。Mirally 启动脚本、产品私有数据和可运行 UI 仍使用已保存的 OpenClaw 基线；新内核接入、领域模块迁移、完整构建与端到端运行属于后续[重构阶段](../docs/framework-refactor-plan.md)。
