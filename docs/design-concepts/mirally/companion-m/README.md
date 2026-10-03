# Mirally · 相伴 M

当前正式母版为 [精修 SVG](../../../../openassist/assets/mirally.svg)，应用中的 PNG 与 ICO 均由它导出。[矢量制作说明](vector-redraw.md) 记录轮廓、交叠曲线与渐变的拟合方法和重建命令。下面保留生成参考图的来源记录。

用户选定新的「冰蓝双人流线」参考图，以此替代之前的合翼 A。主体是两个人彼此连接形成的 M：M 呼应 Mirally 与 MIRA，双人和连接表达伙伴、协作与相互支持。

## 生成参考图（历史来源）

保留蓝青渐变、两个圆形头部、柔和且交叠的流线身体；移除参考图的浅色圆角底板、环境光晕和投影，改为真正透明背景。无需为石墨主题另外加白色底板。主体四周留有空隙，适用于界面标志、浮窗、开屏和任务栏。

使用用户指定的 imagegen 原版 `scripts/image_gen.py edit`，通过当前 provider 调用 `gpt-image-2.5-sunburst`，参数为 `--quality high --size auto --background transparent --output-format png --prompt-file ... --no-augment`。凭据仅传入进程环境，原配置没有修改。

共生成两张：第一张放大较多，透明边缘与头部间隙不够理想；第二张保留更充足的边距与更清楚的头身分离，选为正式图。

- [最终完整提示词](production-clean.txt)
- [第一张提示词](production-transparent.txt)
- [原始参考图](../../../../output/imagegen/mirally-companion-m/reference.png)
- [选定透明 PNG](../../../../output/imagegen/mirally-companion-m/companion-m-clean.png)
- [参考图、浅深背景及实际尺寸预览](../../../../output/imagegen/mirally-companion-m/index.html)
- [生成参数与原图校验记录](../../../../output/imagegen/mirally-companion-m/generation-clean.json)

生成参考图为 1254 × 1254 RGBA，现保存在仓库中的 [generated-reference.png](generated-reference.png)，用于复现矢量拟合。正式资源已切换为精修 SVG 的导出图，包括 16、20、24、32、40、48、64、96、128、256、512 px PNG，以及包含 16–256 px 的 Windows ICO。生产资源和重建方式见 [openassist/assets](../../../../openassist/assets/README.md)；早期探索保留为历史记录。
