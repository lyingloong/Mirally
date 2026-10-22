# Mirally · A 字母图标探索

> 历史设计记录。当前正式图标为 [相伴 M](../companion-m/README.md)；下文中的选择与验证描述当时状态。本地 output/ 预览不随 Git 上传。

用户最新方向：图标应可辨认出 Mirally 中的字母，优先 A；参考早期折翼的美感，在同一造型中融入伙伴关系。此前「并行／合契／续环」的抽象方向未获认可，本轮不沿用其造型。

## 核心含义

**A 是 MIRA 与 ally 共用的字母，也是连接实验室身份和伙伴定位的交点。**

视觉层次依次是：先读出 A，再感受两条笔画的互相支撑，最后通过舒展或向前的姿态表达行动。意义来自结构，不叠加人像、握手、爱心等小图案。

## 三种处理

| 方向 | 造型侧重 | 完整提示词 |
| --- | --- | --- |
| 01 · 合翼 A | 早期折翼的舒展感，补足明确的顶点、双脚与横梁 | [01-ally-wing.txt](01-ally-wing.txt) |
| 02 · 并肩 A | 两条相互支撑的笔画，共同构成一个完整的 A | [02-ally-shoulder.txt](02-ally-shoulder.txt) |
| 03 · 共桥 A | 简洁的承托结构，让 A 同时有连接和协作的意味 | [03-ally-span.txt](03-ally-span.txt) |

共同要求：天蓝主色、清晰轮廓、轻微立体层次；大图与缩小预览都必须保留 A 的可读性。

## 生成方式

- imagegen 技能原版 CLI `scripts/image_gen.py edit`。
- 模型：`gpt-image-2.5-sunburst`，参数 `--quality high --size auto --background opaque --prompt-file ... --no-augment`。
- 原图：[早期天蓝折翼](../../../../output/imagegen/icons-sky-blue-2026-10-03/b-foldwing.png)，保持原文件，以新文件保存不同设计。
- 使用当前配置 provider 的中转 API，凭据只传给进程环境，不打印、不修改原配置。
- 使用独立的 `.artifacts/imagegen-venv`；本轮不修改应用运行环境或安装未选定的图标。

## 本轮成图

[打开三款 A 图标对比页](../../../../output/imagegen/mirally-a-monogram/index.html)

| 最终比较项 | 原图 | 最终提示词 | 观察 |
| --- | --- | --- | --- |
| 01 · 合翼 A | [PNG](../../../../output/imagegen/mirally-a-monogram/01-ally-wing-v2.png) | [精修提示词](01-ally-wing-v2.txt) | 最接近原折翼，舒展感强；A 较抽象 |
| 02 · 并肩 A | [PNG](../../../../output/imagegen/mirally-a-monogram/02-ally-shoulder.png) | [提示词](02-ally-shoulder.txt) | A 最直观，横笔有伸向伙伴的动作；造型温和 |
| 03 · 联结 A | [PNG](../../../../output/imagegen/mirally-a-monogram/03-ally-link-v2.png) | [精修提示词](03-ally-link-v2.txt) | A 的留白和连接较清楚，顶端保留向前动势；优先推荐继续发展 |

初稿三款的字母虽然清晰，但轮廓过于相近、偏普通字标。01 与 03 分别做了定向精修：01 保持原型外轮廓，03 改为更利落的笔画交接。初稿全部保留，最终对比页展示上表三款，不把未采用初稿混入候选。

所有五张 PNG 均为 1254 × 1254 RGB 不透明图片，逐张打开检视，并通过 Pillow 完整性检查。它们属于概念图，尚未安装为应用图标。

对比页的 24 个本地资源引用均已验证；页面经过无界面 Edge 实际渲染并打开截图检查。三款字标搭配及 24 / 48 / 96 px 预览均正常显示：[页面预览](../../../../output/imagegen/mirally-a-monogram/comparison-preview.png)。

生成参数与校验记录：[初稿](../../../../output/imagegen/mirally-a-monogram/generation.json)、[精修](../../../../output/imagegen/mirally-a-monogram/generation-v2.json)。两次运行中，原配置文件生成前后校验均一致。

## 2026-10-03：正式选定 01 · 合翼 A

用户选定 **01-ally-wing-v2**，认可其含蓄的 A 轮廓与环抱、伙伴的感觉。
以上候选探索记录保留；当前正式采用 01，03 不再作为推荐替代。

为适应晴空蓝与石墨等主题，使用同一 `gpt-image-2.5-sunburst` CLI 对选定图片仅做去白底处理；
参数为 `edit --quality high --size auto --background transparent --no-augment`。
[完整生产提示词](production-transparent.txt)明确锁定外形、留白、位置、蓝色、折面和纹理，禁止重设计。
[透明生产图](../../../../output/imagegen/mirally-a-monogram/01-ally-wing-production-transparent.png)
为 1254 × 1254 RGBA，透明通道范围 0–255，已打开检视。选定原稿保持不变。

运行资源放在 [mirally/assets](../../../../mirally/assets/README.md)，包含生产 PNG、11 个尺寸 PNG
和覆盖 16–256 px 的 Windows ICO。应用通过 `mirally.branding` 加载，启动动画通过
`mirally.startup.StartupReveal` 使用同一标记。开屏约 0.94 秒，首个点击或键盘输入即可跳过，
“减少动态效果”下直接展示主界面。
