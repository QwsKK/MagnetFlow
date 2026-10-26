# -*- coding: utf-8 -*-
"""MagnetFlow 主窗口：五个页签 + 统一的日志排空循环。

启动（项目根目录）：
    python app.py            # 或双击 启动界面.bat
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__, paths
from .tab_harvest import HarvestTab
from .tab_library import LibraryTab
from .tab_overview import OverviewTab
from .tab_push import PushTab
from .tab_settings import SettingsTab
from .tab_subtitle import SubtitleTab

_TABS = [
    ("总览", OverviewTab),
    ("磁力采集", HarvestTab),
    ("磁力库", LibraryTab),
    ("推送 115", PushTab),
    ("字幕流水线", SubtitleTab),
    ("设置", SettingsTab),
]


class MagnetFlowApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"MagnetFlow v{__version__} — 磁力采集 · 推送115 · 自动字幕")
        self.geometry("1000x720")
        self.minsize(880, 600)

        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                continue
        default_font = ("Microsoft YaHei UI", 10)
        self.option_add("*TCombobox*Listbox.font", ("Consolas", 10))

        self.consoles = []           # 所有页签的 LogConsole，统一 after 排空
        self.tabs = {}               # 标题 -> 页签实例

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))
        for title, cls in _TABS:
            tab = cls(nb, self)
            self.tabs[title] = tab
            nb.add(tab, text=title)
        self.notebook = nb

        bar = ttk.Frame(self, padding=(10, 4))
        bar.pack(fill=tk.X)
        py = paths.python_exe()
        venv = "项目 venv" if "Scripts" in py or "bin" in py else "系统 Python"
        ttk.Label(bar, text=f"MagnetFlow v{__version__} ｜ Python: {py}（{venv}）",
                  foreground="#666").pack(side=tk.LEFT)
        self.global_label = ttk.Label(bar, text="", foreground="#0a6ebd")
        self.global_label.pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    # ------------------------------------------------------------ 公共接口
    def register_console(self, console) -> None:
        self.consoles.append(console)

    # ------------------------------------------------------------ 轮询循环
    def _poll(self) -> None:
        try:
            import time
            now = time.time()
            busy = []
            for title, tab in self.tabs.items():
                try:
                    tab.tick(now)
                except Exception:
                    pass
                runner = getattr(tab, "runner", None)
                if runner is not None and runner.running:
                    busy.append(title)
            self.global_label["text"] = (
                "运行中：" + "、".join(busy) if busy else "空闲")
            for console in self.consoles:
                console.drain()
        finally:
            self.after(120, self._poll)

    # ------------------------------------------------------------ 退出确认
    def _on_close(self) -> None:
        running = [t for t, tab in self.tabs.items()
                   if getattr(tab, "runner", None) and tab.runner.running]
        if running:
            if not messagebox.askyesno(
                    "退出 MagnetFlow",
                    f"还有任务在运行：{'、'.join(running)}。\n\n"
                    "退出会强制结束这些进程（含字幕的 ffmpeg / infer.exe），"
                    "下次启动可断点续跑。确定退出？"):
                return
            for title in running:
                try:
                    self.tabs[title].runner.stop()
                except Exception:
                    pass
        self.destroy()


def main() -> int:
    paths.ensure_dirs()
    app = MagnetFlowApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
