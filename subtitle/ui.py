# -*- coding: utf-8 -*-
"""
115Translate 运行控制台 UI

用法：
    python ui.py          # 直接运行
    或双击 ui.bat

按钮：
    开始  -> 以 venv Python 运行 `run.py run`，输出实时进日志窗口
    暂停  -> 挂起整个子进程树（含 ffmpeg / infer.exe），再点一次恢复
    结束  -> 强制结束整棵进程树

日志窗口：
    - 按级别着色（ERROR 红 / WARNING 橙 / DEBUG 灰 / UI 消息蓝）
    - 隐藏冗长的 logger 名列，只留 时间 级别 消息
    - 级别过滤 + 关键字过滤（输入即生效）
    - 自动滚动可关（往上翻历史时不会拽着你跑）
    - 底部状态条实时显示 当前第几部 / 下载或转写进度
    - 行数上限 8000，超出自动丢最旧的，防内存膨胀
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
RUN_SCRIPT = os.path.join(ROOT, "run.py")
VENV_PYTHON = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
LOG_FILE = os.path.join(ROOT, "logs", "run.log")

IS_WINDOWS = os.name == "nt"

MAX_LINES = 8000  # 日志窗口保留的最大行数（超出丢最旧）

# 子进程日志行：`12:34:56 | INFO    | rawsub.pipeline | 消息`
# （兼容带日期前缀的格式；解析失败的行当「续行」处理，跟随上一行的级别）
LOG_LINE_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[ T])?"
    r"(\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*\|\s*"
    r"(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s*\|\s*"
    r"[A-Za-z0-9_.\-]+\s*\|\s?"
    r"(.*)$", re.S,
)

_LEVEL_RANK = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30,
               "ERROR": 40, "CRITICAL": 50}

# 状态条提取规则
_RE_ITEM = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+)$")
_RE_ASR_PCT = re.compile(r"进度\s+(\d+)\s*%")
_RE_DL_PCT = re.compile(r"下载\s+\S+\s*/\s*\S+（(\d+)%）")

# ---------------------------------------------------------------------------
# Windows 进程树挂起 / 恢复（stdlib ctypes，不依赖 psutil）
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    PROCESS_SUSPEND_RESUME = 0x0800
    TH32CS_SNAPPROCESS = 0x2
    INFINITE = 0xFFFFFFFF

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    def _descendant_pids(pid):
        """返回 pid 的所有后代进程（不含自身）。"""
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return []
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        children = {}
        ok = bool(_kernel32.Process32First(snap, ctypes.byref(entry)))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(
                entry.th32ProcessID)
            ok = bool(_kernel32.Process32Next(snap, ctypes.byref(entry)))
        _kernel32.CloseHandle(snap)

        result, stack = [], [pid]
        while stack:
            cur = stack.pop()
            for child in children.get(cur, []):
                result.append(child)
                stack.append(child)
        return result

    def _each_handle(pid):
        h = _kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if h:
            try:
                yield h
            finally:
                _kernel32.CloseHandle(h)

    def suspend_tree(pid):
        for p in [pid] + _descendant_pids(pid):
            for h in _each_handle(p):
                _ntdll.NtSuspendProcess(h)

    def resume_tree(pid):
        for p in [pid] + _descendant_pids(pid):
            for h in _each_handle(p):
                _ntdll.NtResumeProcess(h)


def child_python():
    """跑流水线用的解释器：优先 venv，兜底当前解释器。"""
    return VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


class RunConsoleUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("115Translate - 空闲")
        self.geometry("980x640")
        self.minsize(700, 420)

        self.proc = None          # Popen
        self.reader = None        # 读日志线程
        self.paused = False
        self.log_queue = queue.Queue()
        self._closing = False
        self._started_at = None   # 运行开始时间（算耗时）

        # 日志条目存储：[(raw, ts, level, msg), ...] 渲染随过滤条件重放
        self._entries = []
        self._level_min = 0       # 当前级别过滤阈值
        self._keyword = ""        # 当前关键字过滤（小写）

        # 底部状态条数据
        self._st_item = ""
        self._st_phase = ""
        self._st_pct = ""

        self._build_ui()
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        # ---------- 工具栏：按钮 ----------
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill=tk.X)

        self.btn_start = ttk.Button(bar, text="开始", width=10,
                                    command=self.start_run)
        self.btn_pause = ttk.Button(bar, text="暂停", width=10,
                                    command=self.toggle_pause,
                                    state=tk.DISABLED)
        self.btn_stop = ttk.Button(bar, text="结束", width=10,
                                   command=self.stop_run,
                                   state=tk.DISABLED)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_pause.pack(side=tk.LEFT, padx=(8, 4))
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(bar, text="打开日志文件", width=12,
                   command=self.open_logfile).pack(side=tk.LEFT)
        ttk.Button(bar, text="清屏", width=8,
                   command=self.clear_log).pack(side=tk.LEFT, padx=8)

        # ---------- 过滤栏 ----------
        fbar = ttk.Frame(self, padding=(8, 0, 8, 4))
        fbar.pack(fill=tk.X)

        ttk.Label(fbar, text="级别：").pack(side=tk.LEFT)
        self.level_var = tk.StringVar(value="全部")
        level_box = ttk.Combobox(
            fbar, textvariable=self.level_var, width=12, state="readonly",
            values=["全部", "INFO及以上", "WARNING及以上", "仅ERROR"])
        level_box.pack(side=tk.LEFT)
        level_box.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Label(fbar, text="   关键字：").pack(side=tk.LEFT)
        self.kw_var = tk.StringVar()
        kw_entry = ttk.Entry(fbar, textvariable=self.kw_var, width=22)
        kw_entry.pack(side=tk.LEFT, padx=(4, 0))
        kw_entry.bind("<KeyRelease>", lambda e: self._apply_filter())

        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fbar, text="自动滚动",
                        variable=self.autoscroll_var).pack(side=tk.RIGHT)

        # ---------- 日志窗口 ----------
        self.log_text = scrolledtext.ScrolledText(
            self, wrap=tk.WORD, font=("Consolas", 10), cursor="arrow",
            undo=False)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 4))

        # 只读观感但保留选择/复制：拦截按键输入，放行复制
        self.log_text.bind("<Key>", self._block_key)
        self.log_text.bind("<Control-c>", self._copy_selection)
        self.log_text.bind("<Control-a>", self._select_all)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="复制", command=self._copy_selection)
        menu.add_command(label="全选", command=self._select_all)
        self.log_text.bind("<Button-3>",
                           lambda e: menu.tk_popup(e.x_root, e.y_root))

        self._setup_tags()

        # ---------- 底部状态条 ----------
        sbar = ttk.Frame(self, padding=(8, 0, 8, 6))
        sbar.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(sbar, textvariable=self.status_var,
                  anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_tags(self):
        t = self.log_text.tag_configure
        t("ts", foreground="#6a737d")
        t("sep", foreground="#9ea7b3")
        t("DEBUG", foreground="#8b949e")
        t("INFO", foreground="")
        t("WARNING", foreground="#9a6700")
        t("ERROR", foreground="#cf222e")
        t("CRITICAL", foreground="#cf222e")
        t("ui", foreground="#0969da")
        t("banner", foreground="#0550ae",
          font=("Consolas", 10, "bold"))
        t("cont", foreground="#57606a")   # 解析失败的续行（堆栈等）

    # ---------------------------------------------------------------- 日志
    def append_log(self, text):
        """UI 自己的消息（蓝色，带前缀）。"""
        self._push_entries([(text, None, None, None)], is_ui=True)

    def _push_entries(self, entries, is_ui=False):
        """条目入存储并渲染。entry=(raw, ts, level, msg)。"""
        self._entries.extend(entries)
        if len(self._entries) > MAX_LINES:
            self._entries = self._entries[-MAX_LINES:]
            self._rerender()
        else:
            for e in entries:
                self._render_entry(e, is_ui=is_ui)
        self._update_status_from_entries()

    def _match_filter(self, idx, entry):
        raw, ts, level, msg = entry
        rank = _LEVEL_RANK.get(level, None)
        if rank is None:
            rank = self._cont_rank(idx)  # 续行跟随上一条级别
        if rank < self._level_min:
            return False
        if self._keyword and self._keyword not in raw.lower():
            return False
        return True

    def _cont_rank(self, idx):
        """续行（堆栈/裸文本）的级别视作它上面第一条有级别行。"""
        for j in range(idx, -1, -1):
            r = _LEVEL_RANK.get(self._entries[j][2])
            if r is not None:
                return r
        return _LEVEL_RANK["INFO"]

    def _apply_filter(self):
        lv = self.level_var.get()
        self._level_min = {
            "全部": 0,
            "INFO及以上": _LEVEL_RANK["INFO"],
            "WARNING及以上": _LEVEL_RANK["WARNING"],
            "仅ERROR": _LEVEL_RANK["ERROR"],
        }.get(lv, 0)
        self._keyword = self.kw_var.get().strip().lower()
        self._rerender()

    def _rerender(self):
        self.log_text.delete("1.0", tk.END)
        for idx, e in enumerate(self._entries):
            if self._match_filter(idx, e):
                self._render_entry(e, to_end=False)
        if self.autoscroll_var.get():
            self.log_text.see(tk.END)

    def _render_entry(self, entry, is_ui=False, to_end=True):
        raw, ts, level, msg = entry
        w = self.log_text
        if is_ui:
            text = raw.strip("\n")
            if text.startswith("[UI] ===="):
                w.insert(tk.END, "\n" + text + "\n", "banner")
            else:
                w.insert(tk.END, text + "\n", "ui")
        elif ts is None:
            # 续行（堆栈/裸文本）
            w.insert(tk.END, raw if raw.endswith("\n") else raw + "\n", "cont")
        else:
            lvl = "WARN" if level == "WARN" else level
            w.insert(tk.END, ts + "  ", "ts")
            w.insert(tk.END, f"{lvl:<8}", lvl)
            w.insert(tk.END, msg.rstrip("\n") + "\n",
                     lvl if level in ("ERROR", "CRITICAL") else "")
        if to_end and self.autoscroll_var.get():
            w.see(tk.END)

    def clear_log(self):
        self._entries.clear()
        self.log_text.delete("1.0", tk.END)

    def _block_key(self, event):
        if event.state & 0x0004 and event.keysym in ("c", "a"):
            return None  # 放行 Ctrl+C / Ctrl+A
        return "break"   # 其他按键一律拦截，保持只读

    def _copy_selection(self, event=None):
        try:
            self.log_text.event_generate("<<Copy>>")
        except Exception:
            pass
        return "break"

    def _select_all(self, event=None):
        self.log_text.tag_add("sel", "1.0", tk.END)
        return "break"

    def open_logfile(self):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)  # noqa: S606
        else:
            messagebox.showinfo("提示", f"日志文件不存在：{LOG_FILE}")

    def _parse_line(self, line):
        """子进程日志行 -> (raw, ts, level, msg)；解析失败 ts=None。"""
        m = LOG_LINE_RE.match(line)
        if not m:
            return (line, None, None, None)
        return (line, m.group(1), m.group(2), m.group(3))

    def _update_status_from_entries(self):
        """从最新日志提取进度信息刷新底部状态条（倒着找最近的有效片段）。"""
        for raw, ts, level, msg in reversed(self._entries[-200:]):
            if msg is None:
                continue
            m = _RE_ITEM.match(msg.strip())
            if m:
                self._st_item = f"[{m.group(1)}/{m.group(2)}] {m.group(3).strip()}"
                break
        for raw, ts, level, msg in reversed(self._entries[-30:]):
            if msg is None:
                continue
            m = _RE_ASR_PCT.search(msg)
            if m:
                self._st_phase, self._st_pct = "转写", f"{m.group(1)}%"
                break
            m = _RE_DL_PCT.search(msg)
            if m:
                self._st_phase, self._st_pct = "下载", f"{m.group(1)}%"
                if "预下载" in msg:
                    self._st_phase = "预下载"
                break
            if "转写完成" in msg or "已生成" in msg:
                self._st_phase, self._st_pct = "回写字幕", ""
                break
        parts = [p for p in (self._st_item,
                             f"{self._st_phase} {self._st_pct}".strip()
                             if self._st_phase else "") if p]
        self._refresh_status_base(" | ".join(parts) if parts else "")

    def _refresh_status_base(self, tail=""):
        if self.proc is not None:
            state = "已暂停" if self.paused else "运行中"
            elapsed = ""
            if self._started_at:
                s = int(time.time() - self._started_at)
                h, r = divmod(s, 3600)
                m, sec = divmod(r, 60)
                elapsed = f" · {h:d}:{m:02d}:{sec:02d}"
            self.status_var.set(f"{state}{elapsed}" + (f" | {tail}" if tail else ""))
        else:
            self.status_var.set("空闲" + (f" · 上次：{tail}" if tail else ""))

    def _poll_queue(self):
        try:
            batch = []
            while True:
                line = self.log_queue.get_nowait()
                batch.append(self._parse_line(line))
        except queue.Empty:
            pass
        if batch:
            self._push_entries(batch)
        # 检查子进程是否已退出
        if self.proc is not None and self.reader is not None \
                and not self.reader.is_alive():
            self.proc.wait()
            code = self.proc.returncode
            self.proc = None
            self.reader = None
            self.paused = False
            self._set_buttons("idle")
            self.title("115Translate - 空闲")
            self._started_at = None
            self.append_log(f"\n[UI] 流水线已结束，退出码 {code}\n")
        else:
            self._refresh_status_base(
                " | ".join(p for p in (
                    self._st_item,
                    f"{self._st_phase} {self._st_pct}".strip()
                    if self._st_phase else "") if p))
        self.after(100, self._poll_queue)

    # -------------------------------------------------------------- 按钮
    def _set_buttons(self, state):
        if state == "idle":
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_pause.configure(state=tk.DISABLED, text="暂停")
            self.btn_stop.configure(state=tk.DISABLED)
        elif state == "running":
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_pause.configure(state=tk.NORMAL, text="暂停")
            self.btn_stop.configure(state=tk.NORMAL)
        elif state == "paused":
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_pause.configure(state=tk.NORMAL, text="继续")
            self.btn_stop.configure(state=tk.NORMAL)

    def start_run(self):
        if self.proc is not None:
            return
        self.append_log(
            f"\n[UI] ===== {datetime.now():%Y-%m-%d %H:%M:%S} 开始运行 =====\n")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                [child_python(), RUN_SCRIPT, "run"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=flags,
                bufsize=1,
                encoding="utf-8",   # 子进程被强制以 UTF-8 输出，读取侧必须一致
                errors="replace",
            )
        except OSError as e:
            self.append_log(f"[UI] 启动失败：{e}\n")
            return
        self.reader = threading.Thread(
            target=self._read_output, args=(self.proc,), daemon=True)
        self.reader.start()
        self.paused = False
        self._started_at = time.time()
        self._st_item = self._st_phase = self._st_pct = ""
        self._set_buttons("running")
        self.title("115Translate - 运行中")

    def _read_output(self, proc):
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.log_queue.put(line)
        except (ValueError, OSError):
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def toggle_pause(self):
        if self.proc is None:
            return
        if not IS_WINDOWS:
            self.append_log("[UI] 暂停功能仅支持 Windows。\n")
            return
        pid = self.proc.pid
        if not self.paused:
            suspend_tree(pid)
            self.paused = True
            self._set_buttons("paused")
            self.title("115Translate - 已暂停")
            self.append_log("\n[UI] ===== 已暂停（进程树已挂起） =====\n")
        else:
            resume_tree(pid)
            self.paused = False
            self._set_buttons("running")
            self.title("115Translate - 运行中")
            self.append_log("\n[UI] ===== 已继续 =====\n")

    def stop_run(self):
        if self.proc is None:
            return
        pid = self.proc.pid
        self.append_log("\n[UI] ===== 正在结束进程树 =====\n")
        try:
            if IS_WINDOWS:
                # 先恢复再杀，避免挂起状态下残留；/T 连子进程一起
                if self.paused:
                    resume_tree(pid)
                    self.paused = False
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=30)
            else:
                self.proc.terminate()
        except Exception as e:
            self.append_log(f"[UI] 结束进程出错：{e}\n")
        # _poll_queue 检测到读取线程结束后会自动复位按钮和标题

    # -------------------------------------------------------------- 关闭
    def on_close(self):
        if self.proc is not None:
            if not messagebox.askyesno(
                    "确认退出", "流水线正在运行，退出将结束它。确定吗？"):
                return
            self._closing = True
            try:
                self.stop_run()
            except Exception:
                pass
        self.destroy()


def main():
    app = RunConsoleUI()
    app.mainloop()


if __name__ == "__main__":
    main()
