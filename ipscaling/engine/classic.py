"""传统基线算法：最近邻、双线性、双三次、Lanczos、区域平均。

实现说明：
- 双三次 / Lanczos 采用"可分离重采样矩阵"实现（scipy.sparse 稀疏矩阵乘），
  既能自定义三次核系数 a、Lanczos 窗半径 a，也能在缩小路径上把核半径按缩放比扩展，
  等价于 Pillow 的高质量重采样，天然抗走样；
- 缩小前额外做一次与目标采样率匹配的轻微低通（σ ∝ 1/scale），进一步压掉摩尔纹；
- 所有算法以 float32 [0,1] 运算，最终统一裁剪量化。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

import cv2
import numpy as np
from scipy.sparse import csr_matrix

from .base import BaseEngine, prefilter_antialias

_MATRIX_CACHE: Dict[Tuple[int, int, str, float], csr_matrix] = {}
_CACHE_LIMIT = 64


# ---------------------------------------------------------------- 核函数


def cubic_kernel(a: float) -> Callable[[np.ndarray], np.ndarray]:
    """Keys 三次卷积核（a = -0.75 时等价 OpenCV INTER_CUBIC）。"""

    def kernel(distance: np.ndarray) -> np.ndarray:
        x = np.abs(np.asarray(distance, dtype=np.float64))
        out = np.zeros_like(x)
        near = x <= 1.0
        far = (x > 1.0) & (x < 2.0)
        xn = x[near]
        xf = x[far]
        out[near] = ((a + 2.0) * xn - (a + 3.0)) * xn * xn + 1.0
        out[far] = (((xf - 5.0) * xf + 8.0) * xf - 4.0) * a
        return out

    return kernel


def lanczos_kernel(a: float) -> Callable[[np.ndarray], np.ndarray]:
    """窗化 sinc 核。"""

    def kernel(distance: np.ndarray) -> np.ndarray:
        x = np.abs(np.asarray(distance, dtype=np.float64))
        out = np.zeros_like(x)
        inside = x < a
        xi = x[inside]
        out[inside] = np.sinc(xi) * np.sinc(xi / a)
        return out

    return kernel


def _build_matrix(src_len: int, dst_len: int, kernel: Callable, support: float,
                  cache_key: str | None = None) -> csr_matrix:
    """构造 (dst_len, src_len) 的一维重采样稀疏矩阵。"""
    if cache_key is not None:
        cached = _MATRIX_CACHE.get((src_len, dst_len, cache_key, support))
        if cached is not None:
            return cached

    scale = src_len / float(dst_len)
    inv_scale = 1.0 / scale
    # 缩小（scale > 1）时按比例加宽插值核以抗混叠；放大（scale < 1）时核宽保持不变。
    dist_scale = inv_scale if scale > 1.0 else 1.0
    support_eff = support * max(1.0, scale)
    centers = (np.arange(dst_len, dtype=np.float64) + 0.5) * scale - 0.5

    indptr = np.zeros(dst_len + 1, dtype=np.int64)
    index_parts = []
    data_parts = []

    for i, center in enumerate(centers):
        left = int(np.floor(center - support_eff))
        right = int(np.ceil(center + support_eff))
        taps = np.arange(left, right + 1, dtype=np.int64)
        valid = (taps >= 0) & (taps < src_len)
        taps = taps[valid]
        if taps.size == 0:
            taps = np.array([int(np.clip(round(center), 0, src_len - 1))], dtype=np.int64)
            weights = np.array([1.0], dtype=np.float64)
        else:
            weights = np.asarray(kernel((taps - center) * dist_scale), dtype=np.float64)
        total = float(weights.sum())
        if not np.isfinite(total) or abs(total) < 1e-12:
            weights = np.ones_like(weights) / max(1, weights.size)
        else:
            weights = weights / total
        index_parts.append(taps.astype(np.int32))
        data_parts.append(weights.astype(np.float64))
        indptr[i + 1] = indptr[i] + taps.size

    matrix = csr_matrix(
        (np.concatenate(data_parts) if data_parts else np.zeros(0),
         np.concatenate(index_parts) if index_parts else np.zeros(0, dtype=np.int32),
         indptr),
        shape=(dst_len, src_len),
    )
    if cache_key is not None:
        if len(_MATRIX_CACHE) >= _CACHE_LIMIT:
            _MATRIX_CACHE.clear()
        _MATRIX_CACHE[(src_len, dst_len, cache_key, support)] = matrix
    return matrix


def resample_separable(
    image: np.ndarray,
    dst_size: Tuple[int, int],
    kernel: Callable,
    support: float,
    cache_key: str | None = None,
) -> np.ndarray:
    """可分离重采样（先垂直后水平），支持灰度/彩色与任意倍率。"""
    arr = np.asarray(image, dtype=np.float32)
    src_h, src_w = arr.shape[:2]
    dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
    if (src_w, src_h) == (dst_w, dst_h):
        return arr.copy()

    mh = _build_matrix(src_h, dst_h, kernel, support, cache_key)
    mw = _build_matrix(src_w, dst_w, kernel, support, cache_key)

    if arr.ndim == 2:
        vertical = mh.dot(arr)
        horizontal = mw.dot(np.ascontiguousarray(vertical.T))
        return np.ascontiguousarray(horizontal.T, dtype=np.float32)

    channels = []
    for c in range(arr.shape[2]):
        vertical = mh.dot(arr[:, :, c])
        horizontal = mw.dot(np.ascontiguousarray(vertical.T))
        channels.append(np.ascontiguousarray(horizontal.T, dtype=np.float32))
    return np.stack(channels, axis=2)


# ---------------------------------------------------------------- 引擎实现


class NearestEngine(BaseEngine):
    key = "nearest"
    display_name = "最近邻 (Nearest)"
    category = "baseline"
    supports_upscale = True
    supports_downscale = False
    arbitrary_scale = True

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        return cv2.resize(src, (int(dst_size[0]), int(dst_size[1])), interpolation=cv2.INTER_NEAREST)


class BilinearEngine(BaseEngine):
    key = "bilinear"
    display_name = "双线性 (Bilinear)"
    category = "baseline"
    supports_upscale = True
    supports_downscale = True
    arbitrary_scale = True

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        scale_x = dst_w / src.shape[1]
        scale_y = dst_h / src.shape[0]
        if scale_x < 1.0 or scale_y < 1.0:
            # 缩小：抗混叠预滤波 + 面积平均
            src = prefilter_antialias(src, scale_x, scale_y)
            return cv2.resize(src, (dst_w, dst_h), interpolation=cv2.INTER_AREA)
        return cv2.resize(src, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)


class BicubicEngine(BaseEngine):
    key = "bicubic"
    display_name = "双三次 (Bicubic)"
    category = "baseline"
    supports_upscale = True
    supports_downscale = True
    arbitrary_scale = True

    def adaptive_params(self, image: np.ndarray, plan, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = super().adaptive_params(image, plan, params)
        merged.setdefault("a", -0.75)
        merged["a"] = float(np.clip(float(merged["a"]), -1.0, 0.0))
        return merged

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        a = float(params.get("a", -0.75))
        scale_x = dst_w / src.shape[1]
        scale_y = dst_h / src.shape[0]
        if scale_x < 1.0 - 1e-9 or scale_y < 1.0 - 1e-9:
            src = prefilter_antialias(src, scale_x, scale_y)
        return resample_separable(src, (dst_w, dst_h), cubic_kernel(a), 2.0, cache_key=f"cubic{a:.3f}")


class LanczosEngine(BaseEngine):
    key = "lanczos"
    display_name = "Lanczos"
    category = "baseline"
    supports_upscale = True
    supports_downscale = True
    arbitrary_scale = True

    def adaptive_params(self, image: np.ndarray, plan, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = super().adaptive_params(image, plan, params)
        merged["a"] = int(np.clip(int(merged.get("a", 3)), 1, 8))
        return merged

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        a = float(int(params.get("a", 3)))
        scale_x = dst_w / src.shape[1]
        scale_y = dst_h / src.shape[0]
        if scale_x < 1.0 - 1e-9 or scale_y < 1.0 - 1e-9:
            src = prefilter_antialias(src, scale_x, scale_y)
        return resample_separable(src, (dst_w, dst_h), lanczos_kernel(a), a, cache_key=f"lanczos{int(a)}")


class AreaEngine(BaseEngine):
    key = "area"
    display_name = "区域平均 (Area)"
    category = "baseline"
    supports_upscale = False
    supports_downscale = True
    arbitrary_scale = True

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        return cv2.resize(src, (dst_w, dst_h), interpolation=cv2.INTER_AREA)
