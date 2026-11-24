"""GUI 冒烟测试：离屏模式跑通「载入 → 全算法对比 → 报告导出 → 截图」。

运行：``.venv\\Scripts\\python.exe tools\\smoke_gui.py``
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402

ctx = bootstrap.prepare(verbose=False)


def spin(app, win, timeout: float = 300.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if not win._busy and not win._queue:
            for _ in range(6):
                app.processEvents()
                time.sleep(0.02)
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    from PyQt5.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: print("[CRITICAL]", a[1:3]))

    from ipscaling.gui.theme import apply_theme

    apply_theme(app)

    from ipscaling.gui.main_window import MainWindow

    win = MainWindow(ctx.registry, ctx.config)
    win.resize(1480, 940)
    win.show()
    app.processEvents()

    from ipscaling.utils.config import DATA_DIR
    from ipscaling.utils.testpatterns import ensure_patterns

    sample = ensure_patterns(DATA_DIR / "smoke" / "patterns")["photo.png"]
    win.workbench.add_files([str(sample)])
    app.processEvents()
    print("已载入：", win.workbench.current_file())

    # 单算法
    win._run_single()
    print("单算法完成：", spin(app, win, 120), "busy:", win._busy)

    # 全算法对比
    win._run_compare_all()
    done = spin(app, win, 600)
    print("全算法对比完成：", done, "行数：", win.table.rowCount())
    for row in win._compare_rows:
        print(
            f"  {row['display_name']:<16} {row['elapsed_ms']:8.1f} ms  "
            f"PSNR={row.get('psnr')}  SSIM={row.get('ssim')}  "
            f"out={row['output_size']}  tiles={row['tiles']}"
        )
    print("摘要：", win.lbl_summary.text())

    # 差异图
    win.toolbar_view.chk_diff.setChecked(True)
    app.processEvents()
    win.toolbar_view.chk_diff.setChecked(False)
    app.processEvents()

    # 报告导出
    win._export_report()
    app.processEvents()

    out_dir = ROOT / "data" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "gui_compare.png"
    win.grab().save(str(shot))
    print("截图：", shot)

    log_text = win.log_view.toPlainText()
    print("日志尾部：")
    print("\n".join(log_text.splitlines()[-8:]))
    ok = done and win.table.rowCount() >= 6
    print("SMOKE:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
