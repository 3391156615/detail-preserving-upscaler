"""传统「细节保持」算法：边缘导向插值与引导滤波上采样。

边缘导向插值（本实现的 NEDI 变体，2× 逐级放大）：
1. 在低分辨率域用 Sobel 梯度构造结构张量（张量分量 box 平滑，半径由 window 决定），
   取「垂直于梯度」的单位向量作为边缘切线方向 t，并用相干度
   coh = sqrt((Jxx−Jyy)² + 4Jxy²) / (Jxx+Jyy) 衡量方向的可信程度；
2. 高分辨率像素按半像素中心约定定位其源坐标 u，沿 ±t 各偏移 step 个源像素采样后取均值，
   等价于沿边缘方向平均：跨边缘不混合，因此不产生锯齿，也避免 NEDI 回归核在
   棋盘/正弦纹理上的过冲；
3. 与 Lanczos4 基线按 coh × strength 融合：平坦区域保持插值核的锐度，
   方向明确的边缘处抑制锯齿；
4. 系数/方向场全部在低分辨率域估计并双线性上采样，1080P 保持亚秒级，
   整数倍以外的倍率由基类分块 + 末级重采样处理。

引导滤波上采样（He et al. 2010 的 upsampling 用法）：
- 在低分辨率域计算保边系数 a、b，上采样到高分辨率后再与高分辨率引导图组合，
  避免直接把高频噪声插值放大；
- 为弥补保边滤波的欠锐问题，把双三次基线的相对高频按 detail_gain 回注。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .base import BaseEngine, prefilter_antialias
from .classic import cubic_kernel, lanczos_kernel, resample_separable


# ---------------------------------------------------------------- 通用工具


def _box(image: np.ndarray, radius: int) -> np.ndarray:
    """归一化盒式均值滤波（引导滤波 / 协方差统计共用）。"""
    radius = max(1, int(radius))
    size = radius * 2 + 1
    return cv2.boxFilter(image, -1, (size, size), normalize=True, borderType=cv2.BORDER_REFLECT)


def _resample(image: np.ndarray, dst_size: Tuple[int, int], mode: str = "cubic") -> np.ndarray:
    """上/下采样（带抗锯齿），mode ∈ {cubic, lanczos, linear}。"""
    dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
    if image.shape[1] == dst_w and image.shape[0] == dst_h:
        return image
    if mode == "linear":
        interp = cv2.INTER_AREA if (dst_w < image.shape[1] or dst_h < image.shape[0]) else cv2.INTER_LINEAR
        return cv2.resize(image, (dst_w, dst_h), interpolation=interp)
    if mode == "lanczos":
        if dst_w < image.shape[1] or dst_h < image.shape[0]:
            image = prefilter_antialias(image, dst_w / image.shape[1], dst_h / image.shape[0])
        return resample_separable(image, (dst_w, dst_h), lanczos_kernel(3.0), 3.0, cache_key="lanczos3")
    if dst_w < image.shape[1] or dst_h < image.shape[0]:
        image = prefilter_antialias(image, dst_w / image.shape[1], dst_h / image.shape[0])
    return resample_separable(image, (dst_w, dst_h), cubic_kernel(-0.75), 2.0, cache_key="cubic-0.750")


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return image[:, :, 0]



def _edge_2x(image: np.ndarray, window: int = 8, strength: float = 0.35, step: float = 0.75) -> np.ndarray:
    """单次 2× 边缘导向上采样（结构张量导向版）。

    1. 在低分辨率域用 Sobel 梯度构造结构张量，得到「边缘切线方向」与相干度；
    2. 对每个高分辨率像素，按半像素中心约定定位其源坐标，沿切线方向 +step 与 −step
       两处采样后取均值（沿边缘平滑、跨边缘不混合，避免锯齿）；
    3. 与 Lanczos4 基线按相干度加权融合，平坦区域保持基线锐度，边缘处抑制锯齿。
    """
    arr = np.asarray(image, dtype=np.float32)
    h, w = arr.shape[:2]
    radius = max(1, min(4, int(window) // 2))

    if arr.ndim == 2:
        guide = arr
    elif arr.shape[2] >= 3:
        guide = cv2.cvtColor(arr[..., :3], cv2.COLOR_RGB2GRAY)
    else:
        guide = arr[..., 0]

    gx = cv2.Sobel(guide, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(guide, cv2.CV_32F, 0, 1, ksize=3)
    jxx = _box(gx * gx, radius)
    jyy = _box(gy * gy, radius)
    jxy = _box(gx * gy, radius)
    coherence = np.sqrt((jxx - jyy) ** 2 + 4.0 * jxy ** 2) / (jxx + jyy + 1e-8)
    norm = np.sqrt(gx * gx + gy * gy) + 1e-8
    tangent_x = -gy / norm
    tangent_y = gx / norm

    dst_w, dst_h = w * 2, h * 2
    xs = (np.arange(dst_w, dtype=np.float32) + 0.5) * 0.5 - 0.5
    ys = (np.arange(dst_h, dtype=np.float32) + 0.5) * 0.5 - 0.5
    grid_x = np.tile(xs[None, :], (dst_h, 1)).astype(np.float32)
    grid_y = np.tile(ys[:, None], (1, dst_w)).astype(np.float32)

    tx = cv2.resize(tangent_x, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
    ty = cv2.resize(tangent_y, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
    edge = cv2.resize(coherence, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)

    plus = cv2.remap(arr, grid_x + step * tx, grid_y + step * ty,
                     interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    minus = cv2.remap(arr, grid_x - step * tx, grid_y - step * ty,
                      interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    directional = 0.5 * (plus + minus)
    base = cv2.resize(arr, (dst_w, dst_h), interpolation=cv2.INTER_LANCZOS4)

    alpha = np.clip(edge * float(strength), 0.0, 0.95)
    if arr.ndim == 3:
        alpha = alpha[..., None]
    out = base * (1.0 - alpha) + directional * alpha
    return np.clip(out, 0.0, 1.0).astype(np.float32)



class NediEngine(BaseEngine):
    key = "nedi"
    display_name = "NEDI 边缘导向"
    category = "edge"
    supports_upscale = True
    supports_downscale = False
    arbitrary_scale = False
    # 2/3/4 为典型整数倍；6/8 通过连续 2× 迭代实现
    integer_scales: Tuple[int, ...] = (2, 3, 4, 6, 8)
    MAX_ITERATIONS = 3

    def unavailable_reason(self, plan) -> str:
        reason = super().unavailable_reason(plan)
        if reason:
            return reason
        if plan.direction == "identity":
            return ""
        if not plan.is_integer_scale:
            return (f"NEDI 需要整数倍放大，当前请求为 {plan.scale_label}；"
                    f"可改用「双三次 / Lanczos / 引导滤波」处理非整数倍")
        if int(round(plan.scale_x)) not in self.integer_scales:
            allowed = "、".join(f"{s}x" for s in self.integer_scales)
            return f"NEDI 支持的倍率为 {allowed}，当前为 {plan.scale_label}"
        return ""

    def adaptive_params(self, image: np.ndarray, plan, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = super().adaptive_params(image, plan, params)
        merged["window"] = int(np.clip(int(merged.get("window", 8)), 3, 24))
        merged["strength"] = float(np.clip(float(merged.get("strength", 0.35)), 0.0, 1.0))
        merged["sharpen"] = float(np.clip(float(merged.get("sharpen", 0.0)), 0.0, 1.0))
        # 参数自适应：大倍率时缩小统计窗口，避免过度平滑细节
        max_scale = max(plan.scale_x, plan.scale_y)
        if max_scale >= 3.0 and merged["window"] > 8:
            merged["window"] = 8
        return merged

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        window = int(params.get("window", 8))
        sharpen = float(params.get("sharpen", 0.0))
        strength = float(np.clip(float(params.get("strength", 0.35)), 0.0, 1.0))

        current = np.asarray(src, dtype=np.float32)
        iterations = 0
        while (current.shape[1] < dst_w or current.shape[0] < dst_h) and iterations < self.MAX_ITERATIONS:
            current = _edge_2x(current, window, strength)
            iterations += 1

        if current.shape[1] != dst_w or current.shape[0] != dst_h:
            current = _resample(current, (dst_w, dst_h), "cubic")

        if sharpen > 1e-6:
            blur = cv2.GaussianBlur(current, (0, 0), 1.0, borderType=cv2.BORDER_REFLECT)
            current = current + sharpen * (current - blur)
        return np.clip(current, 0.0, 1.0)


# ---------------------------------------------------------------- 引导滤波


def _guided_coefficients(guide: np.ndarray, source: np.ndarray, radius: int, eps: float):
    """在低分辨率域求解引导滤波的 a、b 系数。"""
    mean_guide = _box(guide, radius)
    mean_source = _box(source, radius)
    var_guide = _box(guide * guide, radius) - mean_guide * mean_guide
    cov = _box(guide * source, radius) - mean_guide * mean_source
    a = cov / (var_guide + eps)
    b = mean_source - a * mean_guide
    return _box(a, radius), _box(b, radius)


class GuidedFilterEngine(BaseEngine):
    key = "guided"
    display_name = "引导滤波上采样"
    category = "edge"
    supports_upscale = True
    supports_downscale = True
    arbitrary_scale = True

    def adaptive_params(self, image: np.ndarray, plan, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = super().adaptive_params(image, plan, params)
        merged["radius"] = int(np.clip(int(merged.get("radius", 8)), 1, 64))
        eps = float(merged.get("eps", 1e-4))
        merged["eps"] = float(np.clip(eps, 1e-8, 1e-1))
        merged["detail_gain"] = float(np.clip(float(merged.get("detail_gain", 0.85)), 0.0, 1.5))
        merged["prefilter"] = str(merged.get("prefilter", "cubic"))
        # 倍率越高，引导半径适当放大以覆盖更大的结构
        scale = max(plan.scale_x, plan.scale_y)
        if scale >= 3.0:
            merged["radius"] = int(np.clip(merged["radius"] + 2 * int(scale // 2), 1, 64))
        return merged

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        radius = int(params.get("radius", 8))
        eps = float(params.get("eps", 1e-4))
        gain = float(params.get("detail_gain", 0.85))
        prefilter = str(params.get("prefilter", "cubic"))

        source = np.asarray(src, dtype=np.float32)
        channels = 1 if source.ndim == 2 else source.shape[2]

        # 缩小：引导滤波不适用，退化为高质量抗锯齿重采样
        if dst_w < source.shape[1] - 1 or dst_h < source.shape[0] - 1:
            return _resample(source, (dst_w, dst_h), "lanczos" if prefilter == "lanczos" else "cubic")

        guide_lr = _to_gray(source)
        guide_hr = _resample(guide_lr, (dst_w, dst_h), prefilter)

        if channels == 1:
            planes = [source]
        else:
            planes = [source[:, :, c] for c in range(channels)]

        outputs: List[np.ndarray] = []
        for plane in planes:
            a_lr, b_lr = _guided_coefficients(guide_lr, plane, radius, eps)
            a_hr = cv2.resize(a_lr, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
            b_hr = cv2.resize(b_lr, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
            guided = a_hr * guide_hr + b_hr

            base = _resample(plane, (dst_w, dst_h), prefilter)
            outputs.append(guided + gain * (base - guided))

        if channels == 1:
            return np.clip(outputs[0], 0.0, 1.0)
        return np.clip(np.stack(outputs, axis=2), 0.0, 1.0)


