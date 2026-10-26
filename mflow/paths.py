# -*- coding: utf-8 -*-
"""全项目路径与解释器定位。

所有模块统一从这里取路径，保证无论从哪个目录启动（GUI、bat、计划任务），
各子模块都能正确解析到自己该待的工作目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

# mflow 包的上级目录就是 MagnetFlow 根
ROOT = Path(__file__).resolve().parent.parent

HARVESTER_DIR = ROOT / "harvester"
SUBTITLE_DIR = ROOT / "subtitle"
CONFIG_DIR = ROOT / "config"
LOGS_DIR = ROOT / "logs"

# 采集侧
HARVEST_SCRIPT = HARVESTER_DIR / "harvest.py"
PICK_MAX_SCRIPT = HARVESTER_DIR / "pick_max.py"
SITES_DIR = HARVESTER_DIR / "sites"
HARVEST_DB = HARVESTER_DIR / "state" / "harvest.db"
HARVEST_OUT_DIR = HARVESTER_DIR / "out"

# 字幕侧
SUBTITLE_RUN = SUBTITLE_DIR / "run.py"
SUBTITLE_CONFIG = SUBTITLE_DIR / "config.yaml"
SUBTITLE_DB = SUBTITLE_DIR / "data" / "state.db"

# 推送侧
PUSH_CONFIG = CONFIG_DIR / "push115.yaml"
PUSH_CONFIG_EXAMPLE = CONFIG_DIR / "push115.example.yaml"


def python_exe() -> str:
    """子进程统一使用的 Python：优先项目根 .venv，兜底当前解释器。"""
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return sys.executable


def pythonw_exe() -> str:
    """GUI 无窗口启动用的 pythonw，兜底 python -c 方式。"""
    exe = Path(python_exe())
    w = exe.with_name("pythonw.exe")
    return str(w) if w.exists() else str(exe)


def ensure_dirs() -> None:
    """补齐运行需要的目录（logs 等），GUI 与脚本共用。"""
    for d in (LOGS_DIR, HARVEST_OUT_DIR, SITES_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
