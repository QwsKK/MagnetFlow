# -*- coding: utf-8 -*-
"""页签：磁力采集（跑 harvester/harvest.py）。"""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import ttk

from . import paths
from .logconsole import LogConsole
from .runner import CommandRunner

_PAGES_SPLIT = re.compile(r"[,\s]+")


class HarvestTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.console = LogConsole(self)
        self.runner = CommandRunner(master.winfo_toplevel(), self.console)
        self.runner.on_finish = self._on_finish
        app.register_console(self.console)

        # ---------------- 参数行 1：站点 ----------------
        row1 = ttk.Frame(self)
        row1.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row1, text="站点：").pack(side=tk.LEFT)
        self.site_var = tk.StringVar()
        self.site_box = ttk.Combobox(row1, textvariable=self.site_var,
                                     width=28, state="readonly")
        self.site_box.pack(side=tk.LEFT)
        ttk.Button(row1, text="刷新列表", width=9,
                   command=self.refresh_sites).pack(side=tk.LEFT, padx=6)
        ttk.Button(row1, text="编辑站点配置",
                   command=lambda: os.startfile(str(paths.SITES_DIR))
                   ).pack(side=tk.LEFT)
        ttk.Label(row1, text="（站点规则都在 sites/*.yaml，界面只负责跑）",
                  foreground="#666").pack(side=tk.LEFT, padx=12)

        # ---------------- 参数行 2：页数 / 关键词 / 上限 ----------------
        row2 = ttk.Frame(self)
        row2.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row2, text="页码范围：").pack(side=tk.LEFT)
        self.pages_var = tk.StringVar(value="1 3")
        ttk.Entry(row2, textvariable=self.pages_var, width=10
                  ).pack(side=tk.LEFT)
        ttk.Label(row2, text="（如 1 5 表示抓 1~5 页）").pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row2, text="关键词：").pack(side=tk.LEFT)
        self.kw_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.kw_var, width=18).pack(side=tk.LEFT)
        ttk.Label(row2, text="（可留空）").pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row2, text="磁力上限：").pack(side=tk.LEFT)
        self.limit_var = tk.StringVar(value="0")
        ttk.Entry(row2, textvariable=self.limit_var, width=7).pack(side=tk.LEFT)
        ttk.Label(row2, text="（0 = 不限）").pack(side=tk.LEFT, padx=(4, 10))

        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="试跑（不入库不导出）",
                        variable=self.dry_var).pack(side=tk.LEFT)

        # ---------------- 控制行 ----------------
        row3 = ttk.Frame(self)
        row3.pack(fill=tk.X, pady=(2, 6))
        self.btn_start = ttk.Button(row3, text="开始采集", width=11,
                                    command=self.start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_pause = ttk.Button(row3, text="暂停", width=9,
                                    command=self.pause_resume, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, padx=6)
        self.btn_stop = ttk.Button(row3, text="停止", width=9,
                                   command=self.stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)
        self.status_label = ttk.Label(row3, text="空闲", foreground="#666")
        self.status_label.pack(side=tk.LEFT, padx=16)
        ttk.Button(row3, text="打开输出目录",
                   command=lambda: os.startfile(str(paths.HARVEST_OUT_DIR))
                   ).pack(side=tk.RIGHT)

        self.console.pack(fill=tk.BOTH, expand=True)
        self.refresh_sites()

    # ------------------------------------------------------------------ 逻辑
    def refresh_sites(self) -> None:
        names = []
        if paths.SITES_DIR.exists():
            for p in sorted(paths.SITES_DIR.glob("*.yaml")):
                if not p.name.startswith("_"):
                    names.append(p.stem)
        self.site_box["values"] = names
        if names and self.site_var.get() not in names:
            default = names[0]
            self.site_var.set(default)
        elif not names:
            self.site_var.set("")
        self.console.append_ui(
            f"sites/ 可用站点：{'、'.join(names) if names else '（空，复制 example.yaml 改一份）'}")

    def _build_args(self):
        site = self.site_var.get().strip()
        if not site:
            self.console.append_ui("请先选择站点（sites/ 目录为空时先建一个站点配置）")
            return None
        args = [paths.HARVEST_SCRIPT, "--site", site]
        parts = [p for p in _PAGES_SPLIT.split(self.pages_var.get().strip()) if p]
        try:
            nums = [int(p) for p in parts]
            if nums:
                args += ["--pages", *[str(n) for n in nums]]
        except ValueError:
            self.console.append_ui("页码范围请填两个整数，例如：1 5")
            return None
        kw = self.kw_var.get().strip()
        if kw:
            args += ["--keyword", kw]
        try:
            limit = int(self.limit_var.get().strip() or "0")
            if limit > 0:
                args += ["--limit", str(limit)]
        except ValueError:
            self.console.append_ui("磁力上限请填整数")
            return None
        if self.dry_var.get():
            args.append("--dry-run")
        return args

    def start(self) -> None:
        args = self._build_args()
        if args is None:
            return
        if self.runner.start(paths.HARVEST_SCRIPT, args[1:],
                             cwd=paths.HARVESTER_DIR, name="采集"):
            self._set_running(True, "采集中")

    def pause_resume(self) -> None:
        state = self.runner.pause_resume()
        if state is None:
            self.console.append_ui("进程不支持挂起（或已退出）")
            return
        self.btn_pause["text"] = "恢复" if state else "暂停"
        self.status_label["text"] = "已暂停" if state else "采集中"
        self.console.append_ui("[界面] 进程树已挂起" if state else "[界面] 进程树已恢复")

    def stop(self) -> None:
        self.runner.stop()
        self.status_label["text"] = "正在停止…"
        self.btn_stop["state"] = tk.DISABLED

    def _on_finish(self, code, name) -> None:
        self._set_running(False, "空闲")

    def _set_running(self, running: bool, status: str) -> None:
        self.btn_start["state"] = tk.DISABLED if running else tk.NORMAL
        self.btn_pause["state"] = tk.NORMAL if running else tk.DISABLED
        self.btn_pause["text"] = "暂停"
        self.btn_stop["state"] = tk.NORMAL if running else tk.DISABLED
        self.status_label["text"] = status

    def tick(self, now: float) -> None:
        if self.runner.running:
            pause_txt = "（已暂停）" if self.runner.paused else ""
            self.status_label["text"] = f"采集中 {self.runner.elapsed_text()}{pause_txt}"
