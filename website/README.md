# Mirally 产品网站

Mirally 的对外展示站点源码，独立于产品运行时：不需要模型配置、API Key、产品数据库或私有凭据。

网站最初来自主仓的 `showcase/`，2026-11 拆出为独立仓库；现将独仓最新版本 `be5235490f95bb680a387aa7df2b1dfa45f473dd` 连同 Git 历史合回主仓 `website/`，继续独立构建与发布。

- 新站部署地址：<https://lyingloong.github.io/Mirally/>；新站确认后，旧地址 <https://lyingloong.github.io/Mirally-site/> 再切为保留路径、query 和 hash 的跳转。
- 素材来源与边界：[CREDITS.md](public/CREDITS.md)
- 迁移前历史验收：[VERIFICATION.md](VERIFICATION.md)，不代表本次合仓及新地址已验收。

## 本地运行

需要 Node.js 20.19+ 或 22.12+，以及 npm。以下命令均在 `website/` 目录执行。

```powershell
npm ci
npm run dev
```

默认只绑定 `127.0.0.1`，按终端提示打开本机地址；端口可指定，例如 `npm run dev -- --port 4173`。

## 构建

```powershell
npm run build
npm run preview
```

`dist/` 是完整静态站，可部署到任意静态托管；模块脚本需要 HTTP 服务，不要直接双击 `dist/index.html`。构建产物、图片与字体都在本地，不依赖外部 CDN。

主仓 [website.yml](../.github/workflows/website.yml) 对网站相关 PR 执行独立构建；`main` push 确认网站变更后发布到 GitHub Pages，也可从 `main` 手动发布。产品 CI 与网站流程分开。发布验收见[验证文档](../docs/reference/validation.md#网站独立验证)。

## 单文件离线版

需要发给别人或在另一台电脑直接打开时：

```powershell
npm run export:html
```

输出 `exports/Mirally.html`：图片、字体、样式和脚本全部内嵌，复制这一个文件用现代浏览器打开即可。`exports/` 是可再生成的产物，不纳入 Git。

## 页面内容

本轮预览以白色、中性灰和少量品牌蓝组织产品页：两栏首屏左侧为中文价值主张与入口，右侧展示开发版真实截图节选，点击打开完整原图；演示章节使用全页唯一的浅灰舞台。后续以两个无编号的平面能力栏介绍材料与行动、记忆与提醒，再以独立白底章节介绍海獭伙伴，以及主窗口、侧栏和桌宠共享对话、草稿与材料的体验；扩展行与源码安装 FAQ 保留。导航包含产品、演示、更新；首屏主按钮“体验演示”跳转到 `#a-day`，次链接“查看源码”指向 GitHub，页尾安装指南与 FAQ 提供源码运行准备入口。页面尚未提供中英文切换。本轮参考 Claude Code 产品页的信息层次与成熟质感，不照搬布局、品牌或能力承诺；方案仍待用户预览反馈，尚未合并或发布。

首页主图 `public/images/product-desktop.webp` 取自隔离运行的 Mirally 开发版界面，使用本地模型 fixture 与预设会话资料；它是产品界面截图，不是真实用户会话，也不代表真实外部模型或服务的验收结果。

沿用上一版自然滚动、真实截图与一处演示的结构，补充产品价值、伙伴形态、扩展入口和入门信息；不恢复六章宣讲、愿景大图或商业路径。源码安装 FAQ 说明 Windows、Python 3.10+、Node.js 24+、WebView2 与自配模型的要求，明确暂未提供独立安装包，不设置安装包下载入口。原生成资产和提示词保留作来源历史。

内容边界：

- 两个能力栏介绍材料与行动、记忆与提醒，伙伴形态独立成章；模型连接、Skill、MCP 与可信插件通过扩展行及文档链接说明。能力范围以 Mirally 的[能力矩阵](../docs/reference/capabilities.md)为准，生活演示中的角色识别属于概念示意。
- 交互演示的资料和回答均为预设，人物、剧集与活动均为虚构，不连接产品宿主、模型服务或个人数据。会议摘要由浏览器本地生成并标注演示数据。
- 页面不接入统计、追踪、登录或支付。
- 修改文案或素材后重新构建，并检查窄窗口与键盘操作。

## 更新日志

`updates/` 是更新日志：`/updates/` 按日期列出各期，每期是独立页面，例如 `/updates/2026-11-01/`。正文原文是 Markdown，由前端渲染。

- 每期一个文件：`content/updates/<日期>.md`，开头用 frontmatter 写日期和标题：

```md
---
date: 2026-11-01
title: 微信、日历与桌面执行
---

## 新功能

**微信渠道**：支持扫码绑定、文字私聊……

![微信对话与文件回传](../../public/images/updates/wechat.webp)
```

- 小节用 `##`，每个点一段，用 `**标题**：` 开头；细小的改动合并成一条，例如「视觉效果」「其他修复」，不必逐项列出。
- 图片路径相对于 Markdown 文件，文件放在 `public/images/` 下；渲染时会按页面位置解析。
- 合成示意图不是实机截图时，在 Markdown 的 alt 里以「合成示意图：」开头，页面会在图注前加「合成示意」标记；实机截图直接写内容。
- 不写营销式导语、数量统计和结尾推广；每期页面由 `scripts/sync-update-pages.mjs` 生成（`npm run dev` / `npm run build` 前自动执行），生成的 `updates/<slug>/index.html` 不纳入 Git。
- 发布前检查桌面与窄窗口的排版；打印样式会隐藏导航、返回链接和页脚。

## 修改入口

| 文件 | 内容 |
| --- | --- |
| `src/App.jsx` | 两栏首屏与完整截图弹窗、两个能力栏、独立伙伴章节、扩展行、源码安装 FAQ 与导航 |
| `src/DayExperience.jsx`、`src/day-experience.css` | 工作/记忆/生活三个标签页的预设交互与摘要下载 |
| `public/images/product-desktop.webp` | 开发版真实界面截图，使用隔离演示会话与预设数据 |
| `public/images/product-conversation.webp` | 首页节选，裁自 `product-desktop.webp` 的 `(260, 0, 1280, 720)` 区域，1020 × 720；弹窗仍展示完整原图 |
| `public/images/mirally-pet-otter.svg` | 来自主仓 `mirally/assets/` 的海獭伙伴，隐藏非默认状态层以显示静止默认形态 |
| `content/updates/*.md` | 每期更新日志原文 |
| `src/updates/content.js` | Markdown 解析与图示渲染 |
| `src/updates/UpdatesIndex.jsx` | 更新列表页 |
| `src/updates/ReleasePage.jsx` | 单期更新页 |
| `src/updates/chrome.jsx` | 更新页共用的页头页脚 |
| `scripts/sync-update-pages.mjs` | 读取 Markdown 并生成每期静态页面 |
| `src/updates/updates.css` | 更新页样式 |
| `src/style.css` | 首页布局、文字层级、弹窗与响应式样式 |
| `public/images/`、`public/CREDITS.md` | 当前展示素材、截图来源与历史生成资产说明 |
| `image-prompts/`、`IMAGE-BRIEFS.md` | 历史生图提示词与生成记录 |

依赖版本由 `package-lock.json` 锁定。正常展示不发生付费 API 请求。
