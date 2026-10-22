# OpenAssist 视觉方向探索 · 2026-10-02

本页保留当时的产品旧名 OpenAssist、原始提示词和概念图，以便核对生成记录。当前产品与代码包已统一为 Mirally / `mirally`。

本轮使用内置 image_gen 工具生成三张高保真概念图。该工具未提供模型选择参数，返回结果也没有型号信息，因此不宣称使用了某个指定型号或“最强模型”。未使用 CLI/API 备用流程。

图片是用于比较视觉方向的静态概念，不代表功能已经实现，也不能证明转场动效、截图隔离、输入隔离或停止控制已经通过验证。本轮没有修改应用代码。

## 方向与建议

- [01 · 瓷白 + 钴蓝](01-porcelain-cobalt.png)：对比清晰、通用性强，展示执行与确认状态。
- [02 · 石墨黑 + 香槟](02-graphite-champagne.png)：沉稳的深色方向，展示执行与暂停状态。
- [03 · 矿物白 + 鼠尾草绿](03-mineral-sage.png)：更柔和清爽，推荐作为后续细化起点；展示同尺寸的开始与执行状态。

后续实现应固定用户选择的窗口位置和尺寸，通过当前动作的位移、字重和颜色变化表达阶段转换；避免执行时自动缩小、移动或变透明。歌词区只展示真实的当前动作与已发生记录，不暗示尚未完成的动作已经完成，不虚构任务完成百分比。日志默认折叠，停止与接管应能可靠触达。

生成图存在需在落地时纠正的细节：第一、二张加入了提示词未要求的宣传文案；第三张仍偏向时间线表达，需用交互原型进一步验证歌词过渡。图中的暂停/停止/接管按钮、快捷键及确认描述都是设计示例，须接入真实控制能力后才能作为已实现功能展示。窗口在画板中的展示比例不构成运行时缩放要求。

## 完整生成提示词

### 01 · 瓷白 + 钴蓝

输出：01-porcelain-cobalt.png

```text
Use case: ui-mockup
Asset type: high-fidelity visual concept for OpenAssist, a premium Windows desktop AI agent. This is a software interface design board, not a website or dashboard.
Primary request: explore direction A, PORCELAIN / COBALT. An exceptionally polished, fashionable, investor-demo-ready desktop companion with calm warm off-white surfaces, near-black editorial typography, restrained vivid cobalt accents. Prioritize typographic composition and exquisite proportions over decoration. Create a crisp landscape 3:2 board with two complete app windows side by side: a larger execution-state panel left and a smaller presentation of the same window design in confirmation state right. They represent alternate states, not multiple simultaneously open overlays. Include subtle labels "01 / PORCELAIN" and "执行中" / "需要确认" outside the windows. Use a neutral warm-gray canvas, delicate realistic shadow, thin frame, modest 18px corner radius. No perspective, no computer hardware, front-on pixel-sharp UI.
Execution window: about 480 by 640 logical pixels; slim titlebar with minimalist OpenAssist mark, wordmark, pin and close controls. Tiny status dot and elapsed time "01:24". Task line "整理本周的项目资料". Main section is a beautifully composed lyric-style progress view: previous line "已找到项目文件夹" rendered smaller in mid-gray, current line "正在整理本周资料" rendered very large bold near-black with a small cobalt vertical marker; another genuinely past line "已读取 12 份文档" in subdued gray above. Clear spacing, no log dump, no fake percentage. Auxiliary row "文件资源管理器 · 正在执行". Lower area generous blank space and small collapsible "查看执行记录". Stable bottom bar with clear elegant controls "暂停" and "停止", secondary "接管"; helper "Ctrl + Alt + Esc 停止". Bottom input area says "补充任务信息…", small submit arrow.
Confirmation-state window uses same visual language and proportions, a warm amber dot, heading "需要你的确认", concrete example "将 12 份文件移动到归档文件夹", "目标：项目资料 / 归档", two well-designed buttons "允许移动" and "拒绝", quiet "人工接管". No scary alert dialog.
Constraints: readable simplified Chinese and exact OpenAssist brand text, feasible interface with consistent spacing and high contrast, fully opaque stationary window, mature modern desktop design, visual hierarchy should feel like a premium music player combined with a professional assistant. Lyric transition implied by hierarchy of past and present lines, not motion blur. No transparent content, no glow, no cyan/purple gradients, no generic card grids, no robot mascot, no financial charts, no marketing slogan, no decorative progress percentages, no mobile phone, no macOS traffic lights, no watermarks. Render at very high detail.
```

### 02 · 石墨黑 + 香槟

输出：02-graphite-champagne.png

```text
Use case: ui-mockup
Asset type: premium Windows desktop AI assistant visual design exploration for OpenAssist.
Primary request: direction B, GRAPHITE / CHAMPAGNE. A spectacularly tasteful, implementable desktop application concept, like a high-end audio player and an advanced creative productivity tool. A landscape 3:2 front-facing concept board, two app windows side by side showing execution and paused/manual takeover variants. Neutral dark graphite board, subtle labels "02 / GRAPHITE", "执行中" and "已暂停" outside the windows. Same window design in alternate states, larger left window, smaller right window. This is a design presentation, not two floating HUDs. No website.
Color palette: true neutral graphite #18191B, charcoal #222326, warm ivory text #F1EEE8, subdued champagne #CAB38C for one carefully controlled accent. No blue cast. Window bodies 100% opaque, tiny hairline borders, realistic soft shadow, restrained 16px corners. Tactile sophistication through typography, depth and spacing. No decorative 3D object.
Main execution window: approx 480x640 logical pixels. Refined titlebar "OpenAssist" with minimal ring brand glyph, pin, minimize and close line icons. Slim task row "整理本周的项目资料" and tiny illuminated dot "执行中 · 01:24". Beautiful music-player lyric composition occupies the center: past line "已找到项目文件夹" in medium gray, past line "已读取 12 份文档" in medium gray below it, then very large bright ivory current line split intentionally into two lines "正在整理" / "本周资料". Small warm champagne moving-line motif aligned with current line; no blurred text. Below the current line a quiet caption "文件资源管理器". Past lines clearly subordinate but readable. Generous empty space separates progress from controls. Small collapsed "执行记录" row. Persistent tasteful pill controls at bottom: champagne-filled "暂停", outlined "停止", quiet "接管". Tiny footer "Ctrl + Alt + Esc 停止". An opaque slim composer "补充任务信息…" with submit arrow. Stylish precise spacing.
The paused window at right has exact same component language. Task row stays. Big heading "已暂停", supporting line "你可以安全操作电脑", a quieter line "继续后将重新观察桌面". Main button "继续任务", secondary "结束任务"; history and composer remain. Show capability through calm legibility and superb typography.
Constraints: simplified Chinese text crisp and correct, mature and beautiful product design, all chrome Windows-compatible without macOS traffic lights. User controls have visual priority. Lyric reading rhythm, not raw logs or planned future actions masquerading as completed. No percent progress bar, no equal five-step pipeline, no dashboard metrics, no sidebar full of menu items. No sci-fi HUD, no holograms, no neon edges, no cyan, no purple gradients, no transparency or ghosted windows, no metallic text, no watermarks, no marketing headline or invented slogan. High detail, portfolio-quality yet realistic software UI.
```

### 03 · 矿物白 + 鼠尾草绿

输出：03-mineral-sage.png

```text
Use case: ui-mockup
Asset type: desktop AI agent visual concept for OpenAssist.
Primary request: Direction C, MINERAL / SAGE. Create a beautiful, distinctive premium desktop assistant UI design board. Contemporary industrial design sensibility, fine Swiss editorial typography, stone-white canvas, opaque pale mineral-gray windows, dark forest-black text, sophisticated deep sage-green action color. More youthful and fresh than black-and-gold, quieter and more original than blue enterprise software. Extremely crisp high-fidelity interface rendering, landscape 3:2 aspect ratio. Top tiny direction label "03 / MINERAL". Two COMPLETE identical-size 460x660 logical pixel app windows side by side at the SAME height, showing two states of the SAME application at fixed dimensions: idle on left, executing on right. Never show a compacted tiny floating window. Label states outside "准备开始" / "任务进行中". Show no desktop background imagery, no perspective, no hardware.
App frame: soft 20px corners, fine neutral border, subtle lifted shadow, completely opaque surface, compact Windows titlebar with custom outlined geometric mark and "OpenAssist", two small pin/close icons, no macOS traffic lights. Purposeful 24-32px padding, subtle rules, high quality glyphs, strong readable hierarchy.
Idle state: small status pill "准备就绪". Main welcoming headline "让想法，" / "成为下一步。" in large forest-black Chinese type, tiny supporting copy "告诉我你想完成什么". Large elegant but restrained composer with placeholder "例如：整理本周的项目资料"; submit button is a deep sage square with white upward arrow. Beneath, two quiet suggestion rows "整理文件" and "填写表单" with small line icons, not a card grid. Bottom subdued mode row "桌面助手" and settings icon. Spacious unhurried composition.
Execution state: the top task row "整理本周的项目资料", small filled dot and "执行中 · 01:24". Central lyric-like status area: two previous lines "已找到项目文件夹" and "已读取 12 份文档" in muted but legible gray, then current line "正在整理" / "本周资料" in large very sharp dark forest text. A fine green vertical marker at left of current action and tiny tasteful four-bar motion indicator nearby. Small caption "文件资源管理器". Avoid future lines that look like already executed steps. Bottom "查看执行记录" collapsed row. Stable bottom controls: deep sage "暂停", outlined "停止", simple "接管". Bottom composer "补充任务信息…" with small send arrow. Tiny clear hint "Ctrl + Alt + Esc 停止".
Constraints: product-ready visual concept with excellent implementable desktop proportions, sophisticated spacing, no arbitrary movement or transparency on task start. Simplified Chinese labels must be accurate, readable and restrained. Do not add invented marketing slogans outside UI, no gradient backgrounds, no neon cyan, no purple, no metallic gold, no glowing AI orb, no mascot, no fake charts or percentages, no oversized empty dashboard, no watermark. Small accent areas only, neutral surfaces dominate. Design quality suitable for a premium startup product showcase.
```
