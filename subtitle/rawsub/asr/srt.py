# -*- coding: utf-8 -*-
"""
SRT 字幕的解析、生成与规范化。

时间戳解析做得比较宽容：`,` 和 `.` 分隔毫秒都认，秒/毫秒缺失也认，
索引编号错乱不影响解析（以出现顺序重建）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from ..utils import (
    dedupe_repeat,
    fmt_ts,
    is_filler_only,
    is_nonlinguistic,
    looks_like_hallucination,
)

# 00:01:02,345  |  1:02:03.45  |  02:03,4
_TS = re.compile(r"^\s*(\d{1,3}):([0-5]?\d):([0-5]?\d)[,.](\d{1,3})\s*$")
_TS_NO_MS = re.compile(r"^\s*(\d{1,3}):([0-5]?\d):([0-5]?\d)\s*$")
_INDEX_LINE = re.compile(r"^\s*\d+\s*$")


@dataclass
class Cue:
    """一条字幕。"""

    index: int
    start: float  # 秒
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def with_text(self, text: str) -> "Cue":
        return replace(self, text=text)


# ---------------------------------------------------------------------- 解析


def _parse_timestamp(token: str) -> float | None:
    m = _TS.match(token)
    if m:
        h, mnt, s, ms = m.groups()
        ms = ms.ljust(3, "0")
        return int(h) * 3600 + int(mnt) * 60 + int(s) + int(ms) / 1000.0
    m = _TS_NO_MS.match(token)
    if m:
        h, mnt, s = m.groups()
        return float(int(h) * 3600 + int(mnt) * 60 + int(s))
    return None


def parse_srt(text: str) -> list[Cue]:
    """把 SRT 文本解析成 Cue 列表。"""
    if text.startswith("﻿"):
        text = text[1:]
    # 统一行尾。历史上出现过 \r\r\n（Windows 文本模式把 \r\n 又翻译了一次），
    # 这里全部归一成 \n，再按空行切块，任何平台生成的 SRT 都能读。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[Cue] = []

    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue

        idx = 0
        if _INDEX_LINE.match(lines[0]):
            idx = int(lines[0].strip())
            lines = lines[1:]
        if not lines:
            continue

        ts_line = lines[0]
        if "-->" not in ts_line:
            continue
        left, _, right = ts_line.partition("-->")
        start = _parse_timestamp(left.strip())
        end = _parse_timestamp(right.strip().split()[0] if right.strip() else "")
        if start is None or end is None:
            continue

        body = "\n".join(lines[1:]).strip()
        cues.append(Cue(index=idx if idx else len(cues) + 1, start=start, end=end, text=body))

    # 重建索引，保证连续
    for i, cue in enumerate(cues, start=1):
        cue.index = i
    return cues


def load_srt(path: str | Path, encoding: str = "utf-8-sig") -> list[Cue]:
    return parse_srt(Path(path).read_text(encoding=encoding, errors="replace"))


# ---------------------------------------------------------------------- 生成


def dumps_srt(cues: list[Cue], line_ending: str = "\r\n") -> str:
    parts: list[str] = []
    for i, cue in enumerate(cues, start=1):
        parts.append(str(i))
        parts.append(f"{fmt_ts(cue.start)} --> {fmt_ts(cue.end)}")
        parts.append(cue.text.replace("\r", " ").strip() or " ")
        parts.append("")
    return line_ending.join(parts).rstrip() + line_ending


def save_srt(cues: list[Cue], path: str | Path, encoding: str = "utf-8-sig",
             line_ending: str = "\r\n") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline="" 很关键：禁止 Python 在 Windows 上再做一次 \n -> \r\n 翻译，
    # 否则我们手工拼好的 \r\n 会变成 \r\r\n，部分播放器会解析失败。
    p.write_text(dumps_srt(cues, line_ending), encoding=encoding, newline="")
    return p


# ---------------------------------------------------------------------- 规范化


def normalize_cues(cues: list[Cue], max_chars: int = 40, max_duration: float = 7.0,
                   max_cue_duration: float = 20.0, filter_hallucination: bool = True,
                   filter_nonlinguistic: bool = True,
                   remove_filler_lines: bool = True) -> list[Cue]:
    """Whisper 原始分段 -> 适合观看 + 适合翻译的字幕。

    依次做：去幻觉复读 -> 过滤噪声行（幻觉/非语言假名/纯填充词）
    -> 截断异常时长 -> 过长的段按标点切分 -> 过短的段合并。

    截断异常时长：Whisper 在长静音后经常把 segment 的结束时间拉得很长
    （比如 3 秒的台词标成 4 分钟），不截的话这条字幕会在屏幕上挂一整首歌。
    """
    cleaned: list[Cue] = []
    for cue in cues:
        text = (cue.text or "").strip()
        if not text:
            continue
        text = dedupe_repeat(text)
        if filter_hallucination and looks_like_hallucination(text):
            continue
        # 呻吟/喘息等纯声音假名行、纯填充词行（あのー…ええと…）：没有翻译价值，
        # 留着只会让 LLM 产出垃圾中文，还浪费 token
        if filter_nonlinguistic and is_nonlinguistic(text):
            continue
        if remove_filler_lines and is_filler_only(text):
            continue
        cleaned.append(cue.with_text(text))

    split: list[Cue] = []
    for cue in cleaned:
        if cue.duration > max_cue_duration:
            cue = replace(cue, end=cue.start + max_cue_duration)
        split.extend(_split_long(cue, max_chars, max_duration))

    merged = _merge_short(split, max_chars)
    # 兜底：保证时间单调、互不重叠
    merged.sort(key=lambda c: c.start)
    for i, cue in enumerate(merged):
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if cue.end <= cue.start:
            merged[i] = replace(cue, end=cue.start + 0.5)
        elif nxt and cue.end > nxt.start:
            merged[i] = replace(cue, end=max(nxt.start, cue.start + 0.3))
    for i, cue in enumerate(merged, start=1):
        cue.index = i
    return merged


def _split_long(cue: Cue, max_chars: int, min_duration: float) -> list[Cue]:
    """单条太长就按标点/空格切开，时间按字数比例分配。"""
    text = cue.text
    if len(text) <= max_chars and cue.duration <= min_duration * 2:
        return [cue]

    pieces = _split_text(text, max_chars)
    if len(pieces) <= 1:
        return [cue]

    total = sum(len(p) for p in pieces) or 1
    out: list[Cue] = []
    cursor = cue.start
    span = cue.duration
    for piece in pieces:
        share = span * (len(piece) / total)
        out.append(Cue(0, cursor, cursor + share, piece))
        cursor += share
    if out:
        out[-1] = Cue(0, out[-1].start, max(out[-1].end, cue.end), out[-1].text)
    return out


def _split_text(text: str, max_chars: int) -> list[str]:
    """先把换行当天然断点，再按标点、最后按空格贪心切分。"""
    chunks: list[str] = []
    for para in text.split("\n"):
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        chunks.extend(_greedy_split(para, max_chars))
    return [c for c in (s.strip() for s in chunks) if c]


_PUNCT = "。．.!！?？,，、;；:：)）]】」』》\""


def _greedy_split(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars and (ch in _PUNCT or ch == " "):
            out.append(cur)
            cur = ""
        elif len(cur) >= max_chars * 1.6:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def _merge_short(cues: list[Cue], max_chars: int) -> list[Cue]:
    """把过短的相邻片段合并（Whisper 常把一句话切得稀碎）。

    借鉴 WhisperJAV 的孤立助词回并：`よ/ね/わ/よね/のよ` 这类终助词被
    单独切出来时没有任何含义，必须并回前一句（…です + よ -> …ですよ）。
    """
    particles = {
        "よ", "ね", "な", "わ", "の", "さ", "ぞ", "ぜ", "か", "かな", "っけ",
        "よね", "よな", "わね", "わよ", "のよ", "のね", "だよ", "だね", "だな",
        "ですよ", "ですね", "ますよ", "ますね",
    }
    merged: list[Cue] = []
    for cue in cues:
        prev = merged[-1] if merged else None
        text = cue.text.strip()
        # 孤立助词：无论时长条件如何都并回前一条
        if (
            prev is not None
            and text in particles
            and len(prev.text) + len(text) + 1 <= max_chars
        ):
            merged[-1] = Cue(0, prev.start, max(prev.end, cue.end), f"{prev.text} {text}".strip())
            continue
        if (
            prev is not None
            and len(prev.text) + len(cue.text) + 1 <= max_chars
            and cue.start - prev.end <= 0.35
            and prev.duration < 1.2
        ):
            merged[-1] = Cue(0, prev.start, max(prev.end, cue.end), f"{prev.text} {cue.text}".strip())
        else:
            merged.append(cue)
    return merged


# ------------------------------------------------------------------ 翻译后时间轴校正

# 借鉴 WhisperJAV timing_adjuster 的「STRICT」原则：只对命中问题的行调整，
# 其余行绝不碰，避免矫正本身制造新的漂移。end 固定、只推 start。

# 中文观看舒适区约 4~9 字/秒；超过 12 一闪而过，低于 4 挂屏发呆
MIN_SAFE_CPS = 4.0
MAX_SAFE_CPS = 12.0
MIN_TEXT_LEN_FOR_CPS = 4  # 太短的行不做 CPS 检查（"啊？""好"无所谓语速）
HALLUCINATION_DURATION_S = 12.0  # 单条超过这个时长基本是 Whisper 时间幻觉
MIN_DURATION_S = 0.5
MAX_DURATION_S = 8.0
OVERLAP_GAP_S = 0.05  # 与上一条的最小间隔


def adjust_timing_post(src: list[Cue], dst: list[Cue]) -> list[Cue]:
    """翻译完成后按译文实际长度校正时间轴。

    触发条件（借鉴 WhisperJAV TimingAdjuster，但按日译中的特点调整——
    译文天然比原文短 30% 以上，"内容变化"在这里是常态而非异常，
    所以核心判据改用 CPS 与时长异常）：
      (a) 单条时长 > 12s（Whisper 时间幻觉）；
      (b) 疑似被合并：时长比原文明显变长且译文很短；
      (c) CPS 过慢（< 4 字/秒，挂屏）→ 压缩显示时长；
      (d) CPS 过快（> 12 字/秒，一闪而过）→ 尽量向前扩展。

    处理：end 固定，start = end - 理想时长（按译文长度 / CPS 反推），
    并保证不与上一条重叠。未命中条件的行原样保留（STRICT 原则）。
    """
    if len(src) != len(dst):
        return dst

    adjusted: list[Cue] = []
    stats = {"hallucination": 0, "merged": 0, "slow": 0, "fast": 0}

    for i, cue in enumerate(dst):
        orig = src[i]
        text = (cue.text or "").strip()
        if not text:
            adjusted.append(cue)
            continue

        duration = cue.duration
        ideal = _ideal_duration(text)
        reason = ""

        # (a) 时长幻觉
        if duration > HALLUCINATION_DURATION_S:
            reason = "hallucination"
            stats["hallucination"] += 1
        # (b) 疑似合并：时长比原文明显变长且译文不长
        elif abs(duration - orig.duration) > 0.25 and len(text) < 20:
            reason = "merged"
            stats["merged"] += 1
        # (c)/(d) CPS 双向检查
        elif len(text) >= MIN_TEXT_LEN_FOR_CPS and duration > 0:
            cps = len(text) / duration
            if cps < MIN_SAFE_CPS:
                reason = "slow"
                stats["slow"] += 1
            elif cps > MAX_SAFE_CPS:
                reason = "fast"
                stats["fast"] += 1

        if reason:
            new_start = max(cue.end - ideal, 0.0)
            # 防重叠：与上一条至少留 50ms（fast 扩展时这里会自动收紧）
            prev_end = adjusted[-1].end if adjusted else 0.0
            new_start = max(new_start, prev_end + OVERLAP_GAP_S)
            # 别把 start 推到 end 之后（保底 0.3s 可读时长）
            new_start = min(new_start, cue.end - 0.3)
            cue = replace(cue, start=max(new_start, 0.0))

        adjusted.append(cue)

    n_changed = sum(stats.values())
    if n_changed:
        import logging

        logging.getLogger("rawsub.asr.srt").info(
            "时间轴校正：%d 条（时长幻觉 %d / 疑似合并 %d / CPS 过慢 %d / CPS 过快 %d）",
            n_changed, stats["hallucination"], stats["merged"], stats["slow"], stats["fast"],
        )
    return adjusted


def _ideal_duration(text: str) -> float:
    """按译文长度反推理想时长，夹在 [MIN, MAX] 之间。"""
    ideal = max(len(text) / MIN_SAFE_CPS, MIN_DURATION_S)
    return min(ideal, MAX_DURATION_S)
