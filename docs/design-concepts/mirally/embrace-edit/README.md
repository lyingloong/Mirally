# Mirally：基于现用图标的环抱感编辑

> 历史设计记录。当前正式图标为 [相伴 M](../companion-m/README.md)；下文中的选择与验证描述当时状态。本地 output/ 预览不随 Git 上传。

该轮直接把 `mirally/assets/mirally.png`（当时为合翼 A）作为每次 API 编辑请求的输入图，而不是用文字重新生成蓝色 A。当时已核对原图与应用加载路径一致，此轮没有替换图标。当前生产文件已经更新为相伴 M；复现此轮编辑应改用本地 `output/imagegen/mirally-embrace-edit/original.png`。

使用 imagegen 技能原版 `scripts/image_gen.py edit --image mirally/assets/mirally.png`，通过当前配置的 provider 请求 `gpt-image-2.5-sunburst`。参数：`--quality high --size auto --background transparent --prompt-file ... --no-augment`。六个候选分别请求，每次都从原图开始；没有串行修改上一个候选。

| 编号 | 编辑方向 | 提示词 |
| --- | --- | --- |
| 01 | 内侧轻轻靠拢 | [01.txt](01.txt) |
| 02 | 下方温柔托抱 | [02.txt](02.txt) |
| 03 | 上下两片呼应 | [03.txt](03.txt) |
| 04 | 环抱走势舒展 | [04.txt](04.txt) |
| 05 | 下方尖端向内上方弯起，增加可见变化 | [05.txt](05.txt) |
| 06 | 上下两片相向成为开放臂弯，允许更明显形变 | [06.txt](06.txt) |

前四张保留原版蓝色和光泽，但结构变化较小，因此追加 05、06 两个更明确的形状方向。候选名是编辑意图，不代表模型已完整实现；应以原图并排比较为准。

[打开原版与六个候选的本地对比页](../../../../output/imagegen/mirally-embrace-edit/index.html)。支持白色、浅蓝、深色衬底及 24/48/72 像素预览。图像保存在 `output/imagegen/mirally-embrace-edit/`，遵循现有规则不随 Git 上传。此处保留提示词和生成方式。

使用已有 imagegen 虚拟环境；配置文件只读，provider 地址和凭据仅放入 CLI 子进程环境，不打印或保存到产物。生成后核对原配置字节未改变。
