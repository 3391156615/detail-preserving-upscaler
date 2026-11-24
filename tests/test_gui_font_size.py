"""回归测试：界面字体大小设置（默认字号 + 视图菜单调节 + 配置持久化）。

覆盖点：
1. ``clamp_font_size`` 能把任意输入收敛到 [FONT_SIZE_MIN, FONT_SIZE_MAX]。
2. ``font_sizes`` 按基准字号推导各层级字号，且默认基准比历史 13px 更小。
3. ``build_qss`` 生成的样式表没有残留占位符，字号随参数变化。
4. 「视图 → 界面字体大小」子菜单包含预设档位、增大/减小/恢复默认/自定义，
   且勾选状态与当前字号一致。
5. ``_set_font_size`` 会即时应用样式表、刷新勾选并写入 ``gui.font_size``。
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

from ipscaling.gui.theme import (
    DEFAULT_FONT_SIZE,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    FONT_SIZE_PRESETS,
    build_qss,
    clamp_font_size,
    font_sizes,
)


def test_clamp_font_size_bounds():
    assert clamp_font_size(12) == 12
    assert clamp_font_size(FONT_SIZE_MIN - 5) == FONT_SIZE_MIN
    assert clamp_font_size(FONT_SIZE_MAX + 5) == FONT_SIZE_MAX
    assert clamp_font_size("14") == 14
    assert clamp_font_size(None) == DEFAULT_FONT_SIZE
    assert clamp_font_size("abc") == DEFAULT_FONT_SIZE


def test_default_font_size_is_smaller_than_legacy():
    # 历史版本正文固定 13px，用户反馈偏大，默认基准必须更小
    assert DEFAULT_FONT_SIZE < 13
    sizes = font_sizes(DEFAULT_FONT_SIZE)
    assert sizes["FS_BASE"] == DEFAULT_FONT_SIZE
    assert sizes["FS_TITLE"] > sizes["FS_BASE"] > sizes["FS_TINY"] >= 8


def test_font_sizes_grow_with_base():
    small = font_sizes(11)
    large = font_sizes(15)
    for key in ("FS_BASE", "FS_TITLE", "FS_PRIMARY", "FS_SMALL", "FS_TINY"):
        assert large[key] > small[key]


def test_build_qss_has_no_placeholder_left():
    for size in (10, 12, 15, 18):
        qss = build_qss(size)
        assert "{FS_" not in qss
        assert f"font-size: {size}px;" in qss


@pytest.fixture(scope="module")
def qt_app(runtime):
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ipscaling.gui.theme import apply_theme

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


def test_font_menu_structure(window):
    labels = [a.text() for a in window.m_font.actions() if a.text()]
    for _size, name in FONT_SIZE_PRESETS:
        assert any(name in text for text in labels)
    for text in ("增大字号", "减小字号", "恢复默认字号", "自定义字号…"):
        assert text in labels

    checked = [int(a.data()) for a in window._font_actions if a.isChecked()]
    assert checked == [int(window._font_size)]


def test_set_font_size_applies_and_persists(window, qt_app, config, monkeypatch):
    from PyQt5.QtWidgets import QApplication

    original = int(config.get("gui.font_size", DEFAULT_FONT_SIZE))
    saved_calls = []
    monkeypatch.setattr(type(config), "save", lambda self: saved_calls.append(1))

    try:
        window._set_font_size(15)
        qt_app.processEvents()
        assert window._font_size == 15
        assert "font-size: 15px;" in QApplication.instance().styleSheet()
        assert config.get("gui.font_size") == 15
        assert saved_calls, "字号变化必须触发配置落盘"
        checked = [int(a.data()) for a in window._font_actions if a.isChecked()]
        assert checked == []

        window._set_font_size(99)  # 越界被钳制
        assert window._font_size == FONT_SIZE_MAX

        window._set_font_size(DEFAULT_FONT_SIZE)  # 恢复默认并重新勾选
        checked = [int(a.data()) for a in window._font_actions if a.isChecked()]
        assert checked == [DEFAULT_FONT_SIZE]
    finally:
        config.set("gui.font_size", original)
        window._set_font_size(original)
