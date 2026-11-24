"""质量评估脚本：对示例图做“下采样-再上采样”往返实验，比较各算法与原图的差距。

用法：
    python tools/eval_quality.py            # 全部示例图，输出表格并写入 results/reports
    python tools/eval_quality.py --quick    # 只跑传统算法，跳过深度学习
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

import bootstrap
from ipscaling.utils import evaluator
from ipscaling.utils.config import DATA_DIR

DEEP_KEYS = {"espcn", "fsrcnn", "onnx", "swinir"}
LEGACY_KEYS = ["nearest", "bilinear", "bicubic", "lanczos", "area", "nedi", "guided"]


def load_samples(limit: int | None = None) -> list[Path]:
    """内置合成测试图案写入 data/eval/patterns 后返回（无需外部样例目录）。"""
    from ipscaling.utils.testpatterns import sample_paths

    return sample_paths(DATA_DIR / "eval" / "patterns", limit=limit)


def evaluate(registry, keys: list[str], files: list[Path], scale: float) -> list[dict]:
    rows: list[dict] = []
    for path in files:
        hr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if hr is None:
            continue
        # 用 INTER_AREA 下采样得到低分辨率输入，模拟真实退化
        lr_size = (max(16, hr.shape[1] // 2), max(16, hr.shape[0] // 2))
        lr = cv2.resize(hr, lr_size, interpolation=cv2.INTER_AREA)
        hr_crop = hr[: lr_size[1] * 2, : lr_size[0] * 2]

        for key in keys:
            try:
                result = registry.run_with_fallback(key, lr, scale=scale)
            except Exception as exc:  # 单个算法失败不影响整体
                rows.append({"image": path.name, "algorithm": key, "psnr": float("nan"),
                             "ssim": float("nan"), "fallback": f"ERROR: {exc}"})
                continue
            out = result.image
            h = min(out.shape[0], hr_crop.shape[0])
            w = min(out.shape[1], hr_crop.shape[1])
            metrics = evaluator.compute_metrics(hr_crop[:h, :w], out[:h, :w])
            rows.append({
                "image": path.name,
                "algorithm": key,
                "display_name": registry.get(key).display_name,
                "psnr": float(metrics["PSNR(dB)"]),
                "ssim": float(metrics["SSIM"]),
                "elapsed_ms": float(result.elapsed_ms),
                "fallback": ",".join(result.fallbacks) if getattr(result, "fallbacks", None) else "",
            })
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}
    for row in rows:
        psnr = row["psnr"]
        if not np.isfinite(psnr):
            continue
        item = stats.setdefault(row["algorithm"], {"algorithm": row["algorithm"],
                                                   "display_name": row.get("display_name", row["algorithm"]),
                                                   "psnr": [], "ssim": [], "elapsed_ms": []})
        item["psnr"].append(psnr)
        item["ssim"].append(row["ssim"])
        item["elapsed_ms"].append(row.get("elapsed_ms", float("nan")))
    summary = []
    for item in stats.values():
        summary.append({
            "algorithm": item["algorithm"],
            "display_name": item["display_name"],
            "mean_psnr": float(np.mean(item["psnr"])),
            "mean_ssim": float(np.mean(item["ssim"])),
            "mean_ms": float(np.nanmean(item["elapsed_ms"])) if item["elapsed_ms"] else float("nan"),
        })
    summary.sort(key=lambda r: r["mean_psnr"], reverse=True)
    return summary


def print_summary(summary: list[dict]) -> None:
    baseline = next((r["mean_psnr"] for r in summary if r["algorithm"] == "bicubic"), None)
    print("\n各算法平均指标（相对双三次的增益）")
    print(f"  {'算法':<22}{'PSNR(dB)':>10}{'SSIM':>10}{'增益(dB)':>10}{'耗时(ms)':>12}")
    for row in summary:
        gain = row["mean_psnr"] - baseline if baseline is not None else float("nan")
        gain_text = f"{gain:+.3f}" if np.isfinite(gain) else "-"
        print(f"  {row['display_name']:<22}{row['mean_psnr']:>10.3f}{row['mean_ssim']:>10.4f}"
              f"{gain_text:>10}{row['mean_ms']:>12.1f}")


def write_reports(rows: list[dict], summary: list[dict]) -> list[Path]:
    reports_dir = ROOT / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    detail_path = reports_dir / f"quality_detail_{stamp}.csv"
    with detail_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["image", "algorithm", "display_name",
                                                "psnr", "ssim", "elapsed_ms", "fallback"])
        writer.writeheader()
        writer.writerows(rows)

    summary_path = reports_dir / f"quality_summary_{stamp}.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["algorithm", "display_name",
                                                "mean_psnr", "mean_ssim", "mean_ms"])
        writer.writeheader()
        writer.writerows(summary)
    return [detail_path, summary_path]


def main() -> int:
    parser = argparse.ArgumentParser(description="插值/超分算法质量评估")
    parser.add_argument("--quick", action="store_true", help="跳过深度学习算法")
    parser.add_argument("--limit", type=int, default=None, help="最多评估的图像数量")
    parser.add_argument("--scale", type=float, default=2.0, help="放大倍率")
    args = parser.parse_args()

    ctx = bootstrap.prepare(verbose=False)
    registry = ctx.registry

    if args.quick:
        keys = [k for k in LEGACY_KEYS if k in registry.keys()]
    else:
        keys = [k for k in registry.keys() if k in LEGACY_KEYS or k in DEEP_KEYS]

    files = load_samples(args.limit)
    if not files:
        print("内置测试图案生成失败，无法评测")
        return 1

    print(f"评估图像 {len(files)} 张，算法 {len(keys)} 个，放大 {args.scale}x")
    rows = evaluate(registry, keys, files, args.scale)
    summary = summarize(rows)
    print_summary(summary)
    for path in write_reports(rows, summary):
        print(f"已写入：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
