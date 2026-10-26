# -*- coding: utf-8 -*-
"""页签：设置（快捷打开各配置文件 / 目录 + 环境信息）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import paths

ENTRIES = [
    ("推送 115 配置", paths.PUSH_CONFIG, "本项目的 115 推送设置（cookie 等）"),
    ("采集器主配置", paths.HARVESTER_DIR / "config" / "default.yaml",
     "并发、限速、去重等采集参数"),
    ("站点规则目录", paths.SITES_DIR, "每个网站一份 yaml 规则，example.yaml 是模板"),
    ("字幕流水线配置", paths.SUBTITLE_CONFIG, "WebDAV 地址、并发、GPU、翻译开关"),
    ("字幕引擎目录", Path("D:/BaiduNetdiskDownload/AI_src"),
     "外部转写+翻译引擎（infer.exe）所在位置"),
    ("磁力库数据库", paths.HARVESTER_DIR / "state", "harvest.db：全部磁力与推送状态"),
    ("字幕状态数据库", paths.SUBTITLE_DIR / "data", "state.db：断点续跑记录"),
    ("采集导出目录", paths.HARVEST_OUT_DIR, "latest.json / -max.json / csv / txt"),
    ("字幕工作目录", paths.SUBTITLE_DIR / "work", "下载、转写中间文件与成品字幕"),
    ("项目日志目录", paths.LOGS_DIR, "本项目自己的日志"),
]


class SettingsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app

        intro = ttk.LabelFrame(self, text="运行环境", padding=10)
        intro.pack(fill=tk.X, pady=(0, 10))
        py = paths.python_exe()
        try:
            ver = subprocess.run(
                [py, "-c", "import sys,platform;"
                 "print(platform.python_version())"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
        except Exception:
            ver = "?"
        lines = [
            f"项目根目录：{paths.ROOT}",
            f"子进程 Python：{py}   （版本 {ver}）",
            f"界面 Python：{sys.version.split()[0]}",
        ]
        try:
            import p115client  # noqa: F401
            lines.append("p115client：已安装 ✓（推送 115 可用）")
        except ImportError:
            lines.append("p115client：未安装（pip install p115client 后推送可用，需 Py3.12+）")
        for line in lines:
            ttk.Label(intro, text=line, foreground="#333").pack(anchor=tk.W)

        grid = ttk.LabelFrame(self, text="配置文件与目录（点开即用默认程序打开）",
                              padding=10)
        grid.pack(fill=tk.BOTH, expand=True)
        for i, (name, target, desc) in enumerate(ENTRIES):
            rowf = ttk.Frame(grid)
            rowf.pack(fill=tk.X, pady=2)
            btn = ttk.Button(rowf, text=name, width=18,
                             command=lambda t=target: self._open(t))
            btn.pack(side=tk.LEFT)
            if not os.path.exists(str(target)):
                btn["state"] = tk.DISABLED
            ttk.Label(rowf, text=desc, foreground="#666").pack(side=tk.LEFT, padx=10)

        ttk.Label(self, foreground="#888", justify=tk.LEFT, text=(
            "定时任务：字幕自动夜跑用 subtitle/scripts/register_task.ps1（Windows 计划任务）。\n"
            "命令行等价操作见项目根目录 run.py chain / harvest / push / subtitle。"
        )).pack(anchor=tk.W, pady=(10, 0))

    @staticmethod
    def _open(target) -> None:
        t = str(target)
        if os.path.isdir(t):
            os.startfile(t)
        elif os.path.isfile(t):
            os.startfile(t)
        else:
            from tkinter import messagebox
            messagebox.showinfo("不存在", f"路径不存在：\n{t}")

    def tick(self, now: float) -> None:
        pass
