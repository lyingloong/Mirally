# Mirally 前端内容与导航

2026-10-24，Asia/Shanghai。依据用户在 PN-0022 的确认，沿用原 Mirally 视觉、布局习惯、主题和组件，导航与页面内容围绕自有产品调整。

## 页面归属

| 入口 | 内容与真实数据来源 |
| --- | --- |
| 对话 / 工作 | 保留现有输入、流式 Markdown、材料、文件与成果、十个工作起点和四种输出；同一 DSH Session |
| 消息 `/inbox` | 产品收件箱中的提醒与主动联系；明确标记已读，可接回原会话，不复制聊天正文 |
| 提醒 `/reminders` | 创建、查看、取消本机提醒，显示本地时间与实际状态；独立于主动联系开关 |
| 活动 `/activity` | 复用完整的 Mirally 活动管理面板、采集范围确认、暂停/恢复及历史浏览；查看不触发采集 |
| 记忆 `/memory` | 复用来源/候选/类型/日期筛选与正文分页，增加明确记忆创建与纠正 |
| 扩展 `/extensions` | Skill 目录、MCP stdio/HTTP、可信原生插件配置与启停，展示真实 Skill 和 MCP 工具/资源/模板/提示目录 |
| 设置 | 通用、个性化、模型连接、原外观、主动联系、关于与运行状态 |

原 OpenClaw 渠道、Gateway、节点、账号和管理入口不再列入默认导航、工作区菜单和命令面板。已有书签按相关产品页兼容跳转；页面复用不再意味着默认提供旧宿主的后台管理能力。内部保留一部分原路由 ID，以兼容已有壳和会话组件，例如 `custodian` 现对应消息、`cron` 对应提醒；产品 URL 与可见名称使用 Mirally 语义。

## 实现与控制

`frontend/control-ui/ui/src/pages/mirally/` 维护自有内容：共享产品 API、设置、消息/提醒、记忆入口和扩展页面。使用原 `settings-ui`、`settings-workspace`、主题 token 与菜单组件。`app-routes.ts` 保留原对话、新建对话和外观页面，将其他正常入口装配为自有页面或兼容跳转；旧管理页面的 loader 不参与这些入口。

页面 API 复用当前 `MirallyBrowserClient` 已有的鉴权、同源检查、HTTP/SSE 和重连。没有新连接、第二份会话或另一个模型循环。表单以 owner 回执为准，错误明确展示；加载失败不展示成“没有数据”。模型连接只表示配置已保存，不宣称验证了服务；密钥不回显，留空保留原值。

提醒与手动记忆的未确认表单在当前标签页 `sessionStorage` 保存草稿及请求编号，刷新后重试沿用同一编号，不自动提交。它们是待提交草稿，不是产品事实副本；成功后清空。`memory.correct` 新增可选 `request_id`，相同创建请求只读重放、不同内容冲突；纠正已有记忆仍使用明确记忆 ID。撤回来源不会因重放自动恢复。

记忆保存、偏好变更及扩展调整可能停止正在运行的回复；扩展变更会更新后续会话能力。Activity 启用仍必须经过原范围确认流程。扩展需要明确审阅再应用，本地插件/进程是受信任代码；页面不另造独立工具执行链，也没有插件市场。Skill 说明与脚本执行、MCP 读取与工具行动仍遵循各自边界。

伙伴三形态、窗口裁剪/菜单/托盘、开屏、工作准备和成果继续复用。UFO 不接入默认链。原完整 OpenClaw 维护入口及其私有数据不因本轮页面重构而改变。

## 来源维护与验证

原组件、样式和翻译来源仍在 `upstream-files.json` 与 `locales/upstream.json`。自有维护的原文件及理由显式登记在 `local-changes.json`，其余源文件继续逐字节校验；这是固定来源后的自主维护，不再承诺全部页面保持上游原样。许可证与第三方声明保留。

正式构建继续原子发布并检查浏览器 import。真实隔离宿主验收包括原聊天/材料、工作/伙伴/外观，以及新产品页的偏好、提醒、消息、记忆、Skill 和网络丢响应重放。复现：

```powershell
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
# 对显式 --test-mode --home 隔离宿主，GUI 顺序运行：
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
node tools/verify_framework_product_pages.mjs <隔离home/runtime.json>
```

外部模型质量、跨设备推送、语音、插件市场和长期陪伴效果不因内容页接通而视为完成。新产品页正文当前以中文呈现，原组件和外观继续保留原多语言能力。
