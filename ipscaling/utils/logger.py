"""统一日志：控制台 + 滚动文件。

设计要点：
1. 幂等 —— 重复调用 setup_logging 不会挂上重复 handler；
2. 目录自愈 —— 日志目录不存在时自动创建；
3. 未捕获异常统一落盘，保证任何一次崩溃都能在 logs/ 里找到堆栈。
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-26s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False
_lock = threading.Lock()
_root_logger = logging.getLogger("ipscaling")


def setup_logging(
    log_dir: Path | str | None = None,
    level: str | int = "INFO",
    to_console: bool = False,
    max_mb: int = 2,
    backup_count: int = 5,
    log_name: str = "ipscaling.log",
) -> logging.Logger:
    """初始化根日志器（幂等）。返回 `ipscaling` 根日志器。"""

    global _configured
    with _lock:
        if _configured:
            return _root_logger

        _root_logger.setLevel(_resolve_level(level))
        _root_logger.propagate = False

        fmt = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

        if log_dir is not None:
            directory = Path(log_dir)
            try:
                directory.mkdir(parents=True, exist_ok=True)
                file_handler = RotatingFileHandler(
                    directory / log_name,
                    maxBytes=max(1, int(max_mb)) * 1024 * 1024,
                    backupCount=max(1, int(backup_count)),
                    encoding="utf-8",
                )
                file_handler.setFormatter(fmt)
                file_handler.setLevel(logging.DEBUG)
                _root_logger.addHandler(file_handler)
            except OSError as exc:  # 日志目录不可写时不阻断主流程
                _root_logger.addHandler(logging.NullHandler())
                _root_logger.warning("日志文件不可用（%s），仅输出到控制台", exc)

        if to_console and sys.stderr is not None:
            console = logging.StreamHandler(stream=sys.stderr)
            console.setFormatter(fmt)
            console.setLevel(logging.INFO)
            _root_logger.addHandler(console)

        if not _root_logger.handlers:
            _root_logger.addHandler(logging.NullHandler())

        _install_excepthook()
        _configured = True
        return _root_logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取子日志器；未初始化时自动补一个最小可用配置。"""
    if not _configured:
        setup_logging()
    if not name:
        return _root_logger
    return _root_logger.getChild(name) if not name.startswith("ipscaling") else logging.getLogger(name)


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def _install_excepthook() -> None:
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):  # pragma: no cover - 仅在崩溃路径触发
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        _root_logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        previous(exc_type, exc_value, exc_tb)

    if not getattr(sys, "_ipscaling_excepthook", False):
        sys.excepthook = _hook
        sys._ipscaling_excepthook = True  # type: ignore[attr-defined]
