"""GUI 缩放口径单元测试：确保预览与后台处理共用同一份尺寸换算。

覆盖历史缺陷：
1. 直接缩放 + 4× 时输出必须是真实 4 倍尺寸（曾被自参照评估钉在原尺寸，放大看似无效）；
2. 缩小预览不得裁剪原图（曾按放大方向裁剪，左侧只剩原图一角并被拉伸）。
"""

from __future__ import annotations

from ipscaling.gui.scale_plan import (
    PSNR_CAP_DB,
    direct_reference,
    direct_size,
    eval_crop_size,
    plan_sizes,
    prepare_inputs,
    preview_input,
    reference_basis,
    use_eval,
)


def test_direct_size_upscale_and_downscale():
    assert direct_size(1024, 1023, 4.0) == (4096, 4092)
    assert direct_size(1020, 1020, 4.0) == (4080, 4080)
    assert direct_size(1020, 1020, 0.25) == (255, 255)
    assert direct_size(1, 1, 0.25) == (1, 1)  # 不允许出现 0 尺寸


def test_eval_crop_size_is_integer_multiple_for_int_scale():
    w, h = eval_crop_size(1024, 1023, 4.0)
    assert w % 4 == 0 and h % 4 == 0
    assert w <= 1024 and h <= 1023


def test_use_eval_only_for_upscale():
    assert use_eval(4.0, "eval") is True
    assert use_eval(1.5, "eval") is True
    assert use_eval(0.25, "eval") is False
    assert use_eval(4.0, "direct") is False


def test_plan_sizes_direct_returns_real_target():
    dst, ref = plan_sizes(1024, 1023, 4.0, "direct")
    assert dst == (4096, 4092)
    assert ref is None


def test_plan_sizes_eval_downscale_equals_direct():
    assert plan_sizes(1024, 1023, 0.25, "eval") == plan_sizes(1024, 1023, 0.25, "direct")


def test_prepare_inputs_direct_passes_full_image(color_image):
    """直接缩放：送算法的仍是完整原图，且必须同时构造同尺寸参考基准（指标通路不能断）。"""
    h, w = color_image.shape[:2]
    reference, work, dst = prepare_inputs(color_image, 4.0, "direct")
    assert work is color_image
    assert dst == direct_size(w, h, 4.0)
    assert reference is not None
    assert reference.shape[:2] == (dst[1], dst[0])


def test_prepare_inputs_downscale_never_crops(color_image):
    h, w = color_image.shape[:2]
    reference, work, dst = prepare_inputs(color_image, 0.25, "eval")
    assert work.shape[:2] == (h, w)  # 缩小必须使用完整原图
    assert dst == direct_size(w, h, 0.25)
    assert reference is not None  # 缩小也要有参考基准，否则 PSNR/SSIM 全为“—”
    assert reference.shape[:2] == (dst[1], dst[0])


def test_prepare_inputs_eval_upscale_builds_reference(color_image):
    h, w = color_image.shape[:2]
    reference, work, dst = prepare_inputs(color_image, 2.0, "eval")
    assert reference is not None and dst == (reference.shape[1], reference.shape[0])
    assert work.shape[1] == reference.shape[1] // 2
    assert work.shape[0] == reference.shape[0] // 2


def test_preview_input_matches_algorithm_input(color_image):
    h, w = color_image.shape[:2]
    preview, dst, cropped = preview_input(color_image, 0.25, "eval")
    assert cropped is False
    assert preview.shape[:2] == (h, w)
    assert dst == direct_size(w, h, 0.25)

    preview, dst, cropped = preview_input(color_image, 2.0, "eval")
    assert cropped is True
    _, work, work_dst = prepare_inputs(color_image, 2.0, "eval")
    assert dst == work_dst
    assert preview.shape[:2] == (dst[1], dst[0])


def test_direct_reference_matches_target_size(color_image):
    h, w = color_image.shape[:2]
    for scale in (0.25, 4.0):
        dst = direct_size(w, h, scale)
        ref = direct_reference(color_image, dst)
        assert (ref.shape[1], ref.shape[0]) == dst
        assert ref.dtype == color_image.dtype


def test_reference_basis_text_differs_by_strategy(color_image):
    assert "自参照" in reference_basis(4.0, "eval")
    assert "降采样基准" in reference_basis(0.25, "direct")
    assert "上采样基准" in reference_basis(4.0, "direct")


def test_direct_metrics_have_psnr_and_ssim(color_image):
    """回归：直接缩放下 compute_metrics 必须拿到参考真值，PSNR/SSIM/清晰度都有数值。"""
    import numpy as np

    from ipscaling.utils import evaluator

    for scale in (0.25, 4.0):
        reference, work, dst = prepare_inputs(color_image, scale, "direct")
        assert reference is not None
        # 轻微扰动模拟算法输出，断言语义为“有限且合理”的指标值
        out = np.clip(
            reference.astype(np.int16) + np.random.default_rng(0).integers(-6, 7, reference.shape),
            0, 255,
        ).astype(reference.dtype)
        metrics = evaluator.compute_metrics(reference, out)
        for key in ("PSNR(dB)", "SSIM", "MAE", "锐度"):
            assert metrics.get(key) is not None
        assert 20.0 < metrics["PSNR(dB)"] < PSNR_CAP_DB
        assert 0.0 < metrics["SSIM"] <= 1.0
