"""算法引擎单元测试：接口统一性、尺寸正确性、数值合法性、方向支持。"""

from __future__ import annotations

import numpy as np
import pytest

from ipscaling.utils import evaluator

ALGORITHM_KEYS = ["nearest", "bilinear", "bicubic", "lanczos", "area", "nedi", "guided", "onnx"]


def test_catalog_covers_required_algorithm_groups(registry):
    """至少 3 种基线 + 2 种传统细节保持 + 1 种深度学习。"""
    catalog = {item["key"]: item for item in registry.catalog()}
    assert {"nearest", "bilinear", "bicubic", "lanczos"} <= set(catalog)
    assert {"nedi", "guided"} <= set(catalog)
    assert "onnx" in catalog

    categories = [item["category"] for item in catalog.values()]
    assert sum(c == "baseline" for c in categories) >= 3
    assert sum(c == "edge" for c in categories) >= 2
    assert sum(c == "deep" for c in categories) >= 1


@pytest.mark.parametrize("key", ALGORITHM_KEYS)
def test_upscale_output_size_and_dtype(registry, color_image, key):
    result = registry.run_with_fallback(key, color_image, scale=2)
    out = result.image
    assert out.shape[:2] == (color_image.shape[0] * 2, color_image.shape[1] * 2)
    assert out.dtype == np.uint8
    assert out.shape[2] == 3
    assert out.min() >= 0 and out.max() <= 255


@pytest.mark.parametrize("key", ["nearest", "bilinear", "bicubic", "lanczos", "guided"])
def test_non_integer_scale_supported(registry, color_image, key):
    result = registry.run_with_fallback(key, color_image, scale=1.7)
    out = result.image
    expected_h = int(round(color_image.shape[0] * 1.7))
    expected_w = int(round(color_image.shape[1] * 1.7))
    assert out.shape[:2] == (expected_h, expected_w)


def test_bicubic_matches_opencv_reference(registry, color_image):
    """经典基线实现须与 OpenCV 参考实现一致（非整数倍时允许插值核差异）。"""
    import cv2

    out = registry.run_with_fallback("bicubic", color_image, scale=2.0).image
    reference = cv2.resize(
        color_image,
        (color_image.shape[1] * 2, color_image.shape[0] * 2),
        interpolation=cv2.INTER_CUBIC,
    )
    assert evaluator.psnr(reference, out) > 35.0


@pytest.mark.parametrize("key", ["bicubic", "lanczos", "nedi", "guided"])
def test_flat_image_stays_flat(registry, key):
    """平坦区域不应出现超出原值范围的振铃溢出（边缘保持的前提）。"""
    flat = np.full((60, 80, 3), 117, dtype=np.uint8)
    out = registry.run_with_fallback(key, flat, scale=2.0).image
    assert int(out.min()) >= 112 and int(out.max()) <= 122


@pytest.mark.parametrize("key", ["bicubic", "lanczos", "area"])
def test_downscale_has_antialiasing(registry, color_image, key):
    """缩小必须带抗走样预滤波：细密条纹缩小时不应产生严重摩尔纹（方差不应异常放大）。"""
    stripes = np.zeros((200, 200, 3), dtype=np.uint8)
    stripes[:, ::2] = 255  # 1 像素周期条纹，直接抽样会退化为纯色或强摩尔纹

    result = registry.run_with_fallback(key, stripes, scale=0.5)
    out = result.image
    assert out.shape[:2] == (100, 100)

    nearest = registry.run_with_fallback("nearest", stripes, scale=0.5).image
    assert float(out.std()) <= float(nearest.std()) + 1.0
    assert float(out.mean()) == pytest.approx(float(nearest.mean()), abs=12.0)


def test_grayscale_input_is_preserved(registry, gray_image):
    result = registry.run_with_fallback("bicubic", gray_image, scale=2)
    assert result.image.ndim == 2
    assert result.image.shape == (gray_image.shape[0] * 2, gray_image.shape[1] * 2)


def test_area_upscale_falls_back_instead_of_crashing(registry, color_image):
    result = registry.run_with_fallback("area", color_image, scale=2, fallback="bicubic")
    assert result.image.shape[:2] == (color_image.shape[0] * 2, color_image.shape[1] * 2)
    assert result.fallback_from in {"area", None}


def test_engine_metadata_is_exposed(registry):
    for key in ALGORITHM_KEYS:
        engine = registry.get(key)
        info = engine.info()
        assert info["key"] == key
        assert info["name"]
        assert isinstance(info["category"], str)
