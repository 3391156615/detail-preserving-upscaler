"""缩放口径统一模块。

GUI 的预览侧（左侧输入图、目标尺寸标签）与后台处理侧（_Task）必须共用同一份
「倍率 + 输入策略 → 输出尺寸 / 实际输入图」的口径，否则会出现两类典型问题：

1. 标签写着 4× 的目标尺寸 4096×4092，实际输出却是原图附近的尺寸（放大看似无效）；
2. 缩小时按放大方向裁掉原图，左侧预览只剩左上角一小块并被拉伸成整块画布。

本模块把这些换算集中在一处，供两处共同调用。
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

Strategy = str

# 直接缩放策略下用「理想重采样基准」当作参考真值：放大用 Lanczos4（高质量、带振铃），
# 缩小用面积平均（抗混叠低通）。二者都不与内置算法逐像素等价，因此各算法仍能得到
# 可比较的有限指标值；万一与基准完全一致，PSNR 会趋于无穷，统一按该上限记录。
PSNR_CAP_DB = 99.0
_BASIS_UPSCALE = cv2.INTER_LANCZOS4
_BASIS_DOWNSCALE = cv2.INTER_AREA


def direct_size(src_w: int, src_h: int, scale: float) -> Tuple[int, int]:
    """直接缩放策略下的目标尺寸（真实放大/缩小到该尺寸）。"""
    return max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))


def eval_crop_size(src_w: int, src_h: int, scale: float) -> Tuple[int, int]:
    """自参照评估的裁剪尺寸。

    整数倍率时把评估区域裁剪成恰好 scale 倍，保证 NEDI / ONNX 等仅支持整数倍率
    的算法也能参与对比；非整数倍率则按近似尺寸评估。
    """
    if abs(scale - round(scale)) < 1e-6:
        factor = max(1, int(round(scale)))
        return max(1, src_w // factor) * factor, max(1, src_h // factor) * factor
    lr_w = max(1, int(round(src_w / scale)))
    lr_h = max(1, int(round(src_h / scale)))
    ref_w = max(1, min(src_w, int(round(lr_w * scale))))
    ref_h = max(1, min(src_h, int(round(lr_h * scale))))
    return ref_w, ref_h


def use_eval(scale: float, strategy: Strategy) -> bool:
    """自参照评估只在放大且倍率大于 1 时才有意义（缩小没有真值可参照）。"""
    return strategy == "eval" and scale > 1.0


def reference_basis(scale: float, strategy: Strategy) -> str:
    """参考基准的中文说明（写进日志与界面，说明指标是相对谁算出来的）。"""
    if use_eval(scale, strategy):
        return "自参照真值（原图裁剪后降采样）"
    if scale < 1.0:
        return "面积平均降采样基准（理想低通）"
    return "Lanczos4 上采样基准（高质量重采样）"


def direct_reference(image: np.ndarray, dst_size: Tuple[int, int]) -> np.ndarray:
    """直接缩放策略下的参考基准图：把原图重采样到目标尺寸，作为 PSNR/SSIM 的真值。

    缩小用 INTER_AREA（抗混叠），放大用 INTER_LANCZOS4（高质量）。返回图与算法输出
    同尺寸，保证指标可直接比对，无需再做尺寸对齐。
    """
    dst_w, dst_h = max(1, int(dst_size[0])), max(1, int(dst_size[1]))
    src_h, src_w = image.shape[:2]
    shrinking = dst_w < src_w or dst_h < src_h
    interpolation = _BASIS_DOWNSCALE if shrinking else _BASIS_UPSCALE
    resized = cv2.resize(image, (dst_w, dst_h), interpolation=interpolation)
    return np.ascontiguousarray(resized)


def plan_sizes(
    src_w: int, src_h: int, scale: float, strategy: Strategy
) -> Tuple[Tuple[int, int], Optional[Tuple[int, int]]]:
    """返回 (实际输出尺寸, 参考真值尺寸或 None)。"""
    if use_eval(scale, strategy):
        ref = eval_crop_size(src_w, src_h, scale)
        return ref, ref
    return direct_size(src_w, src_h, scale), None


def prepare_inputs(
    image: np.ndarray, scale: float, strategy: Strategy
) -> Tuple[Optional[np.ndarray], np.ndarray, Tuple[int, int]]:
    """按策略构造（参考图, 送算法的输入图, 目标尺寸）。

    自参照评估（放大）：先把原图降采样成 LR，再放大回原尺寸，从而得到可用 PSNR/SSIM
    的真值；此时**输出尺寸等于评估区尺寸，并不会放大**。
    直接缩放（含评估策略下的缩小）：输入就是完整原图，直接放大/缩小到目标尺寸；
    参考图按「理想重采样基准」在同一目标尺寸上构造，保证 PSNR/SSIM 仍有真值可比。
    """
    src_h, src_w = image.shape[:2]

    if use_eval(scale, strategy):
        ref_w, ref_h = eval_crop_size(src_w, src_h, scale)
        reference = np.ascontiguousarray(image[:ref_h, :ref_w])
        if abs(scale - round(scale)) < 1e-6:
            factor = max(1, int(round(scale)))
            lr_w, lr_h = max(1, ref_w // factor), max(1, ref_h // factor)
        else:
            lr_w = max(1, int(round(src_w / scale)))
            lr_h = max(1, int(round(src_h / scale)))
        lr = cv2.resize(reference, (lr_w, lr_h), interpolation=cv2.INTER_AREA)
        return reference, lr, (ref_w, ref_h)

    dst_w, dst_h = direct_size(src_w, src_h, scale)
    dst_size = (dst_w, dst_h)
    return direct_reference(image, dst_size), image, dst_size


def preview_input(
    image: np.ndarray, scale: float, strategy: Strategy
) -> Tuple[np.ndarray, Tuple[int, int], bool]:
    """左侧预览应显示的输入图。

    返回 (预览图, 目标尺寸, 是否为评估裁剪区)。除「自参照评估 + 放大」外一律显示
    完整原图，避免缩小或直接缩放时把原图裁掉一角后拉伸显示。
    """
    if use_eval(scale, strategy):
        reference, _work, dst = prepare_inputs(image, scale, strategy)
        return reference, dst, True
    return image, direct_size(image.shape[1], image.shape[0], scale), False
