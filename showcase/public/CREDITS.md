# Mirally — 展示说明与素材来源

本页是一份产品与愿景展示，包含预设的交互概念演示。不会访问用户的桌面、活动数据、模型服务或 API 凭据。

## 品牌

Mirally 相伴 M 标识来自本仓库 `mirally/assets/`，保持现有品牌形态。

## AI 宣传图

日间工作、夜晚生活、海岸愿景与虚构剧照于 2026-10-15 使用 `gpt-image-2` 生成；夜景以日间图为参考，保持人物和空间的连续性。页面里的角色、剧集、情境与资料均为虚构。视觉场景不代表已实现全部产品能力。

图像通过 imagegen 官方 CLI/API 工作流生成，完整提示词与生成记录在源码的 `image-prompts/` 和 `IMAGE-BRIEFS.md`。网页只加载本地保存的图片，不连接生图 API。

## 字体与图标

- DM Sans：Colophon Foundry，SIL Open Font License 1.1。页面自托管字体，许可证位于 `fonts/OFL.txt`。
- Lucide：ISC License，https://lucide.dev/license
- React：MIT License，https://github.com/facebook/react
- 组件依赖许可证随构建资源保留，原始副本见 `LICENSES.txt`。

## 内容边界

“已在发生”依据仓库 README 与 CHANGELOG 描述现有能力；视频人物理解、跨设备陪伴、主动服务属于未来愿景。

演示会议摘要由浏览器本地生成，明确标注演示数据。页面未接入统计、追踪、登录或支付功能。
