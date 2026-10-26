# -*- coding: utf-8 -*-
"""用 work/subs 里缓存的上次翻译结果，补生成翻译记录（并验证 _write_record）。"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from rawsub.config import load_config
from rawsub.pipeline import Pipeline, WorkItem
from rawsub.asr import load_srt
from rawsub.state import FileRecord
from rawsub.webdav import VideoItem

cfg = load_config()
pipe = Pipeline.__new__(Pipeline)  # 只借用 _write_record，不跑 __init__
pipe.cfg = cfg

for key in ("9be073fbdec7d2da", "40ce6d1cd7f64173"):
    with __import__("sqlite3").connect(cfg.workspace.state_db) as conn:
        row = conn.execute(
            "SELECT path, name, size FROM files WHERE key=?", (key,)
        ).fetchone()
    path, name, size = row
    rec = FileRecord(key=key, path=path)
    video = VideoItem(path=path, name=name, size=size, key=key)
    en_srt = cfg.workspace.subtitle_dir / f"{key}.en.srt"
    zh_srt = cfg.workspace.subtitle_dir / f"{key}.zh.srt"
    item = WorkItem(video=video, record=rec, en_srt=en_srt, zh_srt=zh_srt, wav=cfg.workspace.temp_dir / "x.wav")
    src = load_srt(en_srt, encoding="utf-8")
    dst = load_srt(zh_srt, encoding="utf-8")
    print(f"{key}: 原文 {len(src)} 条 / 中译 {len(dst)} 条")
    if len(src) == len(dst):
        pipe._write_record(item, src, dst, elapsed=0.0)
print("done")
