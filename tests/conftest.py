"""pytest 公共夹具：复用正式启动链路（bootstrap.prepare）构建真实运行时上下文。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402


@pytest.fixture(scope="session")
def runtime():
    """完整的运行上下文（registry + config），与 GUI 启动时完全一致。"""
    return bootstrap.prepare(verbose=False)


@pytest.fixture(scope="session")
def registry(runtime):
    return runtime.registry


@pytest.fixture(scope="session")
def config(runtime):
    return runtime.config


@pytest.fixture(scope="session")
def color_image() -> np.ndarray:
    """合成彩色测试图：渐变 + 棋盘边缘 + 轻噪声，覆盖平滑区与锐边。"""
    rng = np.random.default_rng(2026)
    height, width = 180, 240
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    base = 128.0 + 90.0 * np.sin(xx / 23.0) * np.cos(yy / 17.0)
    checker = (((xx // 12).astype(np.int32) + (yy // 12).astype(np.int32)) % 2) * 50.0
    gray = np.clip(base + checker + rng.normal(0, 6, (height, width)), 0, 255)
    img = np.dstack([np.roll(gray, 2, axis=1), np.roll(gray, 1, axis=0), gray])
    return img.astype(np.uint8)


@pytest.fixture(scope="session")
def gray_image(color_image) -> np.ndarray:
    return color_image[:, :, 0].copy()
