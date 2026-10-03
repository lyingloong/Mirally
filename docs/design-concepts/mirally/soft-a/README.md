# Mirally：简短提示词的蓝色 A 探索

> 历史设计记录。当前正式图标为 [相伴 M](../companion-m/README.md)；下文中的选择与验证描述当时状态。本地 output/ 预览不随 Git 上传。

保留用户提出的要求：现有天蓝色调、大致 A 形、有一点怀抱感、飘逸柔和。不指定折带、角度、比例、结构或材质。

使用 imagegen 技能原版 `scripts/image_gen.py generate`，通过当前 provider 的中转 API 调用 `gpt-image-2.5-sunburst`；参数为 `--quality high --size auto --background opaque --prompt-file ... --no-augment`。

- 01—06：[完整提示词](prompt.txt)。六次结果均将伙伴、怀抱解释为两个人相拥，形状差异较小。
- 07—09：[更短的抽象表达](prompt-abstract.txt)。减少拟人语义，保留同样的核心要求。
- 第一请求指定 `--n 6`，但中转仅返回一张；其余候选分别请求生成，未将一张图片复制成多个方案。

[本地对比页与开屏预览](../../../../output/imagegen/mirally-soft-a/index.html)。图片保存于 `output/imagegen/mirally-soft-a/soft-a-1.png` 至 `soft-a-9.png`，均为本地生成产物，按现有规则不上传 Git。用用图标仍使用原选定合翼 A，待用户选稿后另行制作生产资源。

依赖复用 `.artifacts/imagegen-venv` 的 openai、Pillow、socksio。provider 配置只读，地址和密钥仅传入 CLI 进程环境，未写入本目录或输出日志；执行后核对原配置字节未改变。
