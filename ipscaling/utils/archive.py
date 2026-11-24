"""结果归档：按日期 / 算法名分目录保存图像、对比图与实验报告。

目录约定（results/ 下）：
    results/20261123/bicubic/lena_bicubic_x2.png
    results/20261123/bicubic/lena_bicubic_x2_comparison.png
    results/20261123/benchmark_20261123_142501.csv
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from .evaluator import export_csv, rows_to_markdown

_SAFE_PATTERN = re.compile(r"[^\w\-.]+", re.UNICODE)


def safe_name(text: str, default: str = "output") -> str:
    """清洗文件名（保留中文、字母数字、下划线、点、连字符）。"""
    cleaned = _SAFE_PATTERN.sub("_", str(text)).strip("._-")
    return cleaned or default


def format_scale_label(scale_x: float, scale_y: Optional[float] = None) -> str:
    """把倍率格式化成文件名片段，如 x2、x1.5、x2x3。"""
    def _fmt(value: float) -> str:
        return f"{value:.2f}".rstrip("0").rstrip(".") if abs(value - round(value)) > 1e-6 else str(int(round(value)))

    if scale_y is None or abs(scale_x - scale_y) < 1e-6:
        return f"x{_fmt(scale_x)}"
    return f"x{_fmt(scale_x)}y{_fmt(scale_y)}"


def resolve_target_dir(
    root: Path | str,
    algorithm_key: str,
    group_by_date: bool = True,
    group_by_algorithm: bool = True,
    timestamp: Optional[_dt.datetime] = None,
) -> Path:
    target = Path(root)
    stamp = timestamp or _dt.datetime.now()
    if group_by_date:
        target = target / stamp.strftime("%Y%m%d")
    if group_by_algorithm:
        target = target / safe_name(algorithm_key, "misc")
    target.mkdir(parents=True, exist_ok=True)
    return target


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """避免覆盖同名结果：追加 _1、_2 后缀。"""
    candidate = directory / f"{stem}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def save_image(
    root: Path | str,
    algorithm_key: str,
    image: np.ndarray,
    source_stem: str,
    scale_x: float,
    scale_y: Optional[float] = None,
    extension: str = "png",
    jpeg_quality: int = 95,
    group_by_date: bool = True,
    group_by_algorithm: bool = True,
) -> Optional[Path]:
    """保存处理结果，返回实际写入路径（失败返回 None，绝不抛出）。"""
    try:
        directory = resolve_target_dir(root, algorithm_key, group_by_date, group_by_algorithm)
        ext = extension.lower().lstrip(".")
        stem = safe_name(f"{source_stem}_{algorithm_key}_{format_scale_label(scale_x, scale_y)}")
        target = unique_path(directory, stem, f".{ext}")
        params: List[int] = []
        if ext in ("jpg", "jpeg"):
            params = [int(cv2.IMWRITE_JPEG_QUALITY), int(np.clip(jpeg_quality, 1, 100))]
        ok = cv2.imwrite(str(target), image, params)
        return target if ok else None
    except (OSError, cv2.error):
        return None


def save_array(root: Path | str, sub_dir: str, filename: str, image: np.ndarray) -> Optional[Path]:
    try:
        directory = Path(root) / safe_name(sub_dir, "misc")
        directory.mkdir(parents=True, exist_ok=True)
        target = unique_path(directory, safe_name(Path(filename).stem), Path(filename).suffix or ".png")
        ok = cv2.imwrite(str(target), image)
        return target if ok else None
    except (OSError, cv2.error):
        return None


def save_report(
    root: Path | str,
    rows: Sequence[Dict[str, Any]],
    title: str = "实验报告",
    meta: Optional[Dict[str, Any]] = None,
    sub_dir: str = "",
) -> Dict[str, str]:
    """导出 Markdown + CSV 报告，返回 {markdown: path, csv: path}。"""
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = Path(root) / (safe_name(sub_dir, "") if sub_dir else "")
    directory.mkdir(parents=True, exist_ok=True)

    outputs: Dict[str, str] = {}
    md_path = directory / f"report_{stamp}.md"
    lines = [f"# {title}", "", f"- 生成时间：{_dt.datetime.now():%Y-%m-%d %H:%M:%S}"]
    for key, value in (meta or {}).items():
        lines.append(f"- {key}：{value}")
    lines += ["", "## 指标明细", "", rows_to_markdown(rows), ""]
    try:
        md_path.write_text("\n".join(lines), encoding="utf-8")
        outputs["markdown"] = str(md_path)
    except OSError:
        pass

    csv_path = directory / f"benchmark_{stamp}.csv"
    if export_csv(csv_path, rows):
        outputs["csv"] = str(csv_path)
    return outputs


def load_image(path: Path | str) -> np.ndarray:
    """读取图像（支持中文路径）。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"图像不存在：{file_path}")
    data = np.fromfile(str(file_path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"无法解码图像：{file_path}")
    return image


def save_image_unicode(path: Path | str, image: np.ndarray, quality: int = 95) -> bool:
    """支持中文路径的保存。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ext = target.suffix.lower() or ".png"
    params: List[int] = []
    if ext in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(np.clip(quality, 1, 100))]
    ok, buffer = cv2.imencode(ext, image, params)
    if not ok:
        return False
    buffer.tofile(str(target))
    return True
