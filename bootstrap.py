"""环境初始化与路径配置核心逻辑。

职责边界：
1. 保证 ``ipscaling`` 包可被导入（无论从何处启动、是否被 PyInstaller 打包）。
2. 保证运行期目录（configs/data/results/logs/dist）存在。
3. 初始化日志系统，把启动异常写入 logs/。
4. 统一处理第三方运行库的导入顺序（历史经验：onnxruntime 与 PyQt5 同进程时，
   若 PyQt5 先加载，部分 Windows 环境下会让 onnxruntime 的 DLL 加载失败）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def project_root() -> Path:
    """返回项目根目录（兼容源码运行与 PyInstaller 解包运行）。"""
    if getattr(sys, "frozen", False):
        # 打包后：exe 所在目录即项目根
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def ensure_sys_path() -> Path:
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


@dataclass
class RuntimeContext:
    """一次启动过程中准备好的运行环境。"""

    root: Path
    config: object
    registry: object
    logger: object


def _preload_onnxruntime() -> str:
    """在导入 Qt 之前预加载 onnxruntime，规避 Windows 下的 DLL 顺序问题。"""
    try:
        import onnxruntime  # noqa: F401

        return f"onnxruntime {onnxruntime.__version__}"
    except Exception as exc:  # noqa: BLE001
        return f"onnxruntime 不可用（{type(exc).__name__}），深度学习超分将自动回退"


def prepare(verbose: bool = False, log_level: str = "INFO") -> RuntimeContext:
    """完成全部启动前准备工作，返回运行上下文。"""
    root = ensure_sys_path()

    # 延迟导入，确保 sys.path 已就绪
    from ipscaling.utils.config import Config, ensure_directories
    from ipscaling.utils.logger import get_logger, setup_logging

    ensure_directories()
    setup_logging(level=log_level, to_console=verbose)
    log = get_logger("ipscaling.bootstrap")

    log.info("项目根目录：%s", root)
    log.info("Python：%s", sys.version.split()[0])

    runtime_note = _preload_onnxruntime()
    log.info("运行库：%s", runtime_note)

    config = Config.load()

    from ipscaling.engine.registry import EngineRegistry

    registry = EngineRegistry(config)
    keys = [item["key"] for item in registry.catalog()]
    log.info("已注册算法：%s", ", ".join(keys))

    return RuntimeContext(root=root, config=config, registry=registry, logger=log)


def configure_qt_environment() -> None:
    """在创建 QApplication 之前设置 DPI 相关环境变量。"""
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")


def open_path(path: Path) -> None:
    """用系统默认程序打开路径（Windows 资源管理器）。"""
    if not path.exists():
        return
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606
    else:
        import subprocess

        subprocess.Popen(["xdg-open", str(path)])
