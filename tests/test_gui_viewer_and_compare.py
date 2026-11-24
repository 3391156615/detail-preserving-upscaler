"""回归测试：画布 ROI 残留与「未开启自动保存」时的对比图合成。

覆盖点：
1. ``ImageViewer.set_image`` / ``clear`` 必须把 ROI 框从场景里移除，
   否则换图或清空画布后旧 ROI 会留在画面上（坐标还属于上一张图）。
2. ``MainWindow._finalize_compare`` 在结果未落盘（自动保存关闭）时，
   必须回退使用内存中的缩略副本合成对比图，不能只剩参考基准一张面板。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qt_app(runtime):
    # 必须先由 runtime 触发 bootstrap.prepare（预加载 onnxruntime），再创建 QApplication：
    # 反之在本机部分环境下 onnxruntime 的 DLL 会与已加载的 Qt 冲突导致进程崩溃。
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def main_window(qt_app, registry, config):
    from ipscaling.gui.main_window import MainWindow

    win = MainWindow(registry, config)
    yield win
    win.close()
    win.deleteLater()


def test_set_image_drops_previous_roi(qt_app):
    from PyQt5.QtCore import QRectF

    from ipscaling.gui.viewer import ImageViewer

    viewer = ImageViewer()
    viewer.set_image(np.zeros((120, 160, 3), np.uint8))
    viewer._roi_item = viewer._scene.addRect(QRectF(8, 8, 40, 40))
    assert viewer._roi_item is not None

    viewer.set_image(np.full((120, 160, 3), 180, np.uint8))
    assert viewer._roi_item is None
    assert all(not isinstance(item, type(viewer._border_item)) or item is viewer._border_item
               for item in viewer._scene.items())


def test_clear_drops_roi_from_scene(qt_app):
    from PyQt5.QtCore import QRectF

    from ipscaling.gui.viewer import ImageViewer

    viewer = ImageViewer()
    viewer.set_image(np.zeros((120, 160, 3), np.uint8))
    roi = viewer._scene.addRect(QRectF(4, 4, 30, 30))
    viewer._roi_item = roi
    viewer.clear()
    assert viewer._roi_item is None
    assert roi not in viewer._scene.items()
    assert viewer.has_image() is False


def test_finalize_compare_uses_memory_panels_without_saved_output(main_window, monkeypatch, color_image):
    from ipscaling.gui import main_window as module

    captured = {}

    def fake_save_image(root, subdir, image, prefix, *_args, **_kwargs):
        captured["shape"] = image.shape
        captured["panels_ok"] = True
        return Path(root) / "fake_comparison.png"

    monkeypatch.setattr(module, "save_image", fake_save_image)

    win = main_window
    win._current_reference = color_image.copy()
    win._reference_kind = "原图"
    win._compare_rows = [
        {"algorithm": "nearest", "display_name": "最近邻", "elapsed_ms": 1.0,
         "output_path": None},
        {"algorithm": "bicubic", "display_name": "双三次", "elapsed_ms": 2.0,
         "output_path": None},
    ]
    win._compare_images = {
        "nearest": color_image.copy(),
        "bicubic": color_image.copy(),
    }

    win._finalize_compare()

    assert captured.get("panels_ok") is True, "未落盘时应回退用内存副本合成对比图"
    assert captured["shape"][0] > 0 and captured["shape"][1] > 0
    assert not win._compare_images, "对比结束后必须清空内存副本，避免大图堆积"


def test_thin_copy_limits_long_side(main_window):
    big = np.zeros((900, 1800, 3), np.uint8)
    thin = main_window._thin_copy(big, max_side=600)
    assert max(thin.shape[:2]) <= 600
    small = np.zeros((100, 120, 3), np.uint8)
    assert main_window._thin_copy(small, max_side=600) is small
