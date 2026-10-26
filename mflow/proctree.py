# -*- coding: utf-8 -*-
"""进程树控制：挂起 / 恢复 / 杀掉一个进程及其全部子孙。

从 115Translate 的 ui.py 提炼。字幕流水线会派生 ffmpeg、infer.exe 等
孙进程，只 terminate 直接子进程不够，需要整树操作。
非 Windows 平台退化为 SIGSTOP / SIGCONT / SIGKILL。
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    PROCESS_SUSPEND_RESUME = 0x0800
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _PROCESSENTRY32(ctypes.Structure):
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

    def descendant_pids(root_pid: int) -> list[int]:
        """广度遍历，返回 root_pid 下所有子孙进程 pid（不含自身）。"""
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == INVALID_HANDLE_VALUE:
            return []
        children: dict[int, list[int]] = {}
        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
            if not _kernel32.Process32First(snap, ctypes.byref(entry)):
                return []
            while True:
                children.setdefault(entry.th32ParentProcessID, []).append(
                    entry.th32ProcessID
                )
                if not _kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
        finally:
            _kernel32.CloseHandle(snap)

        out: list[int] = []
        stack = [root_pid]
        while stack:
            pid = stack.pop()
            for child in children.get(pid, ()):
                if child != root_pid and child not in out:
                    out.append(child)
                    stack.append(child)
        return out

    def _open(pid: int):
        return _kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)

    def suspend_tree(pid: int) -> bool:
        opened, all_ok = 0, True
        for p in [pid] + descendant_pids(pid):
            h = _open(p)
            if h:
                opened += 1
                try:
                    status = _ntdll.NtSuspendProcess(h)
                    if status not in (0, None):
                        all_ok = False
                except Exception:
                    all_ok = False
                finally:
                    _kernel32.CloseHandle(h)
        return opened > 0 and all_ok

    def resume_tree(pid: int) -> bool:
        opened, all_ok = 0, True
        for p in [pid] + descendant_pids(pid):
            h = _open(p)
            if h:
                opened += 1
                try:
                    status = _ntdll.NtResumeProcess(h)
                    if status not in (0, None):
                        all_ok = False
                except Exception:
                    all_ok = False
                finally:
                    _kernel32.CloseHandle(h)
        return opened > 0 and all_ok

    def _no_window_kwargs() -> dict:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags, "startupinfo": _hidden_startupinfo()}

    def _hidden_startupinfo():
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return si

    def kill_tree(pid: int, grace_s: float = 5.0) -> None:
        """先礼后兵：taskkill 整树，失败再补刀。"""
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False, capture_output=True, timeout=grace_s + 5,
                **_no_window_kwargs(),
            )
        except Exception:
            pass

else:  # POSIX 退化实现

    def descendant_pids(root_pid: int) -> list[int]:
        return []

    def suspend_tree(pid: int) -> bool:
        try:
            os.killpg(os.getpgid(pid), signal.SIGSTOP)
            return True
        except Exception:
            try:
                os.kill(pid, signal.SIGSTOP)
                return True
            except Exception:
                return False

    def resume_tree(pid: int) -> bool:
        try:
            os.killpg(os.getpgid(pid), signal.SIGCONT)
            return True
        except Exception:
            try:
                os.kill(pid, signal.SIGCONT)
                return True
            except Exception:
                return False

    def kill_tree(pid: int, grace_s: float = 5.0) -> None:
        import time

        for sig, wait in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 2)):
            try:
                os.killpg(os.getpgid(pid), sig)
            except Exception:
                try:
                    os.kill(pid, sig)
                except Exception:
                    return
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    done, _ = os.waitpid(pid, os.WNOHANG)
                    if done:
                        return
                except ChildProcessError:
                    return
                time.sleep(0.2)
