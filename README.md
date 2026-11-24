---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 8e84b5d34d88e73908e70a82a7f4a1fc_7ff7fba1e7c411f1961f525400f8a581
    ReservedCode1: PokPZJixF1pfNMFgDBIDEy/F66WhceoE+sBq1r9KdRgKQ/4janG/262fC5VFs6vxtmygzoxge/DHdniRj/tR3nw0Mq9dGuB+u1S+qY/jNSsGIip+kYgG2lopkj+H7NVFwTkArdXyyUqBItD+B4EwKlpjp3QiqvCgL51klZIfVVQhhf/7tOBroeLf82Y=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 8e84b5d34d88e73908e70a82a7f4a1fc_7ff7fba1e7c411f1961f525400f8a581
    ReservedCode2: PokPZJixF1pfNMFgDBIDEy/F66WhceoE+sBq1r9KdRgKQ/4janG/262fC5VFs6vxtmygzoxge/DHdniRj/tR3nw0Mq9dGuB+u1S+qY/jNSsGIip+kYgG2lopkj+H7NVFwTkArdXyyUqBItD+B4EwKlpjp3QiqvCgL51klZIfVVQhhf/7tOBroeLf82Y=
---







# 细节保持图像插值/缩放系统

面向「传统插值 + 边缘导向 + 深度超分」的桌面图像缩放工具，提供统一的算法注册表、自动分块、
多算法对比与指标导出能力。GUI 基于 PyQt5，传统算法 1080P 处理可在亚秒级完成。

## 功能特性

| 能力 | 说明 |
| --- | --- |
| Zero-config 启动 | 双击 `Start.cmd` 自动校验依赖与权重，缺失时调用 `setup.ps1` 安装 |
| 浅色 GUI | 左侧算法与参数面板；主体区域为原图/结果对比画布（缩放平移）；底部为「运行日志」「性能指标与实验报告」两个可切换页签 |
| 8 种算法 | 传统插值 6 种 + 边缘导向 2 种 + ONNX 深度超分 1 种（见下表） |
| 多算法对比 | 同图同倍率并列渲染，输出对比面板与放大细节对 |
| 批量处理 | 目录级批处理，支持多算法并行产出与命名模板 |
| 自动分块 | 超过 12 MP 的输出自动分块（带重叠），避免内存峰值；分块结果无缝拼接 |
| 缩小抗锯齿 | 缩小时先做低通预滤波再做重采样，抑制摩尔纹与混叠 |
| 报告导出 | Markdown / CSV 质量与性能报告，含 PSNR、SSIM、MAE、锐度与时延 |
| 失败回退 | 算法不可用或异常时按配置回退到双三次，并在结果中标注 `fallback_from` |

## 快速开始

```powershell
# 推荐：双击 Start.cmd（自动完成环境准备并启动 GUI）
# 手动方式：
.\setup.ps1                 # 首次运行：创建 .venv、安装依赖、生成样例与权重
.\.venv\Scripts\python.exe app.py
```

命令行工具（均使用项目虚拟环境）：

```powershell
.\.venv\Scripts\python.exe tools\eval_quality.py      # 质量评估：PSNR/SSIM/MAE/锐度 + 对比图
.\.venv\Scripts\python.exe tools\bench_perf.py        # 性能基准：1080P / 4K / 缩小
.\.venv\Scripts\python.exe tools\export_manifest.py   # 重新生成交付清单
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=C:\Temp\pt_run   # 测试套件
```

> Windows 下若 `%TEMP%\pytest-of-*` 因权限清理失败，请显式指定 `--basetemp` 到可写目录。

## 输入策略与输出尺寸

界面「输入策略」决定输出尺寸的口径，默认 **直接缩放**：

| 策略 | 输出尺寸 | 参考基准（reference） | PSNR/SSIM |
| --- | --- | --- | --- |
| 直接缩放我的图片（默认） | 原图 × 倍率，如 1024×1023 选 4× → 4096×4092 | 同尺寸高质量重采样基准：缩小用 INTER_AREA（面积平均，理想低通），放大用 INTER_LANCZOS4 | 有（衡量相对理想重采样的保真度，不是“无真值”） |
| 自参照评估 | 放大时固定在原尺寸评估区（先降采样再放回），缩小时等同直接缩放 | 放大：原图降采样后的自参照真值；缩小：面积平均基准 | 有 |

要点：只有「自参照评估 + 放大」会把输出钉在原尺寸，因为它要拿原图当真值算指标；
该策略下界面会明确标注「评估输出（不放大）」并给出真实倍率尺寸。左侧预览与算法实际输入
保持一致口径——缩小时显示完整原图，不会只裁出一角。左右两侧画布按「相对适配倍数 + 滚动比例」
同步，尺寸差异很大的原图与放大结果也能整幅对照。

指标口径（两种策略都出数值，避免指标面板出现「—」）：

- 参考基准由 `ipscaling/gui/scale_plan.py` 的 `direct_reference()` 统一构造，与算法输出严格同尺寸，
  界面左侧标题显示「参考基准 · W×H」，报告 meta 中的「参考基准」字段说明其定义。
- PSNR 上限按 `PSNR_CAP_DB` 记录：当算法输出与参考基准逐像素一致（如面积平均做缩小）时，
  PSNR 趋于无穷，按上限值显示并在结果备注中注明，不会回退成空值。
- 最近邻不支持缩小，选 0.25× 时会回退双三次并在表格中标注「（回退至 双三次）」，尺寸与其他算法一致；
  每轮对比开始都会递增 run_id 并清空表格，上一轮遗留结果会被丢弃，不会出现某行尺寸与本轮不符。

## 缩放倍率与自定义倍率

左侧「缩放设置」的倍率下拉框内置 0.25×、0.5×、1.5×、2×、3×、4×、6×、8× 八档预设，末项为
**「自定义倍率…」**：

- 下方「自定义」数字框**始终可编辑**，不用先去下拉框里找「自定义倍率…」：直接点进去键入
  0.1–8.0 之间的任意倍率（支持 1.5、2.6、0.75 这类小数），倍率下拉会自动切到「自定义倍率…」。
- 选中「自定义倍率…」后数字框获得焦点并全选原值，敲入即覆盖，**不需要先清空旧数值**。
- 焦点停在下拉框上时敲数字不会被「按首字母跳条目」抢走（敲 2 不会跳回「2× 放大」），
  数字键会直接进入数字框连续输入，输入过程中档位不会被撤销。
- 反向同步：切回任意预设档时数字框同步为该预设值，避免「下拉写 4×、数字框还留 2.6」的错位。
- 数字框每一次改动都会实时刷新下方「输出尺寸：W × H（N×）」标签；输出尺寸按
  `round(原宽 × 倍率) × round(原高 × 倍率)` 计算，与真正处理时的输出严格一致。
  加载/切换图片、切换输入策略时也会同步刷新，不会停留在「目标尺寸：—」。
- 自定义小数倍率对所有算法生效：nedi、ONNX 这类模型在整数倍上走原生实现，非整数倍由基类
  先做分块插值、再末级重采样到目标尺寸；模型或权重不可用时自动回退并在表格备注中标注。

## 输出与保存

- 复选框「**自动保存处理后的图像**」默认勾选：处理完成时自动把处理后的图像写入 `results/`
  目录（目录由配置项 `output.root` 决定，默认 `results`）；取消勾选则只在界面内展示结果、不落盘。
  该选项只影响是否保存图像，配置键名保持不变，旧配置文件可直接沿用。
- 报告与对比图同样输出在 `results/`，可用「打开结果目录」按钮直接查看。

## 目录结构

```
Detail-Preserving Image Upscaling System/
├─ app.py                 # GUI 入口
├─ bootstrap.py           # 环境与注册表引导（字体、日志、权重、配置）
├─ Start.cmd / setup.ps1 / launcher.ps1   # 一键启动与环境准备
├─ configs/default_config.json            # 默认算法参数、主题、分块阈值
├─ ipscaling/
│  ├─ engine/             # 算法实现：插值、边缘导向、ONNX、注册表、分块调度
│  ├─ gui/                # PyQt5 界面：主窗口、画布、底部日志/指标页签、参数面板
│  └─ utils/             # 配置、指标计算、对比图、报告导出、图像 IO
├─ weights/               # espcn_x2/x3/x4.onnx + model_index.json
├─ tools/                 # 评估、基准、权重构建、清单导出、GUI 冒烟
├─ tests/                 # pytest 套件（引擎、指标、分块、配置与资产）
└─ results/               # 报告与对比图输出
```

## 算法清单

| key | 显示名 | 类别 | 放大 | 缩小 | 任意倍率 | 原理要点 |
| --- | --- | --- | --- | --- | --- | --- |
| `nearest` | 最近邻 | baseline | ✓ | | ✓ | 邻域复制，最快，用于对照 |
| `bilinear` | 双线性 | baseline | ✓ | ✓ | ✓ | 2×2 线性权重 |
| `bicubic` | 双三次 | baseline | ✓ | ✓ | ✓ | Keys 三次核，锐度参数可调 |
| `lanczos` | Lanczos | baseline | ✓ | ✓ | ✓ | 可调 a=2/3/4 的 sinc 窗核 |
| `area` | 区域平均 | baseline | ✓ | ✓ | ✓ | 面积加权，缩小时天然抗锯齿 |
| `nedi` | NEDI 边缘导向 | edge | ✓（2/3/4/6/8×） | | | 结构张量估计边缘切线方向 → 沿切线重采样 → 与 Lanczos 按相干度融合 |
| `guided` | 引导滤波上采样 | edge | ✓ | ✓ | ✓ | 低分辨率求保边系数、上采样后组合，再按 `detail_gain` 回注双三次高频 |
| `onnx` | 深度学习超分 | deep | ✓（2/3/4×） | | | ONNX Runtime 推理 ResizeConv-SR 模型，超大图内部按 tile/overlap 切分，异常回退双三次 |

### 边缘导向插值（`nedi`）实现说明

1. 低分辨率域用 Sobel 梯度构造结构张量（`Jxx/Jyy/Jxy` 的 box 平滑半径来自 `window`），
   取垂直于梯度的单位向量作为边缘切线方向 `t`，相干度
   `coh = sqrt((Jxx−Jyy)² + 4Jxy²) / (Jxx+Jyy)` 衡量方向可信度；
2. 高分辨率像素按半像素中心约定定位源坐标 `u`，沿 `±t` 各偏移 `step` 个源像素采样后取均值：
   沿边缘平均、跨边缘不混合，因此不产生锯齿；
3. 与 `Lanczos4` 基线按 `coh × strength` 融合，平坦区域保持插值核锐度；
4. 方向场全部在低分辨率域估计后双线性上采样，1080P 保持毫秒级。

参数：`window`（方向估计窗口，默认 8）、`strength`（导向强度，默认 0.35）、`sharpen`（高频补偿，默认 0）。

## 质量评估（2× 放大，7 张样例平均）

| 算法 | PSNR(dB) | SSIM | 相对双三次 | 耗时(ms) |
| --- | --- | --- | --- | --- |
| 深度学习超分 (ONNX) | 30.554 | 0.9305 | +0.694 | 207.8 |
| Lanczos | 30.284 | 0.9371 | +0.424 | 11.3 |
| 双三次 (Bicubic) | 29.861 | 0.9365 | ±0.000 | 10.7 |
| 区域平均 (Area) | 29.861 | 0.9365 | ±0.000 | 7.3 |
| 引导滤波上采样 | 29.831 | 0.9364 | −0.030 | 17.5 |
| NEDI 边缘导向 | 29.134 | 0.9313 | −0.726 | 12.9 |
| 最近邻 (Nearest) | 27.766 | 0.9128 | −2.095 | 0.8 |
| 双线性 (Bilinear) | 27.079 | 0.9095 | −2.782 | 0.5 |

结论：ONNX 与 Lanczos 在 PSNR 上领先；边缘导向算法在 PSNR 上接近双三次，优势体现在
棋盘/正弦等强方向纹理的锯齿抑制（口径见 `results/reports/quality_detail_*.csv` 的逐图数值）。

## 性能基准

| 场景 | 结果 | 判定 |
| --- | --- | --- |
| 1080P 传统算法最慢（引导滤波 705 ms） | ≤ 2000 ms | PASS |
| 4K（3840×2160 → 7680×4320） | 7/7 传统算法触发自动分块（40 块） | PASS |
| 缩小 960×540 → 480×270 | 双三次 + 预滤波 31.6 ms | 达标 |
| ONNX 1080P 单图推理 | 约 9.2 s（受 CPU 推理限制，建议大图使用传统算法） | 记录 |

分块阈值由 `configs/default_config.json` 的 `tile_threshold_mp` 控制（默认 12 MP）。

## 测试

```
pytest tests -q      # 70 项用例：引擎契约与元数据、指标与 skimage 一致性、
                     # 分块与倍率、GUI 缩放口径（预览与输出尺寸一致）、配置文件与权重资产校验
```

## 已知限制

- `nedi` 仅支持整数倍放大（2/3/4/6/8×），非整数倍请在界面中改用 Lanczos 或引导滤波；
- ONNX 超分在 CPU 上耗时明显（1080P 约 9.2 s），批量任务建议只对关键图使用；
- 分块重叠区按加权羽化拼接，极端强噪声图像在块边界可能出现轻微不连续；
- 报告中的 PSNR 以理想重采样为基准（缩小用 INTER_AREA 面积平均、放大用 INTER_LANCZOS4），
  衡量的是“相对理想重采样的保真度”，与真实相机退化模型存在差异；放大场景下 Lanczos 因同为基准算法会偏高。

## 交付清单

`delivery_manifest.json` 记录版本、入口、8 个算法、3 个 ONNX 模型（含参数规模与 SHA256 前缀）、
依赖版本与性能目标，可随代码一并归档。
*（内容由AI生成，仅供参考）*

## 打包分发给组员

`.venv` 与 `.setup.lock` 记录了创建环境时的绝对路径（见 `.venv\pyvenv.cfg` 的 `home` 字段），**不可跨机器复制**，交付压缩包必须排除二者。

- 一键打包：双击 `PackForDelivery.cmd`（内部调用 `tools/pack_for_delivery.ps1`），产物为 `delivery\DetailPreservingImageUpscalingSystem_portable_<时间戳>.zip`，约 2.5 MB。
- 自动排除：`.venv`、`.venv.broken-*`、`.setup.lock`、`__pycache__`、`.pytest_cache`、`.git`、`logs`、`results`、`*.pyc`、`*.log`，并在压缩后重新打开 zip 校验包内无 `.venv` / `.setup.lock` 残留。
- 权限净化（关键）：打包阶段对暂存目录执行 `attrib -R /S /D` 清除只读属性，再执行 `icacls /reset /T` 移除源机器上的显式 ACL 并恢复继承，因此接收方解压后不会继承"拒绝写入"权限，可直接创建 `.venv`、`logs\` 与 `.setup.lock`。
- 需要保留旧结果时加参数：`tools\pack_for_delivery.ps1 -KeepResults`；可自定义输出：`-Output D:\x\pkg.zip`。
- 打包过程只读源目录：不删除、不移动任何文件，本机 `.venv` 保持可用。
- 接收方解压后双击 `Start.cmd`：启动前会自动清除脚本的网络标记（Zone.Identifier）、探测项目目录写权限，不可写时依次用 `attrib -R /S /D` 与 `icacls /reset` + `icacls /grant` 自动修复，再重建环境；仍失败时会在窗口给出可操作提示（移至本地磁盘、避免 Program Files / OneDrive 目录、取消只读）。
- 路径长度（关键）：Windows 仍有 260 字符路径上限，项目根路径超过约 110 字符时 `venv` 会在 `ensurepip` 阶段失败，日志里只剩一句 `exit code 1`，看起来像权限问题。解压请放在短路径（如 `D:\Upscaler`、桌面根目录），`launcher.ps1 -Check` 与 `setup.ps1` 在超长路径下都会先打印醒目警告。
- 环境自检：`Start.cmd`（`launcher.ps1 -Check`）与 `Diagnose.cmd` 都会输出 Writable / Read-only 字段，便于快速定位权限问题；`setup.ps1` 的验证阶段按模块逐个独立探测（numpy / OpenCV / SciPy / scikit-image / PyQt5 / Pillow），某个可选依赖（如 onnxruntime）缺失或崩溃不会让整次安装失败，只会降级提示。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
