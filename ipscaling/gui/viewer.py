"""高性能图像查看器组件。

提供三件事：
1. ``ImageViewer``：基于 QGraphicsView 的画布，支持滚轮缩放（以光标为中心）、
   拖拽平移、双击自适应、ROI 框选。
2. ``CompareView``：左右两个画布同步缩放/平移，用于原图与结果对比。
3. ``make_difference_image``：结果与参考的差异可视化（伪彩），供右侧画布切换显示。

所有画布共享同一份 numpy(BGR/RGB, uint8) -> QImage 的极速转换，避免逐像素拷贝。
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PyQt5.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_VIEW_BG = QColor("#e9f1fa")
_ROI_COLOR = QColor("#1a6fd4")
_IMAGE_BORDER = QColor("#a9c6e4")


def ndarray_to_qimage(image: np.ndarray) -> QImage:
    """把 OpenCV 风格的 numpy 图像转换为 QImage（会做必要的连续化与通道翻转）。"""
    if image is None:
        return QImage()
    if image.ndim == 2:
        data = np.ascontiguousarray(image)
        height, width = data.shape[:2]
        return QImage(data.data, width, height, data.strides[0], QImage.Format_Grayscale8).copy()
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError(f"不支持的图像形状: {image.shape}")

    if image.dtype != np.uint8:
        data = np.clip(image, 0, 255).astype(np.uint8)
    else:
        data = image
    # BGR(A) -> RGB(A)
    if data.shape[2] == 3:
        data = np.ascontiguousarray(data[:, :, ::-1])
        fmt = QImage.Format_RGB888
    else:
        data = np.ascontiguousarray(data[:, :, [2, 1, 0, 3]])
        fmt = QImage.Format_RGBA8888
    height, width = data.shape[:2]
    return QImage(data.data, width, height, data.strides[0], fmt).copy()


def ndarray_to_pixmap(image: np.ndarray) -> QPixmap:
    qimage = ndarray_to_qimage(image)
    return QPixmap.fromImage(qimage) if not qimage.isNull() else QPixmap()


class ImageViewer(QGraphicsView):
    """单张图像画布：滚轮缩放、拖拽平移、ROI 框选。"""

    roiSelected = pyqtSignal(tuple)  # (x, y, w, h) 图像像素坐标
    viewTransformed = pyqtSignal(float, float, float)  # 归一化 zoom, nh, nv

    MIN_SCALE = 0.02
    MAX_SCALE = 32.0

    def __init__(self, pick_roi: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)
        # 浅色画布上给图像加一圈描边，避免白/浅色图像与背景融为一体
        border_pen = QPen(_IMAGE_BORDER, 0, Qt.SolidLine)
        border_pen.setCosmetic(True)
        self._border_item = QGraphicsRectItem()
        self._border_item.setPen(border_pen)
        self._border_item.setBrush(QBrush(Qt.NoBrush))
        self._border_item.setZValue(5)
        self._border_item.setVisible(False)
        self._scene.addItem(self._border_item)
        self._roi_item: Optional[QGraphicsRectItem] = None
        self._drag_origin: Optional[QPoint] = None
        self._syncing = False
        self._pick_roi = pick_roi

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(_VIEW_BG)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumSize(150, 110)

        self.horizontalScrollBar().valueChanged.connect(self._emit_state)
        self.verticalScrollBar().valueChanged.connect(self._emit_state)

    # ------------------------------------------------------------------ 图像
    def set_image(self, image: Optional[np.ndarray], autofit: bool = True) -> None:
        if image is None:
            self.clear()
            return
        pixmap = ndarray_to_pixmap(image)
        if pixmap.isNull():
            self.clear()
            return
        # 换图后旧 ROI 框记录的是上一张图的坐标，必须先移除，否则会残留在新图上
        self.clear_roi()
        self._drag_origin = None
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._border_item.setRect(QRectF(pixmap.rect()))
        self._border_item.setVisible(True)
        if autofit:
            self.fit_to_window()

    def clear(self) -> None:
        # 曾经只把引用置空未从场景移除，导致清空画布/换图后 ROI 框留在画面上
        self.clear_roi()
        self._drag_origin = None
        self._item.setPixmap(QPixmap())
        self._scene.setSceneRect(QRectF(0, 0, 1, 1))
        self._border_item.setVisible(False)

    def has_image(self) -> bool:
        return not self._item.pixmap().isNull()

    # ------------------------------------------------------------------ 视图
    def fit_to_window(self) -> None:
        if self._item.pixmap().isNull():
            return
        self.resetTransform()
        self.fitInView(self._item, Qt.KeepAspectRatio)
        self._clamp_scale()
        self._emit_state()

    def zoom_by(self, factor: float) -> None:
        if not self.has_image():
            return
        self.scale(factor, factor)
        self._clamp_scale()
        self._emit_state()

    def _clamp_scale(self) -> None:
        scale = self.transform().m11()
        if scale < self.MIN_SCALE:
            self.scale(self.MIN_SCALE / scale, self.MIN_SCALE / scale)
        elif scale > self.MAX_SCALE:
            self.scale(self.MAX_SCALE / scale, self.MAX_SCALE / scale)

    def fit_scale(self) -> float:
        """当前视口下「整幅图刚好放满」所对应的缩放系数。"""
        pixmap = self._item.pixmap()
        if pixmap.isNull():
            return 1.0
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        return min(vw / max(1, pixmap.width()), vh / max(1, pixmap.height()))

    def norm_state(self) -> Tuple[float, float, float]:
        """导出归一化视图状态：zoom 相对自身适配缩放，nh/nv 为滚动位置比例。"""
        fit = self.fit_scale()
        zoom = self.transform().m11() / fit if fit > 0 else 1.0
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        nh = hbar.value() / hbar.maximum() if hbar.maximum() > 0 else 0.0
        nv = vbar.value() / vbar.maximum() if vbar.maximum() > 0 else 0.0
        return float(zoom), float(nh), float(nv)

    def apply_state(self, zoom: float, nh: float, nv: float) -> None:
        """按归一化状态同步另一侧视图，避免信号回环。

        左右图尺寸可能相差很大（1024 原图 vs 4096 放大结果），若同步绝对缩放，
        小图会被缩成一块缩略图、大图只剩左上角一隅；改为按「相对适配倍数 + 滚动
        比例」同步后，两侧始终呈现同一视野范围，原图与结果都完整可见。
        """
        if not self.has_image():
            return
        self._syncing = True
        target = self.fit_scale() * float(zoom)
        target = max(self.MIN_SCALE, min(self.MAX_SCALE, target))
        current = self.transform().m11()
        if current > 0 and abs(current - target) > 1e-9:
            self.scale(target / current, target / current)
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        if hbar.maximum() > 0:
            hbar.setValue(int(round(max(0.0, min(1.0, float(nh))) * hbar.maximum())))
        if vbar.maximum() > 0:
            vbar.setValue(int(round(max(0.0, min(1.0, float(nv))) * vbar.maximum())))
        self._syncing = False

    def _emit_state(self, *_args) -> None:
        if self._syncing or not self.has_image():
            return
        zoom, nh, nv = self.norm_state()
        self.viewTransformed.emit(zoom, nh, nv)

    def wheelEvent(self, event) -> None:
        if not self.has_image():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._clamp_scale()
        self._emit_state()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.has_image() and self.transform().m11() <= 0.051:
            self.fit_to_window()

    def mouseDoubleClickEvent(self, event) -> None:
        self.fit_to_window()
        event.accept()

    # ------------------------------------------------------------------ ROI
    def mousePressEvent(self, event) -> None:
        if (
            self._pick_roi
            and self.has_image()
            and event.button() == Qt.LeftButton
            and event.modifiers() & Qt.ShiftModifier
        ):
            self._drag_origin = event.pos()
            if self._roi_item is not None:
                self._scene.removeItem(self._roi_item)
                self._roi_item = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None:
            rect = self._scene_rect(self._drag_origin, event.pos())
            if self._roi_item is None:
                pen = QPen(_ROI_COLOR, 0, Qt.SolidLine)
                pen.setCosmetic(True)
                pen.setWidth(2)
                self._roi_item = self._scene.addRect(rect, pen)
                self._roi_item.setZValue(10)
            else:
                self._roi_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin is not None:
            rect = self._scene_rect(self._drag_origin, event.pos())
            self._drag_origin = None
            if rect.width() >= 8 and rect.height() >= 8:
                self.roiSelected.emit(
                    (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
                )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _scene_rect(self, start: QPoint, end: QPoint) -> QRectF:
        p1 = self.mapToScene(start)
        p2 = self.mapToScene(end)
        return QRectF(p1, p2).normalized()

    def clear_roi(self) -> None:
        if self._roi_item is not None:
            self._scene.removeItem(self._roi_item)
            self._roi_item = None


class _TitledViewer(QWidget):
    """在画布上方叠一条标题栏的包装组件。"""

    def __init__(self, title: str, viewer: ImageViewer, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("viewerTitle")
        self.title_label.setFixedHeight(26)
        layout.addWidget(self.title_label)
        layout.addWidget(viewer, 1)
        self.viewer = viewer
        self.apply_font_metrics()

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def apply_font_metrics(self) -> None:
        """按当前字体度量重算标题栏高度，避免调节界面字号后文字被裁切。"""
        metrics = self.title_label.fontMetrics()
        self.title_label.setFixedHeight(max(24, metrics.height() + 10))


class CompareView(QWidget):
    """左右同步对比视图。"""

    roiSelected = pyqtSignal(tuple)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.left = ImageViewer()
        self.right = ImageViewer()
        self._left_box = _TitledViewer("原图 / 输入", self.left)
        self._right_box = _TitledViewer("结果 / 输出", self.right)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._left_box, 1)
        layout.addWidget(self._right_box, 1)

        self.left.viewTransformed.connect(self._sync_left)
        self.right.viewTransformed.connect(self._sync_right)
        self.left.roiSelected.connect(self.roiSelected.emit)
        self.right.roiSelected.connect(self.roiSelected.emit)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _sync_left(self, zoom, nh, nv) -> None:
        self.right.apply_state(zoom, nh, nv)

    def _sync_right(self, zoom, nh, nv) -> None:
        self.left.apply_state(zoom, nh, nv)

    def set_images(self, left: Optional[np.ndarray], right: Optional[np.ndarray],
                   left_title: str = "原图 / 输入", right_title: str = "结果 / 输出") -> None:
        self._left_box.set_title(left_title)
        self._right_box.set_title(right_title)
        self.left.set_image(left, autofit=True)
        self.right.set_image(right, autofit=True)
        # 两侧各自整幅适配后再按归一化状态对齐，保证尺寸不同也能同视野对照
        if left is not None and right is not None:
            self.right.apply_state(*self.left.norm_state())

    def fit_all(self) -> None:
        self.left.fit_to_window()
        self.right.fit_to_window()

    def clear(self) -> None:
        self.left.clear()
        self.right.clear()

    def apply_font_metrics(self) -> None:
        """界面字号变化后同步两块视图的标题栏高度。"""
        self._left_box.apply_font_metrics()
        self._right_box.apply_font_metrics()


def make_difference_image(reference: np.ndarray, result: np.ndarray, amplify: float = 4.0) -> np.ndarray:
    """生成差异可视化：|差值| 放大后映射为伪彩（BGR 输出）。"""
    if reference is None or result is None:
        return np.zeros((32, 32, 3), np.uint8)
    h = min(reference.shape[0], result.shape[0])
    w = min(reference.shape[1], result.shape[1])
    a = reference[:h, :w].astype(np.float32)
    b = result[:h, :w].astype(np.float32)
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    if b.ndim == 2:
        b = np.repeat(b[:, :, None], 3, axis=2)
    diff = np.abs(a - b).mean(axis=2)
    diff = np.clip(diff * float(amplify), 0, 255).astype(np.uint8)
    # 简易 colormap：黑 -> 蓝 -> 青 -> 黄 -> 红
    yy = diff.astype(np.float32) / 255.0
    r = np.clip(3.0 * yy - 0.6, 0, 1)
    g = np.clip(3.0 * yy - 1.6, 0, 1) + np.clip(1.4 - abs(3.0 * yy - 1.2), 0, 1) * 0.4
    bch = np.clip(1.6 - 3.0 * yy, 0, 1)
    vis = np.stack([bch, g, r], axis=2)
    return (np.clip(vis, 0, 1) * 255).astype(np.uint8)


class ViewerToolbar(QWidget):
    """画布下方的轻量工具条：缩放/适应/差异图切换。"""

    fitRequested = pyqtSignal()
    zoomInRequested = pyqtSignal()
    zoomOutRequested = pyqtSignal()
    differenceToggled = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PyQt5.QtWidgets import QCheckBox, QPushButton

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        self.btn_zoom_in = QPushButton("＋ 放大")
        self.btn_zoom_out = QPushButton("－ 缩小")
        self.btn_fit = QPushButton("适应窗口")
        self.chk_diff = QCheckBox("显示差异图")
        self.hint = QLabel("Shift + 左键拖拽框选区域进行局部对比 · 双击适应窗口")
        self.hint.setObjectName("hintLabel")
        # 窄窗口下允许被压缩，避免把左侧按钮挤出可视区
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.hint.setToolTip(self.hint.text())

        for btn in (self.btn_zoom_in, self.btn_zoom_out, self.btn_fit):
            btn.setCursor(Qt.PointingHandCursor)
            layout.addWidget(btn)
        layout.addWidget(self.chk_diff)
        layout.addSpacing(10)
        layout.addWidget(self.hint, 1)

        self.btn_zoom_in.clicked.connect(self.zoomInRequested.emit)
        self.btn_zoom_out.clicked.connect(self.zoomOutRequested.emit)
        self.btn_fit.clicked.connect(self.fitRequested.emit)
        self.chk_diff.toggled.connect(self.differenceToggled.emit)
