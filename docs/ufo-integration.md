# Mirally 与 UFO 执行器适配

Mirally 的唯一产品界面是 OpenClaw Control UI。UFO 通过 `mirally-ufo` 插件和内部子代理执行主 Agent 委派的 Windows 桌面任务；产品不再维护专用 UFO 界面、第二套对话入口或旧任务会话库。委派链路与部署边界见 [UFO 桌面任务子代理](ufo-openclaw-agent.md)。

## 产品适配层

| 路径 | 责任 |
| --- | --- |
| `integrations/openclaw/ufo/` | Harness 注册、宿主授权和模型路由、原生历史与控制回调 |
| `mirally/core/execution.py` | 任务请求、服务回调与终态结果协议 |
| `mirally/runtime/ufo/adapter.py` | 一次 `normal` Session、适配安装与收尾 |
| `mirally/runtime/ufo/worker.py` | 私有 stdio 请求与桌面任务服务 |
| `mirally/runtime/ufo/controls.py` | 模型/工具预算、动作准入、输入版本失效和重观察 |
| `mirally/runtime/ufo/observation.py` | 产品进程、窗口与控件排除及捕获恢复 |
| `mirally/runtime/ufo/model_bridge.py` | 临时宿主模型服务注入与恢复 |
| `mirally/runtime/ufo/compat.py` | 有作用域的内核兼容适配 |
| `mirally/runtime/ufo/events.py` | 阶段记录和保守结果判定 |

UFO 内核负责 Host/App 状态机和实际 Windows 工具。配置只维护在 `config/mirally/ufo/`，worker 使用宿主准备的一次性模型路由。`UFO/` 为只读 submodule，不修改其源码或配置副本；升级 gitlink 需明确授权。

## 执行边界

多个桌面任务串行取得桌面互斥量。模型、工具和动作准入均核对宿主执行权、停止与输入版本；旧规划失效后需重新观察。兼容补丁和临时模型服务在退出或异常时恢复，清理等待真实在途调用，不把取消 Future 当成系统调用已停止。

截图、窗口枚举和 UIA 尽力排除已登记产品前端；异常恢复不抢焦点。已执行的系统操作不能撤销，DLL、COM 和第三方工具阻塞可能需要 worker 进程收尾。当前产品不提供旧专用界面的暂停/接管控制。

进度事件记录阶段和等待原因。只有最终业务答案及已验证结果可用于完成判定；窗口切换、动作计数或早期中间结果不能单独证明成功。部分完成、拒绝、失败及取消回到原生子会话，由主 Agent 汇总。

## 范围与验证

现有适配复用 UFO `normal` Session，尚未进一步改造成主 Agent 逐个调用的 SDK 桌面工具集；Galaxy、Follower 和跨设备编排不在产品范围。Activity 和 Memory 由 OpenClaw 管理，UFO 不直接消费 Activity。

回归位于根目录 `tests/`，使用临时配置、隔离进程和合成端点核对真实初始化、Host/App 路由、控制回调与恢复。离线链路通过不代表真实模型桌面成功率；真实用户应用、多显示器/DPI 和长阻塞调用需在具体任务授权下验证。当前入口与旧界面删除仅作代码和离线验证。
