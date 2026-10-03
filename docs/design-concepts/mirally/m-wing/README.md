# Mirally：保留合翼 A，探索 M 的暗示

用户要求使用 sunburst 在现用图标基础上生成几版 M 变体，保留 A、拥抱和飘逸感。

选择结果：三版均不采用。正式图标继续使用 `01-ally-wing-v2`；本目录仅保留探索记录，输出对比页是本地生成产物，不随源码上传。

[打开原稿与三版对比页](../../../../output/imagegen/mirally-m-wing/index.html)

| 变体 | 处理 | 实际观察 | 完整提示词 |
| --- | --- | --- | --- |
| 01 隐峰 | 上翼浅凹口 | 最接近原稿；M 暗示较弱，小尺寸下不明显 | [01](01-hidden-m.txt) |
| 02 并翼 | 不等高双折翼 | 双峰更清楚，伙伴感更强；上部也更厚实 | [02](02-twin-fold.txt) |
| 03 相拥 M | 内侧折带形成第二肩部 | 三版里最容易联想到 M；保留拥抱，但改动最大 | [03](03-embrace-m.txt) |

三版都以 `01-ally-wing-v2.png` 为编辑目标。使用 imagegen 技能原版 `scripts/image_gen.py edit`，模型严格为 `gpt-image-2.5-sunburst`，参数为 `--quality high --size auto --background opaque --prompt-file ... --no-augment`。

使用已有 `.artifacts/imagegen-venv`，确认 openai、Pillow、socksio 可用。从当前 provider 读取 base_url 与 experimental_bearer_token，仅放入 CLI 子进程的 OPENAI_BASE_URL、OPENAI_API_KEY。没有打印凭据；生成前后原配置字节一致。

三张均已打开检视；对比页提供原图与 24 / 48 / 80 px 预览。现用应用图标仍为已选定的合翼 A，本轮只提供备选。

[生成记录](../../../../output/imagegen/mirally-m-wing/generation.json)
