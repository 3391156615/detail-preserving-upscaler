"""回归测试：右侧性能指标面板「打开输出结果」按钮文案。

覆盖点：
1. 按钮文字不再是带路径的 `打开 results/`，改为面向用户的「打开输出结果」。
2. 提示文案仍明确指向 results/ 目录，便于用户定位产物。
3. 按钮点击仍接到打开结果目录的槽上（功能未被文案改动影响）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(scope="module")
def qt_app(runtime):
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ipscaling.gui.theme import DEFAULT_FONT_SIZE, apply_theme

    apply_theme(app, DEFAULT_FONT_SIZE)
    yield app


@pytest.fixture()
def window(qt_app, registry, config):
    from ipscaling.gui.main_window import MainWindow

    win = MainWindow(registry, config)
    win.resize(1480, 940)
    win.show()
    qt_app.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qt_app.processEvents()


def test_open_results_button_wording(window):
    button = window.btn_open_results
    assert button.text() == "打开输出结果"
    assert "results/" not in button.text()
    assert button.toolTip() == "打开输出结果目录（results/）"


def test_open_results_button_still_opens_dir(window, monkeypatch):
    """点击按钮仍会打开结果目录（用假 startfile 拦截，避免真的弹资源管理器）。"""
    from ipscaling.gui import main_window as mw

    opened: list[str] = []
    monkeypatch.setattr(mw.os, "startfile", lambda p: opened.append(str(p)), raising=False)
    window.btn_open_results.click()
    assert len(opened) == 1
    assert opened[0].replace("\\", "/").endswith("results")
