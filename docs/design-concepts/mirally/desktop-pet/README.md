# 桌面小图标与伙伴造型

2026-10-05。侧栏、图标与伙伴分开承担工作进展、快速输入和陪伴互动；应用正式相伴 M 标志保持不变。本轮机器侧检查通过，实际使用体感待验收。

## 造型

当前选择是**默认小海獭，可选小飞狐**。伙伴右键打开菜单，或显式打开详情后，用形态/造型选项切换；选择后收起。偏好保存在此桌面页面来源的 WebView 配置档中，重开窗口可沿用，不是 Gateway 配置或助手记忆。「相伴精灵」仍为未接入概念。

运行资源分别为[小海獭 SVG](../../../../mirally/assets/mirally-pet-otter.svg)和[小飞狐 SVG](../../../../mirally/assets/mirally-pet.svg)，继续使用既有状态动画。换造型不影响会话、草稿、附件或任务执行。

### 历史探索

最初 Sunburst 生成两套动作参考：小飞狐与云团精灵，先实现了小飞狐，有明显耳朵、爪子、蓬松尾巴和小翅膀，小尺寸仍能辨认成宠物。没有把生成图直接裁成按钮，而是按参考重新画成[独立 SVG](../../../../mirally/assets/mirally-pet.svg)，约 6 KB，使用路径和少量渐变，不嵌入位图。正式图标继续使用相伴 M，仅改善浮动底座、图形大小和状态显示。

本机可打开[概念与动画预览](../../../../output/imagegen/mirally-companion-pet/index.html)，包含两个生成参考、实际矢量动作与 64 px 图标、80×96 px 伙伴预览。`output/` 是本机设计输出，不作为应用运行依赖，也不默认上传仓库。

随后按品牌关联探索了「相伴精灵」和「小海獭」两条方向，分别生成 Sunburst 概念、再画矢量候选；[候选设计记录](candidates/README.md)与[三款造型对比页](../../../../output/imagegen/mirally-ally-pets/index.html)用于选型。当时程序仅使用狐狸；之后用户选择小海獭为默认、小飞狐为可选，形成当前运行造型。

## 动作与交互

| 场景 | 表现 |
| --- | --- |
| 空闲 | 呼吸、眨眼、偶尔摆尾和动耳朵 |
| 鼠标靠近 | 伙伴挥手，不自动开面板、不抢输入焦点 |
| 轻点伙伴 | 爱心和笑眼回应；不发送消息或采集页面 |
| 右键伙伴 | 打开左侧菜单，可说一句、看进展、回工作台或选造型 |
| 工作中 | 出现小电脑，轻敲键盘与专注动作 |
| 等你决定 | 歪头、抬爪，保留实际待处理入口 |
| 出错 / 完成 | 疑惑动作 / 笑眼和轻跳，文字状态仍可查看 |
| 最终回复到达 | 左侧主动出现回复气泡，直接显示回答或工作结果；可查看全文或接着聊 |
| 移动位置 | 直接拖动本体或详情顶部；⠿ 与 Alt 拖动也可用，拖动与点击分开处理 |

伙伴本体保持 80×96 px，可直接拖动位置。右键菜单约 220×252 px；「说一句或补充要求」打开约 248×148 px 小输入，「查看工作进展」才开约 376×560 px 详情，详情顶部仍可拖动。气泡与本体约 8 px 间隙及外部区域透明、点击穿透。输入焦点或键盘操作可保持界面，草稿本身不锁展开；Esc 或×可收起，不清除草稿和附件。

收到当前会话的新最终回复时，自动显示约 296 px 宽、132～228 px 高的回复气泡，短问候直接可读，长内容可滚动；不必打开工作进展。气泡不抢焦点，也不因鼠标离开而消失，× 或 Esc 关闭，「接着聊」回共享输入框，「查看完整回复」回原生对话。已有草稿、附件或菜单/详情交互保留，提醒待收起后展示；历史与重复事件不重弹。只有结束事件而正文未到时先提示结束，随后更新同一气泡。行为、验证范围和当前会话限制见[套壳说明](../../../openclaw-desktop-shell.md#侧边伙伴)。

右键菜单保留网页菜单事件、右键松开与键盘入口，并增加 WebView2 原生菜单请求兜底，仅接管收起状态的桌宠。“右键后左侧竖线一闪”另已复现为菜单位置动画越界，导致宿主保留旧宠物裁剪；小输入与菜单改为只淡入，切换时立即同步裁剪，宠物边框保持一致以避免细小偏移。角色轮廓内黑底来自宿主合成，使用 DWM 客户区玻璃合成与黑色清屏修复，不靠颜色键抠除角色颜色；收起、小输入和菜单保持透明，详情与工作台恢复不透明背景。

其他两种形态有不同用途：48 px 相伴 M 图标悬停仅开小输入气泡，点击可输入，并提供文件、详情、关闭快捷项；80×300 px 侧栏贴近屏幕边缘约 4 px，直接展示实际状态、当前步骤与最近已处理，悬停才开详情。步骤按原生会话与运行 ID 过滤，不拿旧回合冒充当前进展，也不编造百分比。伙伴动画由原生工作状态驱动，装饰互动不调用模型。减少动态效果时保留静态角色与功能。

独立截图手柄用于带回一次窗口画面，完成后打开详情核对预览；普通文件拖入只准备材料，进入小输入或侧栏详情，不自动发送。位置拖动与截图是两种操作，截图不等于实时控制授权。

## 生图方式与提示词

使用 imagegen 技能的原版 `scripts/image_gen.py generate`，CLI/API 模式，模型 `gpt-image-2.5-sunburst`，参数 `--quality high --size auto --background opaque --prompt-file … --no-augment`。复用 `.artifacts/imagegen-venv` 的 openai、Pillow、socksio，不升级应用依赖。当前 provider 配置只读，地址与凭据仅设置到生图进程环境 `OPENAI_BASE_URL`、`OPENAI_API_KEY`，不记录凭据。

- [小飞狐提示词](fox-prompt.txt) → [生成参考](../../../../output/imagegen/mirally-companion-pet/fox-concept.png)
- [云团精灵提示词](cloud-prompt.txt) → [生成参考](../../../../output/imagegen/mirally-companion-pet/cloud-concept.png)

两张 RGB PNG 均已打开检查；生成图只用作概念参考。运行时使用手绘 SVG 与现有品牌图标。

## 检查范围

主动回复通过 20 组界面检查、23 项办公投影 JavaScript 回归与 20 项品牌/宿主 Python 回归；自有 Windows 窗口验证浅深主题及三种形态的首帧显示、按钮命中、间隙点透、焦点与窗口恢复。另验证晚到长正文扩展裁剪，侧栏外框不变时也不截断内容，关闭后不重新弹出。记录在本机 `output/openclaw-native-verification/reply-ui/verification.json` 与 `reply-native/verification.json`；使用模拟消息，真实模型和自然鼠标操作按验收清单核对。

右键兜底的 14 项网页与 6 项原生事件检查仅验证路由。本轮已另验证首帧裁剪扩展、菜单和按钮原生命中、选择狐狸收起及图标小输入命中，证据为 `output/openclaw-native-verification/menu-short-trace/trace.json`，旧版对照为同目录 `baseline.json`。18 项 Python 与 JavaScript 语法检查通过。测试投递原生消息而不移动系统鼠标，真实物理操作与长期悬停体感仍待验收。内部透明像素生产验证见验收根目录的 `pet-alpha-fixed/verification.json`。

本轮通过 35 项 WebView 交互检查、浅深主题共 14 组实际布局检查，以及自有 Windows 测试宿主的透明和间隙点透检查。证据在本机 `output/openclaw-native-verification/three-modes/verification.json`、`three-view-layout/verification.json` 与 `native-menu-clip/verification.json`。工作状态用可恢复的合成数据，发送用本地回调核对，不发起模型任务；失焦条件用模拟验证。实际应用切换、自然拖动与动画体感仍需用户验收。以下保留此前所有形态悬停开大面板的历史检查；旧 64 px 图标预览也保留为历史设计。

此前针对选择后仍像侧栏、伸缩失效和顶部黑带复验：32 项 WebView 交互检查覆盖草稿与附件、选择、重新悬停、收起及顶部拖动；旧紧凑原位置拖动的 5 项检查与菜单宿主的 10 项检查分别记录在 `compact-flow/verification.json`、`compact-direct-drag/verification.json`、`native-menu-clip/verification.json`，位于本机 `output/openclaw-native-verification/`。当前图标/伙伴改为独立本体直接拖动，不沿用旧紧凑原位置方案。真实应用切换与新动画体感继续由用户验收。

此前造型选择独立通过 32 项 WebView 检查：默认海獭、两款切换、重新加载后保留、无效偏好与存储不可用时回退，原生会话/草稿/附件/执行状态不变；SVG ID 唯一、透明区域和动画行为通过。记录在本机 `output/openclaw-native-verification/pet-choice/verification.json`。当时 13 项 Python 与 JavaScript 语法检查通过；这些不代表本轮右键菜单流程已经验证。

以下为此前狐狸版本的检查。

原生 WebView 的悬停、移入容错、点击、键盘、拖动、窗口外释放、材料拖入和按钮通过 22 项交互检查，记录在本机 `output/openclaw-native-verification/companion-interaction/verification.json`。动作边界另按完成、挥手、轻触、拖动各采样 24 帧，均在 80×96 px 内；可见副本运行、隐藏副本暂停及减少动态效果检查通过，记录在 `pet-layout/layout.json`。13 项 Python 回归检查与 JavaScript 语法检查通过。

宿主裁剪与固定右边缘在不显示的合成窗口中独立检查，记录在 `compact-host/verification.json`。图片捕获用于检查浅深背景及紧凑/展开造型；程序化指针事件不代替用户实际移动鼠标的体感验收。当前窗口需重新打开才能加载新皮肤。
