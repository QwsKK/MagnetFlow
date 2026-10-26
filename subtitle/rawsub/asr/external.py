# -*- coding: utf-8 -*-
"""外部「转写+翻译」一体化引擎。

对接旧版工具 AI_src 的 infer.exe（faster-whisper large-v3 打包版）。
它对视频/音频直接输出中文字幕，转写+翻译一步到位，用法（来自其使用说明）：

    infer.exe --audio_suffixes="mp4,mkv" --sub_formats="srt" --device="cuda" <文件或目录>

支持参数：--overwrite / --output_dir / --audio_suffixes / --sub_formats / --log_level

设计要点：
- 黑盒调用：不关心内部模型和翻译策略，只认「输入媒体文件 -> 输出同名 .srt」
- 不加载内置 Whisper、不拉起 llama.cpp，显存全留给外部工具
- 转写进度不可见（黑盒），只能看到进程存活与日志文件增长
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..errors import TranscribeError
from ..utils import ensure_dir
from .srt import Cue, load_srt

log = logging.getLogger("rawsub.asr.external")

# infer.exe 按后缀过滤输入文件；对本地下载的视频统一改名成 .mkv（容器不变，
# 内部 ffmpeg 按内容识别，改名只是为了让后缀过滤通过）
_VIDEO_EXTS = {".mkv", ".mp4"}

# 日志里的进度线索：百分比（tqdm 风格或纯文本）+ 字幕段时间戳（[00:12 --> 00:15] 等）
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SEG_RE = re.compile(
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:[.,]\d+)?\s*-->\s*(\d{1,2}):(\d{2})(?::(\d{2}))?(?:[.,]\d+)?"
)

# 进度轮询间隔（秒）；infer.exe 是黑盒，靠读它自己的输出日志估进度
_POLL_INTERVAL_S = 10.0


class ExternalASR:
    """调用外部转写+翻译工具，产出中文字幕。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.exe = Path(cfg.asr.external_exe)
        self.timeout_s = float(cfg.asr.external_timeout_s)

    # ------------------------------------------------------------------ 对外

    def run(self, input_path: Path, output_dir: Path, *, is_video: bool,
            on_partial: Callable[[list[Cue]], None] | None = None) -> list[Cue]:
        """处理单个媒体文件，返回解析好的中文字幕条目。

        :param input_path: 输入视频/wav（会被 infer.exe 在原地读取）
        :param output_dir: 字幕输出目录（infer.exe 在此生成 <stem>.srt）
        :param is_video: 输入是否为视频（决定 --audio_suffixes）
        :param on_partial: 转写过程中字幕文件每增长一批就回调一次
            （传入当前已解析的完整 Cue 列表），用于增量回填；可None。
            注意：黑盒引擎若是一次性在结尾写文件，此回调自然不会触发。
        """
        input_path = Path(input_path)
        if not input_path.is_file():
            raise TranscribeError(f"外部引擎输入不存在：{input_path}")

        suffixes = "mp4,mkv" if is_video else "wav"
        output_dir = ensure_dir(output_dir)
        # infer.exe 的输出文件名 = 输入文件名主体 + .srt
        expected = output_dir / f"{input_path.stem}.srt"

        cmd: list[str] = [str(self.exe)]
        cmd += ["--overwrite"]
        cmd += [f"--audio_suffixes={suffixes}"]
        cmd += ["--sub_formats=srt"]
        cmd += [f"--device={self.cfg.asr.device}"]
        cmd += ["--log_level=INFO"]
        cmd += [f"--output_dir={output_dir}"]
        cmd += list(self.cfg.asr.external_extra_args)
        cmd += [str(input_path)]

        log_path = output_dir / "external-asr.log"
        log.info("  调用外部引擎：%s", self.exe.name)
        log.info("  输入：%s（video=%s）", input_path.name, is_video)

        started = time.time()
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.Popen(
                cmd, stdout=fh, stderr=subprocess.STDOUT,
                cwd=str(self.exe.parent),
            )
            try:
                self._wait_with_progress(proc, log_path, input_path,
                                         expected if on_partial else None, on_partial)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=15)
                raise TranscribeError(
                    f"外部引擎超过 {self.timeout_s / 3600:.1f} 小时未完成，已终止"
                    f"（日志：{log_path}）"
                )

        elapsed = time.time() - started
        if expected.is_file() and expected.stat().st_size > 0:
            # infer.exe（PyInstaller + CUDA）退出清理阶段可能崩掉
            # （0xC0000409 fail-fast），但字幕在崩溃前已完整写出——
            # 只要产物在就当成功，别把干完的活当失败
            if proc.returncode != 0:
                log.warning(
                    "  外部引擎异常退出（退出码 %s），但字幕已产出，继续处理",
                    proc.returncode,
                )
        elif proc.returncode != 0:
            raise TranscribeError(
                f"外部引擎退出码 {proc.returncode}（用时 {elapsed:.0f}s，"
                f"日志：{log_path}）"
            )
        else:
            raise TranscribeError(
                f"外部引擎未产出字幕：期望 {expected}（日志：{log_path}）"
            )

        log.info("  外部引擎完成，用时 %s", f"{elapsed / 60:.1f} 分钟")
        cues = load_srt(expected, encoding="utf-8-sig")
        log.info("  解析到 %d 条中文字幕", len(cues))
        return cues

    @staticmethod
    def normalize_video_name(local_video: Path) -> Path:
        """把下载好的本地视频后缀统一成 infer.exe 认识的 .mkv（仅改名，不转码）。"""
        if local_video.suffix.lower() not in _VIDEO_EXTS:
            renamed = local_video.with_suffix(".mkv")
            local_video.rename(renamed)
            return renamed
        return local_video

    # ------------------------------------------------------------------ 进度监视

    def _wait_with_progress(self, proc: subprocess.Popen, log_path: Path,
                            input_path: Path,
                            partial_path: Path | None = None,
                            on_partial: Callable[[list[Cue]], None] | None = None) -> None:
        """等待外部引擎结束，期间轮询它自己的输出日志展示进度。

        若传入 partial_path/on_partial，则同时监视字幕文件增长做增量回调
        （读到一半的半行文本由上层容忍：解析失败的碎块会被自动跳过，
        最终以进程结束后的完整解析为准）。
        """
        total_s = self._probe_duration(input_path)
        reported_pct = -1.0
        reported_ts = -1.0
        last_srt_size = 0

        while True:
            try:
                proc.wait(timeout=_POLL_INTERVAL_S)
                return
            except subprocess.TimeoutExpired:
                pass

            pct, end_ts = self._progress_from_log(log_path, total_s)
            if pct is not None and pct >= reported_pct and pct - reported_pct >= 2.0:
                reported_pct = pct
                if total_s:
                    log.info("  进度 %.0f%%（已处理到 %s / %s）",
                             pct, human_clock(end_ts or 0), human_clock(total_s))
                else:
                    log.info("  进度 %.0f%%", pct)
            elif pct is None and end_ts is not None and end_ts >= reported_ts:
                reported_ts = end_ts
                # 没拿到总时长时退而求其次：只报绝对进度点
                log.info("  已处理到 %s", human_clock(end_ts))

            if partial_path is not None and on_partial is not None:
                size = self._srt_size(partial_path)
                if size > last_srt_size:
                    last_srt_size = size
                    try:
                        cues = load_srt(partial_path, encoding="utf-8-sig")
                    except Exception:  # noqa: BLE001 — 文件正被写入，读坏了等下轮
                        continue
                    if cues:
                        try:
                            on_partial(cues)
                        except Exception as exc:  # noqa: BLE001
                            # 增量回填失败绝不打断转写本身
                            log.warning("  增量回填失败（不影响转写）：%s", exc)

    @staticmethod
    def _srt_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _progress_from_log(self, log_path: Path, total_s: float | None):
        """从外部引擎日志尾部解析进度，返回（百分比, 已处理到的秒数），都可能为 None。"""
        tail = _read_tail(log_path)
        if not tail:
            return None, None

        # 1) 百分比线索：取最后一个 0~100 的百分数（tqdm 或文本进度）
        pct = None
        for m in _PCT_RE.finditer(tail):
            v = float(m.group(1))
            if 0.0 <= v <= 100.0:
                pct = v

        # 2) 字幕段时间戳线索：取最新的「-->」终点，换算成百分比
        end_ts = None
        for m in _SEG_RE.finditer(tail):
            g = m.groups()
            ts = _ts_seconds(g[0], g[1], g[2])
            te = _ts_seconds(g[3], g[4], g[5])
            end_ts = max(end_ts or 0.0, te if te >= ts else ts)

        if pct is not None:
            return pct, end_ts
        if end_ts is not None and total_s:
            return min(100.0, end_ts / total_s * 100.0), end_ts
        return None, end_ts

    def _probe_duration(self, input_path: Path) -> float | None:
        """用 ffprobe 拿媒体总时长（秒），失败不影响主流程。"""
        ffprobe = getattr(self.cfg.tools, "ffprobe", None)
        if not ffprobe or not Path(ffprobe).is_file():
            return None
        try:
            out = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(input_path)],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            return float(out.stdout.strip().splitlines()[0])
        except Exception:  # noqa: BLE001
            return None


def _read_tail(path: Path, size: int = 8192) -> str:
    """读日志最后 size 字节（黑盒引擎可能用 \\r 刷进度条，一并按行拆开）。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            fh.seek(max(0, end - size))
            return fh.read().decode("utf-8", errors="replace").replace("\r", "\n")
    except OSError:
        return ""


def _ts_seconds(a: str, b: str, c: str | None) -> float:
    """「mm:ss」或「hh:mm:ss」 -> 秒。"""
    if c is not None:
        return int(a) * 3600 + int(b) * 60 + float(c)
    return int(a) * 60 + int(b)


def human_clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
