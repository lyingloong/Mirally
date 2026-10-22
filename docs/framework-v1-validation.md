# 第一版验收记录

日期：2026-10-23（Asia/Shanghai）。分支：`codex/mirally-framework-refactor`。本记录对应[第一版范围](framework-v1.md)，长期产品方案继续演进。

## 已通过

| 验证 | 结果 | 说明 |
| --- | --- | --- |
| 根 Python 全套 | 930 项，928 通过、2 跳过 | 包含实际 UFO 隔离导入/初始化、产品 SQLite、来源与派生链撤回、命令、后台生命周期、WAL 迁移和回退；两项需要明确真实桌面授权的测试保持跳过 |
| 根与产品 Node | 115 项通过 | 包含旧维护入口回归、真实脚本进程控制、桌面协议、并发与幂等回执 |
| 内核 MCP 协议 | 7 项通过 | 真实 Vitest，覆盖协议接缝与产品授权回调 |
| 完整内核构建 | 9 项通过 | Host/Client compiler、bundles、native addon、Desktop、Web 与 aggregate 检查；其后源码 MJS 改动经 Node 语法、单测及实际端到端验证 |
| 精确依赖与锁文件 | 通过 | Node 24.19.0、pnpm 11.28.5；368 workspace，最终 offline frozen install 成功 |
| 原生产品端到端 | 14 组通过 | 实际构建内核、PiAI provider、Agent loop、真实 MCP stdio 和子进程，合成服务仅提供模型/MCP 输入 |
| Edge Headless 自有 UI | 通过 | 流式对话、cookie 刷新、会话切换、记忆纠正/检索、安全文本、提醒与已读、独立桌面确认、窄屏布局 |
| 实际 WebView2 生命周期 | 通过 | 隔离 test home，前端注册、关闭隐藏到托盘、恢复、明确退出与宿主清理；无桌面任务动作 |

端到端包含：模型私有配置与默认关闭状态、回环鉴权/跨域拒绝、浏览器认证交接、单一原生历史和命令去重、真实偏好与提醒工具、停止与冷重启、原生 Skill 正文、实际脚本文件产物与取消、MCP 工具/资源/资源模板/提示模板、确认后的 MCP 调用、原生插件加载与撤下、辅助模型文字/有效 PNG 图像输入、主动候选入箱，以及 sidecar 故障后仍可鉴权退出并释放宿主锁。

源码检查包含 `git diff --check`、Python compileall 与产品 JS/MJS 的 Node 语法检查。未修改 UFO 内核、gitlink 或 `.gitmodules`。未覆盖现有私有配置，未把凭据、测试 home、日志或截图提交。

## 复现

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_*.mjs integrations/openclaw/*.test.mjs kernel/dsh/packages/mirally/mirally-product/tests/*.test.mjs kernel/dsh/packages/mirally/mirally-extensions/tests/*.test.mjs
.\.venv\Scripts\python.exe tools/verify_framework.py
Push-Location kernel/dsh
try {
    $env:LEFTHOOK = "0"
    node node_modules/pnpm/bin/pnpm.cjs exec vitest run packages/mcp/mcp-client/tests/protocol.spec.ts
} finally { Pop-Location }
```

浏览器脚本要求独立的 `--test-mode --home` 宿主，把其 `runtime.json` 路径交给 `tools/verify_framework_browser.mjs`；不要把私有 token 放在 shell 参数或提交中。准备工具与构建命令见[第一版说明](framework-v1.md)。

## 保留的实际边界

本轮没有调用真实外部模型供应商，也没有运行真实 UFO 桌面任务。回环服务证明的是实际适配与执行管线，不证明具体模型的质量、供应商计费、桌面成功率或长期陪伴品质。停止模型连接不能收回已发送数据或已经发生的计费。

没有全新机器安装演练、签名安装包、跨设备推送、系统通知或完整语音体验。采集器本体继续使用已有实现与回归测试，本轮新增监督接入以真实配置解析和受控生命周期 fixture 验证，未开启开发者桌面活动采集。旧会话仅索引，不做双向 transcript 转换。

首次全量 Python 运行曾在旧 UFO `pythonw.exe` 导入的 20 秒窗口超时；独立复测通过，后续两轮完整回归分别以 915 项、最终 930 项通过。未放宽该时间断言或修改 UFO 源码来掩盖超时。
