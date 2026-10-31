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
    $env:LEFTHOOK = "0"
    pnpm --version  # 应为 11.28.5
    pnpm install --frozen-lockfile
    pnpm run build
} finally { Pop-Location }
```

安装会联网下载依赖并运行 workspace 已审查的依赖构建脚本；生成的 `node_modules/`、`lib/`、Web 构建、缓存和二进制不提交，换机器后重新安装和构建。第一版已完成精确版本依赖安装和 9 项完整构建检查，原生会话与产品链路另经回环端到端验证。

原上游 `postinstall` 的 hooks 安装行为已加以限制：只有内核本身是 Git 根时才可安装，嵌入 Mirally 时保持外层 hooks 配置。上游部分 Git gate 假定自己处于仓库根，后续应在需要时改为识别内核目录，不能将它们未适配视为已通过。

## 当前实施状态

默认入口已切到 `packages/mirally/` 的产品组合与自有 UI；领域状态由 Python owner 管理，内核保留单一 Session/Agent loop。安装、接入与验收边界见 [系统概览](../docs/architecture/overview.md) 和 [DSH 运行时](../docs/architecture/native-dsh.md)，旧 OpenClaw 仅保留显式维护入口。
