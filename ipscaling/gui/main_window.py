"""主窗口：三栏工作台 + 后台任务调度 + 指标/报告面板。"""

from __future__ import annotations

import logging
import os
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ipscaling import __app_name__, __version__
from ipscaling.engine.registry import EngineRegistry
from ipscaling.gui.viewer import (
    CompareView,
    ImageViewer,
    ViewerToolbar,
    make_difference_image,
)
from ipscaling.gui.wheel_guard import install_wheel_guard
from ipscaling.gui.workbench import Workbench
from ipscaling.utils.archive import load_image, save_image, save_report
from ipscaling.utils.config import RESULTS_DIR, Config, ensure_directories
from ipscaling.utils.evaluator import (
    compute_metrics,
    make_comparison,
    make_zoom_pair,
)
from ipscaling.gui.scale_plan import (
    PSNR_CAP_DB,
    prepare_inputs,
    preview_input,
    reference_basis,
)

_LOG = logging.getLogger("ipscaling.gui")


# --------------------------------------------------------------------------- 日志桥
class _LogBridge(QObject):
    message = pyqtSignal(str)


_log_bridge = _LogBridge()


def _get_log_bridge() -> _LogBridge:
    """取日志桥；QApplication 重建后旧桥可能已被销毁，这里自动重建。"""
    global _log_bridge
    try:
        _log_bridge.message  # 访问已删除的 C++ 对象会抛 RuntimeError
    except RuntimeError:
        _log_bridge = _LogBridge()
    return _log_bridge


class _QtLogHandler(logging.Handler):
    """把 ipscaling 日志转发到 GUI 日志面板（线程安全）。"""

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _get_log_bridge().message.emit(self.format(record))
        except Exception:
            pass


# --------------------------------------------------------------------------- 任务
@dataclass
class Job:
    """一次缩放任务。"""

    path: str
    key: str
    scale: float
    strategy: str = "direct"
    params: Dict[str, Any] = field(default_factory=dict)
    save: bool = True
    run_id: int = 0


class _WorkerSignals(QObject):
    done = pyqtSignal(object, object)
    failed = pyqtSignal(object, str)


class _Task(QRunnable):
    """单个算法的后台执行单元。"""

    def __init__(self, job: Job, registry: EngineRegistry, config: Config,
                 signals: _WorkerSignals) -> None:
        super().__init__()
        self.job = job
        self.registry = registry
        self.config = config
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401
        job = self.job
        try:
            image = load_image(job.path)
            reference, work, dst_size = prepare_inputs(image, job.scale, job.strategy)
            result = self.registry.run_with_fallback(
                job.key, work, dst_size=dst_size, params=dict(job.params), fallback="bicubic"
            )
            metrics = compute_metrics(reference, result.image)
            # 统一成内部短键，便于表格/报告复用（evaluator 原键为 "PSNR(dB)"/"SSIM"/"锐度"）
            psnr_value = metrics.get("PSNR(dB)")
            notes = list(result.notes)
            if psnr_value is not None and (
                not np.isfinite(psnr_value) or psnr_value > PSNR_CAP_DB
            ):
                # 输出与参考基准逐像素一致时 PSNR 趋于无穷，按上限记录，避免面板显示“—”
                psnr_value = PSNR_CAP_DB
                notes.append(f"与参考基准完全一致，PSNR 按上限 {PSNR_CAP_DB:.2f} dB 记录")
            metric_view = {
                "psnr": psnr_value,
                "ssim": metrics.get("SSIM"),
                "mae": metrics.get("MAE"),
                "sharpness": metrics.get("锐度"),
            }

            output_path = None
            if job.save:
                try:
                    saved = save_image(
                        RESULTS_DIR,
                        job.key,
                        result.image,
                        Path(job.path).stem,
                        result.plan.scale_x,
                        result.plan.scale_y,
                        extension=str(self.config.get("output.format", "png")),
                        jpeg_quality=int(self.config.get("output.jpeg_quality", 95)),
                    )
                    output_path = str(saved)
                except Exception:
                    _LOG.warning("自动保存失败：%s", traceback.format_exc(limit=2))

            # 日志用“请求的算法名”，回退时标注回退目标，与表格口径保持一致
            try:
                requested = self.registry.get(job.key)
                log_name = requested.display_name
            except Exception:  # noqa: BLE001
                log_name = job.key
            if result.algorithm != job.key:
                log_name = f"{log_name}(回退→{result.display_name})"
            _LOG.info(
                "%s | %s | %s | %.0f ms | PSNR %s | SSIM %s | 清晰度 %s | 输出 %s",
                Path(job.path).name,
                log_name,
                result.plan.scale_label,
                result.elapsed_ms,
                f"{metric_view['psnr']:.2f} dB" if metric_view["psnr"] is not None else "—",
                f"{metric_view['ssim']:.4f}" if metric_view["ssim"] is not None else "—",
                f"{metric_view['sharpness']:.1f}" if metric_view["sharpness"] is not None else "—",
                f"{result.image.shape[1]}×{result.image.shape[0]}",
            )

            payload = {
                "reference": reference,
                "work": work,
                "image": result.image,
                "result": result,
                "metrics": metric_view,
                "notes": notes,
                "basis": reference_basis(job.scale, job.strategy),
                "output_path": output_path,
                "source_size": (image.shape[1], image.shape[0]),
            }
            self.signals.done.emit(job, payload)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(job, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


# --------------------------------------------------------------------------- 局部对比对话框
class ZoomDialog(QDialog):
    def __init__(self, panels: List[Tuple[str, np.ndarray]], roi: Tuple[int, int, int, int],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("局部细节对比")
        self.resize(1100, 620)
        layout = QVBoxLayout(self)
        viewer = ImageViewer("局部对比")
        composite = make_zoom_pair(panels, roi, zoom=3.0)
        viewer.set_image(composite)
        layout.addWidget(viewer, 1)
        hint = QLabel("已按框选区域合成对比条（左右滑动查看，滚轮缩放）")
        hint.setObjectName("hintLabel")
        layout.addWidget(hint)


# --------------------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self, registry: Optional[EngineRegistry] = None,
                 config: Optional[Config] = None) -> None:
        super().__init__()
        ensure_directories()
        self.config = config or Config.load()
        self.registry = registry or EngineRegistry(self.config)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _WorkerSignals()
        self._signals.done.connect(self._on_job_done)
        self._signals.failed.connect(self._on_job_failed)

        self._queue: deque[Job] = deque()
        self._busy = False
        self._run_id = 0
        self._compare_phase = False
        self._compare_rows: List[Dict[str, Any]] = []
        self._compare_images: Dict[str, np.ndarray] = {}

        self._current_reference: Optional[np.ndarray] = None
        self._reference_kind = "参考基准"
        self._current_result: Optional[np.ndarray] = None
        self._current_source: Optional[np.ndarray] = None
        self._current_path: Optional[str] = None

        self._build_ui()
        self._build_menus()
        self._install_log_handler()
        self._update_status_summary()

    # ------------------------------------------------------------- 界面搭建
    def _build_ui(self) -> None:
        self.setWindowTitle("图像插值/缩放程序")
        self.resize(int(self.config.get("gui.window.width", 1480)),
                    int(self.config.get("gui.window.height", 920)))
        # 允许窗口缩到更小：各面板内部均可滚动，内容不会被截断
        self.setMinimumSize(920, 620)
        self.setAcceptDrops(True)

        self.workbench = Workbench(self.registry, self.config)
        self.compare = CompareView()
        self.toolbar_view = ViewerToolbar()

        # 中部区域整块用于图像对比展示（原图 / 处理结果）
        center = QWidget()
        # 向上拖动底部面板时图像区仍保留可用高度，不会被挤成一条缝
        center.setMinimumHeight(200)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(6, 6, 6, 6)
        center_layout.setSpacing(4)
        center_layout.addWidget(self.compare, 1)
        center_layout.addWidget(self.toolbar_view)

        # 底部：运行日志 + 性能指标与实验报告（同一区域两个可切换页签）
        self.bottom_tabs = self._build_bottom_tabs()

        right_side = QSplitter(Qt.Vertical)
        right_side.setObjectName("rightSideSplitter")
        right_side.addWidget(center)
        right_side.addWidget(self.bottom_tabs)
        # 窗口变高时新增高度优先给图像区，底部面板保持用户拖出的高度
        right_side.setStretchFactor(0, 4)
        right_side.setStretchFactor(1, 0)
        right_side.setChildrenCollapsible(False)
        right_side.setHandleWidth(6)
        saved_bottom = int(self.config.get("gui.bottom_height", 330) or 330)
        saved_bottom = max(self.bottom_tabs.minimumHeight(), min(saved_bottom, 620))
        right_side.setSizes([540, saved_bottom])
        self._bottom_height = saved_bottom
        right_side.splitterMoved.connect(self._remember_bottom_height)
        self.right_splitter = right_side

        # 左侧折叠条：参数面板隐藏后按钮依然可见，点击即可恢复
        self.left_strip = self._build_sidebar_strip()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.left_strip)
        splitter.addWidget(self.workbench)
        splitter.addWidget(right_side)
        # 左侧参数栏保持固定观感，其余宽度全部留给图像对比区
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setCollapsible(1, True)  # 允许把参数面板折叠到 0 宽
        splitter.setHandleWidth(6)
        total = max(self.width(), 960)
        left = max(260, int(total * 0.24))
        strip_w = self.left_strip.width()
        splitter.setSizes([strip_w, left, max(600, total - strip_w - left)])
        self._left_width = left
        self._left_collapsed = False
        self.splitter = splitter
        self.setCentralWidget(splitter)

        self._build_status_bar()
        self._connect_signals()
        install_wheel_guard(self)

    def _build_metrics_panel(self) -> QWidget:
        """性能指标与实验报告内容（放入底部页签直接使用）。"""
        panel = QWidget()
        panel.setObjectName("metricsPanel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["算法", "尺寸", "耗时 ms", "PSNR", "SSIM", "清晰度"])
        self.table.setToolTip("输出尺寸为处理后的图像分辨率；耗时为单张处理耗时（毫秒）")
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(44)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(150)
        layout.addWidget(self.table, 1)

        side = QWidget()
        side.setObjectName("metricsSide")
        side.setFixedWidth(300)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self.lbl_info = QLabel("尚未处理")
        self.lbl_info.setObjectName("infoBox")
        self.lbl_info.setWordWrap(True)
        side_layout.addWidget(self.lbl_info)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("mutedLabel")
        self.lbl_summary.setWordWrap(True)
        side_layout.addWidget(self.lbl_summary)
        side_layout.addStretch(1)

        self.btn_report = QPushButton("导出报告")
        self.btn_report.setObjectName("primaryButton")
        self.btn_report.setToolTip("导出本次实验的指标报告到 results/")
        self.btn_open_results = QPushButton("打开 results/")
        self.btn_open_results.setToolTip("打开 results/ 结果目录")
        side_layout.addWidget(self.btn_report)
        side_layout.addWidget(self.btn_open_results)

        layout.addWidget(side, 0)

        self.btn_report.clicked.connect(self._export_report)
        self.btn_open_results.clicked.connect(self._open_results_dir)
        return panel

    def _build_bottom_tabs(self) -> QTabWidget:
        """底部页签区：运行日志 / 性能指标与实验报告。"""
        tabs = QTabWidget()
        tabs.setObjectName("bottomTabs")
        # 下限保证页签与内容仍可阅读，上限随窗口尺寸动态计算
        tabs.setMinimumHeight(120)
        tabs.setDocumentMode(False)
        tabs.tabBar().setObjectName("bottomTabBar")
        tabs.tabBar().setExpanding(False)

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setObjectName("logView")
        log_layout.addWidget(self.log_view)
        self.log_page = log_page

        self.metrics_page = self._build_metrics_panel()

        tabs.addTab(log_page, "运行日志")
        tabs.addTab(self.metrics_page, "性能指标与实验报告")
        tabs.setCurrentIndex(0)
        tabs.setVisible(bool(self.config.get("gui.show_log_panel", True)))
        return tabs

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        bar.addPermanentWidget(self.progress)
        self._status_label = QLabel("就绪")
        bar.addWidget(self._status_label, 1)

    def _toggle_bottom_panel(self) -> None:
        """显示/隐藏底部页签区（运行日志 / 性能指标与实验报告）。"""
        self.bottom_tabs.setVisible(not self.bottom_tabs.isVisible())

    # -------------------------------------------------- 可折叠侧边栏 / 可拖面板
    def _build_sidebar_strip(self) -> QWidget:
        """左侧极窄竖条：参数面板折叠后按钮仍然可见，用于一键恢复。"""
        strip = QWidget()
        strip.setObjectName("sidebarStrip")
        strip.setFixedWidth(26)
        strip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        layout = QVBoxLayout(strip)
        layout.setContentsMargins(2, 6, 2, 8)
        layout.setSpacing(8)

        self.btn_sidebar = QToolButton(strip)
        self.btn_sidebar.setObjectName("sidebarToggle")
        self.btn_sidebar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.btn_sidebar.setAutoRaise(True)
        self.btn_sidebar.setFixedSize(22, 24)
        self.btn_sidebar.setCursor(Qt.PointingHandCursor)
        self.btn_sidebar.clicked.connect(lambda: self._toggle_left_panel())
        layout.addWidget(self.btn_sidebar, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        self._update_sidebar_button(True)
        return strip

    def _update_sidebar_button(self, expanded: bool) -> None:
        btn = getattr(self, "btn_sidebar", None)
        if btn is None:
            return
        # 用原生箭头图标，避免中文/符号字体缺字导致按钮空白
        btn.setArrowType(Qt.LeftArrow if expanded else Qt.RightArrow)
        btn.setToolTip("隐藏参数面板（Ctrl+B）" if expanded else "显示参数面板（Ctrl+B）")
        btn.setAccessibleName("隐藏参数面板" if expanded else "显示参数面板")

    def _toggle_left_panel(self, visible: bool | None = None) -> None:
        """折叠 / 展开左侧参数面板（类似 VS Code 侧边栏）。"""
        splitter = getattr(self, "splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) < 3:
            return
        expanded = bool(self.workbench.isVisible()) and sizes[1] > 0
        target = (not expanded) if visible is None else bool(visible)
        total = max(splitter.width(), 1)
        strip_w = self.left_strip.width() or 26

        if target:
            width = max(260, int(getattr(self, "_left_width", 300) or 300))
            width = min(width, max(260, total - strip_w - 480))
            self.workbench.show()
            splitter.setCollapsible(1, True)
            splitter.setSizes([strip_w, width, max(1, total - strip_w - width)])
        else:
            if sizes[1] > 0:
                self._left_width = max(260, sizes[1])
            self.workbench.hide()
            splitter.setSizes([strip_w, 0, max(1, total - strip_w)])
        self._left_collapsed = not target
        self._update_sidebar_button(target)
        try:
            self.config.set("gui.show_left_panel", target)
        except Exception:
            pass
        action = getattr(self, "act_left_panel", None)
        if action is not None:
            action.setText("隐藏参数面板" if target else "显示参数面板")

    def _remember_bottom_height(self, pos: int = 0, index: int = 0) -> None:
        """记录用户拖出的底部面板高度，关闭窗口时写入配置。"""
        splitter = getattr(self, "right_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) >= 2 and 0 < sizes[1] < 10000:
            self._bottom_height = max(120, sizes[1])
            try:
                self.config.set("gui.bottom_height", int(self._bottom_height))
            except Exception:
                pass
        # 拖动后按当前窗口高度刷新上限，避免上限值滞后卡住后续拖动
        self._clamp_bottom_height()

    def _clamp_bottom_height(self) -> None:
        """限制底部面板最大高度，避免向上拖过头把图像区压得不可用。"""
        splitter = getattr(self, "right_splitter", None)
        tabs = getattr(self, "bottom_tabs", None)
        if splitter is None or tabs is None:
            return
        # 取「分割器实际高度」与「窗口内容高度」的较大值，防止布局尚未结算时上限被算小
        height = max(splitter.height(), self.height() - 120)
        if height <= 0:
            return
        limit = max(tabs.minimumHeight(), int(height * 0.72))
        if tabs.maximumHeight() != limit:
            tabs.setMaximumHeight(limit)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._clamp_bottom_height()

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        m_file = menubar.addMenu("文件(&F)")
        act_open = QAction("打开图像…", self, shortcut=QKeySequence.Open)
        act_open.triggered.connect(self.workbench._pick_files)
        act_folder = QAction("添加文件夹…", self)
        act_folder.triggered.connect(self.workbench._pick_folder)
        act_quit = QAction("退出", self, shortcut=QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_open)
        m_file.addAction(act_folder)
        m_file.addSeparator()
        m_file.addAction(act_quit)

        m_view = menubar.addMenu("视图(&V)")
        act_fit = QAction("适应窗口", self, shortcut="Ctrl+0")
        act_fit.triggered.connect(self.compare.fit_all)
        act_zin = QAction("放大", self, shortcut=QKeySequence.ZoomIn)
        act_zin.triggered.connect(lambda: self.compare.left.zoom_by(1.2))
        act_zout = QAction("缩小", self, shortcut=QKeySequence.ZoomOut)
        act_zout.triggered.connect(lambda: self.compare.left.zoom_by(1 / 1.2))
        act_log = QAction("显示/隐藏底部面板（日志/指标）", self, shortcut="Ctrl+L")
        act_log.triggered.connect(self._toggle_bottom_panel)
        self.act_left_panel = QAction("隐藏参数面板", self, shortcut="Ctrl+B")
        self.act_left_panel.triggered.connect(lambda: self._toggle_left_panel())
        m_view.addAction(act_fit)
        m_view.addAction(act_zin)
        m_view.addAction(act_zout)
        m_view.addSeparator()
        m_view.addAction(self.act_left_panel)
        m_view.addAction(act_log)

        m_exp = menubar.addMenu("实验(&E)")
        act_all = QAction("一键对比全部算法", self, shortcut="F5")
        act_all.triggered.connect(self._run_compare_all)
        act_one = QAction("处理当前图像", self, shortcut="F6")
        act_one.triggered.connect(self._run_single)
        act_batch = QAction("批量处理全部图像", self, shortcut="F7")
        act_batch.triggered.connect(self._run_batch)
        act_report = QAction("导出实验报告", self)
        act_report.triggered.connect(self._export_report)
        m_exp.addAction(act_one)
        m_exp.addAction(act_all)
        m_exp.addAction(act_batch)
        m_exp.addSeparator()
        m_exp.addAction(act_report)

        m_help = menubar.addMenu("帮助(&H)")
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _connect_signals(self) -> None:
        wb = self.workbench
        wb.filesChanged.connect(self._on_files_changed)
        wb.runRequested.connect(self._run_single)
        wb.runAllRequested.connect(self._run_compare_all)
        wb.batchRequested.connect(self._run_batch)
        wb.openResultsRequested.connect(self._open_results_dir)
        wb.algorithmChanged.connect(lambda _key: self._update_status_summary())
        # 倍率或输入策略变化后，左侧预览与尺寸信息必须同步刷新（否则会残留旧倍率的画面）
        wb.scaleChanged.connect(lambda _scale: self._refresh_preview())
        wb.strategyChanged.connect(lambda _strategy: self._refresh_preview())

        tb = self.toolbar_view
        tb.fitRequested.connect(self.compare.fit_all)
        tb.zoomInRequested.connect(lambda: self.compare.left.zoom_by(1.2))
        tb.zoomOutRequested.connect(lambda: self.compare.left.zoom_by(1 / 1.2))
        tb.differenceToggled.connect(self._on_difference_toggled)
        self.compare.roiSelected.connect(self._on_roi_selected)

    # ------------------------------------------------------------- 日志
    def _install_log_handler(self) -> None:
        self._log_handler = _QtLogHandler()
        logging.getLogger("ipscaling").addHandler(self._log_handler)
        bridge = _get_log_bridge()
        try:  # 同一窗口重建时避免重复连接
            bridge.message.disconnect(self._append_log)
        except (TypeError, RuntimeError):
            pass
        bridge.message.connect(self._append_log)

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.log_view.moveCursor(self.log_view.textCursor().End)

    # ------------------------------------------------------------- 输入处理
    def _on_files_changed(self, files: List[str]) -> None:
        if not files:
            self.compare.clear()
            self._current_source = None
            self._current_path = None
            self._current_reference = None
            self._current_result = None
            self.lbl_info.setText("尚未处理")
            return
        # 选中的那一张才是当前图：曾固定取 files[0]，导致在列表里切到第 2、3 张后
        # 左侧预览与「文件/原始尺寸」标签仍停在第一张，与随后处理的图不一致。
        target = self.workbench.current_file() or files[0]
        if target == self._current_path and self._current_source is not None:
            # 同一张图重复通知（新增后选中会各发一次），无需重复解码
            return
        self._load_preview(target)

    def _load_preview(self, path: str) -> None:
        try:
            image = load_image(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "无法读取图像", f"{Path(path).name}\n{exc}")
            return
        self._current_source = image
        self._current_path = path
        self._current_reference = None
        self._current_result = None
        self._refresh_preview()

    # ------------------------------------------------------------- 预览口径
    def _refresh_preview(self) -> None:
        """按「倍率 + 输入策略」刷新左侧预览与信息标签。

        预览内容必须与 prepare_inputs 送给算法的输入完全一致：
        只有「自参照评估 + 放大」才显示评估裁剪区，其余情况一律显示完整原图，
        避免缩小时把原图裁掉一角后被拉伸成整块画布。
        """
        image = self._current_source
        if image is None:
            return
        h, w = image.shape[:2]
        scale = self.workbench.scale
        strategy = self.workbench.strategy
        preview, dst, cropped = preview_input(image, scale, strategy)
        if cropped:
            left_title = f"输入（评估裁剪） · {preview.shape[1]}×{preview.shape[0]}"
        else:
            left_title = f"原图 / 输入 · {w}×{h}"
        self.compare.set_images(
            preview,
            None,
            left_title=left_title,
            right_title="结果 / 输出（待处理）",
        )
        self.lbl_info.setText(self._info_text(w, h, scale, strategy, dst, cropped))
        self._update_status_summary()

    def _info_text(self, w: int, h: int, scale: float, strategy: str,
                   dst: Tuple[int, int], cropped: bool) -> str:
        name = Path(self._current_path).name if self._current_path else "—"
        if cropped:
            strategy_text = "自参照评估（先降采样再放回评估区尺寸，用于计算 PSNR/SSIM）"
            size_text = (
                f"倍率：{scale:g}× · 评估输出：{dst[0]} × {dst[1]}\n"
                f"真实 {scale:g}× 目标：{int(round(w * scale))} × {int(round(h * scale))}"
                "（切到「直接缩放」即可获得）"
            )
        elif strategy == "eval":
            strategy_text = "自参照评估（缩小无真值可参照，按直接缩放执行）"
            size_text = f"倍率：{scale:g}× · 输出：{dst[0]} × {dst[1]}"
        else:
            strategy_text = "直接缩放我的图片（输出为真实倍率结果）"
            size_text = f"倍率：{scale:g}× · 输出：{dst[0]} × {dst[1]}"
        return (
            f"文件：{name}\n原始尺寸：{w} × {h}\n"
            f"{size_text}\n"
            f"输入策略：{strategy_text}"
        )

    def _update_status_summary(self) -> None:
        key = self.workbench.current_algorithm()
        if not key:
            return
        engine = self.registry.get(key)
        self._status_label.setText(f"当前算法：{engine.display_name} · 倍率 {self.workbench.scale:g}×")

    # ------------------------------------------------------------- 任务调度
    def _enqueue(self, jobs: List[Job]) -> None:
        if not jobs:
            return
        self._queue.extend(jobs)
        if not self._busy:
            self._pump()

    def _pump(self) -> None:
        if self._busy or not self._queue:
            if not self._queue:
                self._finish_queue()
            return
        job = self._queue.popleft()
        self._busy = True
        self.workbench.set_busy(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        name = Path(job.path).name
        self._status_label.setText(f"正在处理：{name} · {self.registry.get(job.key).display_name}")
        task = _Task(job, self.registry, self.config, self._signals)
        self._pool.start(task)

    def _finish_queue(self) -> None:
        self.workbench.set_busy(False)
        self.progress.setVisible(False)
        if self._compare_phase and self._compare_rows:
            self._finalize_compare()
        self._compare_phase = False
        algorithm = self.workbench.current_algorithm()
        if algorithm:
            self._status_label.setText(
                f"就绪 · 当前算法 {self.registry.get(algorithm).display_name}"
            )

    def _make_jobs(self, paths: List[str], keys: List[str]) -> List[Job]:
        wb = self.workbench
        return [
            Job(path=path, key=key, scale=wb.scale, strategy=wb.strategy,
                params=wb.current_params() if key == wb.current_algorithm() else
                dict(self.config.algorithm_params(key) or {}),
                save=wb.auto_save, run_id=self._run_id)
            for path in paths
            for key in keys
        ]

    def _start_run(self, compare: bool) -> None:
        """开启新一轮执行：递增轮次号并清空待执行队列。

        离开旧任务的“迟到结果”必须被丢弃：曾在 1.5× 下单跑“最近邻”，随即改到
        0.25× 点“对比全部”，旧任务结束时把 1534×1534 的结果写进了新一轮表格，
        导致同一张表里出现与其它算法不一致的输出尺寸。
        """
        self._run_id += 1
        self._queue.clear()
        self._compare_phase = compare
        self._compare_rows = []
        self._compare_images.clear()
        self.table.setRowCount(0)

    def _run_single(self) -> None:
        path = self.workbench.current_file()
        if not path:
            QMessageBox.information(self, "提示", "请先选择一张图像。")
            return
        key = self.workbench.current_algorithm()
        if not key:
            return
        self._start_run(compare=False)
        self._enqueue(self._make_jobs([path], [key]))

    def _run_batch(self) -> None:
        files = self.workbench.files
        if not files:
            QMessageBox.information(self, "提示", "请先添加需要批量处理的图像。")
            return
        key = self.workbench.current_algorithm()
        if not key:
            return
        self._start_run(compare=False)
        self._enqueue(self._make_jobs(files, [key]))
        self._status_label.setText(f"批量处理中：共 {len(files)} 张")

    def _run_compare_all(self) -> None:
        path = self.workbench.current_file()
        if not path:
            QMessageBox.information(self, "提示", "请先选择一张图像。")
            return
        keys = [item["key"] for item in self.registry.catalog()]
        self._start_run(compare=True)
        self._enqueue(self._make_jobs([path], keys))
        self._append_log(f"开始对比 {len(keys)} 种算法：{Path(path).name}")
        self._append_log(
            f"参考基准：{reference_basis(self.workbench.scale, self.workbench.strategy)}"
            "（PSNR / SSIM 均相对该基准计算）"
        )

    # ------------------------------------------------------------- 结果处理
    def _on_job_done(self, job: Job, payload: Dict[str, Any]) -> None:
        if job.run_id != self._run_id:
            # 上一轮遗留任务：结果与当前参数不符，丢弃并继续跑当前队列
            _LOG.debug("忽略过期结果：%s · run_id %s != %s", job.key, job.run_id, self._run_id)
            self._busy = False
            self._pump()
            return
        result = payload["result"]
        metrics = payload["metrics"]
        reference = payload["reference"]
        image = payload["image"]

        self._busy = False
        if self._queue:
            self.progress.setRange(0, 0)
        else:
            self.progress.setVisible(False)

        requested = self.registry.get(job.key)
        fell_back = result.algorithm != job.key
        display = f"{requested.display_name}" if fell_back else result.display_name
        if fell_back:
            display = f"{requested.display_name}（回退至 {result.display_name}）"

        row = {
            "algorithm": job.key,
            "display_name": display,
            "scale": result.plan.scale_label,
            "input_size": payload["source_size"],
            "output_size": (image.shape[1], image.shape[0]),
            "elapsed_ms": result.elapsed_ms,
            "psnr": metrics.get("psnr"),
            "ssim": metrics.get("ssim"),
            "sharpness": metrics.get("sharpness"),
            "tiles": result.tiles,
            "output_path": payload["output_path"],
            "notes": list(payload.get("notes") or result.notes),
            "fallback": fell_back,
        }
        self._compare_rows.append(row)
        self._upsert_row(row)
        if self._compare_phase and not payload["output_path"]:
            # 对比阶段且未落盘：留一份缩略副本供结尾合成对比图
            self._compare_images[job.key] = self._thin_copy(image)

        # 只有当前正在查看的图像才刷新画布
        if Path(job.path).name == Path(self.workbench.current_file() or "").name:
            self._current_reference = reference
            self._current_result = image
            left = reference if reference is not None else payload["work"]
            if reference is not None:
                ref_kind = "参考真值" if job.strategy == "eval" else "参考基准"
                self._reference_kind = ref_kind
                ref_title = f"{ref_kind} · {left.shape[1]}×{left.shape[0]}"
            else:
                ref_title = f"输入 · {left.shape[1]}×{left.shape[0]}"
            self.compare.set_images(
                left, image,
                left_title=ref_title,
                right_title=f"{result.display_name} · {image.shape[1]}×{image.shape[0]} · {result.elapsed_ms:.0f} ms",
            )
            if self.toolbar_view.chk_diff.isChecked():
                self.compare.right.set_image(self._difference_image(), autofit=True)
            self.lbl_info.setText(
                f"文件：{Path(job.path).name}\n"
                f"原始尺寸：{payload['source_size'][0]} × {payload['source_size'][1]}\n"
                f"输出尺寸：{image.shape[1]} × {image.shape[0]}\n"
                f"算法：{result.display_name}\n"
                f"耗时：{result.elapsed_ms:.1f} ms · 分块 {result.tiles}\n"
                f"PSNR：{self._fmt(metrics.get('psnr'), ' dB')} · SSIM：{self._fmt(metrics.get('ssim'))}"
            )

        self._pump()

    def _on_job_failed(self, job: Job, message: str) -> None:
        if job.run_id != self._run_id:
            self._busy = False
            self._pump()
            return
        self._busy = False
        _LOG.error("算法执行失败 %s: %s", job.key, message)
        self._append_log(f"[失败] {Path(job.path).name} · {job.key} → {message.splitlines()[0]}")
        self._status_label.setText(f"失败：{job.key}")
        self._pump()

    @staticmethod
    def _fmt(value: Optional[float], suffix: str = "") -> str:
        if value is None:
            return "—"
        if isinstance(value, float) and not np.isfinite(value):
            return "—"
        return f"{value:.2f}{suffix}"

    def _upsert_row(self, row: Dict[str, Any]) -> None:
        key = row["algorithm"]
        existing = -1
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.data(Qt.UserRole) == key:
                existing = r
                break
        if existing < 0:
            self.table.insertRow(self.table.rowCount())
            existing = self.table.rowCount() - 1
        values = [
            row["display_name"],
            f"{row['output_size'][0]}×{row['output_size'][1]}",
            f"{row['elapsed_ms']:.0f}",
            self._fmt(row.get("psnr")),
            self._fmt(row.get("ssim")),
            self._fmt(row.get("sharpness")),
        ]
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            if col == 0:
                item.setData(Qt.UserRole, key)
                if row.get("output_path"):
                    item.setToolTip(f"保存路径：{row['output_path']}")
            item.setTextAlignment(Qt.AlignCenter if col else Qt.AlignLeft | Qt.AlignVCenter)
            self.table.setItem(existing, col, item)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        rows = self._compare_rows
        if not rows:
            self.lbl_summary.setText("")
            return
        best = max(
            (r for r in rows if r.get("psnr") is not None),
            key=lambda r: r["psnr"],
            default=None,
        )
        fastest = min(rows, key=lambda r: r["elapsed_ms"])
        parts = [f"已完成 {len(rows)} 个算法"]
        if best:
            parts.append(f"最佳保真：{best['display_name']}（PSNR {best['psnr']:.2f} dB）")
        parts.append(f"最快：{fastest['display_name']}（{fastest['elapsed_ms']:.0f} ms）")
        self.lbl_summary.setText(" · ".join(parts))

    def _finalize_compare(self) -> None:
        """对比序列结束后合成并排对比图，并按需保存到结果目录。"""
        panels: List[Tuple[str, np.ndarray]] = []
        if self._current_reference is not None:
            panels.append((self._reference_kind, self._current_reference))
        for row in self._compare_rows:
            label = f"{row['display_name']} {row['elapsed_ms']:.0f}ms"
            image: Optional[np.ndarray] = None
            path = row.get("output_path")
            if path:
                try:
                    image = load_image(path)
                except Exception:
                    image = None
            if image is None:
                # 未开启「自动保存处理后的图像」时结果不落盘，改用内存里的等比缩略副本，
                # 否则「对比全部」只剩参考基准一张面板，对比图被静默跳过
                image = self._compare_images.get(row["algorithm"])
            if image is not None:
                panels.append((label, image))
        if len(panels) >= 2:
            try:
                composite = make_comparison(panels)
                target = save_image(
                    RESULTS_DIR, "comparison", composite, "comparison", 1.0, 1.0,
                    extension="png",
                )
                self._append_log(f"对比图已生成：{target}")
            except Exception:
                self._append_log("对比图生成失败（不影响结果保存）")
        elif self._compare_rows:
            self._append_log("对比图未生成：可用的图像少于 2 张")
        self._compare_images.clear()
        self._refresh_summary()

    @staticmethod
    def _thin_copy(image: np.ndarray, max_side: int = 1600) -> np.ndarray:
        """等比缩小到最长边不超过 max_side 的副本，避免内存里堆积整幅大图。"""
        if image is None:
            return image
        height, width = image.shape[:2]
        longest = max(height, width)
        if longest <= max_side:
            return image
        ratio = max_side / float(longest)
        size = (max(1, int(round(width * ratio))), max(1, int(round(height * ratio))))
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)

    def _difference_image(self) -> np.ndarray:
        left = self._current_reference if self._current_reference is not None else self._current_source
        if left is None or self._current_result is None:
            return np.zeros((32, 32, 3), np.uint8)
        return make_difference_image(left, self._current_result, amplify=6.0)

    def _on_difference_toggled(self, checked: bool) -> None:
        if checked and self._current_result is not None:
            left = self._current_reference if self._current_reference is not None else self._current_source
            if left is not None:
                self.compare.right.set_image(make_difference_image(left, self._current_result, 6.0))
        elif not checked and self._current_result is not None:
            self.compare.right.set_image(self._current_result)

    def _on_roi_selected(self, roi: Tuple[int, int, int, int]) -> None:
        panels: List[Tuple[str, np.ndarray]] = []
        if self._current_reference is not None:
            panels.append((self._reference_kind, self._current_reference))
        elif self._current_source is not None:
            panels.append(("原图", self._current_source))
        if self._current_result is not None:
            panels.append(("处理结果", self._current_result))
        if len(panels) < 2:
            return
        try:
            dialog = ZoomDialog(panels, roi, self)
            dialog.exec_()
        except Exception:
            self._append_log("局部对比生成失败：" + traceback.format_exc(limit=1))

    # ------------------------------------------------------------- 报告
    def _export_report(self) -> None:
        if not self._compare_rows:
            QMessageBox.information(self, "提示", "还没有可导出的实验结果，请先运行算法对比。")
            return
        rows = [
            {
                "算法": r["display_name"],
                "倍率": r["scale"],
                "输入尺寸": f"{r['input_size'][0]}×{r['input_size'][1]}",
                "输出尺寸": f"{r['output_size'][0]}×{r['output_size'][1]}",
                "耗时(ms)": f"{r['elapsed_ms']:.1f}",
                "PSNR(dB)": self._fmt(r.get("psnr")),
                "SSIM": self._fmt(r.get("ssim")),
                "清晰度": self._fmt(r.get("sharpness")),
                "分块数": r.get("tiles", 1),
                "保存路径": r.get("output_path") or "",
            }
            for r in self._compare_rows
        ]
        with_psnr = [r for r in self._compare_rows if r.get("psnr") is not None and np.isfinite(r["psnr"])]
        best_note = ""
        if with_psnr:
            best = max(with_psnr, key=lambda r: r["psnr"])
            best_note = f"{best['display_name']}（PSNR {best['psnr']:.2f} dB）"
        meta = {
            "系统": f"{__app_name__} v{__version__}",
            "图像": Path(self.workbench.current_file() or "").name,
            "输入策略": "自参照评估" if self.workbench.strategy == "eval" else "直接缩放",
            "参考基准": reference_basis(self.workbench.scale, self.workbench.strategy),
            "最佳": best_note,
        }
        try:
            outputs = save_report(RESULTS_DIR, rows, title="超分算法对比实验报告", meta=meta)
        except Exception:
            _LOG.exception("报告导出失败")
            QMessageBox.warning(self, "导出失败", "报告写入失败，请查看 logs/ 下的日志。")
            return

        lines = [f"{label}：{path}" for label, path in outputs.items()]
        self._append_log("实验报告已导出：" + "；".join(lines))
        QMessageBox.information(self, "导出完成", "\n".join(lines) or "未生成任何文件")

    def _open_results_dir(self) -> None:
        ensure_directories()
        try:
            os.startfile(str(RESULTS_DIR))  # noqa: S606
        except Exception:
            QMessageBox.information(self, "结果目录", str(RESULTS_DIR))

    # ------------------------------------------------------------- 其他
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<b>{__app_name__}</b><br/>版本 v{__version__}<br/><br/>"
            "基线：最近邻 / 双线性 / 双三次 / Lanczos / Area<br/>"
            "细节保持：NEDI 边缘导向 / 引导滤波上采样<br/>"
            "深度学习：ONNX Runtime 超分（ESPCN 系列）<br/><br/>"
            "PyQt5 前端 · OpenCV / scikit-image 后端 · 纯 CPU 可运行",
        )

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.workbench.dropEvent(event)

    # ------------------------------------------------------------------ 布局
    def _apply_splitter_ratio(self) -> None:
        """首次显示后按固定比例（左 22% / 右 78%）分配两栏宽度。

        这样不同 DPI / 初始窗口尺寸下两栏比例一致，拉伸窗口时左右同步伸缩，
        不会被某一栏的 sizeHint 挤偏。
        """
        splitter = getattr(self, "splitter", None)
        if splitter is None:
            return
        total = splitter.width()
        if total <= 0:
            return
        strip_w = self.left_strip.width() or 26
        if getattr(self, "_left_collapsed", False):
            splitter.setSizes([strip_w, 0, max(1, total - strip_w)])
            return
        left = max(260, int(total * 0.22))
        left = min(left, max(260, total - strip_w - 480))
        splitter.setSizes([strip_w, left, max(1, total - strip_w - left)])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_ratio_applied", False):
            self._ratio_applied = True
            QTimer.singleShot(0, self._apply_splitter_ratio)
            QTimer.singleShot(0, self._clamp_bottom_height)
            if not bool(self.config.get("gui.show_left_panel", True)):
                QTimer.singleShot(0, lambda: self._toggle_left_panel(False))

    def closeEvent(self, event) -> None:
        # 先断开日志转发，避免窗口销毁后仍有信号指向已删除对象
        try:
            _get_log_bridge().message.disconnect(self._append_log)
        except (TypeError, RuntimeError):
            pass
        handler = getattr(self, "_log_handler", None)
        if handler is not None:
            logging.getLogger("ipscaling").removeHandler(handler)
        self._remember_bottom_height()
        try:
            self.config.set("gui.bottom_height", int(getattr(self, "_bottom_height", 330)))
        except Exception:
            pass
        try:
            self.config.save()
        except Exception:
            pass
        self._pool.clear()
        self._pool.waitForDone(3000)
        super().closeEvent(event)
