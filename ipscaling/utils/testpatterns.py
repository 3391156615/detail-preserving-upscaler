"""内置合成测试图案（不再依赖磁盘上的 samples/ 目录）。

所有图案均由代码即时生成，可直接用于 GUI 冒烟、权重拟合与质量评测：

- ``checkerboard``  棋盘格 + 1 像素斜线，观察插值走样与锯齿
- ``zoneplate``     频率扫描图，观察不同频段的响应与摩尔纹
- ``texture``       多尺度叠加纹理，模拟自然高频细节
- ``edge_chart``    斜边与圆环图，观察边缘锐度与振铃
- ``photo``         程序化合成的「照片」，含渐变、色块、细节与噪声

用法::

    from ipscaling.utils.testpatterns import ensure_patterns
    paths = ensure_patterns(DATA_DIR / "patterns")   # 写入磁盘并返回路径
    images = sample_images()                          # 仅在内存中使用
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np

SIZE = 512


def checkerboard(size: int = SIZE, cells: int = 16) -> np.ndarray:
    """黑白棋盘格 + 1 像素斜线。"""
    cell = max(1, size // cells)
    board = np.zeros((size, size), np.uint8)
    for y in range(cells):
        for x in range(cells):
            board[y * cell:(y + 1) * cell, x * cell:(x + 1) * cell] = (
                255 if (x + y) % 2 == 0 else 20
            )
    board = cv2.resize(board, (size, size), interpolation=cv2.INTER_NEAREST)
    for i in range(size):
        j = int(i * 0.5) % size
        board[i, j] = 255
    return cv2.cvtColor(board, cv2.COLOR_GRAY2BGR)


def zoneplate(size: int = SIZE) -> np.ndarray:
    """频率扫描（zone plate）。"""
    coords = np.arange(size) - size / 2.0
    xx, yy = np.meshgrid(coords, coords)
    r2 = xx ** 2 + yy ** 2
    pattern = 0.5 + 0.5 * np.cos(r2 * (2 * np.pi) / (size * 8.0))
    gray = (pattern * 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def texture(size: int = SIZE) -> np.ndarray:
    """多尺度随机纹理。"""
    rng = np.random.default_rng(20241123)
    base = rng.random((size, size)).astype(np.float32)
    layers = [
        cv2.GaussianBlur(base, (0, 0), sigma) for sigma in (1.0, 2.0, 5.0, 12.0)
    ]
    mixed = 0.45 * layers[0] + 0.25 * layers[1] + 0.2 * layers[2] + 0.1 * layers[3]
    gray = ((mixed - mixed.min()) / (float(np.ptp(mixed)) + 1e-6) * 255).astype(np.uint8)
    return cv2.applyColorMap(gray, cv2.COLORMAP_BONE)


def edge_chart(size: int = SIZE) -> np.ndarray:
    """斜边 + 同心圆 + 文字标注，用于观察边缘与振铃。"""
    canvas = np.full((size, size, 3), 240, np.uint8)
    for index, slope in enumerate((0.05, 0.15, 0.3)):
        y0 = 40 + index * 110
        pts = np.array([[0, y0], [size, y0 + int(size * slope)]], np.int32)
        cv2.polylines(canvas, [pts], False, (25, 25, 25), 2, cv2.LINE_AA)
    cv2.circle(canvas, (size - 90, 90), 60, (30, 30, 200), 2, cv2.LINE_AA)
    cv2.circle(canvas, (size - 90, 90), 40, (30, 30, 200), 1, cv2.LINE_AA)
    cv2.circle(canvas, (size - 90, 90), 20, (30, 30, 200), 1, cv2.LINE_AA)
    for row in range(3):
        for col in range(6):
            cv2.circle(canvas, (40 + col * 26, size - 120 + row * 26), 3,
                       (20, 20, 20), -1, cv2.LINE_AA)
    cv2.putText(canvas, "IPSCALING", (24, size - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (15, 15, 15), 2, cv2.LINE_AA)
    return canvas


def photo(size: int = SIZE) -> np.ndarray:
    """程序化合成的自然图像（渐变天空 + 主体色块 + 细节 + 噪声）。"""
    rng = np.random.default_rng(2026)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    u = xx / max(1, size - 1)
    v = yy / max(1, size - 1)

    # 天空/背景渐变
    image = np.zeros((size, size, 3), np.float32)
    image[..., 0] = 90 + 130 * (1.0 - v)          # B
    image[..., 1] = 120 + 110 * (1.0 - v * 0.7)   # G
    image[..., 2] = 200 - 90 * v                  # R

    # 大面积柔和主体（模拟前景物体）
    cx, cy, radius = 0.42 * size, 0.58 * size, 0.26 * size
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    mask = np.clip(1.0 - (dist - radius * 0.75) / (radius * 0.45), 0.0, 1.0)
    image[..., 0] = image[..., 0] * (1 - mask) + 60 * mask
    image[..., 1] = image[..., 1] * (1 - mask) + 95 * mask
    image[..., 2] = image[..., 2] * (1 - mask) + 150 * mask

    # 高频细节：细线网格与散点（检验锐度与锯齿）
    for k in range(6):
        y0 = int(size * (0.14 + 0.13 * k))
        cv2.line(image, (int(size * 0.06), y0), (int(size * 0.94), y0),
                 (245, 245, 245), 1, cv2.LINE_AA)
    for _ in range(140):
        px = int(rng.integers(0, size))
        py = int(rng.integers(0, size))
        color = tuple(float(c) for c in rng.integers(30, 250, 3))
        cv2.circle(image, (px, py), int(rng.integers(1, 4)), color, -1, cv2.LINE_AA)

    # 轻微噪声，避免 PSNR 出现无限大
    image += rng.normal(0.0, 2.0, image.shape).astype(np.float32)
    return np.clip(image, 0, 255).astype(np.uint8)


PATTERN_BUILDERS = {
    "checkerboard.png": checkerboard,
    "zoneplate.png": zoneplate,
    "texture.png": texture,
    "edge_chart.png": edge_chart,
    "photo.png": photo,
}


def sample_images(size: int = SIZE) -> Dict[str, np.ndarray]:
    """返回名称 -> BGR 图像的字典（不写磁盘）。"""
    return {name: builder(size) for name, builder in PATTERN_BUILDERS.items()}


def ensure_patterns(directory: Path | str, size: int = SIZE) -> Dict[str, Path]:
    """把内置图案写入 directory（已存在且非空则直接复用），返回名称 -> 路径。"""
    from ipscaling.utils.archive import save_image_unicode

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for name, builder in PATTERN_BUILDERS.items():
        path = target / name
        if not path.exists() or path.stat().st_size == 0:
            save_image_unicode(path, builder(size))
        paths[name] = path
    return paths


def sample_paths(directory: Path | str, limit: int | None = None,
                 size: int = SIZE) -> List[Path]:
    """写入并返回图案路径列表（按名称排序，可按数量截断）。"""
    paths = sorted(ensure_patterns(directory, size).values(), key=lambda p: p.name)
    if limit:
        paths = paths[:limit]
    return paths
