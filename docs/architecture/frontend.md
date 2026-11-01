# 前端页面与导航

更新：2026-10-31，Asia/Shanghai。状态：现行。相关实现：`frontend/control-ui/`、`mirally/framework_ui/`。

前端以固定 OpenClaw Control UI `2026.9.6` 源码为组件和视觉基础，沿用原 Mirally 的布局、主题、动画与伙伴体验，导航和页面内容按 Mirally 自有产品组织。源码、许可证和固定提交见[前端来源](../../frontend/control-ui/UPSTREAM.md)。

## 页面

| 入口 | 内容与数据来源 |
| --- | --- |
| 对话 / 快捷开始 | 流式 Markdown、材料、工具详情、历史和成果；请求进入同一 DSH Session。原准备页保留为快捷开始，不建立独立工作 |
| 消息 `/inbox` | 产品收件箱中的提醒与主动联系；明确标记已读，可回到原会话 |
| 提醒 `/reminders` | 创建、查看、取消本机提醒，显示本地时间与实际状态 |
| 日历 | 日程与关联提醒的列表、编辑、取消和回到关联会话 |
| 活动 `/activity` | 活动采集状态、范围确认、暂停/恢复、历史浏览入口 |
| 记忆 `/memory` | 来源/类型/状态/日期筛选、正文分页、明确创建与纠正 |
| 扩展 `/plugins` | Skill、MCP、可信插件的配置与启停，展示真实工具/资源/模板/提示目录 |
| 设置 | 通用、我的偏好、模型连接、主动联系、关于；消息渠道是独立页面 |
| 手机 `/mobile` | 独立受限页面，默认显示会话；见[消息渠道](channels.md) |

内部保留少量原路由 ID 以兼容已有壳和组件，例如 `custodian` 对应消息、`cron` 对应提醒、`settings` 对应通用。产品 URL 和可见名称使用 Mirally 语义。原 OpenClaw 渠道、Gateway、节点和账号管理入口不在默认导航中；未映射的旧操作明确返回不支持，不用空列表或虚假成功冒充已实现。

## 实现接缝

- `frontend/control-ui/ui/src/` 复用原 Lit 壳、对话和外观组件；`pages/mirally/` 维护自有内容页和产品 API。
- `ui/src/app/mirally-client.ts` 向原客户端工厂提供 Mirally HTTP/SSE 传输，把已实现操作映射到本机鉴权接口；不建立第二条会话或模型循环。
- `mirally/framework_ui/native-bridge.js` 将活动、记忆、成果等产品消息接入当前 owner；`mirally-product/src/presentation.mjs` 同源提供品牌与伙伴资源。
- 原 Context 与组件继续管理页面草稿和状态；DSH Session 保留唯一聊天事实。刷新、切换会话或重试时保留应保留的草稿、材料和焦点。
- 提醒与手动记忆的未确认表单在 `sessionStorage` 保存草稿和请求编号；刷新后重试沿用同一编号，不自动提交。成功后清空，不把它当作产品事实副本。
- 模型连接、偏好、提醒、记忆、扩展和渠道页面以产品 owner 回执为准；加载失败不展示成“没有数据”，密钥不回显。

## 来源维护

- 原组件、样式和翻译来源记录在 `upstream-files.json` 与 `locales/upstream.json`；自有维护的原文件和理由登记在 `local-changes.json`，其余源文件继续逐字节校验。
- 这是固定来源后的自主维护，不再承诺全部页面保持上游原样；许可证与第三方声明保留。
- 构建先在临时目录完成，检查浏览器模块不含未打包的裸 import，再原子发布到 `dist/`。失败保留上次成功输出并明确报错，缺少成功构建时宿主提示准备步骤。

只重建前端：

```powershell
npm --prefix frontend/control-ui ci --ignore-scripts --no-audit --no-fund
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
```

## 品牌与首次状态

- 首次未配置模型页、空状态、重连/失败状态、静态与运行时图标、安装清单均使用 Mirally 品牌；页面访客复用原水獭伙伴资源。
- 原兼容组件名、偏好键和协议标识不做机械重命名；未修改的上游翻译目录继续校验，产品文案按键覆盖后再插值。
- 输入框不提供未接入的“只读/工作区/全权”会话权限模式；旧草稿或恢复请求显式携带该模式时，在创建会话前拒绝，不悄悄降级。

## 验证

```powershell
npm --prefix frontend/control-ui run verify:source
npm --prefix frontend/control-ui run build
# 对显式 --test-mode --home 的隔离宿主，GUI 顺序运行：
node tools/verify_framework_browser.mjs <隔离home/runtime.json>
node tools/verify_framework_experience.mjs <隔离home/runtime.json>
node tools/verify_framework_product_pages.mjs <隔离home/runtime.json>
node tools/verify_framework_mobile_browser.mjs <隔离home/runtime.json>
```

真实页面、窄窗口、放大文字、明暗主题和错误状态需实际检查；构建或 DOM 断言不能代替体验验收。
