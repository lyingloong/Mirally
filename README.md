# Mirally 产品网站

Mirally 的对外展示站点源码，独立于产品运行时：不需要模型配置、API Key、产品数据库或私有凭据。

本仓库由 Mirally 主仓库的 `showcase/` 迁移而来（2026-11），此后独立维护；主仓库不再包含展示站源码。

- 线上地址：<https://lyingloong.github.io/Mirally-site/>
- 素材来源与边界：`public/CREDITS.md`
- 历史验收记录：`VERIFICATION.md`

## 本地运行

需要 Node.js 20.19+ 或 22.12+，以及 npm。

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

推送到 `main` 后由 GitHub Actions（`.github/workflows/pages.yml`）构建并发布到 GitHub Pages。

## 单文件离线版

需要发给别人或在另一台电脑直接打开时：

```powershell
npm run export:html
```

输出 `exports/Mirally.html`：图片、字体、样式和脚本全部内嵌，复制这一个文件用现代浏览器打开即可。`exports/` 是可再生成的产物，不纳入 Git。

## 页面内容

当前首页沿用迁移前的六章叙事：认识 Mirally、应用场景、当前能力、下一步能力、我们的愿景、商业路径。场景演示使用预设资料，人物、剧集与活动均为虚构，不连接产品宿主或模型服务。

内容边界：

- 「当前能力」依据 Mirally 主仓库的能力矩阵整理；「下一步能力」与生活场景中的角色识别属于概念示意，不代表已发布功能。
- 演示中的会议摘要由浏览器本地生成；页面不接入统计、追踪、登录或支付。
- 修改文案或素材后重新构建，并检查窄窗口与键盘操作。

## 修改入口

| 文件 | 内容 |
| --- | --- |
| `src/App.jsx` | 主页面、导航、品牌文案与愿景 |
| `src/HeroExperience.jsx` | 首页工作与生活切换 |
| `src/DayExperience.jsx` | 三段场景交互与摘要下载 |
| `src/CapabilityVisual.jsx` | 当前能力的界面示意 |
| `src/NextCapabilities.jsx` | 下一步能力的概念示意 |
| `src/BusinessVisual.jsx` | 商业路径图解 |
| `src/*.css` | 布局、章节适配与响应式样式 |
| `public/images/` | 已生成的 WebP 宣传素材 |
| `image-prompts/`、`IMAGE-BRIEFS.md` | 生图提示词与生成记录 |

依赖版本由 `package-lock.json` 锁定。正常展示不发生付费 API 请求。
