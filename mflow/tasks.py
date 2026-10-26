# -*- coding: utf-8 -*-
"""后台任务管理器：网页控制台用来启动/监控子进程任务。

每个任务 = 一个子进程（复用 proctree 做进程树挂起/杀死），
stdout+stderr 逐行进环形缓冲，前端按 seq 增量拉取（轮询，无代理兼容问题）。
任务历史持久化到 logs/task_history.json（保留最近 200 条）。
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from . import paths, proctree
from .logconsole import extract_progress

# kind -> 互斥组：同组同时只允许一个；flow(全流程) 与任何 step 互斥
_KIND_GROUP = {
    "harvest": "step", "pick_max": "step", "push": "step",
    "subtitle": "step", "doctor": "step", "chain": "flow",
}
_KIND_LABEL = {
    "harvest": "磁力采集", "pick_max": "选最大种子", "push": "推送115",
    "subtitle": "字幕流水线", "doctor": "环境体检", "chain": "一键全流程",
}

_HISTORY_FILE = None  # 惰性 = paths.LOGS_DIR / task_history.json


class Task:
    def __init__(self, kind: str, argv: list[str], cwd: Path, name: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.label = name or _KIND_LABEL.get(kind, kind)
        self.argv = [str(a) for a in argv]
        self.cmd = " ".join(
            f'"{a}"' if (" " in a or not a) else a for a in self.argv)
        self.cwd = str(cwd)
        self.status = "running"          # running | finished | failed | killed
        self.paused = False
        self.created = time.time()
        self.finished: float | None = None
        self.exit_code: int | None = None
        self.proc: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=6000)
        self.seq = 0                     # 与 deque 对应：全局单调计数
        self.dropped = 0
        self.progress: dict = {}
        self.last_activity = time.time()
        self.log_file: Path | None = None
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None

    # ------------------------------------------------------------ 公共读侧
    def to_dict(self, *, brief: bool = False) -> dict:
        d = {
            "id": self.id, "kind": self.kind, "label": self.label,
            "status": self.status, "paused": self.paused,
            "created": self.created, "finished": self.finished,
            "exit_code": self.exit_code, "seq": self.seq,
            "dropped": self.dropped, "progress": dict(self.progress),
            "last_activity": self.last_activity,
            "elapsed": int((self.finished or time.time()) - self.created),
            "cmd": " ".join(self.argv), "cwd": self.cwd,
        }
        if not brief:
            d["log_tail"] = list(self.lines)[-400:]
            d["log_seq_base"] = max(0, self.seq - len(self.lines))
        return d

    def tail_lines(self) -> list[tuple[int, str]]:
        base = max(0, self.seq - len(self.lines))
        with self._lock:
            return [(base + i, ln) for i, ln in enumerate(self.lines)]

    def new_lines(self, after: int) -> list[tuple[int, str]]:
        return [(s, ln) for s, ln in self.tail_lines() if s > after]

    # ------------------------------------------------------------ 写侧
    def add_line(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        with self._lock:
            self.seq += 1
            if self.seq > self.lines.maxlen:
                self.dropped += 1
            self.lines.append(line)
            self.last_activity = time.time()
        try:
            info = extract_progress(line)
            if info:
                self.progress.update({k: str(v) for k, v in info.items()})
        except Exception:
            pass
        if self.log_file:
            try:
                with self.log_file.open("a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
            except OSError:
                pass


class TaskManager:
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.history: list[dict] = []
        self._load_history()

    # ------------------------------------------------------------ 历史
    def _history_file(self) -> Path:
        return paths.LOGS_DIR / "task_history.json"

    def _load_history(self) -> None:
        try:
            self.history = json.loads(
                self._history_file().read_text(encoding="utf-8"))
            if not isinstance(self.history, list):
                self.history = []
        except (OSError, ValueError):
            self.history = []

    def _save_history(self) -> None:
        try:
            paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            self._history_file().write_text(
                json.dumps(self.history[-200:], ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ 启动
    def conflict(self, kind: str) -> Task | None:
        group = _KIND_GROUP.get(kind, "step")
        for tid in self._order:
            t = self.tasks[tid]
            if t.status != "running":
                continue
            og = _KIND_GROUP.get(t.kind, "step")
            if group == og or group == "flow" or og == "flow":
                return t
        return None

    def start(self, kind: str, argv: list[str], cwd: Path | str,
              name: str = "", log_file: Path | None = None) -> Task:
        """启动一个任务；互斥冲突抛 RuntimeError。"""
        with self._lock:
            c = self.conflict(kind)
            if c:
                raise RuntimeError(
                    f"「{c.label}」正在运行（任务 {c.id}），"
                    f"同一时刻只允许一个{'全流程' if 'flow' in (_KIND_GROUP.get(kind,'step'), _KIND_GROUP.get(c.kind,'step')) else '单环节'}任务")
            task = Task(kind, argv, Path(cwd), name)
            task.log_file = log_file or (paths.LOGS_DIR / f"task_{task.id}.log")
            try:
                task.log_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                task.log_file = None
            env = None
            import os
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUNBUFFERED", "1")
            creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            try:
                task.proc = subprocess.Popen(
                    task.argv, cwd=task.cwd,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    env=env, creationflags=creationflags,
                )
            except OSError as exc:
                raise RuntimeError(f"进程启动失败：{exc}") from exc
            task.add_line(f"[启动] {task.cmd}")
            task.add_line(f"[目录] {task.cwd}")
            self.tasks[task.id] = task
            self._order.append(task.id)
            self._reader_thread(task)
        return task

    def _reader_thread(self, task: Task) -> None:
        def worker():
            try:
                assert task.proc is not None and task.proc.stdout is not None
                for line in task.proc.stdout:
                    task.add_line(line)
                code = task.proc.wait()
            except Exception as exc:                      # noqa: BLE001
                task.add_line(f"[管理器异常] {exc}")
                code = -1
            if code == 0:
                task.status = "finished"
            elif getattr(task, "stop_requested", False) or code in (-9, -15, 3221225786):
                task.status = "killed"
            else:
                task.status = "failed"
            task.paused = False
            task.exit_code = code
            task.finished = time.time()
            task.add_line(f"[退出码 {code}]")
            with self._lock:
                self.history.append({
                    "id": task.id, "kind": task.kind, "label": task.label,
                    "status": task.status, "created": task.created,
                    "finished": task.finished, "exit_code": code,
                })
                self._save_history()
        task._reader = threading.Thread(target=worker, daemon=True,
                                        name=f"task-{task.id}")
        task._reader.start()

    # ------------------------------------------------------------ 控制
    def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def pause_resume(self, task_id: str) -> bool | None:
        task = self.get(task_id)
        if not task or task.status != "running" or not task.proc:
            return None
        if task.paused:
            ok = proctree.resume_tree(task.proc.pid)
            task.paused = False if ok else True
        else:
            ok = proctree.suspend_tree(task.proc.pid)
            task.paused = True if ok else False
        task.add_line(f"[界面] {'已挂起进程树' if task.paused else '已恢复进程树' if ok else '挂起/恢复失败（可能非 Windows）'}")
        return task.paused if ok else None

    def stop(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task.status != "running" or not task.proc:
            return False
        task.stop_requested = True
        if task.paused:
            proctree.resume_tree(task.proc.pid)
            task.paused = False
        task.proc.terminate()
        try:
            task.proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proctree.kill_tree(task.proc.pid)
        task.add_line("[界面] 收到手动结束指令")
        return True

    # ------------------------------------------------------------ 查询
    def list_tasks(self) -> list[dict]:
        with self._lock:
            items = [self.tasks[t].to_dict(brief=True)
                     for t in reversed(self._order)]
        his_ids = {t["id"] for t in items}
        for h in reversed(self.history):
            if h["id"] not in his_ids and len(items) < 120:
                items.append(h)
                his_ids.add(h["id"])
        return items

    def active_tasks(self) -> list[Task]:
        return [self.tasks[t] for t in list(self._order)
                if self.tasks[t].status == "running"]


MANAGER = TaskManager()
