# 验证与未覆盖边界

更新：2026-11-01，Asia/Shanghai。状态：现行。本文只保留稳定的验证范围、复现命令和未覆盖边界；各阶段具体测试数量、临时 home 和截图记录属于当次环境，可在 Git 历史中追溯。

## 原则

- 隔离测试使用显式 `--home`、回环模型/iLink fixture 和独立端口，不读取或修改用户当前产品数据、微信绑定、凭据和模型服务。
- GUI 验证顺序运行，只操作自己创建的窗口；不同时运行会抢占桌面或原生窗口的检查。
- 回环测试证明协议与产品链路，不代表真实供应商、真实微信账号、所有显示器或长期运行已经通过。
- 模型生成、桌面动作和外部投递分别验收；“收到”“已排队”“接口接受”“用户已读”和“任务完成”不能互相替代。

## 离线验证

### GitHub Actions 检查范围

[PR 工作流](../../.github/workflows/mirally-pr.yml)在 PR、`main` push 和手动运行时保留开发工具测试、源码与文档链接检查。只有改动完全属于[范围选择器](../../tools/check_ci_scope.py)列出的文档和开发工具路径时，才跳过 Windows 产品依赖安装、内核/前端构建及产品测试；产品代码、依赖、CI 配置、选择器本身和未知路径仍运行原有完整离线子集（包含记忆测试）。检查名称保持不变，跳过的产品 job 显示 `skipped`。

PR 比较事件的 base 与 GitHub 合并预览提交，`main` push 比较事件的 before/after；删除和重命名也计入。浅历史缺少比较基线、分类失败、空 diff 或开发工具检查失败时运行完整产品检查。手动 `workflow_dispatch` 始终运行完整检查，可用于复核或排查。代码 Draft PR 与正式 PR 使用相同规则，合并后的产品集成验证继续保留。

pip、npm 和 pnpm 缓存只复用包下载；每次仍安装依赖并重新构建，不复用 `.venv`、`node_modules` 或构建产物。缓存未命中时照常安装。CI 的离线子集不替代受影响领域的本地验证或实际页面验收。

### 本地命令

DOM 与浏览器验收使用独立的[测试工具依赖](../../tools/testing/README.md)，每个新 worktree 在仓库根目录准备一次：

```powershell
npm --prefix tools/testing ci --ignore-scripts --no-audit --no-fund
node --test tests/test_test_dependencies.cjs
```

```powershell
# 产品 Python 测试（不需要 UFO 子模块或额外依赖）
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Node/产品链路测试
node --test tests/test_*.mjs
node --test kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs
node --test kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs

# 构建内核、启动真实 Host、回环模型与协议子进程
.\.venv\Scripts\python.exe tools\verify_framework.py

# 固定前端来源与正式构建
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
```

`tools/verify_framework.py` 不访问外部 API；它验证会话、工具、停止、重启、Skill、脚本产物、MCP 协议、原生插件、微信回环和主动入箱等链路。浏览器验收使用本机 Edge，独立 `playwright-core` 包不下载浏览器；可通过 `MIRALLY_PLAYWRIGHT` 显式选择模块或路径。相对路径基于仓库根目录，与执行时工作目录无关。

完整 Python 套件依赖可写的临时目录；同一 `TEMP` 目录残留旧数据时，个别记忆/观察分页用例可能报与本改动无关的失败。重跑前换用新的可写临时目录（例如 `$env:TEMP` 指向仓库内 gitignored 的空目录）即可复现全绿；CI runner 使用真实 Temp，不需要此覆盖。

验证产物统一写到 `output/tmp/<工具或开发点>/<运行标识>/`（`output/` 已忽略）。仓库根出现未被忽略的 `tmp*` 条目会让 `git status` 长期带噪声，用只读检查确认：

```powershell
.\.venv\Scripts\python.exe tools\check_local_artifacts.py
```

它只报告、不写盘、不删除、不改 `.gitignore`：退出码 `0` 干净，`1` 有未忽略的根级 `tmp*` 条目（逐个列出），`2` 不是仓库根。清理是显式的人工动作——把需要的文件挪进 `output/tmp/`，其余自行删除；仓库不提供自动清理，也不替你判断哪些还能用。

```powershell
# 对显式 --test-mode --home 的隔离宿主，GUI 顺序运行
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
node tools/verify_framework_product_pages.mjs <隔离home/runtime.json>
node tools/verify_framework_mobile_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_weixin_browser.mjs <隔离home/runtime.json>

# 原生窗口、托盘、伙伴与退出
.\.venv\Scripts\python.exe tools\verify_framework_shell.py
.\.venv\Scripts\python.exe tools\verify_framework_shell.py --close-exit
.\.venv\Scripts\python.exe tools\verify_framework_shell.py --page-quit --reuse-host
```

`--test-mode` 必须同时指定独立 `--home`，用于确定性 UI 测试，不是生产失败回退。

## 已验证范围

| 范围 | 主要证据与说明 |
| --- | --- |
| 产品状态与会话 | 命令去重、停止、冷重启、会话元数据、分叉、模型选择、历史材料投影和归档只读 |
| 原生 DSH 继承 | 完整 base 组合、原生文件/命令/沙箱、压缩、子代理、计划、问题、todo/goal/workflow 挂载 |
| 模型链路 | 本地回环协议、Chat/Responses/Anthropic 适配、推理档位、图片附件、工具续轮；DeepSeek 官方连接另做过一次隔离实测 |
| 微信与日历 | 隔离 iLink、真实 DSH、本地模型：助理/直接会话、委托、编号选择、问题回答、日程与提醒投递、定时执行权限 |
| 手机网页 | 一次性配对、受限 RPC、会话与偏好、设备撤销；headless Edge 覆盖桌面/手机窄屏 |
| 活动与记忆 | 采集器、真实 UIA/WGC/OCR fixture、知识投影、事件视图、历史浏览器、记忆召回与纠正 |
| 扩展 | 纯说明 Skill、资源 Skill、脚本确认与指纹、MCP 全部接入面、可信插件启停 |
| 桌面 | WebView2 原生裁剪、命中区域、最小化/托盘、伙伴交互、关闭与明确退出；真实窗口顺序运行 |
| 前端 | 固定来源逐文件校验、正式构建、浏览器模块 import 检查、主要产品页面与窄屏 |

最近一批完整记录集中在 2026-10-29 的微信、日历和定时能力；2026-10-31 的会话中心重构（移除工作 owner、通用后台作业、摘要）以对应提交的隔离测试和实际页面检查为准，本文不重复抄录每次数量。

## 未覆盖

- **安装**：没有已签名安装包，也没有在全新 Windows 机器上完成安装演练；不同目录、空格/中文路径、工作目录变化仍需按机器验证。
- **真实外部模型**：除一次 DeepSeek 官方连接实测外，未对所有供应商、模型、代理和长期成本做验收；模型质量与任务成功率不能由短用例保证。
- **真实微信**：当前用户完成过扫码和一次文字往返；媒体格式、长期主动发送、平台限流、断线恢复和多账号差异仍需实机验证。
- **桌面环境**：多显示器、混合 DPI、RDP、锁屏/UAC、不同捕获驱动和长时间采集未完整覆盖。
- **桌面动作**：Windows 单步点击/输入未通过交互式桌面实机验收，未向模型开放；桌面接管不是当前能力。
- **语音**：真实麦克风、系统权限、微信语音编码、本地 Whisper 模型和不同设备兼容性仍需实测。
- **手机远程**：私人 HTTPS 转发、公网可达性、实体手机长期使用和跨网络恢复未验证。
- **长期运行**：长时间后台模型预算、内存/磁盘增长、崩溃恢复和主动联系质量没有完整长周期数据。
- **未实现能力**：QQ、邮件推送、跨设备同步、外部日历同步、完整语音回复、插件市场等不在当前验收范围。

## 记录方式

新功能或修复的验证说明应写明：改了什么、如何复现、使用什么隔离环境、实际观察到什么、哪些没有验证。不要把规划、mock 测试、页面截图或构建通过写成完整体验已验收；真实账号、桌面和外部服务的副作用需在任务授权内单独记录。
