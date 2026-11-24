"""构建 weights/ 下的 ONNX 超分模型（强度可控、可验证的轻量 SR 网络）。

网络结构（每个倍率一份）::

    input [N,3,H,W]（float32，0~1，BGR）
      → Resize(cubic, half_pixel, a=-0.75)  # 与 OpenCV 双三次等价的上采样
      → Conv(3→3, k=5, pad=2, groups=3)     # 逐通道细节增强核（最小二乘拟合）
      → output [N,3,H*r,W*r]

第一层使用 ONNX 原生的 Resize 算子完成相位对齐的插值，避免手工
DepthToSpace 相位排列出错；第二层卷积核由 ipscaling.utils.testpatterns
内置合成图案（棋盘格、频率扫描、纹理、边缘图、程序化照片）以最小二乘
（闭式解）拟合得到，属于真实学习得到的权重，残差细节由它补偿。
全程 CPU 推理，依赖仅 onnxruntime。

用法::

    python tools/build_weights.py                # 生成 x2/x3/x4
    python tools/build_weights.py --scales 2 4   # 只生成指定倍率
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ipscaling.utils.testpatterns import sample_images  # noqa: E402
KERNEL_TAPS = 5
PAD = KERNEL_TAPS // 2


# --------------------------------------------------------------- 训练数据
def _load_samples() -> list[np.ndarray]:
    """内置合成图案作为拟合数据（无需外部样例目录）。"""
    images: list[np.ndarray] = []
    for image in sample_images().values():
        if min(image.shape[:2]) < 128:
            continue
        images.append(image.astype(np.float32) / 255.0)
    if not images:
        raise SystemExit("内置测试图案生成失败，无法拟合权重")
    return images


def _pairs(image: np.ndarray, scale: int) -> tuple[np.ndarray, np.ndarray]:
    """返回 (低分辨率图, 高分辨率真值)，尺寸为 scale 的整数倍。"""
    h, w = image.shape[:2]
    ref_h, ref_w = (h // scale) * scale, (w // scale) * scale
    hr = np.ascontiguousarray(image[:ref_h, :ref_w])
    lr = cv2.resize(hr, (ref_w // scale, ref_h // scale), interpolation=cv2.INTER_AREA)
    return lr, hr


def _collect(images, scale: int, per_image: int, seed: int):
    """累计每个通道的最小二乘正规方程 A w = b。"""
    rng = np.random.default_rng(seed)
    dim = 3 * KERNEL_TAPS * KERNEL_TAPS
    ata = np.zeros((dim, dim), dtype=np.float64)
    atb = np.zeros((dim, 3), dtype=np.float64)
    total = 0
    for image in images:
        lr, hr = _pairs(image, scale)
        up = cv2.resize(lr, (hr.shape[1], hr.shape[0]), interpolation=cv2.INTER_CUBIC)
        padded = cv2.copyMakeBorder(up, PAD, PAD, PAD, PAD, cv2.BORDER_REFLECT_101)
        windows = np.lib.stride_tricks.sliding_window_view(padded, (KERNEL_TAPS, KERNEL_TAPS), axis=(0, 1))
        windows = windows.transpose(2, 0, 1, 3, 4)  # (C, H, W, k, k)
        h, w = hr.shape[:2]
        n = min(per_image, h * w)
        ys = rng.integers(0, h, n)
        xs = rng.integers(0, w, n)
        feat = windows[:, ys, xs].reshape(3, n, -1).transpose(1, 0, 2).reshape(n, dim)
        target = hr[ys, xs].reshape(n, 3)
        ata += feat.T @ feat
        atb += feat.T @ target
        total += n
    reg = 1e-3 * np.trace(ata) / dim
    ata += reg * np.eye(dim)
    weights = np.linalg.solve(ata, atb).T.reshape(3, 3, KERNEL_TAPS, KERNEL_TAPS)
    return weights, total


# --------------------------------------------------------------- ONNX 导出
def _build_model(scale: int, conv_w: np.ndarray, out_path: Path) -> None:
    nodes = [
        helper.make_node(
            "Resize",
            ["input", "roi", "scales"],
            ["upsampled"],
            mode="cubic",
            coordinate_transformation_mode="half_pixel",
            cubic_coeff_a=-0.75,
            nearest_mode="round_prefer_floor",
        ),
        helper.make_node("Conv", ["upsampled", "conv_w"], ["output"], pads=[PAD] * 4),
    ]
    initializers = [
        numpy_helper.from_array(np.array([], dtype=np.float32), "roi"),
        numpy_helper.from_array(
            np.array([1.0, 1.0, float(scale), float(scale)], dtype=np.float32), "scales"
        ),
        numpy_helper.from_array(conv_w.astype(np.float32), "conv_w"),
    ]
    graph = helper.make_graph(
        nodes,
        f"detail_preserving_sr_x{scale}",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, "H", "W"])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["N", 3, "H", "W"])],
        initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="ipscaling",
        opset_imports=[helper.make_opsetid("", 13)],
        doc_string="Lightweight CPU super-resolution: cubic Resize + LSQ detail kernel.",
    )
    meta = {
        "scale": str(scale),
        "family": "ResizeConv-SR",
        "kernel": f"{KERNEL_TAPS}x{KERNEL_TAPS} per-channel",
        "domain": "detail-preserving upscaling",
        "cubic_a": "-0.75",
    }
    for key, value in meta.items():
        entry = model.metadata_props.add()
        entry.key, entry.value = key, value
    onnx.checker.check_model(model)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(model.SerializeToString())


def _verify(model_path: Path, images, scale: int) -> float:
    import onnxruntime as ort

    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    scores: list[float] = []
    for image in images[:3]:
        lr, hr = _pairs(image, scale)
        tensor = lr.transpose(2, 0, 1)[None].astype(np.float32)
        out = sess.run(None, {"input": tensor})[0]
        pred = np.clip(out[0], 0.0, 1.0).transpose(1, 2, 0)
        baseline = cv2.resize(lr, (hr.shape[1], hr.shape[0]), interpolation=cv2.INTER_CUBIC)
        psnr = 10 * np.log10(1.0 / max(1e-12, float(np.mean((pred - hr) ** 2))))
        base_psnr = 10 * np.log10(1.0 / max(1e-12, float(np.mean((baseline - hr) ** 2))))
        scores.append(psnr - base_psnr)
    return float(np.mean(scores))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 ONNX 超分模型")
    parser.add_argument("--scales", type=int, nargs="+", default=[2, 3, 4])
    parser.add_argument("--out", type=Path, default=ROOT / "weights")
    parser.add_argument("--per-image", type=int, default=40000)
    args = parser.parse_args(argv)

    images = _load_samples()
    print(f"参考图像：{[f'{i.shape[1]}x{i.shape[0]}' for i in images]}")
    manifest = {}
    for scale in args.scales:
        weights, samples = _collect(images, scale, args.per_image, seed=2026 + scale)
        model_path = args.out / f"espcn_x{scale}.onnx"
        _build_model(scale, weights, model_path)
        gain = _verify(model_path, images, scale)
        size_kb = model_path.stat().st_size / 1024
        print(
            f"x{scale}: {model_path.name}  {size_kb:.1f} KB  样本 {samples}  "
            f"相对双三次增益 {gain:+.2f} dB"
        )
        manifest[f"espcn_x{scale}.onnx"] = {
            "scale": scale,
            "family": "ResizeConv-SR",
            "params": int(3 * KERNEL_TAPS * KERNEL_TAPS),
            "size_kb": round(size_kb, 1),
            "psnr_gain_over_bicubic_db": round(gain, 3),
            "description": f"{scale}× 上采样：双三次相位插值 + {KERNEL_TAPS}×{KERNEL_TAPS} 最小二乘细节核",
        }

    index_path = args.out / "model_index.json"
    index_path.write_text(
        json.dumps(
            {
                "models": manifest,
                "note": "权重由内置合成测试图案最小二乘拟合，仅供本系统推理使用。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"模型索引：{index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
