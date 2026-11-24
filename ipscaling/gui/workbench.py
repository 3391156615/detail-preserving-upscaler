"""左侧工作台：输入源、缩放倍率、算法与参数（含边缘保持）动态面板。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QEvent, QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ipscaling.gui.scale_plan import plan_sizes
from ipscaling.gui.wheel_guard import install_wheel_guard
from ipscaling.utils.config import Config, default_config

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

SCALE_PRESETS = [
    ("0.25× 缩小", 0.25),
    ("0.5× 缩小", 0.5),
    ("1.5× 放大", 1.5),
    ("2× 放大", 2.0),
    ("3× 放大", 3.0),
    ("4× 放大", 4.0),
    ("6× 放大", 6.0),
    ("8× 放大", 8.0),
]

CATEGORY_LABELS = {
    "baseline": "基线算法",
    "edge": "传统细节保持",
    "deep": "深度学习超分",
}

# 参数元数据：键 -> (中文名, 最小, 最大, 步长, 小数位, 提示)
PARAM_META: Dict[str, Dict[str, Any]] = {
    "a": {"label": "核参数 a", "min": -1.0, "max": 1.0, "step": 0.05, "decimals": 2,
          "tip": "双三次/Keys 核的锐度参数，-0.75 为 OpenCV 默认，越接近 -1 越锐"},
    "window": {"label": "方向估计窗口", "min": 4, "max": 12, "step": 2,
               "tip": "结构张量的平滑窗口边长，窗口越大方向越稳但边缘定位变粗"},
    "strength": {"label": "边缘导向强度", "min": 0.0, "max": 1.0, "step": 0.05, "decimals": 2,
                 "tip": "沿边缘切线重采样的融合比例，越大越抑制锯齿、过大则偏软"},
    "sharpen": {"label": "边缘锐化", "min": 0.0, "max": 1.5, "step": 0.05, "decimals": 2,
                "tip": "结果的高频补偿强度，0 表示不做锐化"},
    "radius": {"label": "引导半径", "min": 2, "max": 32, "step": 1,
               "tip": "引导滤波的局部窗口半径，越大越平滑、边缘保持范围越宽"},
    "eps": {"label": "正则化 ε", "min": 1e-6, "max": 0.01, "step": 1e-6, "decimals": 6,
            "tip": "越大越平坦，越小越贴近原图细节（易放大噪声）"},
    "detail_gain": {"label": "细节增强", "min": 0.0, "max": 2.0, "step": 0.05, "decimals": 2,
                    "tip": "把高频细节按比例加回结果，是“细节保持”强度的核心参数"},
    "prefilter": {"label": "抗锯齿预滤波", "choices": ["cubic", "lanczos", "none"],
                  "tip": "缩小方向的预滤波方式，用于抑制摩尔纹与走样"},
    "model": {"label": "ONNX 模型", "choices": ["auto"], "tip": "auto 表示按倍率自动匹配模型"},
    "tile": {"label": "推理分块尺寸", "min": 64, "max": 1024, "step": 32,
             "tip": "大图分块推理的块边长，越小越省内存"},
    "overlap": {"label": "分块重叠", "min": 0, "max": 128, "step": 4,
                "tip": "相邻块的重叠像素，用于消除拼接缝"},
    "fallback": {"label": "推理失败回退", "choices": ["bicubic", "lanczos", "none"],
                 "tip": "模型缺失或异常时使用的兜底算法"},
}


class _ParamEditor(QWidget):
    """把配置里的一个参数映射成合适的控件。"""

    changed = pyqtSignal(str, object)

    def __init__(self, key: str, value: Any, parent=None) -> None:
        super().__init__(parent)
        meta = PARAM_META.get(key, {})
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._key = key
        self._widget: QWidget

        if isinstance(value, bool):
            self._widget = QCheckBox()
            self._widget.setChecked(value)
            self._widget.toggled.connect(lambda v: self.changed.emit(self._key, v))
        elif isinstance(value, int):
            box = QSpinBox()
            box.setRange(int(meta.get("min", 0)), int(meta.get("max", 4096)))
            box.setSingleStep(int(meta.get("step", 1)))
            box.setValue(int(value))
            box.valueChanged.connect(lambda v: self.changed.emit(self._key, int(v)))
            self._widget = box
        elif isinstance(value, float):
            box = QDoubleSpinBox()
            box.setDecimals(int(meta.get("decimals", 3)))
            box.setRange(float(meta.get("min", -1e6)), float(meta.get("max", 1e6)))
            box.setSingleStep(float(meta.get("step", 0.01)))
            box.setValue(float(value))
            box.valueChanged.connect(lambda v: self.changed.emit(self._key, float(v)))
            self._widget = box
        elif isinstance(value, str) and "choices" in meta:
            box = QComboBox()
            for choice in meta["choices"]:
                box.addItem(str(choice))
            if value in meta["choices"]:
                box.setCurrentText(str(value))
            box.currentTextChanged.connect(lambda v: self.changed.emit(self._key, v))
            self._widget = box
        else:
            from PyQt5.QtWidgets import QLineEdit

            box = QLineEdit()
            box.setText("" if value is None else str(value))
            box.textChanged.connect(lambda v: self.changed.emit(self._key, v))
            self._widget = box

        if meta.get("tip"):
            self._widget.setToolTip(meta["tip"])
        self._widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self._widget, 1)


def _compact_combo(combo: QComboBox, min_chars: int = 8) -> QComboBox:
    """让下拉框在窄面板内可被压缩。

    默认 QComboBox 的 minimumSizeHint 由“最长选项文本”决定，会把整个左侧面板
    的最小宽度撑大（从而出现横向滚动条）。这里改为按最小字符数计算宽度，
    超长文本以省略号显示，完整文本通过 tooltip 查看。
    """
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(min_chars)
    combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _sync_tip(*_args, _c: QComboBox = combo) -> None:
        _c.setToolTip(str(_c.currentText()))

    combo.currentIndexChanged.connect(_sync_tip)
    _sync_tip()
    return combo


class _ScaleCombo(QComboBox):
    """倍率下拉框。

    修复要点（真实鼠标键盘实测暴露的问题）：下拉框原生带 typeahead——只要焦点落在
    下拉框上，用户敲数字就会被当作「按首字符跳转条目」，例如敲 "2" 会跳到
    「2× 放大」并把「自定义倍率…」静默撤销，数字框随之被禁用。这里在自定义档下
    把数字键与小数点直接转交给数字框，保证无论焦点停在哪，键入都进入倍率输入。
    """

    custom_target = None
    _typing = False

    def keyPressEvent(self, event):  # noqa: N802 - Qt 命名
        target = self.custom_target
        text = event.text()
        forwardable = (
            target is not None
            and self.currentData() == "custom"
            and bool(text)
            and (text.isdigit() or text in (".", "-"))
        )
        if not forwardable:
            self._typing = False
            super().keyPressEvent(event)
            return
        if not self._typing:
            # 一串连续输入的第一个字符：把焦点交给数字框并全选，直接覆盖原值
            target.setFocus(Qt.OtherFocusReason)
            target.selectAll()
        QApplication.sendEvent(target, event)
        self._typing = True
        event.accept()

    def focusInEvent(self, event):  # noqa: N802 - Qt 命名
        self._typing = False
        super().focusInEvent(event)

    def showPopup(self):  # noqa: N802 - Qt 命名
        self._typing = False
        super().showPopup()


class Workbench(QWidget):
    """参数面板 + 输入列表 + 运行按钮。"""

    filesChanged = pyqtSignal(list)
    algorithmChanged = pyqtSignal(str)
    paramsChanged = pyqtSignal(dict)
    runRequested = pyqtSignal()
    runAllRequested = pyqtSignal()
    batchRequested = pyqtSignal()
    openResultsRequested = pyqtSignal()
    strategyChanged = pyqtSignal(str)
    scaleChanged = pyqtSignal(float)

    def __init__(self, registry, config: Optional[Config] = None, parent=None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._config = config or default_config()
        self._files: List[str] = []
        self._param_widgets: Dict[str, _ParamEditor] = {}
        self._param_values: Dict[str, Any] = {}
        self._size_cache: Dict[Tuple[str, float], Tuple[int, int]] = {}

        self.setObjectName("workbench")
        # 只保留较小的最小宽度，不再限制最大宽度：左右栏宽度交给 QSplitter 按比例分配
        self.setMinimumWidth(260)
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        inner = QWidget()
        self._body = QVBoxLayout(inner)
        self._body.setContentsMargins(12, 12, 12, 12)
        self._body.setSpacing(12)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self._build_input_group()
        self._build_scale_group()
        self._build_algorithm_group()
        self._build_action_buttons()

        self._reload_algorithms()

    # ---------------------------------------------------------------- 输入源
    def _build_input_group(self) -> None:
        group = QGroupBox("输入源")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # 按钮改为两行两列，窄面板下不会被挤成横向滚动
        row = QGridLayout()
        row.setSpacing(8)
        self.btn_open = QPushButton("打开图像")
        self.btn_folder = QPushButton("添加文件夹")
        self.btn_clear = QPushButton("清空")
        for btn in (self.btn_open, self.btn_folder, self.btn_clear):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self.btn_open, 0, 0)
        row.addWidget(self.btn_folder, 0, 1)
        row.addWidget(self.btn_clear, 1, 0, 1, 2)
        layout.addLayout(row)

        self.file_list = QListWidget()
        # 输入源列表不再无限膨胀：给一个上限，避免"输入源"独占左侧面板大部分高度
        self.file_list.setMinimumHeight(80)
        self.file_list.setMaximumHeight(84)
        self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_list.setToolTip("可直接把图片或文件夹拖入窗口")
        layout.addWidget(self.file_list)

        # 计数与拖拽提示合并为一行，避免输入源组被多行说明撑高
        self.lbl_count = QLabel("尚未选择图像 · 可直接拖入")
        self.lbl_count.setObjectName("mutedLabel")
        layout.addWidget(self.lbl_count)

        self.btn_open.clicked.connect(self._pick_files)
        self.btn_folder.clicked.connect(self._pick_folder)
        self.btn_clear.clicked.connect(self.clear_files)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._body.addWidget(group)

    def _pick_files(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in sorted(IMAGE_SUFFIXES))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图像", str(Path.home()), f"图像文件 ({patterns});;所有文件 (*.*)"
        )
        if paths:
            self.add_files(paths)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图像文件夹", str(Path.home()))
        if not folder:
            return
        found = [
            str(p)
            for p in sorted(Path(folder).iterdir())
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        ]
        if found:
            self.add_files(found)

    def add_files(self, paths: List[str]) -> None:
        added = False
        existing = {str(Path(p).resolve()).lower() for p in self._files}
        for raw in paths:
            p = Path(raw)
            if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            key = str(p.resolve()).lower()
            if key in existing:
                continue
            existing.add(key)
            self._files.append(str(p))
            item = QListWidgetItem(p.name)
            item.setToolTip(str(p))
            item.setData(Qt.UserRole, str(p))
            self.file_list.addItem(item)
            added = True
        if added:
            self._refresh_count()
            if self.file_list.currentRow() < 0 and self.file_list.count():
                self.file_list.setCurrentRow(0)
            # 加入图片后立刻按当前倍率刷新目标尺寸，避免标签停留在「目标尺寸：—」
            self._refresh_target_label()
            self.filesChanged.emit(list(self._files))

    def clear_files(self) -> None:
        self._files.clear()
        self.file_list.clear()
        self._refresh_count()
        self._refresh_target_label()
        self.filesChanged.emit([])

    def _refresh_count(self) -> None:
        count = len(self._files)
        self.lbl_count.setText("尚未选择图像" if count == 0 else f"共 {count} 张图像")

    def _on_selection_changed(self) -> None:
        if self.file_list.currentItem() is not None:
            # 换图后目标尺寸要跟着换，否则标签会显示上一张图的尺寸
            self._refresh_target_label()
            self.filesChanged.emit(list(self._files))

    @property
    def files(self) -> List[str]:
        return list(self._files)

    def current_file(self) -> Optional[str]:
        item = self.file_list.currentItem()
        if item is None:
            return self._files[0] if self._files else None
        return item.data(Qt.UserRole)

    # ---------------------------------------------------------------- 缩放
    def _build_scale_group(self) -> None:
        group = QGroupBox("缩放设置")
        form = QFormLayout(group)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.cmb_scale = _compact_combo(_ScaleCombo(), 6)
        for label, value in SCALE_PRESETS:
            self.cmb_scale.addItem(label, float(value))
        self.cmb_scale.addItem("自定义倍率…", "custom")
        self.cmb_scale.setCurrentIndex(3)  # 2×
        self.cmb_scale.setToolTip(
            "常用倍率可直接在这里选；\n"
            "要任意倍率（如 1.5 / 2.6 / 0.75），直接在下面「自定义」数字框里改数值即可，\n"
            "改完会自动切换为「自定义倍率…」。"
        )
        form.addRow("倍率", self.cmb_scale)

        self.spin_custom = QDoubleSpinBox()
        self.spin_custom.setDecimals(2)
        self.spin_custom.setRange(0.10, 8.00)
        self.spin_custom.setSingleStep(0.1)
        self.spin_custom.setValue(2.0)
        # 关键修复：数字框始终可编辑。此前它默认禁用、必须先在倍率下拉里找到
        # 「自定义倍率…」才会启用，实际使用中用户直接点数字框（外观与可编辑态
        # 几乎无差别）会发现「输不进去」，从而认为自定义倍率不可用。
        self.spin_custom.setEnabled(True)
        self.spin_custom.setAccelerated(True)
        self.spin_custom.setToolTip(
            "直接输入任意倍率：0.10–8.00，支持小数（如 1.5 / 2.6 / 0.75）。\n"
            "修改此数值会自动把上面的「倍率」切换为「自定义倍率…」。"
        )
        self.spin_custom.installEventFilter(self)
        form.addRow("自定义", self.spin_custom)

        self.cmb_strategy = _compact_combo(QComboBox(), 5)
        # 默认「直接缩放」：选 4× 就真实输出 4 倍尺寸。自参照评估的产出尺寸不放大，
        # 只用于算 PSNR/SSIM，因此作为可选项放在第二项。
        self.cmb_strategy.addItem("直接缩放我的图片（真实放大/缩小）", "direct")
        self.cmb_strategy.addItem("自参照评估（输出不放大，仅算 PSNR/SSIM）", "eval")
        self.cmb_strategy.setCurrentIndex(0)
        self.cmb_strategy.setToolTip(
            "直接缩放：按所选倍率真实放大/缩小图片，输出尺寸 = 原图 × 倍率。\n"
            "自参照评估：先把原图降采样再放回原尺寸，输出尺寸不放大，仅用于计算 PSNR/SSIM。"
        )
        form.addRow("输入策略", self.cmb_strategy)

        self.lbl_target = QLabel("目标尺寸：—")
        self.lbl_target.setObjectName("mutedLabel")
        self.lbl_target.setWordWrap(True)
        form.addRow(self.lbl_target)

        self.chk_save = QCheckBox("自动保存处理后的图像")
        self.chk_save.setToolTip(
            "勾选后，处理完成时自动把处理后的图像保存到 results/ 目录（目录取自配置项 output.root）；\n"
            "取消勾选则只在界面内展示结果，不落盘。"
        )
        self.chk_save.setChecked(True)
        form.addRow(self.chk_save)

        # 数字键在下拉框上也要能进入倍率输入（见 _ScaleCombo）
        self.cmb_scale.custom_target = self.spin_custom
        self.cmb_scale.currentIndexChanged.connect(self._on_scale_combo_changed)
        self.spin_custom.valueChanged.connect(self._on_spin_custom_changed)
        self.cmb_strategy.currentIndexChanged.connect(
            lambda _: self._on_strategy_changed()
        )
        self._body.addWidget(group)
        # 构建阶段（含 setCurrentIndex / setValue）都发生在信号连接之前，
        # 因此这里必须主动刷新一次，否则「自定义」数字框的使能状态与
        # 目标尺寸标签会停留在初始的占位值（目标尺寸：—）。
        self._on_scale_changed()

    def _on_scale_combo_changed(self, *_args) -> None:
        """倍率下拉框切换：自定义档激活数字框，预设档把预设值同步进数字框。"""
        is_custom = self.cmb_scale.currentData() == "custom"
        if not is_custom:
            try:
                preset = float(self.cmb_scale.currentData())
            except (TypeError, ValueError):
                preset = 2.0
            blocker = QSignalBlocker(self.spin_custom)
            self.spin_custom.setValue(preset)
            del blocker
        self._on_scale_changed()
        if is_custom:
            # 选中「自定义倍率…」后立刻把焦点交给数字框并全选，
            # 用户直接敲数字即可生效（含 1.5 / 2.6 这类小数倍率）。
            self.spin_custom.setFocus(Qt.OtherFocusReason)
            self.spin_custom.selectAll()

    def _on_spin_custom_changed(self, *_args) -> None:
        """自定义数字框被修改：自动把倍率下拉切到「自定义倍率…」。

        这样用户不点下拉、直接改数字就能用自定义倍率，
        也避免出现「数字框有值但倍率仍是 2× 放大」的错位状态。
        """
        if self.cmb_scale.currentData() != "custom":
            index = self.cmb_scale.findData("custom")
            if index >= 0:
                blocker = QSignalBlocker(self.cmb_scale)
                self.cmb_scale.setCurrentIndex(index)
                del blocker
        self._on_scale_changed()

    def _on_scale_changed(self, *_args) -> None:
        is_custom = self.cmb_scale.currentData() == "custom"
        # 数字框始终可编辑，这里只标记当前是否处于自定义档（供样式/提示使用），
        # 不再用 setEnabled 开关，避免用户点进去打字毫无反应。
        self.spin_custom.setProperty("customMode", is_custom)
        self.spin_custom.setEnabled(True)
        self._refresh_target_label()
        self.scaleChanged.emit(self.scale)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        """数字框一获得焦点就切到自定义档，保证点进去就能输入。"""
        if obj is getattr(self, "spin_custom", None) and event.type() == QEvent.FocusIn:
            if self.cmb_scale.currentData() != "custom":
                index = self.cmb_scale.findData("custom")
                if index >= 0:
                    blocker = QSignalBlocker(self.cmb_scale)
                    self.cmb_scale.setCurrentIndex(index)
                    del blocker
                    self._on_scale_changed()
        return super().eventFilter(obj, event)

    def _on_strategy_changed(self) -> None:
        self._refresh_target_label()
        self.strategyChanged.emit(self.strategy)

    def _refresh_target_label(self) -> None:
        """按「倍率 + 输入策略」显示真实产出尺寸，避免标签与输出不一致。"""
        path = self.current_file()
        if not path:
            self.lbl_target.setText("目标尺寸：—")
            return
        try:
            w, h = self._source_size(path)
            scale = self.scale
            strategy = self.strategy
            dst, _reference = plan_sizes(w, h, scale, strategy)
            if strategy == "eval" and scale > 1.0:
                real_w, real_h = int(round(w * scale)), int(round(h * scale))
                self.lbl_target.setText(
                    f"评估输出：{dst[0]} × {dst[1]}（不放大）\n"
                    f"真实 {scale:g}× 输出：{real_w} × {real_h}（需切到「直接缩放」）"
                )
            elif strategy == "eval":
                self.lbl_target.setText(f"输出尺寸：{dst[0]} × {dst[1]}（缩小按直接缩放执行）")
            else:
                self.lbl_target.setText(f"输出尺寸：{dst[0]} × {dst[1]}（{scale:g}×）")
        except Exception:
            self.lbl_target.setText("目标尺寸：—")

    def _source_size(self, path: str) -> Tuple[int, int]:
        """读取原图宽高，并按「路径 + 修改时间」缓存。

        自定义倍率由数字框实时驱动目标尺寸，每敲一位小数都会触发刷新；
        若每次都完整解码原图，大图会明显卡顿（表现为「数字框好像没生效」）。
        缓存键带 mtime，文件被替换后会自动重新读取尺寸。
        """
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            stamp = 0.0
        key = (os.path.normcase(os.path.abspath(path)), round(float(stamp), 3))
        cached = self._size_cache.get(key)
        if cached is not None:
            return cached

        from ipscaling.utils.archive import load_image

        image = load_image(path)
        h, w = image.shape[:2]
        size = (int(w), int(h))
        self._size_cache.clear()
        self._size_cache[key] = size
        return size

    @property
    def scale(self) -> float:
        data = self.cmb_scale.currentData()
        if data == "custom":
            # 自定义倍率：严格取数字框当前值（支持 1.5 / 2.6 等小数），并夹到合法区间
            lo, hi = self.spin_custom.minimum(), self.spin_custom.maximum()
            return float(min(max(self.spin_custom.value(), lo), hi))
        try:
            value = float(data)
        except (TypeError, ValueError):
            value = 2.0
        return min(max(value, 0.1), 8.0)

    @property
    def strategy(self) -> str:
        return str(self.cmb_strategy.currentData() or "direct")

    @property
    def auto_save(self) -> bool:
        return self.chk_save.isChecked()

    # ---------------------------------------------------------------- 算法
    def _build_algorithm_group(self) -> None:
        group = QGroupBox("算法与参数")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self.cmb_algorithm = _compact_combo(QComboBox(), 8)
        layout.addWidget(self.cmb_algorithm)

        self.lbl_algorithm = QLabel("")
        self.lbl_algorithm.setObjectName("mutedLabel")
        self.lbl_algorithm.setWordWrap(True)
        layout.addWidget(self.lbl_algorithm)

        self.param_host = QWidget()
        self.param_form = QFormLayout(self.param_host)
        self.param_form.setContentsMargins(0, 4, 0, 0)
        self.param_form.setSpacing(8)
        self.param_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.param_host)

        self.btn_reset_params = QPushButton("恢复该算法默认参数")
        layout.addWidget(self.btn_reset_params)

        self.cmb_algorithm.currentIndexChanged.connect(self._on_algorithm_changed)
        self.btn_reset_params.clicked.connect(self._reset_params)
        # 多余高度优先给算法/参数组（内容最多），输入源与缩放组保持紧凑
        self._body.addWidget(group, 1)

    def _reload_algorithms(self) -> None:
        self.cmb_algorithm.blockSignals(True)
        self.cmb_algorithm.clear()
        catalog = self._registry.catalog()
        for category, title in CATEGORY_LABELS.items():
            keys = [item["key"] for item in catalog if item.get("category") == category]
            if not keys:
                continue
            self.cmb_algorithm.addItem(f"—— {title} ——", None)
            model = self.cmb_algorithm.model()
            model.item(self.cmb_algorithm.count() - 1).setEnabled(False)
            for key in keys:
                item = next(i for i in catalog if i["key"] == key)
                self.cmb_algorithm.addItem(f"  {item['name']}", key)
        self.cmb_algorithm.blockSignals(False)
        for row in range(self.cmb_algorithm.count()):
            if self.cmb_algorithm.itemData(row):
                self.cmb_algorithm.setCurrentIndex(row)
                break
        self._on_algorithm_changed()

    def _on_algorithm_changed(self, *_args) -> None:
        key = self.current_algorithm()
        if not key:
            return
        self._rebuild_params(key)
        self.lbl_algorithm.setText(self._describe(key))
        self.algorithmChanged.emit(key)
        self._refresh_target_label()

    def _describe(self, key: str) -> str:
        engine = self._registry.get(key)
        info = engine.info()
        parts = [f"类别：{CATEGORY_LABELS.get(info.get('category', ''), info.get('category', ''))}"]
        if info.get("supports_upscale") and info.get("supports_downscale"):
            parts.append("放大/缩小均支持")
        elif info.get("supports_upscale"):
            parts.append("仅放大")
        elif info.get("supports_downscale"):
            parts.append("仅缩小")
        if not info.get("arbitrary_scale", True):
            scales = info.get("integer_scales") or []
            parts.append("整数倍率 " + "/".join(f"{s}×" for s in scales))
        else:
            parts.append("任意倍率")
        return " · ".join(parts)

    def _rebuild_params(self, key: str) -> None:
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self._param_widgets.clear()
        self._param_values = dict(self._config.algorithm_params(key) or {})
        if not self._param_values:
            hint = QLabel("该算法无可调参数")
            hint.setObjectName("hintLabel")
            self.param_form.addRow(hint)
            return
        for name, value in self._param_values.items():
            meta = PARAM_META.get(name, {})
            editor = _ParamEditor(name, value)
            editor.changed.connect(self._on_param_edited)
            self._param_widgets[name] = editor
            self.param_form.addRow(meta.get("label", name), editor)
            if meta.get("tip"):
                editor.setToolTip(meta["tip"])
        # 参数控件是动态重建的，重建后重新挂上滚轮守卫
        install_wheel_guard(self.param_host)

    def _on_param_edited(self, name: str, value: Any) -> None:
        self._param_values[name] = value
        key = self.current_algorithm()
        if key:
            self._config.set(f"algorithms.{key}.{name}", value)
        self.paramsChanged.emit(self.current_params())

    def _reset_params(self) -> None:
        key = self.current_algorithm()
        if not key:
            return
        defaults = default_config().algorithm_params(key) or {}
        for name, value in defaults.items():
            self._config.set(f"algorithms.{key}.{name}", value)
        self._rebuild_params(key)
        self.paramsChanged.emit(self.current_params())

    def current_algorithm(self) -> str:
        data = self.cmb_algorithm.currentData()
        return str(data) if data else ""

    def current_params(self) -> Dict[str, Any]:
        return dict(self._param_values)

    def set_algorithm(self, key: str) -> None:
        for row in range(self.cmb_algorithm.count()):
            if self.cmb_algorithm.itemData(row) == key:
                self.cmb_algorithm.setCurrentIndex(row)
                return

    # ---------------------------------------------------------------- 按钮
    def _build_action_buttons(self) -> None:
        wrap = QWidget()
        wrap.setObjectName("actionBar")
        # 两行两列排布，避免四个按钮竖排占掉左侧面板近三分之一高度
        layout = QGridLayout(wrap)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.btn_run = QPushButton("开始处理")
        self.btn_run.setObjectName("primaryButton")
        self.btn_run.setToolTip("对当前选中的图像运行所选算法")
        self.btn_run.setMinimumHeight(36)

        self.btn_compare_all = QPushButton("对比全部算法")
        self.btn_compare_all.setToolTip("一键对比全部算法在同一张图上的效果")
        self.btn_compare_all.setMinimumHeight(32)

        self.btn_batch = QPushButton("批量处理")
        self.btn_batch.setToolTip("批量处理所选图像")
        self.btn_batch.setMinimumHeight(32)

        self.btn_results = QPushButton("打开结果目录")
        self.btn_results.setToolTip("打开 results/ 结果目录")
        self.btn_results.setMinimumHeight(32)

        for btn in (self.btn_run, self.btn_compare_all, self.btn_batch, self.btn_results):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.btn_run, 0, 0)
        layout.addWidget(self.btn_results, 0, 1)
        layout.addWidget(self.btn_compare_all, 1, 0)
        layout.addWidget(self.btn_batch, 1, 1)

        self.btn_run.clicked.connect(self.runRequested.emit)
        self.btn_compare_all.clicked.connect(self.runAllRequested.emit)
        self.btn_batch.clicked.connect(self.batchRequested.emit)
        self.btn_results.clicked.connect(self.openResultsRequested.emit)

        outer = self.layout()
        outer.addWidget(wrap)

    def set_busy(self, busy: bool) -> None:
        for btn in (self.btn_run, self.btn_compare_all, self.btn_batch):
            btn.setEnabled(not busy)
        self.btn_run.setText("处理中…" if busy else "开始处理")

    # ---------------------------------------------------------------- 拖放
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths: List[str] = []
        for url in event.mimeData().urls():
            local = Path(url.toLocalFile())
            if local.is_dir():
                paths.extend(
                    str(p)
                    for p in sorted(local.iterdir())
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                )
            elif local.is_file() and local.suffix.lower() in IMAGE_SUFFIXES:
                paths.append(str(local))
        if paths:
            self.add_files(paths)
        event.acceptProposedAction()
