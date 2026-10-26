# -*- coding: utf-8 -*-
"""日志初始化：控制台 + 轮转文件，统一 UTF-8。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATEFMT = "%H:%M:%S"
_configured = False


def setup_logging(level: str = "INFO", log_file: str | Path | None = None, console: bool = True) -> None:
    """初始化根 logger。重复调用是安全的。"""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    if console:
        # Windows 控制台默认 GBK，errors="replace" 可避免中文直接崩掉
        try:
            sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        handler.setFormatter(formatter)
        root.addHandler(handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=8 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
