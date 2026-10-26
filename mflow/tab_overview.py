# -*- coding: utf-8 -*-
"""页签：总览（一键全流程 + 环境体检 + 三个环节的状态汇总）。"""
from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import ttk

from . import paths
from .chain import enabled_sites
from .logconsole import LogConsole, extract_progress
from .push115 import fetch_pending, load_push_config
from .runner import CommandRunner


class OverviewTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.console = LogConsole(self)
        self.runner = CommandRunner(master.winfo_toplevel(), self.console)
        self.runner.on_finish = self._on_finish
        app.register_console(self.console)
        self.console.set_hook(self._on_line)

        # ---------------- 大按钮区 ----------------
        hero = ttk.Frame(self)
        hero.pack(fill=tk.X, pady=(0, 8))
        self.btn_chain = ttk.Button(hero, text="▶ 一键全流程",
                                    command=self.run_chain)
        self.btn_chain.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_doctor = ttk.Button(hero, text="环境体检", command=self.run_doctor)
        self.btn_doctor.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = ttk.Button(hero, text="停止", command=self.runner.stop,
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_refresh = ttk.Button(hero, text="刷新状态", command=self.refresh)
        self.btn_refresh.pack(side=tk.RIGHT)
        self.step_label = ttk.Label(hero, text="", foreground="#0a6ebd")
        self.step_label.pack(side=tk.LEFT, padx=12)

        ttk.Label(
            self, foreground="#555",
            text="流程：采集新磁力 → 推送 115 云添加 →（115 自动下载，经 WebDAV 挂载可见）"
                 "→ 字幕流水线转写翻译回写",
        ).pack(anchor=tk.W, pady=(0, 8))

        # ---------------- 状态卡片 ----------------
        cards = ttk.Frame(self)
        cards.pack(fill=tk.X, pady=(0, 8))
        self.card_harvest = ttk.LabelFrame(cards, text="① 磁力采集", padding=8)
        self.card_push = ttk.LabelFrame(cards, text="② 推送 115", padding=8)
        self.card_sub = ttk.LabelFrame(cards, text="③ 字幕流水线", padding=8)
        for i, c in enumerate((self.card_harvest, self.card_push, self.card_sub)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0, 8))
            cards.columnconfigure(i, weight=1)
        self.hv_label = ttk.Label(self.card_harvest, foreground="#333", justify=tk.LEFT)
        self.hv_label.pack(anchor=tk.W)
        self.push_label = ttk.Label(self.card_push, foreground="#333", justify=tk.LEFT)
        self.push_label.pack(anchor=tk.W)
        self.sub_label = ttk.Label(self.card_sub, foreground="#333", justify=tk.LEFT)
        self.sub_label.pack(anchor=tk.W)

        self.console.pack(fill=tk.BOTH, expand=True)
        self.refresh()
        self.console.append_ui("点「一键全流程」按顺序跑完三个环节；单环节去对应页签。")

    # ------------------------------------------------------------------ 汇总
    def refresh(self) -> None:
        sites = enabled_sites()
        self.hv_label["text"] = (
            f"启用站点：{len(sites)} 个\n{'、'.join(sites) or '（无）'}\n"
            f"数据库：{'就绪' if paths.HARVEST_DB.exists() else '未建立'}")

        cfg = load_push_config()
        cookie_ok = bool(str(cfg.get("cookie") or "").strip())
        try:
            import p115client  # noqa: F401
            pkg_ok = True
        except ImportError:
            pkg_ok = False
        try:
            pending = len(fetch_pending(paths.HARVEST_DB, "", 0))
        except Exception:
            pending = "?"
        self.push_label["text"] = (
            f"待推送：{pending} 条\n"
            f"p115client：{'已安装 ✓' if pkg_ok else '未安装 ✗'}\n"
            f"Cookie：{'已配置 ✓' if cookie_ok else '未配置 ✗（推送页签里填）'}")

        lines = []
        try:
            con = sqlite3.connect(f"file:{paths.SUBTITLE_DB}?mode=ro", uri=True)
            stages = dict(con.execute("SELECT stage, COUNT(*) FROM files GROUP BY stage"))
            con.close()
            done = stages.get("done", 0)
            total = sum(stages.values())
            lines.append(f"字幕：{done}/{total} 完成")
            for stage in ("pending", "transcribed", "translated", "written", "failed"):
                if stages.get(stage):
                    lines.append(f"  {stage}: {stages[stage]}")
        except Exception:
            lines.append("字幕状态库未建立")
        infer = r"D:/BaiduNetdiskDownload/AI_src/AI_src/infer.exe"
        try:
            import yaml
            scfg = yaml.safe_load(
                paths.SUBTITLE_CONFIG.read_text(encoding="utf-8")) or {}
            exe = ((scfg.get("asr") or {}).get("external_exe") or infer)
        except Exception:
            exe = infer
        from pathlib import Path
        lines.append(f"转写引擎：{'就绪 ✓' if Path(exe).exists() else '不存在 ✗'}")
        self.sub_label["text"] = "\n".join(lines)

    # ------------------------------------------------------------------ 运行
    def run_chain(self) -> None:
        if self.runner.running:
            return
        self.refresh()
        if self.runner.start(paths.ROOT / "run.py", ["chain"],
                             cwd=paths.ROOT, name="全流程"):
            self._set_running(True)
            self.step_label["text"] = "全流程进行中…"

    def run_doctor(self) -> None:
        if self.runner.running:
            return
        if self.runner.start(paths.ROOT / "run.py", ["doctor"],
                             cwd=paths.ROOT, name="体检"):
            self._set_running(True)
            self.step_label["text"] = "体检中…"

    def _set_running(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        self.btn_chain["state"] = state
        self.btn_doctor["state"] = state
        self.btn_stop["state"] = tk.NORMAL if running else tk.DISABLED

    def _on_finish(self, code, name) -> None:
        self._set_running(False)
        self.step_label["text"] = "全流程完成" if code == 0 else f"结束（退出码 {code}）"
        self.refresh()

    def _on_line(self, raw: str) -> None:
        info = extract_progress(raw)
        if info and "item" in info:
            self.step_label["text"] = f"字幕 {info['item']}：{info['name']}"

    def tick(self, now: float) -> None:
        if self.runner.running:
            t = self.runner.elapsed_text()
            base = self.step_label["text"].split("  ⏱")[0]
            self.step_label["text"] = f"{base}  ⏱{t}"
