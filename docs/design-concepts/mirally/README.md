# Mirally · 品牌与图标探索

## 名称

用户选定的命名方向：**Mirally = MIRA + ally**。两词共用一个 a；ally 意为伙伴、盟友。

MIRA 的完整名称由用户确认：**Machine Intelligence Research and Applications Lab**，中国科学技术大学 MIRA 实验室。

产品定位：来自 MIRA 的个人智能伙伴。能够理解上下文、参与对话并协助执行任务；长期个人记忆是后续能力方向，不作为已完成的功能宣传。

品牌表达：**懂你的意思，与你一起成事。**

## 命名状态

这是已获用户认可的命名方向，并非商标或域名可用性结论。公开搜索尝试遇到超时、脚本访问限制及不相关结果，未获得足以判断重名情况的证据。商标、域名、同类产品和应用商店名称仍待有效查重。

本轮先探索品牌视觉，应用显示名称、Python 包、现有配置目录与历史记录尚未迁移。

## 图标设计原则

- **最新方向见 [A 字母图标探索](a-monogram/README.md)。** 用户希望先识别出名字中的字母，优先 A，并参考早期折翼的美感。
- A 是 MIRA 与 ally 共用的字母；通过两笔的支撑关系表达伙伴，以一个清晰轮廓承载相互关联的含义。
- 保留明确的天蓝色品牌色；界面继续使用银白与深灰、少量蓝色强调。
- 轮廓先于材质。轻微的立体层次只用来辅助辨识，避免厚重塑料感与多余装饰。
- 每款分别检视小尺寸轮廓。概念图通过选择后，才制作正式透明、单色与 ICO 资源。

## 第一轮抽象候选（存档）

本轮「并行／合契／续环」未获用户认可，保留提示词与成图作为设计记录。后续以可辨认的 A 字母方向继续。

| 候选 | 图形方向 | 主要意义 | 提示词 |
| --- | --- | --- | --- |
| 01 · 并行 | 两条平等、相互呼应的向前曲线 | 人与助手朝着同一目标推进 | [01-together.txt](01-together.txt) |
| 02 · 合契 | 两个互补的圆润几何形体 | 协作产生完整的能力 | [02-fit.txt](02-fit.txt) |
| 03 · 续环 | 留有开口的连续不对称环 | 对话、任务与记忆的延续 | [03-continuity.txt](03-continuity.txt) |

## 生成方式

- 使用 imagegen 技能原版 `scripts/image_gen.py generate`，不使用内置生图工具。
- 模型 `gpt-image-2.5-sunburst`；参数 `--quality high --size auto --background opaque --prompt-file ... --no-augment`。
- 无参考图，重新设计，保留之前的图标候选文件。
- 从当前 Codex provider 读取中转地址与凭据，仅传给子进程环境 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`，不输出密钥、不修改原配置。
- 使用 `.artifacts/imagegen-venv`，不改动应用运行环境。

## 成图与检视

[打开三款图标与字标对比页](../../../output/imagegen/mirally-concepts/index.html)

- [01 · 并行](../../../output/imagegen/mirally-concepts/01-together.png)：两条同向曲线，轻快且有行动感。
- [02 · 合契](../../../output/imagegen/mirally-concepts/02-fit.png)：两个互补的形体，中间留出明确的曲线通道；属于未采用的第一轮探索。
- [03 · 续环](../../../output/imagegen/mirally-concepts/03-continuity.png)：开放的不对称环，气质安静；与常见环形标志相比，独特性仍有提升空间。

三张原图均为 1254 × 1254 RGB 不透明 PNG，已逐张打开并通过 Pillow 文件完整性检查。对比页包含 Mirally 字标搭配及整图缩小预览；这些预览尚非裁切后的正式托盘资源。

对比页已通过本地链接检查，并使用无界面 Edge 实际打开、截图与目视检视：[对比页预览](../../../output/imagegen/mirally-concepts/comparison-preview.png)。三款图、字标及不同尺寸预览正常显示。

[生成参数与文件校验记录](../../../output/imagegen/mirally-concepts/generation.json)已保存。原配置文件生成前后校验一致。本轮未修改应用代码或安装候选图标。
