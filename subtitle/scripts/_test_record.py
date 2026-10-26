# -*- coding: utf-8 -*-
"""离线验证 _write_record 的输出格式。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from rawsub.config import load_config
from rawsub.pipeline import Pipeline, WorkItem
from rawsub.asr.srt import Cue
from rawsub.state import FileRecord
from rawsub.webdav import VideoItem

cfg = load_config()
cfg.workspace.record_dir = Path(tempfile.mkdtemp(prefix="rawsub_records_"))
pipe = Pipeline.__new__(Pipeline)
pipe.cfg = cfg

video = VideoItem(path="/115/测试/希德尼娅的骑士.S01E01.h264.1080p.FLAC.mkv",
                  name="希德尼娅的骑士.S01E01.h264.1080p.FLAC.mkv", size=1, key="deadbeef")
item = WorkItem(video=video, record=FileRecord(key="deadbeef", path=video.path),
                en_srt=Path("a.en.srt"), zh_srt=Path("a.zh.srt"), wav=Path("a.wav"))
src = [Cue(1, 0.0, 2.4, "こちら第2開拓閣! こちら第2開拓閣!"),
       Cue(2, 3.0, 6.5, "カーナが出現した!\n我々はカーナの攻撃を受けている!")]
dst = [Cue(1, 0.0, 2.4, "这是第二开拓阁！这是第二开拓阁！"),
       Cue(2, 3.0, 6.5, "卡纳出现了！我们正遭受卡纳的攻击！")]
pipe._write_record(item, src, dst, elapsed=33.0)
out = next(cfg.workspace.record_dir.glob("*.md"))
print(f"生成文件: {out.name}")
print("-" * 50)
print(out.read_text(encoding="utf-8"))
