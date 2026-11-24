"""生成 delivery_manifest.json：交付清单与自校验指纹。

清单内容：算法目录、模型版本与权重哈希、依赖版本、入口文件、目录结构、构建时间。
GUI 与测试通过它做版本自检（见 ipscaling.utils.config / tests/test_config_and_assets.py）。

运行::

    .venv\\Scripts\\python.exe tools\\export_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402

VERSION = "1.0.0"
MANAGED_DIRS = [
    "ipscaling/engine",
    "ipscaling/gui",
    "ipscaling/utils",
    "configs",
    "weights",
    "data",
    "results",
    "logs",
    "dist",
    "tools",
    "tests",
]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def main() -> int:
    ctx = bootstrap.prepare(verbose=False)
    registry = ctx.registry

    from ipscaling.engine.base import ScalePlan

    probe_plan = ScalePlan.create((640, 480), scale=2.0)
    algorithms = []
    for item in registry.catalog():
        engine = registry.get(item["key"])
        algorithms.append(
            {
                "key": item["key"],
                "display_name": engine.display_name,
                "category": engine.category,
                "supports_upscale": engine.supports_upscale,
                "supports_downscale": engine.supports_downscale,
                "integer_scales": sorted(engine.integer_scales) if engine.integer_scales else "any",
                "arbitrary_scale": engine.arbitrary_scale,
                "internal_tiling": engine.internal_tiling,
                "available": engine.is_available(probe_plan),
                "unavailable_reason": engine.unavailable_reason(probe_plan) or "",
            }
        )

    models = []
    index_path = ROOT / "weights" / "model_index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8")).get("models", {})
        for name, meta in sorted(index.items()):
            path = ROOT / "weights" / name
            models.append(
                {
                    "file": name,
                    "scale": meta.get("scale"),
                    "family": meta.get("family"),
                    "params": meta.get("params"),
                    "size_kb": meta.get("size_kb"),
                    "sha256_head": sha256_of(path) if path.exists() else "",
                    "description": meta.get("description", ""),
                }
            )

    dependencies = {}
    lock_path = ROOT / ".setup.lock"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for name in (
            "numpy",
            "opencv-python-headless",
            "PyQt5",
            "onnxruntime",
            "scipy",
            "scikit-image",
            "pillow",
        ):
            version = lock.get("packages", {}).get(name) or lock.get("packages", {}).get(name.lower())
            if version:
                dependencies[name] = version

    manifest = {
        "name": "Detail-Preserving Image Upscaling System",
        "package": "ipscaling",
        "version": VERSION,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "entrypoint": {
            "double_click": "Start.cmd",
            "launcher": "launcher.ps1",
            "environment_setup": "setup.ps1",
            "gui_main": "app.py",
            "bootstrap": "bootstrap.py",
            "environment_lock": ".setup.lock",
        },
        "algorithms": algorithms,
        "models": models,
        "dependencies": dependencies,
        "directories": [d for d in MANAGED_DIRS if (ROOT / d).exists()],
        "features": {
            "zero_config_launch": True,
            "dark_gui": "PyQt5 深色主题，左侧参数 / 中间画布 / 右侧指标",
            "multi_algorithm_compare": True,
            "batch_processing": True,
            "auto_tiling": True,
            "downscale_antialiasing": True,
            "report_export": ["markdown", "csv", "comparison png"],
        },
        "perf_targets": {
            "1080p_classic_ms": 2000,
            "tiling_trigger_mp": ctx.config.performance_tile_threshold_mp()
            if hasattr(ctx.config, "performance_tile_threshold_mp")
            else 12.0,
        },
    }

    out = ROOT / "delivery_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"交付清单已生成：{out}")
    print(f"  算法 {len(algorithms)} 个 / 模型 {len(models)} 个 / 依赖 {len(dependencies)} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
