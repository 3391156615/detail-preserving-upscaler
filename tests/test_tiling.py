"""分块（Tiling）测试：大图自动分块、结果与整图处理一致、不产生内存爆量。"""

from __future__ import annotations

import numpy as np
import pytest

from ipscaling.utils import evaluator


def _large_image(width: int = 2600, height: int = 1800) -> np.ndarray:
    rng = np.random.default_rng(99)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    gray = 128 + 100 * np.sin(xx / 41.0) * np.cos(yy / 33.0) + rng.normal(0, 5, (height, width))
    return np.clip(np.dstack([gray, gray * 0.9, gray * 1.05]), 0, 255).astype(np.uint8)


@pytest.mark.parametrize("key", ["bicubic", "guided"])
def test_large_image_uses_tiling_and_keeps_size(registry, key):
    image = _large_image()
    result = registry.run_with_fallback(key, image, scale=2)
    assert result.image.shape[:2] == (image.shape[0] * 2, image.shape[1] * 2)
    assert result.tiles > 1, "大图应触发自动分块"


def test_tiled_output_matches_direct_resize(registry):
    """分块结果与 OpenCV 整图双三次结果高度一致（块边界无缝）。"""
    image = _large_image(1400, 900)
    tiled = registry.run_with_fallback("bicubic", image, scale=2).image
    import cv2

    direct = cv2.resize(image, (image.shape[1] * 2, image.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
    assert evaluator.psnr(direct, tiled) > 32.0


def test_small_image_single_tile(registry, color_image):
    result = registry.run_with_fallback("bicubic", color_image, scale=2)
    assert result.tiles == 1


def test_onnx_tiled_onnx_runs_on_large_input(registry):
    """ONNX 在中等大图上分块推理不崩溃，且输出尺寸正确。"""
    from ipscaling.engine.base import ScalePlan

    engine = registry.get("onnx")
    plan = ScalePlan.create((1200, 800), scale=2.0)
    if not engine.is_available(plan):
        pytest.skip(f"ONNX 模型不可用：{engine.unavailable_reason(plan)}")
    image = _large_image(1200, 800)
    result = registry.run_with_fallback("onnx", image, scale=2)
    assert result.image.shape[:2] == (1600, 2400)
    assert result.fallback_from is None
