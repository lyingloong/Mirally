# 侧边伙伴：预览、固定与窗口操作

更新于 2026-10-26，Asia/Shanghai。沿用 Mirally 原有晴空蓝、主题、伙伴造型与窗口呈现，重构交互状态和动效同步，不新增会话或模型链。

## 设计依据

| 官方实践 | Mirally 的取舍 |
| --- | --- |
| Microsoft [AppBar 源码](https://github.com/microsoft/Windows-classic-samples/blob/main/Samples/Win7Samples/winui/shell/legacysamples/appbar/AppBar.cpp) 将展开和收起分为不同计时器，展开不激活窗口 | 鼠标停留 140ms 才预览，离开留 400ms 宽限，返入取消关闭；预览不调用 focus，不抢原应用的键盘焦点。数值是本产品初始取值，不是平台强制要求；不复制旧示例的忙循环动画 |
| [Visual Studio 窗口管理](https://learn.microsoft.com/en-us/visualstudio/ide/customizing-window-layouts-in-visual-studio?view=visualstudio#close-and-autohide-tool-windows) 区分自动隐藏、固定和关闭；[VS Code 布局](https://code.visualstudio.com/docs/configure/custom-layout) 提供明确的面板入口并恢复布局 | 悬浮仅打开临时快速预览；点击、键盘进入、输入或添加材料后保持展开。固定按钮显示按下状态；收起保留草稿/材料/工作，最小化与返回主窗口单列 |
| [Fluent Drawer](https://fluent2.microsoft.design/components/web/react/core/drawer/usage) 使用标题与动作区、独立滚动正文和底部动作；复杂工作回到更完整的界面 | 预览聚焦当前事情和快速输入；完整面板承载进展、交接、材料与最近会话；底部常驻“返回主窗口”。形态、造型、最小化放“更多” |
| [浏览器动效性能](https://web.dev/articles/animations-guide) 优先使用透明度和变换；[Windows 时序与缓动](https://learn.microsoft.com/en-us/windows/apps/design/motion/timing-and-easing) 提供 83/167/250ms 的标准时长 | 外层宽高直接到目标布局，内容 167ms 淡入并小幅平移；收起时边缘入口短淡入。取消原宽高动画→ResizeObserver→每帧跨进程裁剪的循环，原生按最终物理像素去重 |
| [W3C 悬浮/焦点内容](https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus) 要求可关闭、可移入、可持续查看；[Disclosure 模式](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/) 定义展开状态和键盘入口 | Enter/Space 明确展开，Escape/收起返回边缘入口焦点；提供 aria-expanded/controls、固定的 aria-pressed、隐藏正文 inert。普通按钮组不冒充缺少方向键行为的 ARIA 菜单 |
| [Windows 触控](https://learn.microsoft.com/en-us/windows/apps/develop/input/touch-interactions) 与 [Edge 减少动态效果](https://learn.microsoft.com/en-us/microsoft-edge/devtools/accessibility/reduced-motion-simulation) | 触屏点击打开，不把接触当悬浮；粗指针按钮高度至少40px，图标操作同时加宽，面板留足空间。CSS与JS都遵守 reduced motion |

这些来源支持交互原则。具体布局、时间和状态转换是 Mirally 的设计决定，需要按实际产品验收，不能把官方示例当作本机性能保证。

## 操作约定

- 侧栏、图标：鼠标短掠过不展开；停留显示快速预览，能移入输入区。鼠标离开400ms后，仅未固定、未交互的预览自动收起。
- 点击侧栏或按 Enter/Space 打开完整面板；点击图标或桌宠打开快速对话并固定。桌宠同时保留打招呼反应与拖动能力，悬浮桌宠不自动弹出对话。
- 点击固定按钮切换保持展开。输入、添加材料、拖入、截图或明确展开保护当前交互；系统文件选择和失焦不会收掉已固定的内容。选择文字只在窗口聚焦时暂缓收起，取消选择或切去其他程序后释放临时预览，不将选择自动当成固定。
- “收起”回到当前侧栏/图标/桌宠，保留草稿和材料。Escape 同样收起，焦点回到边缘入口；鼠标未真正离开/返回前，不因几何变化立刻重新弹出。
- “返回主窗口”恢复用户原来的正常尺寸、位置与最大化状态。“更多 → 最小化到任务栏”是真正的 Windows 最小化，任务栏/托盘可恢复，不表示退出或停止工作。
- 形态与造型仍沿用已有产品偏好。固定状态属于本次交互，不作为新的全局后台开关，不改变提醒、活动、模型或文件授权。

## 动效与原生窗口边界

原生 WebView HWND 在伙伴交互期间保持固定大小，CSS只在视图切换时改变目标布局。`syncShape` 丢弃完全相同的几何消息，原生再按最终整数像素、圆角和徽章边缘去重 Region；缩放与位置变化仍重新核对。内容动效不驱动形状重建，不做每帧宿主窗口 resize。

没有改变原窗口恢复、置顶、截图入口、透明区域透传或明确退出的归属。新界面移动截图手柄到输入区，仍须用户拖动、核对预览、明确发送；普通悬浮不采集桌面。

最小化验收暴露了 WinForms 的另一个边界：伙伴移除原生边框后，托管 Form 仍缓存 Sizable 的客户区换算，恢复时会把边框再次计入尺寸。本轮在新 FrameworkShell 的原生 Resize 边界保存最小化前外框矩形，恢复栈结束后单次还原；托盘入口同步收尾，任务栏的原生恢复也走同一约定。校验同一伙伴 owner、关闭/释放和再次最小化，迟到回调不覆盖新窗口状态；不重建 HWND，不干预普通窗口 resize。

## 返回主窗口的绘制顺序（2026-10-26补充）

尺寸恢复正确还不等于转换过程正确。旧路径会先解除Region，此时Form仍使用DWM glass的黑色清除底板，WebView仍透明；最后才恢复的原生背景又是固定启动色`#0d1520`，与实际浅/深页面都不同。JS此前先发原生还原再退出透明页面模式，原生菜单还会抢先还原窗口，扩大了中间态暴露的机会。

现在进入伙伴前采样实际页面的opaque RGB，保存给原生作无JS重连的后备；返回时先切回workspace页面样式，再采样当前底色。原生验证RGB后，将Form与WebView设置为同一不透明页面色、撤销全窗口glass，保持旧Region直到frame/Bounds/WindowState恢复完毕才解除裁剪。原生菜单调用共享workspace转换，脚本不可用时保留原生后备。启动色不再被当成产品主题色；截图排除、同HWND及草稿材料的归属不变。

[WebView2背景属性](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2controller.defaultbackgroundcolor)说明透明页面会露出宿主背景，属性更新也可能异步生效，因此Form底板同样必须先准备好。这里的DOM模式切换和rAF导航回调不等于GPU帧提交确认；不靠固定延时或隐藏窗口来掩盖转换。具体取证与未覆盖的像素范围见[验收记录](framework-v1-validation.md)。

## 提示与截图显示（2026-10-26补充）

按钮和成果名称提示使用挂在body的单例浮层；只把提示矩形作为额外独立Region part纳入同一窗口裁剪，而不把外包矩形整片变成鼠标拦截区域。提示不改surface布局或原生Bounds，450ms停留后显示，可移入阅读，靠近视口边缘自动换位置；点击/滚动/切视图/失焦时隐藏。Escape先关闭提示，再次按下收起伙伴。`role=tooltip`与触发项`aria-describedby`参考[W3C Tooltip模式](https://www.w3.org/WAI/ARIA/apg/patterns/tooltip/)的交互建议。

设置 → 通用 → 桌面窗口的「截图/录屏时隐藏侧边伙伴」保存布尔偏好`mirally.companion.hideFromCapture`，缺省开启。保存后Host广播偏好变更，当前桌面客户端通知壳重读落盘值，立即更新已展开的伙伴；连接恢复和重新进入伙伴也重新应用。主窗口保持普通捕获行为；用户主动拖动截图时临时隐藏伙伴以免遮挡目标，独立于此开关。

底层复用[Windows SetWindowDisplayAffinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)，设置同一HWND的`WDA_EXCLUDEFROMCAPTURE`，因此提示也属于这一窗口。完整排除值自Windows10 2004起支持，依赖DWM和采集软件采用的API；此开关不承诺全部截图/录屏软件遵守。验收读取真实系统affinity与偏好链路，不据此声称所有外部录屏软件已实测。

## 验证

专用真实 DOM 工具 `tools/verify_companion_interaction.mjs` 加载本仓 JS/CSS、品牌和桌宠资源，测试悬浮/点击/输入/固定/键盘/触控/减少动画及草稿归属；边界只替代 native bridge 与最小会话状态。记录 rAF 间隔和截图，断言稳定布局与有界消息，不以机器固定FPS当作通过门槛。

`tools/verify_framework_shell.py` 使用实际产品 Host、原前端和 WebView2，单独验证原生裁剪、命中区域、固定窗口大小、最小化/托盘和返回主窗口。延迟悬浮用同一 WebView2 的 CDP 可信输入，按钮/右键仍用本进程 HWND 消息，裁剪检查读取真实 Form.Region；没有移动系统鼠标。DOM 通过不代替该原生验收。当前结果见[验收记录](framework-v1-validation.md)。

GUI 必须顺序运行；测试只操作自身窗口和隔离数据，不操作用户应用，不发送外部模型请求。全新显示器、不同DPI与长期实际使用仍需后续检验。
