# Mirally — Everyday Ally 展示站

中文产品与愿景展示站，面向内部演示及产品交流。独立于 Mirally 产品运行时，不需要模型配置、API Key、Gateway 或活动数据库。

## 运行

需要 Node.js 20.19+ 或 22.12+ 的受支持版本及 npm。在本目录执行：

```powershell
npm ci
npm run dev
```

按终端提示打开本机地址。端口可指定，例如 `npm run dev -- --port 4173`。默认仅绑定 `127.0.0.1`。

## 构建与展示

```powershell
npm run build
npm run preview
```

`dist/` 是完整静态站，可部署到静态托管服务，也支持部署到子目录。不要直接双击 `dist/index.html`；浏览器模块需要 HTTP 静态服务。构建文件、图片与字体都在本地，无需外部 CDN。

跨机器或换目录时，只需复制本目录的源码与 `public/`，运行 `npm ci` 重新生成依赖，再构建。不要复制 `node_modules/`。不需要主项目的 Python 虚拟环境或私有配置。

## 页面与互动

- 首屏日间 / 夜晚切换：同一人物与空间的 AI 视觉连续叙事。
- 一天中的三段演示：工作推进、活动记忆、娱乐陪伴。
- 工作演示：可下载浏览器本地生成的 Markdown 会议摘要。
- 记忆演示：可展开预置资料来源。
- 娱乐演示：虚构剧集人物卡及无剧透关系说明。
- 现有能力、产品演进路线、商业路径、品牌收尾。
- 移动端导航、键盘 tabs、对话收起与重开、来源说明对话框。
- 尊重系统“减少动态效果”设置。

所有演示资料均为虚构。页面不访问桌面、活动数据或模型 API，不把概念演示视为已实现能力。

## 修改入口

| 文件 | 内容 |
| --- | --- |
| `src/App.jsx` | 主页面、导航、品牌文案与愿景 |
| `src/style.css` | 主视觉、响应式与动效 |
| `src/DayExperience.jsx` | 三段交互脚本与下载 |
| `src/day-experience.css` | 场景窗口与演示样式 |
| `public/images/` | 已生成的 WebP 宣传素材 |
| `image-prompts/`、`IMAGE-BRIEFS.md` | 生图提示词与实际生成记录 |
| `public/CREDITS.md` | 素材来源与演示边界 |

`package-lock.json` 锁定依赖版本；正常展示不发生付费 API 请求。
