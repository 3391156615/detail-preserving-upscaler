"""回归测试：底部日志/指标面板可拖动调高 + 左侧参数面板折叠开关。

覆盖点：
1. ``right_splitter`` 的上下拖动能真实改变底部页签区高度，且被
   ``bottom_tabs`` 的最小/最大高度钳制（不能拖成一条缝，也不能吃掉整个图像区）。
2. 拖动结果会写入 ``gui.bottom_height``，重启窗口后按保存值还原。
3. 左侧参数面板可折叠 / 展开：折叠后宽度为 0、``workbench`` 隐藏，但折叠竖条与
   箭头按钮仍在且可点击恢复；展开后恢复原宽度。
4. 折叠状态写入 ``gui.show_left_panel``。
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
    # 先由 runtime 触发 bootstrap.prepare（预加载 onnxruntime），再创建 QApplication，
    # 否则部分环境下 onnxruntime 的 DLL 会与已加载的 Qt 冲突导致进程崩溃。
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _drag(qt_app, splitter, index: int, dy: int) -> None:
    """用真实鼠标事件拖动分割条（走 QSplitterHandle 的 press/move/release）。"""
    from PyQt5.QtCore import QEvent, QPoint, Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtTest import QTest

    handle = splitter.handle(index)
    start = QPoint(handle.width() // 2, handle.height() // 2)
    origin = handle.mapToGlobal(start)
    QApplication.sendEvent(handle, QMouseEvent(
        QEvent.MouseButtonPress, start, origin, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    for step in range(1, 7):
        offset = QPoint(0, int(dy * step / 6))
        QApplication.sendEvent(handle, QMouseEvent(
            QEvent.MouseMove, start + offset, origin + offset,
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    end = start + QPoint(0, dy)
    QApplication.sendEvent(handle, QMouseEvent(
        QEvent.MouseButtonRelease, end, origin + QPoint(0, dy),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    qt_app.processEvents()
    QTest.qWait(60)


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


def test_bottom_panel_drag_is_clamped(window, qt_app):
    bottom = window.bottom_tabs
    splitter = window.right_splitter
    splitter.refresh()
    init = splitter.sizes()[1]
    assert init >= bottom.minimumHeight()

    # 向下拖：底部变矮，且不低于最小高度
    _drag(qt_app, splitter, 1, 200)
    assert splitter.sizes()[1] < init

    # 向上猛拖：最多到 max（约 72% 可用高度），不会把图像区压没
    _drag(qt_app, splitter, 1, -2000)
    height = splitter.sizes()[1]
    assert height == bottom.maximumHeight() > bottom.minimumHeight()

    # 再向下猛拖：被最小高度兜住
    _drag(qt_app, splitter, 1, 2000)
    assert splitter.sizes()[1] == bottom.minimumHeight()
    assert splitter.sizes()[0] > 200


def test_bottom_height_saved_and_restored(window, qt_app, registry, config):
    splitter = window.right_splitter
    target = window.bottom_tabs.minimumHeight() + 260
    _drag(qt_app, splitter, 1, -(target - splitter.sizes()[1]))
    dragged = splitter.sizes()[1]
    assert config.get("gui.bottom_height") == dragged

    # 新窗口按保存的高度还原
    from ipscaling.gui.main_window import MainWindow

    other = MainWindow(registry, config)
    other.resize(1480, 940)
    other.show()
    qt_app.processEvents()
    try:
        assert abs(other.right_splitter.sizes()[1] - dragged) <= 12
    finally:
        other.close()
        other.deleteLater()
        qt_app.processEvents()


def test_left_panel_collapse_and_restore(window, qt_app, config):
    from PyQt5.QtCore import Qt

    splitter = window.splitter
    strip_w = window.left_strip.width()
    expanded_width = splitter.sizes()[1]
    assert splitter.sizes()[1] >= 260
    assert window.workbench.isVisible()

    window._toggle_left_panel(False)
    qt_app.processEvents()
    assert splitter.sizes()[1] == 0
    assert not window.workbench.isVisible()
    # 折叠竖条与按钮仍可见，箭头方向指向「展开」
    assert window.left_strip.isVisible()
    assert window.left_strip.width() == strip_w
    assert window.btn_sidebar.isVisible()
    assert window.btn_sidebar.arrowType() == Qt.RightArrow
    assert config.get("gui.show_left_panel") is False
    assert "显示" in window.act_left_panel.text()

    # 点击按钮即可恢复（不依赖菜单）
    window.btn_sidebar.click()
    qt_app.processEvents()
    assert window.workbench.isVisible()
    assert splitter.sizes()[1] >= 260
    assert window.btn_sidebar.arrowType() == Qt.LeftArrow
    assert config.get("gui.show_left_panel") is True
    assert expanded_width >= splitter.sizes()[1] - 240


def test_collapsed_state_restored_on_restart(window, qt_app, registry, config):
    from PyQt5.QtCore import Qt

    window._toggle_left_panel(False)
    qt_app.processEvents()
    try:
        from ipscaling.gui.main_window import MainWindow

        other = MainWindow(registry, config)
        other.resize(1480, 940)
        other.show()
        qt_app.processEvents()
        assert not other.workbench.isVisible()
        assert other.btn_sidebar.isVisible()
        assert other.btn_sidebar.arrowType() == Qt.RightArrow
        other.close()
        other.deleteLater()
        qt_app.processEvents()
    finally:
        config.set("gui.show_left_panel", True)
