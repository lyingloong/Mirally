# DeepSeek 连接与能力验证

更新于 2026-10-25，Asia/Shanghai。本页记录当前 Mirally 对话链路的实测范围，不代表供应商所有 API 都已接入。使用用户已保存的连接凭据，仅复制模型配置到独立测试 home；没有发送用户会话、桌面截图或个人记忆，也没有改写原配置。

## 官方能力与本机配置

当前官方 [`/models`](https://api-docs.deepseek.com/api/list-models/) 和实际账号返回 `deepseek-flash`（DeepSeek-V4.1-Flash）支持文字与图片，`deepseek-v4-pro` 仅支持文字。两者均声明 Low / High / Max 推理强度，默认 High；能力随服务演进，应以实时列表和对应协议实测为准。官方[视觉说明](https://api-docs.deepseek.com/guides/vision/)与[推理说明](https://api-docs.deepseek.com/guides/thinking_mode/)给出实际请求格式。

用户原配置为 Flash、官方基础地址和 Responses 格式。本轮保持该配置不变，同时在隔离 home 验证 Chat Completions。Mirally 仍使用同仓 DSH 的 PiAI 适配器与唯一 Agent/Session 链路，没有增加第二个模型循环。

## 实测结果

| 能力 | 官方 API 直连 | 实际 Mirally / DSH |
| --- | --- | --- |
| 模型列表及能力声明 | 返回 Flash / Pro、输入类型、窗口与推理档位 | 设置页按已保存连接读取列表，明确区分声明与实际测试 |
| 文字与流式适配 | Chat / Responses 均返回正确算术结果 | 两协议经原生流式适配后返回正确结果 |
| 关闭推理 | Chat disabled 无推理正文；Responses none 的 reasoning tokens 为 0 | 显式关闭下传协议；不再以省略参数冒充关闭 |
| Low / High / Max / 服务默认 | 直连核对思考开关与 Low | 两协议的所有上述档位均完成实际生成；另以离线真实 HTTP 检查请求字段，Medium 对 DeepSeek 映射 High |
| 图片理解 | 实际 PNG：左绿方块、右粉色圆形，识别正确 | 两协议从产品图片附件服务，经原生适配提交后识别正确 |
| 推理与工具续轮 | Chat 自制只读工具两轮调用成功 | 两协议在算术推理后调用 `mirally_create_file`，取得成果回执；下载字节与测试内容完全一致 |

直连检查 7 个场景，产品链路 14 个场景通过。图片为本地代码生成的几何图形，文件只写入隔离 home 的会话成果目录。测试会产生 API 用量；凭据、临时数据和日志不提交。

实际 Edge 页面还验证了用户菜单开启推理显示后，思考内容在流式期间真正绘制、刷新后仍存在且不产生重复消息，以及工具卡展开后显示实际的空提醒列表。该检查使用相同隔离 Flash 连接，复现工具为 `tools/verify_framework_reasoning_browser.mjs`；普通模式必须显式传入 `--live`，会向所选模型发送测试消息。

## 设置与协议语义

- DeepSeek 预设仅在用户明确选择后填写地址、格式和模型，不自动修改已有连接或成为空配置默认值。
- 模型列表读取、采用列表能力、保存配置和文字测试是不同操作。采用会更新待保存草稿；检查使用已保存连接，不偷偷保存表单。文字测试只发送固定短消息，不携带聊天或材料。
- `reasoning_effort` 显式值优先于旧 `reasoning_enabled`。空串仅用于旧配置兼容：旧 false 在 DeepSeek 路由明确关闭，旧 true 使用 High；显式 `default` 才遵从服务默认。
- Chat 使用 DeepSeek 的 `thinking.type` 与 `reasoning_effort`；Responses 使用 `reasoning.effort`。推理工具历史通过原生块与协议回放保留，不能只保留最终正文。
- 图片开关声明当前连接可接收图片，不会让纯文本模型获得视觉能力。列表没有声明时仍需用户核实；切换 Pro 后不沿用 Flash 的视觉能力。
- 上下文窗口参与会话预算；最大输出包含服务计算的推理输出，过低可能没有最终回复。列表最大值是服务上限，采用模型不会擅自把用户的输出预算提高至该上限。

离线协议回归：

```powershell
node --test tests/test_framework_model_routing.mjs tests/test_framework_model_settings.mjs
```

## 范围

本轮验证文字、视觉、推理和原生工具链，不把文档中存在的能力视为已经集成。供应商独立 Files、Search/Fetch、FIM、前缀补全，以及所有第三方代理的兼容性未在本轮完整接入或验收；模型质量与长期任务成功率也不能由这些短用例保证。当前仍为一组共用模型连接，多路由和独立后台模型属于后续迁移项。
