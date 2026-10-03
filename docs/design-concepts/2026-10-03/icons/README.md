# 图标形态探索 · 初版

这一轮探索托环、折翼、启门、并肩四种形态。用户随后指定整体天蓝色调，当前候选请看 [晴空蓝版](../sky-blue/README.md)。

| 方向 | 原图 | 原始提示词 |
| --- | --- | --- |
| A · 托环 | [PNG](../../../../output/imagegen/icons-2026-10-03/a-open-core.png) | [提示词](a-open-core.txt) |
| B · 折翼 | [PNG](../../../../output/imagegen/icons-2026-10-03/b-foldwing.png) | [提示词](b-foldwing.txt) |
| C · 启门 | [PNG](../../../../output/imagegen/icons-2026-10-03/c-open-portal.png) | [提示词](c-open-portal.txt) |
| D · 并肩 | [PNG](../../../../output/imagegen/icons-2026-10-03/d-tandem.png) | [提示词](d-tandem.txt) |

使用 imagegen 技能原版 `scripts/image_gen.py generate`，指定 `gpt-image-2.5-sunburst`、`high`、`auto`、`opaque`、`--prompt-file`、`--no-augment`，经当前配置的中转 API 调用。首次请求遇到 DNS 错误，连通性恢复后重试成功。凭据只存在于进程环境，原配置前后校验一致。
