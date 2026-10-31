# Mirally 原界面

这里以重构前真实 Control UI 源码为组件和视觉基础，自行维护 Mirally 的导航与内容页。对话、快捷开始、主题、动画和伙伴体验继续复用；页面映射见 [Mirally 前端](../../docs/architecture/frontend.md)，来源与维护文件见 [UPSTREAM.md](UPSTREAM.md) 和 `local-changes.json`。默认产品只使用 Mirally/DSH 后端；此目录不启动 OpenClaw Gateway。

准备整个产品：在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe tools/setup_framework.py
```

单独准备与构建前端（Node 24 或更新版本）：

```powershell
npm --prefix frontend/control-ui ci --ignore-scripts --no-audit --no-fund
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
```

构建在独立临时目录完成，拒绝未打包的裸模块 import，并在通过后原子替换 `dist/`。`.mirally-build.json` 是完整构建的成功标记。失败保留上次成功输出并返回非零状态，不切换到实验页面。`dist/`、node_modules 和临时输出不提交，迁移机器后按上述命令重新生成。

维护上游导入时使用 `scripts/import-upstream.py`，每个文件必须符合固定提交的 Git blob。可通过显式 `--cache` 指定任意本地只读源码缓存；缓存路径不会写入产品配置。`scripts/collect-shared-imports.mjs` 用 TypeScript AST 排除 `import type` 后定位共享运行依赖。`scripts/import-locales.mjs` 只接受显示声明相同版本及提交的发行包，导出其纯本地化数据。产品启动和普通构建均无需这些维护来源。
