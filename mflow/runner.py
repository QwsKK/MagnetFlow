# -*- coding: utf-8 -*-
"""子进程命令运行器：启动 / 暂停 / 恢复 / 停止，日志流进 LogConsole。

GUI 各页签共用一套契约：
    runner = CommandRunner(root, console)
    runner.start([脚本路径, 参数...], cwd=..., name="采集")
后台线程逐行读 stdout（合并 stderr），喂给 console；结束时通过
root.after 回到 UI 线程执行 on_finish。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import paths, proctree


def _hidden_flags() -> dict:
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                "startupinfo": si}
    return {"start_new_session": True}


class CommandRunner:
    """管理一个正在运行的子命令；同一时间只允许一个实例在跑。"""

    def __init__(self, master: tk.Misc, console):
        self.master = master
        self.console = console
        self.proc: subprocess.Popen | None = None
        self.paused = False
        self.running = False
        self.started_at: float | None = None
        self.name = ""
        self.on_finish = None          # fn(exit_code, name)
        self._stop_requested = False

    # ---------------------------------------------------------------- 启动
    def start(self, script, args=(), cwd=None, name="", console=None,
              extra_env=None) -> bool:
        if self.running:
            return False
        script = Path(script)
        argv = [paths.python_exe(), str(script), *[str(a) for a in args]]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            env.update(extra_env)
        try:
            self.proc = subprocess.Popen(
                argv, cwd=str(cwd or script.parent),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=env, bufsize=1, **_hidden_flags(),
            )
        except OSError as exc:
            self.console.append_ui(f"[启动失败] {exc}")
            return False

        self.running = True
        self.paused = False
        self._stop_requested = False
        self.started_at = time.time()
        self.name = name or script.name
        target_console = console or self.console
        target_console.append_ui(f"$ {' '.join(argv)}   (cwd={cwd or script.parent})")

        t = threading.Thread(target=self._read_output, args=(self.proc, target_console),
                             daemon=True, name=f"reader-{self.name}")
        t.start()
        return True

    def _read_output(self, proc, console) -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                console.feed(line.rstrip("\r\n"))
        except Exception as exc:  # noqa: BLE001
            console.feed(f"[读取输出异常] {exc}")
        code = proc.wait()
        self.master.after(0, self._finish, code)

    def _finish(self, code: int) -> None:
        self.running = False
        self.paused = False
        self.proc = None
        elapsed = time.time() - (self.started_at or time.time())
        self.console.append_ui(
            f"[{self.name}] 进程结束，退出码 {code}，用时 {elapsed:.0f}s")
        if self.on_finish:
            try:
                self.on_finish(code, self.name)
            except Exception:
                pass

    # ------------------------------------------------------- 暂停 / 恢复 / 停止
    def pause_resume(self) -> bool:
        """切换挂起/恢复。返回切换后的状态；不支持时返回 None。"""
        if not self.running or self.proc is None:
            return None
        pid = self.proc.pid
        try:
            if self.paused:
                proctree.resume_tree(pid)
                self.paused = False
            else:
                proctree.suspend_tree(pid)
                self.paused = True
        except Exception:
            return None
        return self.paused

    def stop(self) -> None:
        if not self.running or self.proc is None:
            return
        self._stop_requested = True
        pid = self.proc.pid
        if self.paused:
            try:
                proctree.resume_tree(pid)
                self.paused = False
            except Exception:
                pass

        def waiter():
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proctree.kill_tree(pid)

        threading.Thread(target=waiter, daemon=True).start()

    def elapsed_text(self) -> str:
        if not self.running or self.started_at is None:
            return ""
        secs = int(time.time() - self.started_at)
        return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
