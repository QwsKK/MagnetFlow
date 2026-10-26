# -*- coding: utf-8 -*-
"""页签：推送 115（harvester 磁力库 -> 115 云添加，桥接步骤）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import yaml

from . import paths
from .logconsole import LogConsole
from .runner import CommandRunner
from .push115 import load_push_config

HEADER = """# MagnetFlow 推送 115 的配置
# cookie 获取：浏览器登录 https://115.com -> F12 控制台输入 document.cookie 复制整串。
# 也可以只填 UID=...; SEID=... 两项。留空则写 "${P115_COOKIE}" 走环境变量。
# save_path：115 网盘内的目标目录（离线下载落盘位置），留空 = 115 网页上设置的默认目录。
# 想和字幕流水线衔接：save_path 对应 WebDAV 视图里的 /115/... 同一区域即可。
"""


class PushTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.console = LogConsole(self)
        self.runner = CommandRunner(master.winfo_toplevel(), self.console)
        self.runner.on_finish = self._on_finish
        app.register_console(self.console)

        # ---------------- 配置区 ----------------
        grid = ttk.LabelFrame(self, text="推送配置（保存到 config/push115.yaml）",
                              padding=8)
        grid.pack(fill=tk.X, pady=(0, 6))

        self.cfg = load_push_config()

        ttk.Label(grid, text="cookie：").grid(row=0, column=0, sticky="e")
        self.cookie_var = tk.StringVar(value=str(self.cfg.get("cookie") or ""))
        self.cookie_entry = ttk.Entry(grid, textvariable=self.cookie_var,
                                      show="*", width=72)
        self.cookie_entry.grid(row=0, column=1, columnspan=3, sticky="ew")
        self.show_cookie_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(grid, text="显示", variable=self.show_cookie_var,
                        command=self._toggle_cookie).grid(row=0, column=4)

        ttk.Label(grid, text="保存目录：").grid(row=1, column=0, sticky="e")
        self.path_var = tk.StringVar(value=str(self.cfg.get("save_path") or ""))
        ttk.Entry(grid, textvariable=self.path_var, width=40
                  ).grid(row=1, column=1, sticky="w")
        ttk.Label(grid, text="（留空 = 115 默认下载目录；例：/云下载/学习资料/常规）",
                  foreground="#666").grid(row=1, column=2, columnspan=3, sticky="w")

        ttk.Label(grid, text="单次上限：").grid(row=2, column=0, sticky="e")
        self.max_var = tk.StringVar(value=str(self.cfg.get("max_per_run") or 20))
        ttk.Entry(grid, textvariable=self.max_var, width=8
                  ).grid(row=2, column=1, sticky="w")
        ttk.Label(grid, text="条").grid(row=2, column=1, sticky="e", padx=(60, 0))

        ttk.Label(grid, text="每条间隔：").grid(row=2, column=2, sticky="e")
        self.delay_var = tk.StringVar(value=str(self.cfg.get("delay_s") or 3.0))
        ttk.Entry(grid, textvariable=self.delay_var, width=8
                  ).grid(row=2, column=3, sticky="w")
        ttk.Label(grid, text="秒").grid(row=2, column=3, sticky="e", padx=(60, 0))

        self.enabled_var = tk.BooleanVar(value=bool(self.cfg.get("enabled", True)))
        ttk.Checkbutton(grid, text="启用推送（一键全流程会用到）",
                        variable=self.enabled_var).grid(row=3, column=1, sticky="w",
                                                        pady=(6, 0))

        grid.columnconfigure(1, weight=1)

        btns = ttk.Frame(grid)
        btns.grid(row=3, column=2, columnspan=3, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="保存配置", command=self.save_config
                   ).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="测试 Cookie", command=self.test_cookie
                   ).pack(side=tk.LEFT)

        # ---------------- 运行区 ----------------
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=(2, 6))
        self.btn_start = ttk.Button(row, text="开始推送", width=11,
                                    command=self.start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_stop = ttk.Button(row, text="停止", width=9, command=self.stop,
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=6)
        self.pending_label = ttk.Label(row, text="", foreground="#666")
        self.pending_label.pack(side=tk.LEFT, padx=12)
        ttk.Button(row, text="刷新统计", width=9, command=self.refresh_pending
                   ).pack(side=tk.LEFT)
        self.env_label = ttk.Label(row, text="", foreground="#666")
        self.env_label.pack(side=tk.RIGHT)

        self.console.pack(fill=tk.BOTH, expand=True)

        self._report_env()
        self.refresh_pending()
        if not str(self.cookie_var.get()).strip():
            self.console.append_ui(
                "还没配 cookie：登录 115 网页版，F12 里复制 document.cookie 贴到上面，"
                "保存后点「测试 Cookie」")

    # ------------------------------------------------------------------ 小工具
    def _toggle_cookie(self) -> None:
        self.cookie_entry["show"] = "" if self.show_cookie_var.get() else "*"

    def _report_env(self) -> None:
        try:
            import p115client  # noqa: F401
            self.env_label["text"] = "p115client：已安装 ✓"
        except ImportError:
            self.env_label["text"] = ("p115client：缺失（pip install p115client，"
                                      "需要 Python 3.12+）")

    def refresh_pending(self) -> None:
        from .push115 import fetch_pending
        rows = fetch_pending(paths.HARVEST_DB, "", 0)
        self.pending_label["text"] = f"当前待推送：{len(rows)} 条"

    # ------------------------------------------------------------------ 配置
    def save_config(self) -> None:
        data = dict(self.cfg)
        data["cookie"] = self.cookie_var.get().strip()
        data["save_path"] = self.path_var.get().strip()
        data["enabled"] = bool(self.enabled_var.get())
        try:
            data["max_per_run"] = int(self.max_var.get().strip() or "20")
            data["delay_s"] = float(self.delay_var.get().strip() or "3")
        except ValueError:
            self.console.append_ui("单次上限 / 每条间隔要填数字")
            return
        try:
            paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            paths.PUSH_CONFIG.write_text(
                HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
            self.cfg = data
            self.console.append_ui(f"配置已保存：{paths.PUSH_CONFIG}")
        except OSError as exc:
            self.console.append_ui(f"保存失败：{exc}")

    def test_cookie(self) -> None:
        from .push115 import test_cookie as _test
        self.console.append_ui("正在测试 cookie（会真连一次 115 接口）…")

        def worker():
            ok, msg = _test(self.cookie_var.get().strip())
            self.console.append_ui(("[OK] " if ok else "[FAIL] ") + msg)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ 运行
    def start(self) -> None:
        if self.runner.running:
            return
        self.save_config()
        self.runner.start(
            paths.ROOT / "run.py", ["push"],
            cwd=paths.ROOT, name="推送115",
            extra_env={"MFLOW_PUSH_TEST": ""},
        )
        self.btn_start["state"] = tk.DISABLED
        self.btn_stop["state"] = tk.NORMAL
        self.console.append_ui("推送任务已启动（逐条串行，看到失败先别慌，会自动保留重试）")

    def stop(self) -> None:
        self.runner.stop()
        self.btn_stop["state"] = tk.DISABLED

    def _on_finish(self, code, name) -> None:
        self.btn_start["state"] = tk.NORMAL
        self.btn_stop["state"] = tk.DISABLED
        self.refresh_pending()

    def tick(self, now: float) -> None:
        pass
