# -*- coding: utf-8 -*-
"""
处理状态库（SQLite）。

作用有三：
  1. **增量**：跑过的片子记下来，下次直接跳过，不重复烧 GPU；
  2. **断点续跑**：阶段推进是持久化的，中途断电/关机，下次从断的地方继续；
  3. **失败重试**：失败项记录次数，下一轮自动重试，超过上限才彻底放弃
     （避免一部损坏的片子每晚都被重试一遍）。

阶段流转：new -> transcribed -> translated -> done
                                          \\-> failed
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .utils import ensure_dir

log = logging.getLogger("rawsub.state")

STAGE_NEW = "new"
STAGE_TRANSCRIBED = "transcribed"
STAGE_TRANSLATED = "translated"
STAGE_DONE = "done"
STAGE_FAILED = "failed"

_STAGE_ORDER = {STAGE_NEW: 0, STAGE_TRANSCRIBED: 1, STAGE_TRANSLATED: 2, STAGE_DONE: 3}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    key           TEXT PRIMARY KEY,
    path          TEXT NOT NULL,
    name          TEXT,
    size          INTEGER DEFAULT 0,
    mtime         TEXT,
    stage         TEXT NOT NULL DEFAULT 'new',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    sub_path      TEXT,
    en_srt        TEXT,
    zh_srt        TEXT,
    audio_bytes   INTEGER DEFAULT 0,
    duration_s    REAL DEFAULT 0,
    created_at    REAL,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_files_stage ON files(stage);
"""


@dataclass
class FileRecord:
    key: str
    path: str
    name: str = ""
    size: int = 0
    mtime: str = ""
    stage: str = STAGE_NEW
    attempts: int = 0
    last_error: str = ""
    sub_path: str = ""
    en_srt: str = ""
    zh_srt: str = ""
    audio_bytes: int = 0
    duration_s: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0


class StateDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ 基础

    def _row_to_record(self, row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            key=row["key"], path=row["path"], name=row["name"] or "",
            size=row["size"] or 0, mtime=row["mtime"] or "", stage=row["stage"],
            attempts=row["attempts"] or 0, last_error=row["last_error"] or "",
            sub_path=row["sub_path"] or "", en_srt=row["en_srt"] or "", zh_srt=row["zh_srt"] or "",
            audio_bytes=row["audio_bytes"] or 0, duration_s=row["duration_s"] or 0.0,
            created_at=row["created_at"] or 0.0, updated_at=row["updated_at"] or 0.0,
        )

    def get(self, key: str) -> FileRecord | None:
        cur = self._conn.execute("SELECT * FROM files WHERE key = ?", (key,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def upsert(self, rec: FileRecord) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO files (key, path, name, size, mtime, stage, attempts, last_error,
                               sub_path, en_srt, zh_srt, audio_bytes, duration_s, created_at, updated_at)
            VALUES (:key, :path, :name, :size, :mtime, :stage, :attempts, :last_error,
                    :sub_path, :en_srt, :zh_srt, :audio_bytes, :duration_s, :created_at, :updated_at)
            ON CONFLICT(key) DO UPDATE SET
                path=excluded.path, name=excluded.name, size=excluded.size, mtime=excluded.mtime,
                last_error=excluded.last_error, sub_path=excluded.sub_path,
                audio_bytes=excluded.audio_bytes, updated_at=excluded.updated_at
            """,
            {
                "key": rec.key, "path": rec.path, "name": rec.name, "size": rec.size,
                "mtime": rec.mtime, "stage": rec.stage, "attempts": rec.attempts,
                "last_error": rec.last_error, "sub_path": rec.sub_path,
                "en_srt": rec.en_srt, "zh_srt": rec.zh_srt,
                "audio_bytes": rec.audio_bytes, "duration_s": rec.duration_s,
                "created_at": rec.created_at or now, "updated_at": now,
            },
        )
        self._conn.commit()

    # ------------------------------------------------------------------ 阶段

    def mark_stage(self, key: str, stage: str, **fields) -> None:
        """推进阶段，可同时更新若干列。"""
        allowed = {"last_error", "en_srt", "zh_srt", "audio_bytes", "duration_s", "size", "mtime"}
        sets = ["stage = ?", "updated_at = ?"]
        params: list = [stage, time.time()]
        for name, value in fields.items():
            if name in allowed:
                sets.append(f"{name} = ?")
                params.append(value)
        params.append(key)
        self._conn.execute(
            f"UPDATE files SET {', '.join(sets)} WHERE key = ?", params
        )
        self._conn.commit()

    def bump_attempt(self, key: str, error: str = "", stage: str = STAGE_FAILED) -> int:
        self._conn.execute(
            "UPDATE files SET attempts = attempts + 1, last_error = ?, stage = ?, updated_at = ? "
            "WHERE key = ?",
            (error[:1000], stage, time.time(), key),
        )
        self._conn.commit()
        cur = self._conn.execute("SELECT attempts FROM files WHERE key = ?", (key,))
        row = cur.fetchone()
        return int(row["attempts"]) if row else 0

    # ------------------------------------------------------------------ 查询

    def all(self) -> list[FileRecord]:
        cur = self._conn.execute("SELECT * FROM files ORDER BY updated_at DESC")
        return [self._row_to_record(r) for r in cur.fetchall()]

    def by_stage(self, *stages: str) -> list[FileRecord]:
        if not stages:
            return []
        placeholders = ",".join("?" * len(stages))
        cur = self._conn.execute(
            f"SELECT * FROM files WHERE stage IN ({placeholders}) ORDER BY updated_at",
            stages,
        )
        return [self._row_to_record(r) for r in cur.fetchall()]

    def counts(self) -> dict[str, int]:
        cur = self._conn.execute("SELECT stage, COUNT(*) AS n FROM files GROUP BY stage")
        return {row["stage"]: row["n"] for row in cur.fetchall()}

    def is_done(self, key: str) -> bool:
        rec = self.get(key)
        return rec is not None and rec.stage == STAGE_DONE

    def reset(self, key: str | None = None, only_failed: bool = False) -> int:
        """清空状态，用于重跑。返回受影响行数。"""
        if key:
            cur = self._conn.execute("DELETE FROM files WHERE key = ?", (key,))
        elif only_failed:
            cur = self._conn.execute("DELETE FROM files WHERE stage = ?", (STAGE_FAILED,))
        else:
            cur = self._conn.execute("DELETE FROM files")
        self._conn.commit()
        return cur.rowcount or 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def stage_rank(stage: str) -> int:
    return _STAGE_ORDER.get(stage, -1)
