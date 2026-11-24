"""滚轮守卫：禁止下拉框 / 数字输入框被鼠标滚轮误改数值。

PyQt5 中 QComboBox 与 QAbstractSpinBox 收到滚轮事件时默认会直接修改数值，
鼠标滑过面板时很容易误触，尤其是在可滚动面板里上下滚动时。

这里统一安装一个事件过滤器：

* 拦截 QComboBox / QAbstractSpinBox 的 wheel 事件，控件自身不再改值；
* 把同一个滚轮事件转发给最近的 QAbstractScrollArea 视口，
  让滚轮依然能正常滚动所在面板，而不是被"吃掉"。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QEvent, QObject, QPointF
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QWidget,
)

_GUARD_ATTR = "_wheel_guard"


def _find_scroll_area(widget: QWidget) -> Optional[QAbstractScrollArea]:
    """向上查找最近的滚动容器（面板的 QScrollArea）。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class WheelGuard(QObject):
    """拦截下拉框 / 数字输入框的滚轮事件并转交给父级滚动容器。"""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        if event.type() != QEvent.Wheel:
            return False
        if not isinstance(obj, (QComboBox, QAbstractSpinBox)):
            return False
        # 下拉列表已经展开时，让列表自身正常滚动，不拦截
        if isinstance(obj, QComboBox):
            view = obj.view()
            if view is not None and view.isVisible():
                return False

        self._forward_to_scroll_area(obj, event)
        return True

    @staticmethod
    def _forward_to_scroll_area(widget: QWidget, event: QWheelEvent) -> None:
        area = _find_scroll_area(widget)
        if area is None:
            return
        viewport = area.viewport()
        if viewport is None:
            return
        local = QPointF(viewport.mapFromGlobal(widget.mapToGlobal(event.pos())))
        try:
            forwarded = QWheelEvent(
                local,
                QPointF(event.globalPos()),
                event.pixelDelta(),
                event.angleDelta(),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
            )
        except TypeError:  # 某些 PyQt5 版本的构造重载差异
            forwarded = QWheelEvent(event)
        QApplication.sendEvent(viewport, forwarded)


def install_wheel_guard(root: QWidget) -> WheelGuard:
    """给下拉框 / 数字输入框安装滚轮守卫（可重复调用，动态创建的控件同样覆盖）。

    除对当前已存在的子控件逐个安装外，还会在 QApplication 上挂一个过滤器，
    这样算法参数面板里后续动态创建的下拉框 / 数字输入框也不会漏掉。
    """
    app = QApplication.instance()
    guard = getattr(app, _GUARD_ATTR, None) if app is not None else None
    if not isinstance(guard, WheelGuard):
        guard = WheelGuard(app if app is not None else root)
        if app is not None:
            app.installEventFilter(guard)
            setattr(app, _GUARD_ATTR, guard)
    for widget in root.findChildren(QWidget):
        if isinstance(widget, (QComboBox, QAbstractSpinBox)):
            widget.installEventFilter(guard)
    return guard
