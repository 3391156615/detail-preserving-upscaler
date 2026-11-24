"""性能基准：验证「1080P 传统算法 < 2s」与「4K 自动分块」两项硬指标。

运行::

    .venv\\Scripts\\python.exe tools\\bench_perf.py
    .venv\\Scripts\\python.exe tools\\bench_perf.py --json data\\bench.json

输出每个算法在 1080P / 4K 上的耗时、分块数、吞吐量，并给出指标通过与否。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402

BUDGET_SECONDS = 2.0  # 1080P 传统算法耗时上限
LEGACY_KEYS = ("nearest", "bilinear", "bicubic", "lanczos", "area", "nedi", "guided")


def synthetic(width: int, height: int, seed: int = 7) -> np.ndarray:
    """构造兼顾平滑区与丰富边缘的测试图（渐变 + 棋盘 + 噪声纹理）。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    base = (0.5 + 0.35 * np.sin(xx / 37.0) * np.cos(yy / 29.0)) * 255.0
    checker = (((xx // 16).astype(np.int32) + (yy // 16).astype(np.int32)) % 2) * 60.0
    noise = rng.normal(0, 12, (height, width))
    gray = np.clip(base + checker - 30.0 + noise, 0, 255).astype(np.uint8)
    blue = np.roll(gray, 3, axis=1)
    green = np.roll(gray, 1, axis=0)
    return np.dstack([blue, green, gray])


def measure(registry, key: str, image, scale: float) -> dict:
    start = time.perf_counter()
    result = registry.run_with_fallback(key, image, scale=scale)
    elapsed = (time.perf_counter() - start) * 1000.0
    out = result.image
    return {
        "key": key,
        "name": registry.get(key).display_name,
        "elapsed_ms": round(elapsed, 1),
        "tiles": int(getattr(result, "tiles", 1) or 1),
        "fallback_from": getattr(result, "fallback_from", None),
        "out": f"{out.shape[1]}x{out.shape[0]}",
        "megapixels": round(float(getattr(result, "megapixels", 0.0)), 2),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="性能基准")
    parser.add_argument("--json", type=Path, default=None, help="把结果写入 JSON")
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args(argv)

    ctx = bootstrap.prepare(verbose=False)
    registry = ctx.registry

    report: dict[str, list[dict]] = {}
    for label, (w, h) in (("1080P", (1920, 1080)), ("4K", (3840, 2160))):
        image = synthetic(w, h)
        keys = [k for k in registry.keys() if label == "1080P" or k in LEGACY_KEYS]
        rows = [measure(registry, key, image, args.scale) for key in keys]
        report[label] = rows
        print(f"\n[{label}] {w}x{h}  放大 {args.scale}x")
        print(f"  {'算法':<22}{'耗时(ms)':>10}{'分块':>6}{'输出':>14}{'回退':>18}")
        for row in rows:
            print(
                f"  {row['name']:<22}{row['elapsed_ms']:>10.1f}{row['tiles']:>6}"
                f"{row['out']:>14}{(row['fallback_from'] or '-'):>18}"
            )

    small = synthetic(960, 540)
    down = measure(registry, "bicubic", small, 0.5)
    print(f"\n[缩小] 960x540 → 0.5x  双三次 + 预滤波 {down['elapsed_ms']:.1f} ms  输出 {down['out']}")

    legacy_1080 = [r for r in report["1080P"] if r["key"] in LEGACY_KEYS]
    slowest = max(legacy_1080, key=lambda r: r["elapsed_ms"])
    tiled_4k = [r for r in report["4K"] if r["tiles"] > 1]
    ok_speed = slowest["elapsed_ms"] <= BUDGET_SECONDS * 1000
    print(
        f"\n指标1 1080P 传统算法最慢 {slowest['name']} {slowest['elapsed_ms']:.0f} ms "
        f"≤ {BUDGET_SECONDS * 1000:.0f} ms → {'PASS' if ok_speed else 'FAIL'}"
    )
    print(
        f"指标2 4K 触发自动分块：{len(tiled_4k)}/{len(report['4K'])} 个算法 "
        f"→ {'PASS' if tiled_4k else 'FAIL'}"
    )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"scale": args.scale, "report": report}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已写入 {args.json}")
    return 0 if ok_speed and tiled_4k else 1


if __name__ == "__main__":
    raise SystemExit(main())
