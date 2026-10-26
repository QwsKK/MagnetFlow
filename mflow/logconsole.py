# -*- coding: utf-8 -*-
"""通用日志控制台（tkinter）。

从 115Translate 的 ui.py 提炼成可复用控件：
- 按日志级别着色（INFO / SUCCESS / WARNING / ERROR / DEBUG）；
- 级别 + 关键字过滤，过滤条件改变时重放渲染；
- 线程安全 feed()：后台读日志线程只管塞队列，界面用 after 轮询排空；
- 同时兼容三种日志样式：
    1) rawsub 管道样式   12:34:56 | INFO     | rawsub.pipeline | 消息
    2) logging 基本样式  2026-10-24 12:34:56,789 INFO    消息
    3) 裸文本（原样显示，沿用上一条的级别）
"""
from __future__ import annotations

import queue
import re
import tkinter as tk
from tkinter import ttk

MAX_LINES = 8000

_LEVEL_RANK = {"DEBUG": 10, "INFO": 20, "SUCCESS": 20, "WARNING": 30,
               "WARN": 30, "ERROR": 40, "CRITICAL": 40}

# rawsub 管道样式
_PIPE_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2})\s*\|\s*"
    r"(DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)\s*\|\s*([^|]*?)\s*\|\s*(.*)$"
)
# logging.basicConfig 样式（采集器用的就是这种）
_PLAIN_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s+"
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b\s+(.*)$"
)

_MAGNET_RE = re.compile(r"magnet:\?")

# 字幕流水线状态条提取（沿用 115Translate ui.py 的规则）
_RE_ITEM = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+)$")
_RE_ASR_PCT = re.compile(r"进度\s+(\d+)\s*%")
_RE_DL_PCT = re.compile(r"下载\s+\S+\s*/\s*\S+（(\d+)%）")


def extract_progress(raw: str) -> dict | None:
    """从一行日志里抠出「第几部 / 第几个百分比」，给状态条用。

    返回形如 {"item": "1/5", "name": "xxx"} 或 {"pct": "37"}；无进度信息返回 None。
    """
    msg = raw
    m = _PIPE_RE.match(raw)
    if m:
        msg = m.group(4)
    for candidate in (msg.strip(), raw.strip()):
        m = _RE_ITEM.match(candidate)
        if m:
            return {"item": f"{m.group(1)}/{m.group(2)}",
                    "name": m.group(3).strip()[:42]}
    m = _RE_ASR_PCT.search(raw) or _RE_DL_PCT.search(raw)
    if m:
        return {"pct": m.group(1) + "%"}
    return None

_LEVEL_TAGS = {
    "DEBUG": "debug", "INFO": "info", "SUCCESS": "success",
    "WARNING": "warning", "WARN": "warning",
    "ERROR": "error", "CRITICAL": "error",
}


class LogConsole(ttk.Frame):
    """带过滤栏和自动着色的日志窗口。"""

    def __init__(self, master, *, show_filter: bool = True, read_only: bool = True):
        super().__init__(master)
        self._entries: list[tuple[str, str, str, str]] = []  # (raw, ts, level, msg)
        self._level_min = 0
        self._keyword = ""
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._hook = None          # 每条原始日志的旁路钩子（进度解析用）
        self._dropped = 0

        self._build_ui(show_filter)

        text = self.log_text
        tag_cfg = {
            "debug":   {"foreground": "#888888"},
            "info":    {"foreground": "#1a1a1a"},
            "success": {"foreground": "#1a7f37"},
            "warning": {"foreground": "#b07d0a", "font": ("Consolas", 10, "bold")},
            "error":   {"foreground": "#c00000", "font": ("Consolas", 10, "bold")},
            "ui":      {"foreground": "#0a6ebd", "font": ("Consolas", 10, "bold")},
            "magnet":  {"foreground": "#0b6e8c"},
        }
        for tag, cfg in tag_cfg.items():
            text.tag_configure(tag, **cfg)

    # ------------------------------------------------------------------ UI
    def _build_ui(self, show_filter: bool):
        self.autoscroll_var = tk.BooleanVar(value=True)
        if show_filter:
            fbar = ttk.Frame(self, padding=(4, 2, 4, 2))
            fbar.pack(fill=tk.X)
            ttk.Label(fbar, text="级别：").pack(side=tk.LEFT)
            self.level_var = tk.StringVar(value="全部")
            box = ttk.Combobox(fbar, textvariable=self.level_var, width=11,
                               state="readonly",
                               values=["全部", "INFO及以上", "WARNING及以上", "仅ERROR"])
            box.pack(side=tk.LEFT)
            box.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())

            ttk.Label(fbar, text="  关键字：").pack(side=tk.LEFT)
            self.kw_var = tk.StringVar()
            kw = ttk.Entry(fbar, textvariable=self.kw_var, width=20)
            kw.pack(side=tk.LEFT, padx=(2, 0))
            kw.bind("<KeyRelease>", lambda e: self.apply_filter())

            self.autoscroll_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(fbar, text="自动滚动", variable=self.autoscroll_var
                            ).pack(side=tk.RIGHT)
            ttk.Button(fbar, text="清屏", width=6, command=self.clear
                       ).pack(side=tk.RIGHT, padx=6)

        wrap = ttk.Frame(self)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(wrap, wrap=tk.NONE, state=tk.DISABLED,
                                font=("Consolas", 10), background="#fbfbfb")
        ysb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.log_text.yview)
        xsb = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, columnspan=2, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

    # ---------------------------------------------------------- 线程安全入口
    def feed(self, line: str) -> None:
        """后台线程调用：把一行日志送进队列（None 为结束哨兵，不用管）。"""
        self._queue.put(line.rstrip("\r\n"))

    def set_hook(self, fn) -> None:
        """设置逐行旁路钩子 fn(raw_line)，在排空队列时（UI 线程）调用。"""
        self._hook = fn

    # ---------------------------------------------------------- UI 线程排空
    def drain(self, max_items: int = 300) -> bool:
        """把队列里的行渲染进文本框。返回是否有新行。"""
        got = False
        for _ in range(max_items):
            try:
                line = self._queue.get_nowait()
            except queue.Empty:
                break
            got = True
            if line is None:
                continue
            self._append_raw(line)
            if self._hook:
                try:
                    self._hook(line)
                except Exception:
                    pass
        return got

    # ------------------------------------------------------------ 内部渲染
    def _append_raw(self, line: str) -> None:
        ts, level, msg = "", "", line
        m = _PIPE_RE.match(line)
        if m:
            ts, level, _logger, msg = m.groups()
        else:
            m = _PLAIN_RE.match(line)
            if m:
                ts, level, msg = m.groups()
        idx = len(self._entries)
        self._entries.append((line, ts, level, msg))
        if len(self._entries) > MAX_LINES:
            # 丢弃前半，保留后半；前移索引基准
            cut = len(self._entries) - MAX_LINES // 2
            del self._entries[:cut]
            self._dropped += cut
            idx -= cut
        if self._match(idx):
            self._render_entry(self._entries[-1])

    def _match(self, idx: int) -> bool:
        entry = self._entries[idx]
        rank = _LEVEL_RANK.get(entry[2], 0)
        if rank and rank < self._level_min:
            return False
        if self._keyword and self._keyword not in entry[0].lower():
            return False
        if not self._keyword and self._level_min and rank and rank < self._level_min:
            return False
        if self._level_min and rank == 0 and self._keyword not in entry[0].lower():
            # 级别过滤开启时，裸文本按上一条级别走（简化：直接放行）
            pass
        return True

    def _render_entry(self, entry) -> None:
        raw, _ts, level, _msg = entry
        tag = _LEVEL_TAGS.get(level, "info")
        text = self.log_text
        state = text["state"]
        text["state"] = tk.NORMAL
        if _MAGNET_RE.search(raw):
            text.insert(tk.END, raw + "\n", (tag, "magnet"))
        else:
            text.insert(tk.END, raw + "\n", tag)
        if text.index(tk.END) > MAX_LINES:
            text.delete("1.0", f"{MAX_LINES // 2}.0")
        text["state"] = state
        if getattr(self, "autoscroll_var", None) and self.autoscroll_var.get():
            text.see(tk.END)

    def append_ui(self, msg: str) -> None:
        """界面自己发的提示（蓝色），不走队列。"""
        text = self.log_text
        state = text["state"]
        text["state"] = tk.NORMAL
        text.insert(tk.END, msg + "\n", "ui")
        text["state"] = state
        if self.autoscroll_var.get():
            text.see(tk.END)

    def apply_filter(self) -> None:
        self._level_min = {"全部": 0,
                           "INFO及以上": _LEVEL_RANK["INFO"],
                           "WARNING及以上": _LEVEL_RANK["WARNING"],
                           "仅ERROR": _LEVEL_RANK["ERROR"]}.get(self.level_var.get(), 0)
        self._keyword = self.kw_var.get().strip().lower()
        self._rerender()

    def _rerender(self) -> None:
        text = self.log_text
        state = text["state"]
        text["state"] = tk.NORMAL
        text.delete("1.0", tk.END)
        for idx, entry in enumerate(self._entries):
            if self._match(idx):
                self._render_entry(entry)
        text["state"] = state
        if self.autoscroll_var.get():
            text.see(tk.END)

    def clear(self) -> None:
        self._entries.clear()
        self._dropped = 0
        text = self.log_text
        state = text["state"]
        text["state"] = tk.NORMAL
        text.delete("1.0", tk.END)
        text["state"] = state
