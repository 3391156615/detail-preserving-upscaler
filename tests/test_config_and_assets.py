"""配置与工程资产测试：默认配置合法性、目录自举、交付清单完整性。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ipscaling.utils import config  # noqa: E402


def test_default_config_has_required_sections():
    data = config.default_config()
    for section in ("gui", "output", "logging", "performance", "evaluation", "algorithms"):
        assert section in data
    assert data["performance"]["auto_tiling"] is True
    assert data["performance"]["tile_threshold_mp"] > 0
    assert data["output"]["root"].endswith("results")


def test_config_module_paths_exist_after_ensure_directories():
    config.ensure_directories()
    for path in (config.RESULTS_DIR, config.LOGS_DIR, config.DATA_DIR, config.WEIGHTS_DIR):
        assert Path(path).is_dir()


def test_algorithm_catalog_matches_engine_registry(registry):
    catalog_keys = {entry["key"] for entry in config.ALGORITHM_CATALOG}
    registry_keys = set(registry.keys())
    assert catalog_keys == registry_keys
    for key in registry_keys:
        assert config.display_name(key)


def test_default_config_file_is_valid_json():
    payload = json.loads(config.DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    assert payload["output"]["format"] in {"png", "jpg", "jpeg", "tiff", "bmp"}


def test_requirements_contains_no_stray_text():
    """requirements.txt 只能有合法依赖声明与注释，避免 pip 解析失败。"""
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert not line.startswith("（") and "AI生成" not in line
        assert " " not in line or line.startswith("-"), f"可疑依赖行：{line}"


@pytest.mark.parametrize(
    "relative",
    [
        "Start.cmd",
        "launcher.ps1",
        "setup.ps1",
        "app.py",
        "bootstrap.py",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "delivery_manifest.json",
        "configs/default_config.json",
    ],
)
def test_delivery_assets_present(relative):
    assert (ROOT / relative).exists(), f"缺少交付文件：{relative}"


def test_delivery_manifest_lists_algorithms_and_models():
    manifest = json.loads((ROOT / "delivery_manifest.json").read_text(encoding="utf-8"))
    keys = {item["key"] for item in manifest["algorithms"]}
    assert {"nearest", "bilinear", "bicubic", "lanczos", "area", "nedi", "guided", "onnx"} <= keys
    assert manifest["models"], "交付清单必须列出 ONNX 模型版本"
    for package, version in manifest["dependencies"].items():
        assert isinstance(version, str) and version


def test_onnx_models_and_index_are_consistent():
    index_path = ROOT / "weights" / "model_index.json"
    if not index_path.exists():
        pytest.skip("主导出脚本未运行，跳过模型索引校验")
    index = json.loads(index_path.read_text(encoding="utf-8"))["models"]
    assert index, "模型索引为空"
    for name, meta in index.items():
        assert (ROOT / "weights" / name).exists(), f"索引中的模型缺失：{name}"
        assert meta["scale"] in {2, 3, 4}
