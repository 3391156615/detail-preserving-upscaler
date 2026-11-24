"""GUI 程序启动点。

用法（由 launcher.ps1 / setup.ps1 自动调用，也可手动 ``python app.py``）：

    python app.py            # 正常启动
    python app.py --verbose  # 控制台输出日志
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _fail(title: str, detail: str) -> int:
    """启动失败时的兜底提示：写日志 + 弹窗，避免静默退出。"""
    root = Path(__file__).resolve().parent
    try:
        (root / "logs").mkdir(exist_ok=True)
        with open(root / "logs" / "startup_error.log", "a", encoding="utf-8") as fp:
            fp.write(detail + "\n")
    except Exception:
        pass
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, title, detail[-3000:])
    except Exception:
        print(f"{title}\n{detail}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    verbose = "--verbose" in argv or "-v" in argv

    try:
        import bootstrap

        bootstrap.configure_qt_environment()
        ctx = bootstrap.prepare(verbose=verbose)
    except Exception:
        return _fail("环境初始化失败", traceback.format_exc())

    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication

        # 高分屏支持必须在 QApplication 创建前声明
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

        app = QApplication.instance() or QApplication(argv)
        app.setApplicationName("Detail-Preserving Image Upscaling System")
        app.setFont(QFont("Microsoft YaHei UI", 9))

        from ipscaling.gui.theme import apply_theme

        apply_theme(app)

        from ipscaling.gui.main_window import MainWindow

        window = MainWindow(registry=ctx.registry, config=ctx.config)
        window.show()
        return app.exec_()
    except Exception:
        return _fail("界面启动失败", traceback.format_exc())


if __name__ == "__main__":
    sys.exit(main())
