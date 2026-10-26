# -*- coding: utf-8 -*-
"""页签：磁力库（浏览 harvester/state/harvest.db，配合 pick_max 选最大种子）。"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import paths
from .logconsole import LogConsole
from .runner import CommandRunner

_COLUMNS =(("title","标题",430),("site","站点",90),("size_gb","大小GB",70),
           ("first_seen","首次发现",130),("pushed","推送",60),("hash","infohash",96))


class LibraryTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.rows_cache: list[dict] = []

        # ---------------- 过滤行 ----------------
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(bar, text="搜索：").pack(side=tk.LEFT)
        self.kw_var = tk.StringVar()
        kw = ttk.Entry(bar, textvariable=self.kw_var, width=22)
        kw.pack(side=tk.LEFT)
        kw.bind("<Return>", lambda e: self.refresh())

        ttk.Label(bar, text="  站点：").pack(side=tk.LEFT)
        self.site_var = tk.StringVar(value="全部")
        self.site_box = ttk.Combobox(bar, textvariable=self.site_var, width=12,
                                     state="readonly", values=["全部"])
        self.site_box.pack(side=tk.LEFT)
        self.site_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        ttk.Label(bar, text="  推送状态：").pack(side=tk.LEFT)
        self.pushed_var = tk.StringVar(value="未推送")
        ttk.Combobox(bar, textvariable=self.pushed_var, width=8, state="readonly",
                     values=["全部", "未推送", "已推送"]
                     ).pack(side=tk.LEFT)
        self.pushed_var.trace_add("write", lambda *_: self.refresh())

        ttk.Button(bar, text="刷新", width=7, command=self.refresh
                   ).pack(side=tk.LEFT, padx=6)
        self.count_label = ttk.Label(bar, text="", foreground="#666")
        self.count_label.pack(side=tk.LEFT, padx=4)

        ttk.Button(bar, text="每片留最大",
                   command=self.pick_max).pack(side=tk.RIGHT)
        ttk.Button(bar, text="打开导出目录",
                   command=lambda: os.startfile(str(paths.HARVEST_OUT_DIR))
                   ).pack(side=tk.RIGHT, padx=6)
        ttk.Label(bar, text="双击行 = 复制磁力；右键更多操作　", foreground="#666"
                  ).pack(side=tk.RIGHT)

        # ---------------- 表格 ----------------
        tablewrap = ttk.Frame(self)
        tablewrap.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            tablewrap, columns=[c[0] for c in _COLUMNS],
            show="headings", selectmode="browse", height=18)
        for key, text, width in _COLUMNS:
            self.tree.heading(key, text=text)
            anchor = tk.W if key in ("title", "site") else tk.E
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == "title"))
        vsb = ttk.Scrollbar(tablewrap, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tablewrap, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tablewrap.rowconfigure(0, weight=1)
        tablewrap.columnconfigure(0, weight=1)
        self.tree.tag_configure("pushed", foreground="#888888")

        self.tree.bind("<Double-1>", lambda e: self.copy_magnet())
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="复制磁力链接", command=self.copy_magnet)
        self.menu.add_command(label="打开来源页面", command=self.open_source)
        self.menu.add_separator()
        self.menu.add_command(label="标记为已推送",
                              command=lambda: self.set_pushed(1))
        self.menu.add_command(label="标记为未推送",
                              command=lambda: self.set_pushed(0))
        self.menu.add_separator()
        self.menu.add_command(label="删除这条记录", command=self.delete_row)
        self.tree.bind("<Button-3>", self._popup)

        # ---------------- 底部小控制台（pick_max 输出） ----------------
        self.console = LogConsole(self, show_filter=False)
        self.console.pack(fill=tk.X, pady=(6, 0))
        self.runner = CommandRunner(master.winfo_toplevel(), self.console)
        self.runner.on_finish = lambda code, name: self.refresh()
        app.register_console(self.console)

        self.refresh()

    # ------------------------------------------------------------------ 查询
    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not paths.HARVEST_DB.exists():
            self.count_label["text"] = "数据库还没生成（先跑一次采集）"
            return
        site = self.site_var.get()
        site = "" if site in ("", "全部") else site
        pushed_txt = self.pushed_var.get()
        pushed = {"全部": None, "未推送": 0, "已推送": 1}[pushed_txt]
        kw = self.kw_var.get().strip()
        try:
            con = sqlite3.connect(f"file:{paths.HARVEST_DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            sql = ("SELECT infohash, site, magnet, title, filename, size_gb, "
                   "source_url, first_seen, pushed FROM magnets WHERE 1=1")
            params: list = []
            if site:
                sql += " AND site = ?"
                params.append(site)
            if pushed is not None:
                sql += " AND pushed = ?"
                params.append(pushed)
            if kw:
                like = f"%{kw}%"
                sql += (" AND (title LIKE ? OR filename LIKE ? OR magnet LIKE ?"
                        " OR source_url LIKE ?)")
                params += [like] * 4
            sql += " ORDER BY first_seen DESC LIMIT 800"
            rows = [dict(r) for r in con.execute(sql, params)]
            total_pending = con.execute(
                "SELECT COUNT(*) FROM magnets WHERE pushed=0").fetchone()[0]
            total_all = con.execute("SELECT COUNT(*) FROM magnets").fetchone()[0]
            sites = [r[0] for r in con.execute(
                "SELECT DISTINCT site FROM magnets ORDER BY site")]
            con.close()
        except sqlite3.Error as exc:
            self.count_label["text"] = f"读库失败：{exc}"
            return

        self.rows_cache = rows
        for r in rows:
            tags = ("pushed",) if r["pushed"] else ()
            self.tree.insert("", tk.END, iid=r["infohash"], tags=tags, values=(
                r["title"] or r["filename"] or "(无标题)",
                r["site"],
                f"{r['size_gb']:.2f}" if r["size_gb"] else "",
                r["first_seen"] or "",
                "已推送" if r["pushed"] else "未推送",
                (r["infohash"] or "")[:12],
            ))
        shown = len(rows)
        self.count_label["text"] = (f"当前显示 {shown} 条（上限 800）｜全库 {total_all} 条，"
                                    f"未推送 {total_pending} 条")
        cur_sites = ["全部"] + sites
        if list(self.site_box["values"]) != cur_sites:
            self.site_box["values"] = cur_sites
            if self.site_var.get() not in cur_sites:
                self.site_var.set("全部")

    # ------------------------------------------------------------------ 操作
    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        h = sel[0]
        for r in self.rows_cache:
            if r["infohash"] == h:
                return r
        return None

    def _popup(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.menu.tk_popup(event.x_root, event.y_root)

    def copy_magnet(self) -> None:
        r = self._selected()
        if not r:
            return
        self.app.clipboard_clear()
        self.app.clipboard_append(r["magnet"])
        self.console.append_ui("已复制磁力链接到剪贴板")

    def open_source(self) -> None:
        r = self._selected()
        if r and r["source_url"]:
            webbrowser.open(r["source_url"])

    def _update_db(self, sql: str, params: tuple) -> None:
        try:
            con = sqlite3.connect(paths.HARVEST_DB)
            con.execute(sql, params)
            con.commit()
            con.close()
        except sqlite3.Error as exc:
            messagebox.showerror("数据库操作失败", str(exc))
            return
        self.refresh()

    def set_pushed(self, value: int) -> None:
        r = self._selected()
        if not r:
            return
        self._update_db("UPDATE magnets SET pushed = ? WHERE infohash = ?",
                        (value, r["infohash"]))
        self.console.append_ui(
            f"已把 {r['title'] or r['infohash'][:12]} 标记为"
            f"{'已推送' if value else '未推送'}")

    def delete_row(self) -> None:
        r = self._selected()
        if not r:
            return
        if not messagebox.askyesno(
                "删除记录",
                f"确定删掉这条磁力？\n\n{r['title'] or r['filename'] or r['infohash']}\n\n"
                "（只删库里的记录，之后同一磁力再被采到会重新入库）"):
            return
        self._update_db("DELETE FROM magnets WHERE infohash = ?", (r["infohash"],))
        self.console.append_ui("已删除记录")

    # ------------------------------------------------------------- pick_max
    def pick_max(self) -> None:
        if self.runner.running:
            self.console.append_ui("pick_max 正在运行中")
            return
        site = self.site_var.get()
        site = "" if site in ("", "全部") else site
        latest = paths.HARVEST_OUT_DIR / (site or "") / "latest.json"
        if not latest.exists():
            candidates = sorted(paths.HARVEST_OUT_DIR.glob("*/latest.json"))
            if not candidates:
                self.console.append_ui(
                    "还没有导出文件（latest.json），先在采集页跑一轮或到磁力库右键处理")
                return
            latest = candidates[-1]
            site = latest.parent.name
        self.runner.start(paths.PICK_MAX_SCRIPT,
                          ["--in", str(latest)],
                          cwd=paths.HARVESTER_DIR, name="pick_max")
        self.console.append_ui(f"正在为站点 {site} 挑选每个视频的最大种子…")

    def tick(self, now: float) -> None:
        pass
