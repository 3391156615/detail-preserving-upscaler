"""GUI 缩放设置的回归测试：自定义倍率链路与「自动保存处理后的图像」文案。

覆盖点：
1. 加载图片后「目标尺寸」标签不再是占位符「目标尺寸：—」；
2. 倍率下拉框 8 档预设的标签尺寸与 `direct_size()` 完全一致；
3. 选中「自定义倍率…」后数字框启用，1.5 / 2.6 / 0.75 等小数倍率能实时驱动标签与 `scale` 属性；
4. 复选框文案与 tooltip 已改为「自动保存处理后的图像」，且配置键名（output.root）保持不变。
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

from ipscaling.gui.scale_plan import direct_size, plan_sizes
from ipscaling.utils.testpatterns import ensure_patterns

PRESETS = ("0.25", "0.5", "1.5", "2.0")


@pytest.fixture(scope="module")
def qt_app():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def workbench(qt_app, registry, config):
    from ipscaling.gui.workbench import Workbench

    wb = Workbench(registry, config)
    yield wb
    wb.deleteLater()


@pytest.fixture(scope="module")
def sample_path(tmp_path_factory) -> str:
    directory = tmp_path_factory.mktemp("patterns")
    return str(ensure_patterns(directory)["photo.png"])


def _source_size(path: str) -> tuple[int, int]:
    from ipscaling.utils.archive import load_image

    image = load_image(path)
    height, width = image.shape[:2]
    return int(width), int(height)


def test_target_label_filled_after_loading(workbench, sample_path, qt_app):
    """缺陷回归：加载图片后标签曾一直停留在「目标尺寸：—」。"""
    workbench.clear_files()
    workbench.add_files([sample_path])
    qt_app.processEvents()

    text = workbench.lbl_target.text()
    assert "—" not in text, f"加载图片后目标尺寸不应是占位符，实际：{text!r}"

    width, height = _source_size(sample_path)
    expected = direct_size(width, height, workbench.scale)
    assert f"{expected[0]} × {expected[1]}" in text


def test_preset_scale_labels_match_direct_size(workbench, sample_path, qt_app):
    """每档预设倍率的标签尺寸必须与真实输出尺寸一致。"""
    workbench.clear_files()
    workbench.add_files([sample_path])
    qt_app.processEvents()

    width, height = _source_size(sample_path)
    combo = workbench.cmb_scale
    checked = 0
    for index in range(combo.count()):
        data = combo.itemData(index)
        if data == "custom":
            continue
        combo.setCurrentIndex(index)
        qt_app.processEvents()

        scale = float(data)
        expected = direct_size(width, height, scale)
        plan, _ = plan_sizes(width, height, scale, workbench.strategy)
        assert plan == expected, f"{scale}× 下 plan_sizes 与 direct_size 不一致"
        assert abs(workbench.scale - scale) < 1e-9
        assert f"{expected[0]} × {expected[1]}" in workbench.lbl_target.text(), (
            f"{scale}× 的标签尺寸与预期 {expected} 不一致：{workbench.lbl_target.text()!r}"
        )
        checked += 1
    assert checked >= 4


@pytest.mark.parametrize("custom_scale", [1.5, 2.6, 0.75, 3.33])
def test_custom_scale_drives_label_and_scale(workbench, sample_path, qt_app, custom_scale):
    """自定义倍率必须真正驱动 `scale` 属性与目标尺寸标签（含小数）。"""
    workbench.clear_files()
    workbench.add_files([sample_path])
    qt_app.processEvents()

    combo = workbench.cmb_scale
    custom_index = next(i for i in range(combo.count()) if combo.itemData(i) == "custom")
    combo.setCurrentIndex(custom_index)
    qt_app.processEvents()

    spin = workbench.spin_custom
    assert spin.isEnabled(), "选中自定义倍率后数字框必须启用"

    spin.setValue(custom_scale)
    qt_app.processEvents()

    assert abs(workbench.scale - custom_scale) < 1e-9, f"scale 属性没跟上数字框：{workbench.scale}"

    width, height = _source_size(sample_path)
    expected = direct_size(width, height, custom_scale)
    text = workbench.lbl_target.text()
    assert f"{expected[0]} × {expected[1]}" in text, f"{custom_scale}× 标签尺寸错误：{text!r}"
    assert f"{custom_scale:g}×" in text


def test_auto_save_checkbox_wording(workbench):
    """文案改造：复选框与 tooltip 均改为「自动保存处理后的图像」表述。"""
    checkbox = workbench.chk_save
    assert checkbox.text() == "自动保存处理后的图像"
    tooltip = checkbox.toolTip()
    assert "自动" in tooltip and "保存" in tooltip
    assert "results/" in tooltip
    assert "归档" not in tooltip


def test_auto_save_property_tracks_checkbox(workbench):
    """auto_save 语义不变，仅文案调整，旧配置无需迁移。"""
    original = workbench.chk_save.isChecked()
    try:
        workbench.chk_save.setChecked(True)
        assert workbench.auto_save is True
        workbench.chk_save.setChecked(False)
        assert workbench.auto_save is False
    finally:
        workbench.chk_save.setChecked(original)
