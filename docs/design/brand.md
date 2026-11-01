# 品牌与图标

更新：2026-10-31，Asia/Shanghai。状态：现行。资源清单见[mirally/assets/README.md](../../mirally/assets/README.md)。

## 名称与含义

**Mirally = MIRA + ally**，两词共用一个 a；ally 意为伙伴、盟友。MIRA 的完整名称由用户确认：Machine Intelligence Research and Applications Lab，中国科学技术大学 MIRA 实验室。

产品定位：来自 MIRA 的个人智能伙伴。产品表达为「懂你的意思，与你一起成事。」启动标语为「与你并肩，让想法向前。」

这是已获用户认可的命名方向，不是商标或域名可用性结论。商标、域名、同类产品和应用商店名称仍需有效查重。

## 正式图标

当前正式图标为**相伴 M**：两个蓝青渐变的人形通过柔和流线相连，形成 M，呼应 Mirally 与 MIRA，同时表达伙伴和协作。早期合翼 A、合翼 M 和其他探索方案均已停用，仅保留在 Git 历史中。

| 项目 | 说明 |
| --- | --- |
| 正式名称 | 相伴 M / Companion M |
| 轮廓 | 两个圆形头部与相连的流线身体构成 M |
| 颜色 | 蓝青渐变；界面继续使用银白、深灰和少量蓝色强调 |
| 使用位置 | 窗口、任务栏、开屏、侧边伙伴、首次与失败状态、安装清单 |

图标设计原则：轮廓先于材质；轻微立体层次只辅助辨识，避免厚重塑料感和多余装饰；检视浅深背景与小尺寸轮廓。当前没有单独设计单色版本。

## 资源与重建

- 矢量母版：`mirally/assets/mirally.svg`（路径、椭圆和渐变，无嵌入位图）。
- 透明运行时导出：`mirally.png`（1254 × 1254 RGBA）。
- 窗口/任务栏图标：`mirally.ico`（16–256 px 多尺寸）。
- 尺寸导出：`mirally-{size}.png`，含 512 px。
- 开屏、伙伴、快捷开始与主题资源位于 `mirally/assets/control-ui-*`，由自有桌面壳和 `mirally-product/src/presentation.mjs` 提供给前端。

重建图标：

```powershell
.\.venv\Scripts\python.exe tools\render_brand_vector.py
```

渲染使用本机 Microsoft Edge 以 3 倍分辨率输出，再用 Lanczos 下采样；运行时不需要 SVG 渲染器。仓库中的 SVG 足以从新 checkout 重建，不能依赖本机 `output/` 或 `docs/` 中的临时设计产物。

## 来源维护

- 正式图标、提示词和说明文档已纳入仓库；本地对比页、原图和校验记录不随 Git 上传。
- 上游组件名、偏好键和协议标识保持原样；品牌替换只覆盖产品展示资源，不做机械文本替换。
- 许可证、第三方声明和上游来源说明继续保留，见[前端来源](../../frontend/control-ui/UPSTREAM.md)与[第三方声明](../../frontend/control-ui/THIRD_PARTY_NOTICES.md)。
