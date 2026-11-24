"""客观指标与对比图合成。

包含：
- PSNR / SSIM / MSE / MAE（与 scikit-image 口径一致的高斯窗 SSIM）；
- 高精度耗时统计（time.perf_counter）；
- 多算法对比图合成（含中文标签条、ROI 标记、整体限宽）；
- 实验报告（Markdown 表格 / CSV）生成。
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:  # 中文字体渲染（缺失时自动回退英文）
    from PIL import Image, ImageDraw, ImageFont

    _PIL_AVAILABLE = True
except Exception:  # noqa: BLE001 - Pillow 缺失不应阻断算法主流程
    _PIL_AVAILABLE = False

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)
_font_cache: Dict[int, Any] = {}


# ---------------------------------------------------------------- 基础转换


def to_float(image: np.ndarray) -> np.ndarray:
    """uint8/uint16/float → float32，值域 [0, 1]。"""
    if image.dtype == np.uint8:
        return image.astype(np.float32) / 255.0
    if image.dtype == np.uint16:
        return image.astype(np.float32) / 65535.0
    arr = image.astype(np.float32, copy=False)
    if arr.size and float(np.nanmax(arr)) > 1.5:
        arr = arr / 255.0
    return arr


def to_uint8(image: np.ndarray) -> np.ndarray:
    """float/其它整型 → uint8，带饱和截断。"""
    if image.dtype == np.uint8:
        return image
    arr = np.asarray(image)
    if arr.dtype == np.uint16:
        arr = arr.astype(np.float32) / 257.0
    else:
        arr = arr.astype(np.float32)
        if arr.size and float(np.nanmax(arr)) <= 1.5:
            arr = arr * 255.0
    return np.clip(np.rint(arr), 0, 255).astype(np.uint8)


def ensure_gray(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr
    if arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)


def _match_size(reference: np.ndarray, test: np.ndarray) -> np.ndarray:
    if reference.shape[:2] == test.shape[:2]:
        return test
    return cv2.resize(test, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- 指标


def mse(reference: np.ndarray, test: np.ndarray) -> float:
    ref = to_float(reference).astype(np.float64)
    tst = to_float(_match_size(reference, test)).astype(np.float64)
    return float(np.mean((ref - tst) ** 2))


def psnr(reference: np.ndarray, test: np.ndarray, peak: float = 1.0) -> float:
    """峰值信噪比（dB）。peak 与 to_float 值域保持一致，默认 1.0。"""
    error = mse(reference, test)
    if error <= 1e-12:
        return float("inf")
    return float(10.0 * math.log10((peak ** 2) / error))


def _gaussian_kernel(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    coords = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    kernel = np.exp(-(coords ** 2) / (2.0 * sigma ** 2))
    kernel /= kernel.sum()
    return np.outer(kernel, kernel).astype(np.float64)


def ssim(reference: np.ndarray, test: np.ndarray, win: int = 11, sigma: float = 1.5) -> float:
    """结构相似度（Wang 2004 高斯窗实现，灰度计算）。"""
    ref = to_float(reference)
    tst = to_float(_match_size(reference, test))
    if ref.ndim == 3:
        ref_gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY) if ref.shape[2] == 3 else ref[:, :, 0]
        tst_gray = cv2.cvtColor(tst, cv2.COLOR_BGR2GRAY) if tst.shape[2] == 3 else tst[:, :, 0]
    else:
        ref_gray, tst_gray = ref, tst

    ref_gray = ref_gray.astype(np.float64)
    tst_gray = tst_gray.astype(np.float64)
    if min(ref_gray.shape) < win:
        win = max(3, min(ref_gray.shape) // 2 * 2 + 1)

    kernel = _gaussian_kernel(win, sigma)
    c1 = (0.01 * 1.0) ** 2
    c2 = (0.03 * 1.0) ** 2

    mu_ref = cv2.filter2D(ref_gray, -1, kernel, borderType=cv2.BORDER_REFLECT)
    mu_tst = cv2.filter2D(tst_gray, -1, kernel, borderType=cv2.BORDER_REFLECT)
    mu_ref_sq = mu_ref ** 2
    mu_tst_sq = mu_tst ** 2
    mu_cross = mu_ref * mu_tst

    var_ref = cv2.filter2D(ref_gray ** 2, -1, kernel, borderType=cv2.BORDER_REFLECT) - mu_ref_sq
    var_tst = cv2.filter2D(tst_gray ** 2, -1, kernel, borderType=cv2.BORDER_REFLECT) - mu_tst_sq
    cov = cv2.filter2D(ref_gray * tst_gray, -1, kernel, borderType=cv2.BORDER_REFLECT) - mu_cross

    numerator = (2 * mu_cross + c1) * (2 * cov + c2)
    denominator = (mu_ref_sq + mu_tst_sq + c1) * (var_ref + var_tst + c2)
    return float(np.mean(numerator / np.maximum(denominator, 1e-12)))


def mae(reference: np.ndarray, test: np.ndarray) -> float:
    ref = to_float(reference).astype(np.float64)
    tst = to_float(_match_size(reference, test)).astype(np.float64)
    return float(np.mean(np.abs(ref - tst)))


def sharpness(image: np.ndarray) -> float:
    """拉普拉斯方差，用于量化"细节保持"程度（越高越锐）。"""
    gray = ensure_gray(image)
    return float(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F, ksize=3).var())


def compute_metrics(
    reference: Optional[np.ndarray],
    result: np.ndarray,
    peak: float = 1.0,
    include_sharpness: bool = True,
) -> Dict[str, float]:
    """计算指标集合；reference 为空时只返回无参考指标。"""
    report: Dict[str, float] = {}
    if reference is not None:
        report["PSNR(dB)"] = psnr(reference, result, peak=peak)
        report["SSIM"] = ssim(reference, result)
        report["MAE"] = mae(reference, result)
    if include_sharpness:
        report["锐度"] = sharpness(result)
    return report


# ---------------------------------------------------------------- 计时


@dataclass
class BenchmarkRecord:
    """一次算法执行的完整记录，供右侧指标面板与报告导出使用。"""

    algorithm: str
    display_name: str
    scale: str
    input_size: Tuple[int, int]
    output_size: Tuple[int, int]
    elapsed_ms: float
    metrics: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    tiles: int = 0
    output_path: Optional[str] = None


# ---------------------------------------------------------------- 对比图


def _load_font(size: int = 18):
    if not _PIL_AVAILABLE:
        return None
    if size in _font_cache:
        return _font_cache[size]
    font = None
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, size)
                break
            except OSError:
                continue
    _font_cache[size] = font
    return font


def draw_label(image: np.ndarray, text: str, origin: Tuple[int, int] = (8, 6),
               color: Tuple[int, int, int] = (255, 255, 255), font_size: int = 18) -> np.ndarray:
    """在图像左上角绘制带半透明底衬的标签（支持中文）。"""
    canvas = image.copy()
    if _PIL_AVAILABLE:
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil, "RGBA")
        font = _load_font(font_size)
        if font is not None:
            bbox = draw.textbbox((0, 0), text, font=font)
            pad = 4
            box = (origin[0] - pad, origin[1] - pad, origin[0] + bbox[2] + pad, origin[1] + bbox[3] + pad)
            draw.rectangle(box, fill=(0, 0, 0, 130))
            draw.text(origin, text, font=font, fill=(color[2], color[1], color[0], 255))
            return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    cv2.putText(canvas, text.encode("ascii", "ignore").decode() or "view", (origin[0], origin[1] + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
    return canvas


def make_comparison(
    panels: Sequence[Tuple[str, np.ndarray]],
    max_width: int = 3600,
    gap: int = 6,
    background: Tuple[int, int, int] = (24, 26, 30),
) -> np.ndarray:
    """横向拼接多张图并加标签，返回 BGR 图像。"""
    if not panels:
        raise ValueError("对比图至少需要一张面板")

    height = min(p[1].shape[0] for p in panels)
    prepared: List[np.ndarray] = []
    for title, image in panels:
        img = to_uint8(image)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        if img.shape[0] != height:
            scale = height / img.shape[0]
            img = cv2.resize(img, (max(1, int(round(img.shape[1] * scale))), height), interpolation=cv2.INTER_AREA)
        prepared.append(draw_label(img, title))

    total_width = sum(p.shape[1] for p in prepared) + gap * (len(prepared) - 1)
    canvas = np.full((height, total_width, 3), background, dtype=np.uint8)
    x = 0
    for panel in prepared:
        canvas[:, x:x + panel.shape[1]] = panel
        x += panel.shape[1] + gap

    if canvas.shape[1] > max_width:
        ratio = max_width / canvas.shape[1]
        canvas = cv2.resize(canvas, (max_width, max(1, int(round(canvas.shape[0] * ratio)))),
                            interpolation=cv2.INTER_AREA)
    return canvas


def make_split_view(reference: np.ndarray, result: np.ndarray, ratio: float = 0.5) -> np.ndarray:
    """左右分屏滑动对比（左侧参考、右侧结果），并在分界处画一条高亮线。"""
    ref = to_uint8(reference)
    res = to_uint8(_match_size(reference, result))
    if ref.ndim == 2:
        ref = cv2.cvtColor(ref, cv2.COLOR_GRAY2BGR)
    if res.ndim == 2:
        res = cv2.cvtColor(res, cv2.COLOR_GRAY2BGR)
    split = int(np.clip(ratio, 0.0, 1.0) * ref.shape[1])
    canvas = ref.copy()
    if split < ref.shape[1]:
        canvas[:, split:] = res[:, split:]
    cv2.line(canvas, (split, 0), (split, canvas.shape[0] - 1), (76, 154, 255), 2, cv2.LINE_AA)
    return canvas


def make_zoom_pair(images: Sequence[Tuple[str, np.ndarray]], roi: Tuple[int, int, int, int],
                   zoom: float = 3.0) -> np.ndarray:
    """对同一 ROI 做局部放大并拼接，用于细节对比。"""
    x, y, w, h = [int(v) for v in roi]
    panels: List[Tuple[str, np.ndarray]] = []
    for title, image in images:
        img = to_uint8(image)
        x0 = max(0, min(x, img.shape[1] - 1))
        y0 = max(0, min(y, img.shape[0] - 1))
        x1 = min(img.shape[1], x0 + max(1, w))
        y1 = min(img.shape[0], y0 + max(1, h))
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        if zoom != 1.0:
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * zoom)), max(1, int(crop.shape[0] * zoom))),
                              interpolation=cv2.INTER_NEAREST if zoom >= 3 else cv2.INTER_CUBIC)
        panels.append((title, crop))
    return make_comparison(panels, max_width=4200)


# ---------------------------------------------------------------- 报告


def rows_to_markdown(rows: Sequence[Dict[str, Any]]) -> str:
    """把记录行渲染成 Markdown 表格。"""
    if not rows:
        return "_（本轮无实验记录）_"
    headers: List[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "-")) for h in headers) + " |")
    return "\n".join(lines)


def export_csv(path: Path | str, rows: Sequence[Dict[str, Any]]) -> Optional[Path]:
    if not rows:
        return None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    headers: List[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with open(target, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return target


def summarize_best(records: Iterable[BenchmarkRecord]) -> List[str]:
    """给出简单的结论性描述。"""
    records = list(records)
    if not records:
        return []
    notes: List[str] = []
    fast = min(records, key=lambda r: r.elapsed_ms)
    notes.append(f"最快：{fast.display_name}（{fast.elapsed_ms:.1f} ms）")
    with_psnr = [r for r in records if "PSNR(dB)" in r.metrics and math.isfinite(r.metrics["PSNR(dB)"])]
    if with_psnr:
        best = max(with_psnr, key=lambda r: r.metrics["PSNR(dB)"])
        notes.append(f"PSNR 最高：{best.display_name}（{best.metrics['PSNR(dB)']:.2f} dB）")
    with_ssim = [r for r in records if "SSIM" in r.metrics]
    if with_ssim:
        best = max(with_ssim, key=lambda r: r.metrics["SSIM"])
        notes.append(f"SSIM 最高：{best.display_name}（{best.metrics['SSIM']:.4f}）")
    return notes
