# -*- coding: utf-8 -*-
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

for name in ["fbc02abdc78e6db0.en.srt", "fbc02abdc78e6db0.zh.srt"]:
    p = Path(r"D:\Agent\115Translate\work\subs") / name
    txt = p.read_text(encoding="utf-8-sig", errors="replace")
    ts = re.findall(r"(\d{2}):(\d{2}):(\d{2}),\d+ --> (\d{2}):(\d{2}):(\d{2})", txt)
    cues = [((int(a) * 3600 + int(b) * 60 + int(c)), (int(x) * 3600 + int(y) * 60 + int(z)))
            for a, b, c, x, y, z in ts]
    n = len(cues)
    end = cues[-1][1] if cues else 0
    speech = sum(e - s for s, e in cues)
    gaps = sorted(((cues[i + 1][0] - cues[i][1]) for i in range(n - 1)), reverse=True)[:3]
    print("%s | cues=%d | 末条结束 %02d:%02d:%02d | 语音时长 %.0fs | 最大空洞 %s" % (
        name, n, end // 3600, end % 3600 // 60, end % 60, speech,
        ["%ds" % round(g) for g in gaps]))

txt = (Path(r"D:\Agent\115Translate\work\subs") / "fbc02abdc78e6db0.en.srt").read_text(
    encoding="utf-8-sig", errors="replace")
lines = [l for l in txt.splitlines() if l.strip() and "-->" not in l and not l.strip().isdigit()]
print("抽样原文:", lines[3:9])
