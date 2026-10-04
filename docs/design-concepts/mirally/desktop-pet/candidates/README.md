# Mirally 伙伴造型候选

2026-10-04。按「先构思 → Sunburst 探索 → 手绘矢量」比较两条新方向。用户已选择**小海獭为运行默认、小飞狐为可选**；相伴精灵保留为未接入概念。此前独立候选预览与验证作为设计历史保留。

| 方向 | 与 Mirally 的关联 | 造型与动作 |
| --- | --- | --- |
| [相伴精灵](brand-companion.svg) | 现有相伴 M 标志的曲线延伸成柔软手臂，以拥抱和托付表达 ally | 圆润的头部，交叠怀抱；挥手、抱住材料、交出成果 |
| [小海獭](sea-otter.svg) | 海獭常见的牵手漂浮形象对应陪伴、不走散 | 圆耳、短须、白色口鼻与腹部、带肉垫的小爪子；伸爪、抱材料 |

[本机概念与动作对比页](../../../../../output/imagegen/mirally-ally-pets/index.html)包含原来的狐狸、两张 Sunburst 概念、两款手绘矢量，以及浅深背景和实际 80×96 px 尺寸。可切换待机、问好、工作、等待、完成、轻触动作。这里是设计演示，不会发起任务或修改应用设置。

## 生成记录

使用 imagegen 技能的原版 `scripts/image_gen.py`，CLI/API 模式，模型 `gpt-image-2.5-sunburst`，`--quality high --size auto --background opaque --prompt-file … --no-augment`。相伴精灵通过 `edit --image openassist/assets/mirally-512.png` 携带当前标志作为轮廓与色调参考，生成全新的角色提案；小海獭通过 `generate` 生成。

- [相伴精灵提示词](brand-prompt.txt) → [Sunburst 参考](../../../../../output/imagegen/mirally-ally-pets/brand-concept.png)
- [小海獭提示词](otter-prompt.txt) → [Sunburst 参考](../../../../../output/imagegen/mirally-ally-pets/otter-concept.png)

复用独立生图环境，openai、Pillow、socksio 可用。当前 provider 的地址与凭据仅设置到生图进程环境；配置文件生成前后字节一致。两张图片已逐张打开检查，完整性与参数记录在本机 `output/imagegen/mirally-ally-pets/generation.json`。

SVG 按参考重新绘制，保留角色识别特征，简化毛发和材质；不是逐像素复刻。候选与生成参考保留独立文件，应用正式相伴 M 标志不替换。运行时使用[小海獭生产 SVG](../../../../../openassist/assets/mirally-pet-otter.svg)，另保留[小飞狐生产 SVG](../../../../../openassist/assets/mirally-pet.svg)。伙伴右键菜单或显式详情中可切换形态与「小海獭/小飞狐」造型，偏好只保存在当前桌面来源的 WebView 配置档，不写 Gateway 设置或助手记忆。详情保留约 240 ms 尺寸过渡；小输入、菜单和回复气泡只做约 160 ms 淡入，立即使用最终位置，避免原生裁剪越界。普通移出保留 100 ms 容错，回复气泡不随移出自动关闭。现行交互与检查范围见[伙伴设计记录](../README.md)。

## 预览检查

对比页通过 11 项检查，包含浅深背景、实际与放大尺寸、唯一 SVG 资源 ID、图片加载、状态切换、点击回应、窄屏布局及减少动态效果。六种状态按各动画完整周期采样 9 个时间点，三款形象均在 80×96 px 范围内。截图已打开检查，记录在本机 `preview-checks.json` 与 `animation-bounds.json`。这些是独立设计页的历史验证，不代替当前海獭默认与狐狸可选流程的真实浮窗验收。
