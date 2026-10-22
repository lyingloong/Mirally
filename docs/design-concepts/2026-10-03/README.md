# 侧边助手概念 · 2026-10-03

本目录的原始提示词和生成图保留当时的旧名 OpenAssist；当前产品与代码包已统一为 Mirally / `mirally`。

后续配色已按用户要求更新为 [晴空蓝](sky-blue/README.md)。本页保留早期侧边形态探索，窗口交互形态继续沿用。

![侧边助手概念](../../../output/imagegen/2026-10-03-sunburst-edge-assistant.png)

- 图片：[2026-10-03-sunburst-edge-assistant.png](../../../output/imagegen/2026-10-03-sunburst-edge-assistant.png)
- 完整提示词：[edge-assistant-prompt.txt](edge-assistant-prompt.txt)
- 执行方式：imagegen 技能原版 `scripts/image_gen.py generate`，通过用户当前 Codex provider 配置的中转 API。
- 请求参数：`--model gpt-image-2.5-sunburst --quality high --size auto --background opaque --prompt-file ... --no-augment`。
- `base_url`、`experimental_bearer_token` 只在进程内映射为 `OPENAI_BASE_URL`、`OPENAI_API_KEY`，未打印密钥或更改原配置；读取前后的配置校验一致。
- 原项目 OpenAI SDK 为 1.66.2，不支持生图脚本的 `background` 参数。成功调用使用 `.artifacts/imagegen-venv` 中的独立依赖，未升级 Agent 的 SDK。

采用图中的矿物白与鼠尾草绿、右侧紧凑卡片、向左展开输入的形态。概念里的知识库等入口不代表已实现功能；当前实现以会话记忆、任务反馈和运行中补充为范围。
