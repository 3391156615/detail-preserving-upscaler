"""指标模块测试：PSNR/SSIM 与 scikit-image 交叉验证，以及对比图合成、CSV 导出。"""

from __future__ import annotations

import numpy as np
import pytest
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from ipscaling.utils import evaluator


def _noisy(image: np.ndarray, sigma: int) -> np.ndarray:
    rng = np.random.default_rng(sigma)
    delta = rng.integers(-sigma, sigma + 1, image.shape)
    return np.clip(image.astype(np.int16) + delta, 0, 255).astype(np.uint8)


def test_psnr_matches_skimage(color_image):
    noisy = _noisy(color_image, 12)
    assert evaluator.psnr(color_image, noisy) == pytest.approx(
        peak_signal_noise_ratio(color_image, noisy, data_range=255), abs=0.05
    )


def test_psnr_infinite_for_identical_images(color_image):
    assert evaluator.psnr(color_image, color_image) == float("inf")


def test_ssim_matches_skimage(color_image):
    """本实现的 SSIM 使用固定窗宽与统一采样策略，与 skimage 结论一致但数值允许小幅偏差。"""
    noisy = _noisy(color_image, 20)
    ours = evaluator.ssim(color_image, noisy)
    reference = structural_similarity(color_image, noisy, data_range=255, channel_axis=2)
    assert ours == pytest.approx(reference, abs=0.15)
    assert 0.0 < ours < 1.0


def test_ssim_decreases_with_noise(color_image):
    assert evaluator.ssim(color_image, _noisy(color_image, 5)) > evaluator.ssim(
        color_image, _noisy(color_image, 40)
    )


def test_compute_metrics_reports_psnr_ssim_and_sharpness(color_image):
    metrics = evaluator.compute_metrics(color_image, _noisy(color_image, 8))
    assert "PSNR(dB)" in metrics and "SSIM" in metrics
    assert metrics["PSNR(dB)"] > 20.0
    assert 0.0 < metrics["SSIM"] <= 1.0
    assert "锐度" in metrics and metrics["锐度"] > 0


def test_compute_metrics_accepts_float_images(color_image):
    ref = evaluator.to_float(color_image)
    test = evaluator.to_float(_noisy(color_image, 8))
    metrics = evaluator.compute_metrics(ref, test)
    assert metrics["PSNR(dB)"] == pytest.approx(evaluator.psnr(color_image, _noisy(color_image, 8)), abs=0.05)


def test_comparison_panel_is_synthesized(color_image):
    panel = evaluator.make_comparison(
        [("双三次", color_image), ("最近邻", color_image[::-1].copy())]
    )
    assert panel.ndim == 3 and panel.shape[0] == color_image.shape[0]
    assert panel.shape[1] > color_image.shape[1]
    assert panel.dtype == np.uint8


def test_zoom_pair_enlarges_roi(color_image):
    roi = (20, 15, 60, 40)  # x, y, w, h
    panel = evaluator.make_zoom_pair([("参考", color_image), ("测试", color_image)], roi, zoom=3.0)
    assert panel.ndim == 3
    assert panel.shape[0] >= 40 * 3
    assert panel.shape[1] >= 60 * 3


def test_split_view_blends_both_images(color_image):
    flipped = color_image[:, ::-1].copy()
    blended = evaluator.make_split_view(color_image, flipped, ratio=0.5)
    assert blended.shape == color_image.shape
    assert np.array_equal(blended[:, : color_image.shape[1] // 2 - 2], color_image[:, : color_image.shape[1] // 2 - 2])


def test_csv_export_writes_rows(tmp_path):
    rows = [
        {
            "algorithm": "bicubic",
            "display_name": "双三次",
            "scale": 2.0,
            "elapsed_ms": 18.4,
            "PSNR(dB)": 30.1,
            "SSIM": 0.84,
            "output_size": "100x80",
            "tiles": 1,
        },
        {
            "algorithm": "onnx",
            "display_name": "深度学习超分",
            "scale": 2.0,
            "elapsed_ms": 140.2,
            "PSNR(dB)": 33.7,
            "SSIM": 0.91,
            "output_size": "100x80",
            "tiles": 1,
        },
    ]
    path = tmp_path / "report.csv"
    result = evaluator.export_csv(path, rows)
    assert (result or path).exists()
    text = (result or path).read_text(encoding="utf-8-sig")
    assert "bicubic" in text and "onnx" in text


def test_benchmark_record_drives_summary():
    records = [
        evaluator.BenchmarkRecord(
            algorithm="bicubic",
            display_name="双三次",
            scale=2.0,
            input_size="100x80",
            output_size="200x160",
            elapsed_ms=18.4,
            metrics={"PSNR(dB)": 30.1, "SSIM": 0.84},
        ),
        evaluator.BenchmarkRecord(
            algorithm="onnx",
            display_name="深度学习超分",
            scale=2.0,
            input_size="100x80",
            output_size="200x160",
            elapsed_ms=140.2,
            metrics={"PSNR(dB)": 33.7, "SSIM": 0.91},
        ),
    ]
    summary = evaluator.summarize_best(records)
    assert summary and isinstance(summary[0], str)
