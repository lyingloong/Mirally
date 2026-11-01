# Mirally 测试工具依赖

浏览器验收使用 `playwright-core`，隔离 DOM 测试使用 `linkedom`。它们由本目录的独立锁文件管理，不依赖产品运行包。

在仓库根目录准备：

```powershell
npm --prefix tools/testing ci --ignore-scripts --no-audit --no-fund
node --test tests/test_test_dependencies.cjs
```

浏览器检查使用本机 Microsoft Edge，`playwright-core` 不自动下载浏览器。需要其他安装位置时可设置 `MIRALLY_PLAYWRIGHT` 为模块名、绝对路径、文件 URL 或以 `./` / `../` 开头的仓库相对路径；相对路径始终基于仓库根目录，与调用时的工作目录无关。

Node 工具统一通过 [dependencies.cjs](dependencies.cjs) 加载依赖，避免直接引用其他目录的 `node_modules`。新 worktree 需要分别安装本目录依赖。GUI 验收顺序运行，产品构建和隔离数据要求见[验证说明](../../docs/reference/validation.md)。

当前锁定版本沿用原有验收环境已使用的 `playwright-core 1.63.0` 和 `linkedom 0.18.13`；更换版本时验证相关浏览器与 DOM 行为。
