"""蓝白浅色主题：QSS 样式表与调色板。

设计要点：白底 + 浅蓝控件 + 深蓝黑文字，保证白底上的高对比度可读性；
强调色统一为 #1a6fd4，弱化装饰、突出图像画布与关键按钮。
表格采用浅蓝底白字列头 + 浅蓝斑马纹，画布为浅灰蓝底并给图像加描边。
"""

from __future__ import annotations

from typing import Any

ACCENT = "#1a6fd4"
ACCENT_HOVER = "#2f84e6"
ACCENT_PRESSED = "#1257ad"
ACCENT_SOFT = "#dfecfb"

BG_BASE = "#ffffff"
BG_PANEL = "#f7fafe"
BG_ELEVATED = "#ffffff"
BG_INPUT = "#dfecfb"
BG_INPUT_HOVER = "#d3e4f9"
BG_ALT_ROW = "#eff6fe"
BG_HEADER = "#d8e9fb"
BG_LOG = "#f2f7fd"
BG_CANVAS = "#e9f1fa"

TEXT_PRIMARY = "#0f2740"
TEXT_SECONDARY = "#3f5c7a"
TEXT_MUTED = "#4a6785"
TEXT_ON_ACCENT = "#ffffff"

BORDER = "#b9d3ee"
BORDER_STRONG = "#93b8dd"

# ---------------------------------------------------------------- 字号
# 界面基准字号（px）：历史版本为 13px，用户反馈偏大，默认下调到 12px。
# 所有局部字号都由基准推导（见 font_sizes），因此“视图 → 界面字体大小”
# 调节一处即可整体放大/缩小，且保持原有层级比例不变。
DEFAULT_FONT_SIZE = 12
FONT_SIZE_MIN = 10
FONT_SIZE_MAX = 18

# 顶部菜单可选的快捷档位：(字号 px, 名称)
FONT_SIZE_PRESETS = ((11, "小"), (12, "标准"), (13, "较大"), (14, "大"))


def clamp_font_size(font_size: Any) -> int:
    """把外部传入的字号收敛到 [FONT_SIZE_MIN, FONT_SIZE_MAX] 的安全区间。"""
    try:
        value = round(float(font_size))
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE
    return max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, value))


def font_sizes(font_size: int = DEFAULT_FONT_SIZE) -> dict[str, int]:
    """由基准字号推导 QSS 中各类文本的字号，保持“标题 > 正文 > 辅助”的层级。

    历史版本以 13px 为基准（正文 13 / 标题 15 / 主按钮 14 / 辅助 12 / 侧栏 11 / 进度 10）。
    现在基准下调为 12px，且主按钮不再额外放大（强调交由蓝底白字承担），
    实际为：正文 12 / 标题 14 / 主按钮 12 / 辅助 11 / 侧栏 10 / 进度 9。
    """
    base = clamp_font_size(font_size)

    def step(delta: int) -> int:
        return max(8, base + delta)

    return {
        "FS_BASE": base,          # 全局正文
        "FS_TITLE": step(2),      # 面板标题
        "FS_PRIMARY": step(0),    # 主按钮（与正文同号，强调靠配色）
        "FS_SMALL": step(-1),     # 提示/表头/视图标题
        "FS_LOG": step(-1),       # 日志等宽字体
        "FS_TINY": step(-2),      # 侧边栏
        "FS_MICRO": step(-3),     # 进度条
    }


_QSS_TEMPLATE = """
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: {FS_BASE}px;
    outline: none;
}}

QMainWindow, QDialog {{
    background: {BG_BASE};
    color: {TEXT_PRIMARY};
}}

QWidget {{
    color: {TEXT_PRIMARY};
}}

/* ---------- 面板 ---------- */
#workbench {{
    background: {BG_PANEL};
    border-right: 1px solid {BORDER};
}}

#metricsPanel, #metricsScroll {{
    background: {BG_PANEL};
}}

#metricsPanel {{
    border: none;
}}

#bottomTabs::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {BG_PANEL};
    top: -1px;
}}

#bottomTabBar::tab {{
    background: {BG_INPUT};
    color: {TEXT_SECONDARY};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 16px;
    margin-right: 2px;
}}

#bottomTabBar::tab:selected {{
    background: {BG_PANEL};
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}

#bottomTabBar::tab:hover:!selected {{
    background: {BG_INPUT_HOVER};
    color: {TEXT_PRIMARY};
}}

#actionBar {{
    background: {BG_PANEL};
    border-top: 1px solid {BORDER};
}}

#panelTitle {{
    font-size: {FS_TITLE}px;
    font-weight: 600;
    padding: 2px 0 6px 0;
    color: {TEXT_PRIMARY};
}}

#infoBox {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px;
    color: {TEXT_PRIMARY};
    line-height: 160%;
}}

#mutedLabel {{
    color: {TEXT_SECONDARY};
    font-size: {FS_SMALL}px;
}}

#hintLabel {{
    color: {TEXT_MUTED};
    font-size: {FS_SMALL}px;
}}

#viewerTitle {{
    background: {BG_HEADER};
    color: {TEXT_PRIMARY};
    padding-left: 10px;
    font-size: {FS_SMALL}px;
    font-weight: 600;
    border-left: 3px solid {ACCENT};
}}

/* ---------- 分组框 ---------- */
QGroupBox {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px 8px 12px 8px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {ACCENT_PRESSED};
    background: {BG_PANEL};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {BG_INPUT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 6px 12px;
    color: {TEXT_PRIMARY};
}}

QPushButton:hover {{
    background: {BG_INPUT_HOVER};
    border-color: {ACCENT};
}}

QPushButton:pressed {{
    background: #c3dbf8;
}}

QPushButton:disabled {{
    color: #8ba3ba;
    background: #eef4fb;
    border-color: #d5e3f2;
}}

QPushButton#primaryButton {{
    background: {ACCENT};
    border: 1px solid {ACCENT_PRESSED};
    color: {TEXT_ON_ACCENT};
    font-weight: 600;
    font-size: {FS_PRIMARY}px;
}}

QPushButton#primaryButton:hover {{
    background: {ACCENT_HOVER};
}}

QPushButton#primaryButton:pressed {{
    background: {ACCENT_PRESSED};
}}

QPushButton#primaryButton:disabled {{
    background: #a8c7e8;
    border-color: #a8c7e8;
    color: #ffffff;
}}

/* ---------- 输入控件 ---------- */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {BG_INPUT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 22px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: {TEXT_ON_ACCENT};
}}

QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {ACCENT};
}}

QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {ACCENT};
    background: {BG_ELEVATED};
}}

QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{
    background: #eef4fb;
    color: #8ba3ba;
    border-color: #d5e3f2;
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER_STRONG};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
    padding: 4px;
    outline: none;
}}

QCheckBox, QRadioButton {{
    spacing: 6px;
    color: {TEXT_PRIMARY};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {BORDER_STRONG};
    background: {BG_ELEVATED};
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT_PRESSED};
}}

/* ---------- 列表/表格 ---------- */
QListWidget, QTableWidget {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {TEXT_PRIMARY};
    alternate-background-color: {BG_ALT_ROW};
}}

QListWidget::item {{
    padding: 5px 8px;
    border-radius: 5px;
    color: {TEXT_PRIMARY};
}}

QListWidget::item:selected, QListWidget::item:selected:active {{
    background: {ACCENT_SOFT};
    color: {TEXT_PRIMARY};
}}

QListWidget::item:hover {{
    background: {BG_INPUT_HOVER};
}}

QTableWidget {{
    gridline-color: #dbe8f6;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
}}

QTableWidget::item {{
    padding: 3px;
    color: {TEXT_PRIMARY};
}}

QTableWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT_PRIMARY};
}}

QHeaderView {{
    background: {BG_HEADER};
}}

QHeaderView::section {{
    background: {BG_HEADER};
    color: {TEXT_PRIMARY};
    border: none;
    border-bottom: 1px solid {BORDER_STRONG};
    border-right: 1px solid #e2edf9;
    padding: 6px 4px;
    font-size: {FS_SMALL}px;
    font-weight: 600;
}}

QTableCornerButton::section {{
    background: {BG_HEADER};
    border: none;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: #c2d8ef;
    border-radius: 5px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: #a6c6e8;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: #c2d8ef;
    border-radius: 5px;
    min-width: 28px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #a6c6e8;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

/* ---------- 其他 ---------- */
QSplitter::handle {{
    background: {BORDER};
    width: 6px;
}}

QSplitter::handle:horizontal {{
    width: 6px;
}}

/* 上下分割条：加高加宽，便于鼠标上下拖动底部日志/指标面板 */
QSplitter::handle:vertical {{
    height: 6px;
    background: {BORDER};
}}

QSplitter::handle:hover, QSplitter::handle:pressed {{
    background: {ACCENT};
}}

/* ---------- 左侧折叠条（VS Code 风格侧边栏开关） ---------- */
#sidebarStrip {{
    background: {BG_PANEL};
    border-right: 1px solid {BORDER};
}}

#sidebarStripLabel {{
    color: {TEXT_MUTED};
    font-size: {FS_TINY}px;
    letter-spacing: 1px;
}}

QToolButton#sidebarToggle {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {ACCENT};
    font-size: {FS_TINY}px;
    padding: 0px;
}}

QToolButton#sidebarToggle:hover {{
    background: {ACCENT};
    border-color: {ACCENT_PRESSED};
    color: {TEXT_ON_ACCENT};
}}

QToolButton#sidebarToggle:pressed {{
    background: {ACCENT_PRESSED};
    color: {TEXT_ON_ACCENT};
}}

QStatusBar {{
    background: {BG_PANEL};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {BORDER};
}}

QStatusBar QLabel {{
    color: {TEXT_SECONDARY};
}}

QMenuBar {{
    background: {BG_PANEL};
    color: {TEXT_PRIMARY};
}}

QMenuBar::item {{
    background: transparent;
    color: {TEXT_PRIMARY};
}}

QMenuBar::item:selected {{
    background: {BG_INPUT};
    border-radius: 4px;
}}

QMenu {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER_STRONG};
    padding: 6px;
    color: {TEXT_PRIMARY};
}}

QMenu::item {{
    padding: 6px 22px 6px 14px;
    border-radius: 5px;
    color: {TEXT_PRIMARY};
}}

QMenu::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT_PRIMARY};
}}

QDockWidget {{
    color: {TEXT_SECONDARY};
    titlebar-close-icon: none;
}}

QDockWidget::title {{
    background: {BG_HEADER};
    color: {TEXT_PRIMARY};
    padding: 5px 8px;
    border-top: 1px solid {BORDER};
}}

#logView {{
    background: {BG_LOG};
    color: #22405c;
    border: none;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: {FS_LOG}px;
    padding: 6px;
}}

QProgressBar {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    height: 12px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: {FS_MICRO}px;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 4px;
}}

QToolTip {{
    background: #ffffff;
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    padding: 4px 6px;
}}

QMessageBox {{
    background: {BG_BASE};
}}

QMessageBox QLabel {{
    color: {TEXT_PRIMARY};
}}
"""

# QSS 模板引用的颜色变量（与上方常量一一对应）
_QSS_COLORS = {
    "ACCENT": ACCENT,
    "ACCENT_HOVER": ACCENT_HOVER,
    "ACCENT_PRESSED": ACCENT_PRESSED,
    "ACCENT_SOFT": ACCENT_SOFT,
    "BG_BASE": BG_BASE,
    "BG_PANEL": BG_PANEL,
    "BG_ELEVATED": BG_ELEVATED,
    "BG_INPUT": BG_INPUT,
    "BG_INPUT_HOVER": BG_INPUT_HOVER,
    "BG_ALT_ROW": BG_ALT_ROW,
    "BG_HEADER": BG_HEADER,
    "BG_LOG": BG_LOG,
    "TEXT_PRIMARY": TEXT_PRIMARY,
    "TEXT_SECONDARY": TEXT_SECONDARY,
    "TEXT_MUTED": TEXT_MUTED,
    "TEXT_ON_ACCENT": TEXT_ON_ACCENT,
    "BORDER": BORDER,
    "BORDER_STRONG": BORDER_STRONG,
}


def build_qss(font_size: int = DEFAULT_FONT_SIZE) -> str:
    """按基准字号生成完整样式表（颜色不变，仅字号随 font_size 缩放）。"""
    values = dict(_QSS_COLORS)
    values.update(font_sizes(font_size))
    return _QSS_TEMPLATE.format(**values)


# 默认字号下的样式表（历史代码/脚本按 LIGHT_QSS 直接引用时仍可用）
LIGHT_QSS = build_qss()

# 兼容旧命名（历史代码/脚本可能按 DARK_QSS 引用）
DARK_QSS = LIGHT_QSS


def apply_theme(app, font_size: int = DEFAULT_FONT_SIZE) -> None:
    """给 QApplication 应用蓝白浅色主题与调色板（函数名保持向后兼容）。

    font_size 为界面基准字号（px），用于生成样式表并同步 QApplication 默认字体，
    使菜单、原生对话框等 QSS 覆盖不到的位置也跟随“视图 → 界面字体大小”一起变化。
    """
    from PyQt5.QtGui import QColor, QFont, QPalette

    size = clamp_font_size(font_size)

    app_font = QFont("Microsoft YaHei UI")
    app_font.setPixelSize(font_sizes(size)["FS_BASE"])
    app.setFont(app_font)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_BASE))
    palette.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Base, QColor(BG_ELEVATED))
    palette.setColor(QPalette.AlternateBase, QColor(BG_ALT_ROW))
    palette.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Button, QColor(BG_INPUT))
    palette.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Highlight, QColor(ACCENT_SOFT))
    palette.setColor(QPalette.HighlightedText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
    if hasattr(QPalette, "PlaceholderText"):
        palette.setColor(QPalette.PlaceholderText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#8ba3ba"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#8ba3ba"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#8ba3ba"))
    app.setPalette(palette)
    app.setStyleSheet(build_qss(size))
