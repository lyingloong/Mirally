# Mirally · 相伴 M

用户选定新的「冰蓝双人流线」参考图，以此替代之前的合翼 A。主体是两个人彼此连接形成的 M：M 呼应 Mirally 与 MIRA，双人和连接表达伙伴、协作与相互支持。

## 正式处理

保留蓝青渐变、两个圆形头部、柔和且交叠的流线身体；移除参考图的浅色圆角底板、环境光晕和投影，改为真正透明背景。无需为石墨主题另外加白色底板。主体四周留有空隙，适用于界面标志、浮窗、开屏和任务栏。

使用用户指定的 imagegen 原版 `scripts/image_gen.py edit`，通过当前 provider 调用 `gpt-image-2.5-sunburst`，参数为 `--quality high --size auto --background transparent --output-format png --prompt-file ... --no-augment`。凭据仅传入进程环境，原配置没有修改。

共生成两张：第一张放大较多，透明边缘与头部间隙不够理想；第二张保留更充足的边距与更清楚的头身分离，选为正式图。

- [最终完整提示词](production-clean.txt)
- [第一张提示词](production-transparent.txt)
- [原始参考图](../../../../output/imagegen/mirally-companion-m/reference.png)
- [选定透明 PNG](../../../../output/imagegen/mirally-companion-m/companion-m-clean.png)
- [参考图、浅深背景及实际尺寸预览](../../../../output/imagegen/mirally-companion-m/index.html)
- [生成参数与原图校验记录](../../../../output/imagegen/mirally-companion-m/generation-clean.json)

生成原图为 1254 × 1254 RGBA，alpha 范围 0–255。按同一母版机械导出 16、20、24、32、40、48、64、96、128、256、512 px PNG，以及包含 16–256 px 的 Windows ICO；没有为各尺寸重新生成不同造型。生产资源见 [openassist/assets](../../../../openassist/assets/README.md)。原始参考图与前一版生产图保留在本地设计输出目录，早期探索文档保留为历史记录。
