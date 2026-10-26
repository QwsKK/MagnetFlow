# -*- coding: utf-8 -*-
"""通用小工具：时间/体积格式化、重试、计时、路径处理。"""
from __future__ import annotations

import functools
import hashlib
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")

# ------------------------------------------------------------------ 格式化


def human_size(num_bytes: float) -> str:
    """把字节数格式化成人类可读形式。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024.0
    return f"{size:.1f}TB"


def human_duration(seconds: float) -> str:
    """把秒数格式化成 `1h02m03s` / `02m03s` 形式。"""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m:02d}m{s:02d}s"
    return f"{s}s"


def fmt_ts(seconds: float) -> str:
    """SRT 时间戳：00:01:02,345"""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ------------------------------------------------------------------ 字符串


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 80) -> str:
    """把任意文件名清洗成 Windows 合法且短小的名字，用于临时文件。"""
    cleaned = _ILLEGAL.sub("_", name).strip().rstrip(".")
    if not cleaned:
        cleaned = "unnamed"
    if len(cleaned) > max_len:
        digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        cleaned = cleaned[: max_len - 9].rstrip("._-") + "_" + digest
    return cleaned


def stable_key(text: str) -> str:
    """由任意字符串生成稳定的短哈希，用作数据库主键 / 临时文件名。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def truncate(text: str, limit: int = 200) -> str:
    text = (text or "").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------------ 重试


def retry(
    times: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[BaseException, int], None] | None = None,
) -> Callable:
    """指数退避重试装饰器。

    :param times: 总尝试次数（含第一次）
    :param delay: 首次重试前的等待秒数
    :param backoff: 每次重试的等待倍数
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            wait = delay
            last_exc: BaseException | None = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt >= times:
                        break
                    if on_retry:
                        on_retry(exc, attempt)
                    time.sleep(wait)
                    wait *= backoff
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


# ------------------------------------------------------------------ 计时


@contextmanager
def timer() -> Iterator[Callable[[], float]]:
    """用法：with timer() as t: ...   elapsed = t()"""
    start = time.perf_counter()

    def elapsed() -> float:
        return time.perf_counter() - start

    yield elapsed


# ------------------------------------------------------------------ 文件系统


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def free_bytes(path: str | Path) -> int:
    """目录所在盘的剩余空间。目录不存在时自动沿父级找到存在的最近祖先。"""
    import shutil

    p = Path(path)
    probe = p if p.exists() else p.parent
    while not probe.exists():
        parent = probe.parent
        if parent == probe:  # 到根了
            return 0
        probe = parent
    total, used, free = shutil.disk_usage(str(probe))
    return free


def chunked(items: list[T], size: int) -> Iterator[list[T]]:
    """把列表切成大小为 size 的连续块。"""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def iter_batches(items: list[T], predicate_len: Callable[[T], int], max_len: int) -> Iterator[list[T]]:
    """按累计权重分批，而不是简单按条数切。

    用于把字幕句按「累计字符数」攒够一批再发给 LLM，避免短句批次浪费、
    长句批次超长。
    """
    batch: list[T] = []
    acc = 0
    for item in items:
        w = predicate_len(item)
        if batch and acc + w > max_len:
            yield batch
            batch, acc = [], 0
        batch.append(item)
        acc += w
    if batch:
        yield batch


# ------------------------------------------------------------------ 文本清洗


_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# 常见日文片头/片尾/制作信息幻觉（Whisper 在音乐/静音段的高频输出）
_JP_HALLUCINATIONS = {
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "チャンネル登録お願いします",
    "チャンネル登録よろしくお願いします",
    "おしまい",
    "おわり",
    "終わり",
    "完",
    "提供",
    "字幕",
    "制作",
    "著作",
    "all rights reserved",
    "subs by",
    "subtitles by",
}


def dedupe_repeat(text: str, max_run: int = 3) -> str:
    """把 Whisper 常见的复读幻觉压掉：'go go go go go' -> 'go go go'。

    英文按空格分词处理；日文没有空格，改按重复片段检测，
    例如 'ああああああ' -> 'あああ'、'はいはいはいはいはい' -> 'はいはいはい'。
    """
    if _CJK_RE.search(text):
        return _dedupe_cjk(text, max_run)
    words = text.split()
    if len(words) <= max_run:
        return text
    out: list[str] = []
    run = 1
    for i, w in enumerate(words):
        if i and w == words[i - 1]:
            run += 1
        else:
            run = 1
        if run <= max_run:
            out.append(w)
    return " ".join(out)


def _dedupe_cjk(text: str, max_run: int) -> str:
    """无空格文本的复读压缩：找完全重复的片段，重复次数超限就截断。"""
    n = len(text)
    if n < 4:
        return text
    # 从大到小尝试重复片段长度（上限 12 字，避免长文本开销过大）
    for seg_len in range(min(12, n // (max_run + 1)), 0, -1):
        seg = text[:seg_len]
        count = 0
        i = 0
        while i + seg_len <= n and text[i : i + seg_len] == seg:
            count += 1
            i += seg_len
        if count > max_run and i == n:
            return seg * max_run
    return text


def looks_like_hallucination(text: str) -> bool:
    """粗判 Whisper 在无声片段上的典型幻觉输出。"""
    t = (text or "").strip().lower()
    if not t:
        return True
    noise = {
        "[blank_audio]",
        "[silence]",
        "[music]",
        "(silence)",
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "subtitles by",
        "subtitle by",
        "amara.org",
    }
    core = t.strip(" .!?,。！？、…·〜～♪♫ ")
    return core in noise or core in _JP_HALLUCINATIONS


# ------------------------------------------------------------------ 日语清洗

# 「声音假名字母表」：呻吟/喘息/呼吸只会用这些假名（借鉴 WhisperJAV
# nonlinguistic_utterance_filter）：元音、小写元音、促音、长音、拨音。
SOUND_KANA = frozenset("あぁいぃうぅえぇおぉんゔァィゥェォっャュョゎー・はばぱひびぴふぶぷ")

# 纯假名但其实是实词的白名单（避免误杀）
_NONLINGUISTIC_WHITELIST = {
    "あおい", "おおい", "うんどう", "おおきい", "いいえ",
    "いいね", "うんうん", "ええっと", "おおさか", "あいしてる", "おねがい",
    "あつい", "あまい", "うれしい", "おいしい", "えらい", "おっきい",
}

# 填充词/相槌（借鉴 WhisperJAV japanese_postprocessor 的 aizuchi_fillers），
# 整行只有这些词（可重复出现）时视为无信息行
_FILLER_WORDS = frozenset({
    "あの", "あのー", "あのう", "ええと", "えっと", "まあ", "まぁ", "なんか",
    "こう", "うん", "はい", "ええ", "そう", "えっ", "あっ", "うーん", "んー",
    "えー", "えーっと", "その", "うむ", "おう", "ふん", "へえ",
    "へー", "ふーん", "うわ", "うわあ", "わあ", "わー",
})

_FILLER_STRIP = "、。！？!?,.…·〜～♪♫ "


def is_nonlinguistic(text: str) -> bool:
    """整行纯「声音假名」（呻吟/喘息/呼吸），没有语言学内容。

    判定：行内无汉字/英文等实质字符，去掉标点后每个字符都属于
    SOUND_KANA，且不命中实词白名单。
    """
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff a-zA-Z]", t):
        return False
    core = "".join(ch for ch in t if ch not in _FILLER_STRIP)
    if not core:
        return False
    if core in _NONLINGUISTIC_WHITELIST:
        return False
    return all(ch in SOUND_KANA for ch in core)


def is_filler_only(text: str) -> bool:
    """整行只有填充词/相槌（あのー、ええと、うん…），没有实质内容。"""
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", t):
        return False
    core = "".join(ch for ch in t if ch not in _FILLER_STRIP)
    if not core:
        return False
    # 贪心匹配填充词，允许单个元音/拨音/长音穿插
    i = 0
    while i < len(core):
        for w in sorted(_FILLER_WORDS, key=len, reverse=True):
            if core.startswith(w, i):
                i += len(w)
                break
        else:
            if core[i] in "あいうえおんーぁぃぅぇぉ":
                i += 1
            else:
                return False
    return True
