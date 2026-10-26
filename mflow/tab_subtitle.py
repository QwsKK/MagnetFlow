# -*- coding: utf-8 -*-
"""页签：字幕流水线（复用 115Translate / rawsub 的 run.py CLI）。"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from . import paths
from .logconsole import LogConsole, extract_progress
from .runner import CommandRunner

_CMDS = {
    "run 完整流程（扫描→下载→转写→翻译→回写）": ["run"],
    "run --retry-failed（只重试失败条目）": ["run", "--retry-failed"],
    "scan（只扫描 WebDAV 更新清单）": ["scan"],
    "status（查看进度统计）": ["status"],
    "doctor（环境体检）": ["doctor"],
}


class SubtitleTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.console = LogConsole(self)
        self.runner = CommandRunner(master.winfo_toplevel(), self.console)
        self.runner.on_finish = self._on_finish
        app.register_console(self.console)
        self.console.set_hook(self._on_line)

        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text="命令：").pack(side=tk.LEFT)
        self.cmd_var = tk.StringVar(value=list(_CMDS)[0])
        self.cmd_box = ttk.Combobox(row, textvariable=self.cmd_var, width=42,
                                    state="readonly", values=list(_CMDS))
        self.cmd_box.pack(side=tk.LEFT)

        ttk.Label(row, text="  本次上限：").pack(side=tk.LEFT)
        self.limit_var = tk.StringVar(value="0")
        ttk.Entry(row, textvariable=self.limit_var, width=6).pack(side=tk.LEFT)
        ttk.Label(row, text="部（0=不限，仅 run 生效）").pack(side=tk.LEFT, padx=(4, 12))

        self.btn_start = ttk.Button(row, text="开始", width=8, command=self.start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_pause = ttk.Button(row, text="暂停", width=8,
                                    command=self.pause_resume, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, padx=4)
        self.btn_stop = ttk.Button(row, text="结束", width=8, command=self.stop,
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)

        # 状态行：当前第几部 / 进度
        status_row = ttk.Frame(self)
        status_row.pack(fill=tk.X, pady=(0, 6))
        self.item_label = ttk.Label(status_row, text="条目：-", foreground="#0a6ebd")
        self.item_label.pack(side=tk.LEFT)
        self.pct_label = ttk.Label(status_row, text="", foreground="#1a7f37")
        self.pct_label.pack(side=tk.LEFT, padx=20)
        self.timer_label = ttk.Label(status_row, text="", foreground="#666")
        self.timer_label.pack(side=tk.LEFT, padx=20)

        links = ttk.Frame(self)
        links.pack(fill=tk.X, pady=(0, 6))
        for text, target in (
                ("字幕配置文件 config.yaml", paths.SUBTITLE_CONFIG),
                ("工作目录 work/", paths.SUBTITLE_DIR / "work"),
                ("字幕日志 logs/", paths.SUBTITLE_DIR / "logs"),
        ):
            if target.exists() or target.parent.exists():
                ttk.Button(links, text=text, width=26,
                           command=lambda t=target: _open_path(t)
                           ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(links, text="（并发 / GPU / 翻译开关都在 config.yaml 里调）",
                  foreground="#666").pack(side=tk.LEFT)

        self.console.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ 逻辑
    def start(self) -> None:
        cmd = _CMDS.get(self.cmd_var.get(), ["run"])
        args = list(cmd)
        if args[0] == "run":
            try:
                limit = int(self.limit_var.get().strip() or "0")
                if limit > 0:
                    args += ["--limit", str(limit)]
            except ValueError:
                self.console.append_ui("上限要填整数")
                return
        if self.runner.start(paths.SUBTITLE_RUN, args,
                             cwd=paths.SUBTITLE_DIR, name="字幕"):
            self._set_running(True)
            self.item_label["text"] = "条目：-"
            self.pct_label["text"] = ""

    def pause_resume(self) -> None:
        state = self.runner.pause_resume()
        if state is None:
            self.console.append_ui("挂起不可用（需要 Windows）或进程已退出")
            return
        self.btn_pause["text"] = "恢复" if state else "暂停"
        self.console.append_ui("[界面] 已挂起整个进程树（含 ffmpeg / infer.exe）"
                               if state else "[界面] 已恢复")

    def stop(self) -> None:
        if not messagebox_ok(self):
            return
        self.runner.stop()
        self.btn_stop["state"] = tk.DISABLED

    def _on_finish(self, code, name) -> None:
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.btn_start["state"] = tk.DISABLED if running else tk.NORMAL
        self.btn_pause["state"] = tk.NORMAL if running else tk.DISABLED
        self.btn_pause["text"] = "暂停"
        self.btn_stop["state"] = tk.NORMAL if running else tk.DISABLED
        if not running:
            self.timer_label["text"] = ""

    def _on_line(self, raw: str) -> None:
        info = extract_progress(raw)
        if not info:
            return
        if "item" in info:
            self.item_label["text"] = f"条目：{info['item']}  {info['name']}"
            self.pct_label["text"] = ""
        elif "pct" in info:
            self.pct_label["text"] = f"进度：{info['pct']}"

    def tick(self, now: float) -> None:
        if self.runner.running:
            self.timer_label["text"] = f"已运行 {self.runner.elapsed_text()}"


def messagebox_ok(tab) -> bool:
    from tkinter import messagebox
    return messagebox.askyesno(
        "结束任务",
        "确定强制结束字幕流水线？\n"
        "会杀掉整棵进程树（含 ffmpeg / infer.exe），断点续跑会自动接着来。",
        parent=tab)


def _open_path(p) -> None:
    import subprocess
    if p.is_file():
        os.startfile(str(p))
    elif p.is_dir():
        os.startfile(str(p))
    else:
        subprocess.Popen(["explorer", str(p.parent)],
                         creationflags=0x08000000)
