"""真实输入路径回归测试：自定义倍率在「不先选下拉」与「焦点在下拉框上打字」两种场景都必须可用。

缺陷背景（用户实测仍失效的根因）：
1. 数字框曾被 `setEnabled(False)` 默认禁用，用户直接点它打字毫无反应，
   看起来与「可用」几乎无差别，于是判定「自定义倍率不能用」；
2. QComboBox 原生 typeahead（按首字符跳条目）会在自定义档下把数字键吃掉，
   例如敲 "2" 直接跳回「2× 放大」并撤销自定义档，数字框又被禁用。

这两点都无法被「直接调 setValue / 直接改 setCurrentIndex」的测试发现，
因此本文件全部改用 QTest 真实鼠标/键盘事件驱动。
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

from ipscaling.gui.scale_plan import direct_size
from ipscaling.utils.testpatterns import ensure_patterns


@pytest.fixture(scope="module")
def qt_app():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def qt_test():
    from PyQt5.QtTest import QTest

    return QTest


@pytest.fixture()
def wb(qt_app, registry, config, tmp_path):
    """每个用例一个干净面板，避免用例间互相污染下拉/数字框状态。"""
    from ipscaling.gui.workbench import Workbench

    widget = Workbench(registry, config)
    sample = tmp_path / "patterns" / "photo.png"
    ensure_patterns(tmp_path / "patterns")
    widget.add_files([str(sample)])
    widget.file_list.setCurrentRow(0)
    qt_app.processEvents()
    yield widget
    widget.deleteLater()


def _custom_index(combo) -> int:
    return next(i for i in range(combo.count()) if combo.itemData(i) == "custom")


def _type_into(qt_test, widget, text: str) -> None:
    """模拟真人：先 Ctrl+A 全选，再逐字键入，最后回车提交。"""
    from PyQt5.QtCore import Qt

    qt_test.keyClick(widget, Qt.Key_A, Qt.ControlModifier)
    qt_test.keyClicks(widget, text)
    qt_test.keyClick(widget, Qt.Key_Return)


def test_spin_custom_enabled_on_startup(wb):
    """缺陷 1 回归：初始状态数字框必须可编辑，不该等用户先在下拉里找「自定义」。"""
    spin = wb.spin_custom
    assert spin.isEnabled(), "数字框初始状态必须启用（此前默认禁用导致用户以为功能失效）"
    assert not spin.isReadOnly()
    assert spin.minimum() == pytest.approx(0.10)
    assert spin.maximum() == pytest.approx(8.00)
    assert spin.decimals() == 2


def test_direct_typing_in_spin_switches_to_custom(wb, qt_test, qt_app):
    """用户最直接的动作：不碰下拉，点数字框直接打 2.6。"""
    from PyQt5.QtCore import Qt

    combo, spin = wb.cmb_scale, wb.spin_custom
    assert combo.currentData() != "custom"

    qt_test.mouseClick(spin, Qt.LeftButton)
    qt_app.processEvents()
    _type_into(qt_test, spin, "2.6")
    qt_app.processEvents()

    assert combo.currentData() == "custom", "在数字框里改数值后，倍率下拉应自动切到「自定义倍率…」"
    assert wb.scale == pytest.approx(2.6)
    text = wb.lbl_target.text()
    assert "2.6×" in text, f"标签未反映自定义倍率：{text!r}"


def test_digits_typed_on_combo_are_forwarded_to_spin(wb, qt_test, qt_app):
    """缺陷 2 回归：焦点停在下拉框上敲数字时，不得被 typeahead 抢去跳档。"""
    from PyQt5.QtCore import Qt

    combo, spin = wb.cmb_scale, wb.spin_custom

    # 先用真实路径进入自定义档（点数字框 + 键入），不依赖下拉弹出列表
    qt_test.mouseClick(spin, Qt.LeftButton)
    qt_app.processEvents()
    _type_into(qt_test, spin, "1.5")
    qt_app.processEvents()
    assert combo.currentData() == "custom"

    # 再把焦点交回下拉框（真人就是点一下下拉），然后逐字敲 0.75
    combo.setFocus()
    qt_app.processEvents()
    for key in (Qt.Key_0, Qt.Key_Period, Qt.Key_7, Qt.Key_5):
        qt_test.keyClick(combo, key)
        qt_app.processEvents()
        assert combo.currentData() == "custom", "在下拉框上敲数字不得把「自定义倍率…」跳档撤销"

    assert spin.text() == "0.75", f"数字键没进入数字框：{spin.text()!r}"
    assert wb.scale == pytest.approx(0.75)
    assert "0.75×" in wb.lbl_target.text()


def test_preset_selection_syncs_spin_value(wb, qt_test, qt_app):
    """回到预设档时，数字框要跟着预设值走，避免「下拉写 4×、数字框还留 2.6」的错位。"""
    from PyQt5.QtCore import Qt

    combo, spin = wb.cmb_scale, wb.spin_custom

    qt_test.mouseClick(spin, Qt.LeftButton)
    qt_app.processEvents()
    _type_into(qt_test, spin, "2.6")
    qt_app.processEvents()

    index = next(i for i in range(combo.count()) if combo.itemData(i) == 4.0)
    combo.setCurrentIndex(index)
    qt_app.processEvents()

    assert combo.currentData() == 4.0
    assert spin.value() == pytest.approx(4.0), "切到预设档后数字框应同步为预设值"
    assert wb.scale == pytest.approx(4.0)


def test_custom_scale_matches_output_plan(wb, qt_app, qt_test):
    """自定义小数倍率必须真实驱动输出计划尺寸（不只是标签）。"""
    from ipscaling.utils.archive import load_image
    from PyQt5.QtCore import Qt

    combo, spin = wb.cmb_scale, wb.spin_custom
    qt_test.mouseClick(spin, Qt.LeftButton)
    qt_app.processEvents()
    _type_into(qt_test, spin, "0.75")
    qt_app.processEvents()
    assert combo.currentData() == "custom"

    image = load_image(wb.current_file())
    height, width = image.shape[:2]
    expected = direct_size(int(width), int(height), wb.scale)
    assert f"{expected[0]} × {expected[1]}" in wb.lbl_target.text()
