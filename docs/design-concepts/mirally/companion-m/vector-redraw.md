# 相伴 M · 正式矢量母版

## 正式参考图拟合版

[正式 SVG](../../../../mirally/assets/mirally.svg) 按 [1254px 生成参考图](generated-reference.png) 测量外轮廓，以同一轮廓镜像构造两侧，统一头部尺寸、高度和间距。手臂交叠线按原图的曲线位置拟合，分界延伸至主体之外，避免在手部引入额外接缝。没有嵌入位图或外部资源。

渐变在交叠线两侧独立拟合：排除边缘混色像素，以平滑薄板样条曲面代替最近像素延拓，减少交界处的色纹；使用高精度颜色值生成渐变网格。透明 PNG 以 3 倍分辨率渲染 SVG，再以 Lanczos 缩至 1254px，随后统一导出各尺寸 PNG/ICO。外轮廓采用有误差容限的平滑曲线拟合，在手臂内侧转折处提高贴合权重，兼顾平滑边缘与转折形状。

这是对参考图的矢量拟合，不是凭空手绘，也不是逐像素无损复刻；镜像轮廓、边缘平滑和渐变插值会有细小差异。SVG 较复杂，常规界面使用其导出的 PNG/ICO，避免浏览器在小尺寸直接渲染渐变网格时的取整差异。主窗口、浮窗、开屏与任务栏均已使用这套导出资源。

- [新版并排预览](../../../../output/imagegen/mirally-vector-faithful/index.html)
- [透明 PNG](../../../../output/imagegen/mirally-vector-faithful/exports/mirally.png)
- [Windows ICO](../../../../output/imagegen/mirally-vector-faithful/exports/mirally.ico)

设计脚本 [redraw_brand_vector.py](../../../../tools/redraw_brand_vector.py) 使用 Pillow、NumPy、SciPy，仅用于设计阶段，不是运行时依赖。该拟合中的交叠边界专用于当前 1254px 参考图，不能视为任意图标的通用转换器。

普通重建直接从已提交的 SVG 导出；只有调整拟合算法时才需要重新拟合：

```powershell
# 从正式 SVG 生成应用资源，需要本机 Microsoft Edge 和 Pillow
.\.venv\Scripts\python.exe tools/render_brand_vector.py

# 可选：从保留的生成参考图重新拟合 SVG，需要 NumPy 和 SciPy
.\.venv\Scripts\python.exe tools/redraw_brand_vector.py --source docs/design-concepts/mirally/companion-m/generated-reference.png --out mirally/assets/mirally.svg
```

## 初稿（保留对比）

[初稿 SVG](mirally-redrawn.svg) 按双人 M 的视觉结构重新编写，由两个圆形、贝塞尔路径、渐变与裁剪组成，没有嵌入位图、外部图片、脚本或字体依赖。该稿轮廓和色彩未被选用，仅保留作为历史对比。

SVG 可以直接在浏览器或矢量设计工具中打开，修改曲线、颜色、头身间距与留白。PNG 和 ICO 由浏览器渲染结果导出，用于不支持 SVG 的界面与 Windows 任务栏。

- [本地对比页](../../../../output/imagegen/mirally-vector/index.html)：现用位图、矢量稿、深色背景和小尺寸预览。
- [透明 PNG](../../../../output/imagegen/mirally-vector/exports/mirally.png)
- [Windows ICO](../../../../output/imagegen/mirally-vector/exports/mirally.ico)

上述 `output/` 产物是本地预览，不随 Git 上传；SVG 源文件放在设计文档目录中。

原图未观察到可见水印，但这不能证明不存在任何不可见标记。此稿从几何路径重新构造，没有复制原图的像素数据或元数据；不把它描述为已通过所有水印检测，也不将重绘等同于对设计权利的判断。
