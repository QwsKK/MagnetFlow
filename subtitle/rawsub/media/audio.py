# -*- coding: utf-8 -*-
"""
音轨抽取。

两种策略，配置里用 `audio.strategy` 控制：

  stream（默认/首选）
      ffmpeg 直接把 WebDAV 的 HTTP 地址当输入，边下边抽，只落一个 16kHz
      单声道 WAV（2 小时片约 230MB）。服务端支持 Range 时收益最大——
      ffmpeg 可以只取音频相关的字节块；不支持 Range 时至少也省了
      「先把 30GB 原片写进磁盘再读一遍」的开销。

  download（兜底）
      整片下载到临时目录，本地抽完音轨立即删除。
      MKV 索引损坏、115 对 Range 请求限流、流式反复卡死时自动切到这里。

流式策略带一个「看门狗」：输出文件超过 `stall_timeout_s` 没有增长，
就判定卡死并杀掉进程，由上层切到整片下载。
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from ..config import Config
from ..errors import AudioExtractError
from ..utils import ensure_dir, free_bytes, human_size, safe_filename
from ..webdav.client import WebDAVClient
from ..webdav.scanner import VideoItem
from .ffmpeg import (
    FfmpegTools,
    auth_url,
    pick_audio_stream,
    probe_streams,
)

log = logging.getLogger("rawsub.audio")

_STRATEGY_STREAM = "stream"
_STRATEGY_DOWNLOAD = "download"

# ffmpeg 读取 HTTP 时的通用稳健参数
_NET_ARGS = [
    "-user_agent", "115Translate/1.0",
    "-auth_type", "basic",
    "-multiple_requests", "1",      # 允许用 Range 并发/跳转读取
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    "-reconnect_delay_max", "5",
    "-rw_timeout", "30000000",      # 30s，单位微秒
]


class AudioExtractor:
    """把远端视频变成 16kHz 单声道 WAV。"""

    def __init__(self, cfg: Config, client: WebDAVClient, tools: FfmpegTools):
        self.cfg = cfg
        self.client = client
        self.tools = tools
        self.audio = cfg.audio
        self._temp_root = ensure_dir(cfg.workspace.temp_dir)

    # ------------------------------------------------------------------ 对外

    @property
    def temp_root(self) -> Path:
        return self._temp_root

    def work_dir(self, item: VideoItem) -> Path:
        return ensure_dir(self._temp_root / item.key)

    def extract(self, item: VideoItem, dest: Path) -> str:
        """抽取音轨到 dest，返回实际使用的策略名。

        失败时抛 AudioExtractError（两种策略都试过仍不行）。
        """
        dest = Path(dest)
        ensure_dir(dest.parent)
        strategy = self.audio.strategy

        if strategy == _STRATEGY_DOWNLOAD:
            return self._extract_via_download(item, dest)

        if strategy in ("auto", _STRATEGY_STREAM):
            try:
                return self._extract_via_stream(item, dest)
            except AudioExtractError as exc:
                if strategy == _STRATEGY_STREAM:
                    raise
                log.warning("流式抽取失败，切换到整片下载：%s", exc)
                if dest.exists():
                    dest.unlink(missing_ok=True)
                return self._extract_via_download(item, dest)

        raise AudioExtractError(f"未知的 audio.strategy：{strategy}")

    # ------------------------------------------------------------------ 策略一：流式

    def _extract_via_stream(self, item: VideoItem, dest: Path) -> str:
        url = auth_url(
            self.cfg.webdav.base_url, item.path,
            self.cfg.webdav.username, self.cfg.webdav.password,
        )

        stream_index = self._resolve_stream_index(
            source=url,
            forced=self.audio.track_index,
            remote=True,
        )

        cmd: list[str] = [str(self.tools.ffmpeg), "-nostdin", "-y", "-loglevel", "error"]
        cmd += _NET_ARGS
        cmd += ["-i", url]
        if stream_index is not None:
            cmd += ["-map", f"0:{stream_index}"]
        cmd += ["-vn"]
        cmd += self._audio_encode_args()
        cmd += ["-f", "wav", str(dest)]

        log.info("[%s] 流式抽取音轨 -> %s", item.name, dest.name)
        self._run_with_watchdog(cmd, item, dest)
        self._verify_wav(dest, item)
        return _STRATEGY_STREAM

    # ------------------------------------------------------------------ 策略二：整片下载

    def _extract_via_download(self, item: VideoItem, dest: Path) -> str:
        work = self.work_dir(item)
        local_video = work / safe_filename(item.name)
        if local_video.suffix.lower() not in {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"}:
            local_video = local_video.with_suffix(".mkv")

        self._check_disk(item, work)

        log.info("[%s] 整片下载（%s）...", item.name, human_size(item.size))
        last_log = 0.0

        def on_progress(done: int, total: int) -> None:
            nonlocal last_log
            now = time.time()
            if now - last_log >= 20:
                last_log = now
                pct = f"{done / total * 100:.0f}%" if total else "?"
                log.info("  下载 %s / %s（%s）", human_size(done), human_size(total), pct)

        self.client.download(item.path, local_video, progress=on_progress)

        stream_index = self._resolve_stream_index(
            source=str(local_video), forced=self.audio.track_index, remote=False
        )

        cmd: list[str] = [str(self.tools.ffmpeg), "-nostdin", "-y", "-loglevel", "error"]
        cmd += ["-i", str(local_video)]
        if stream_index is not None:
            cmd += ["-map", f"0:{stream_index}"]
        cmd += ["-vn"]
        cmd += self._audio_encode_args()
        cmd += ["-f", "wav", str(dest)]

        try:
            self._run_with_watchdog(cmd, item, dest)
        finally:
            if not self.cfg.workspace.keep_audio:
                local_video.unlink(missing_ok=True)

        self._verify_wav(dest, item)
        return _STRATEGY_DOWNLOAD

    # ------------------------------------------------------------------ 内部

    def _audio_encode_args(self) -> list[str]:
        return [
            "-ac", str(max(1, self.audio.channels)),
            "-ar", str(self.audio.sample_rate),
            "-c:a", "pcm_s16le",
        ]

    def _resolve_stream_index(self, source: str, forced: int | None, remote: bool) -> int | None:
        """决定用哪条音轨。探测失败就返回 None（交给 ffmpeg 默认行为）。"""
        if forced is not None:
            # 显式指定了第 N 条音轨，仍然需要探测才能拿到绝对 stream index
            pass
        timeout = 45.0 if remote else 30.0
        streams = probe_streams(self.tools, source, timeout=timeout)
        if not streams:
            log.debug("未探测到流信息，使用 ffmpeg 默认音轨")
            return None

        audio_count = sum(1 for s in streams if s.is_audio)
        if audio_count <= 1:
            first = next((s for s in streams if s.is_audio), None)
            return first.index if first else None

        index = pick_audio_stream(
            streams, prefer=self.audio.prefer_languages,
            avoid=self.audio.avoid_languages, forced_index=forced,
        )
        langs = ",".join(s.language or "?" for s in streams if s.is_audio)
        log.debug("音轨候选(%d条, lang=%s) -> stream #%s", audio_count, langs, index)
        return index

    def _run_with_watchdog(self, cmd: list[str], item: VideoItem, dest: Path) -> None:
        """跑 ffmpeg，并盯着输出文件是否还在长个儿。"""
        stderr_path = dest.with_suffix(".ffmpeg.log")
        stall = float(self.audio.stall_timeout_s)
        hard_deadline = time.time() + float(self.audio.stream_max_duration_s)

        with open(stderr_path, "w", encoding="utf-8", errors="replace") as err_fh:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=err_fh,
                text=True, encoding="utf-8", errors="replace",
            )
            last_size = 0
            last_growth = time.time()
            while proc.poll() is None:
                time.sleep(2.0)
                size = dest.stat().st_size if dest.exists() else 0
                if size > last_size:
                    last_size = size
                    last_growth = time.time()
                elif time.time() - last_growth > stall:
                    self._kill(proc)
                    raise AudioExtractError(
                        f"抽取停滞超过 {stall:.0f}s（已输出 {human_size(size)}），判定链路卡死"
                    )
                if time.time() > hard_deadline:
                    self._kill(proc)
                    raise AudioExtractError(
                        f"抽取超过总时限 {self.audio.stream_max_duration_s:.0f}s"
                    )

        if proc.returncode != 0:
            tail = ""
            if stderr_path.exists():
                lines = stderr_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                tail = "\n".join(lines[-8:])
            raise AudioExtractError(f"ffmpeg 退出码 {proc.returncode}\n{tail}")

        stderr_path.unlink(missing_ok=True)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    def _check_disk(self, item: VideoItem, work: Path) -> None:
        need = int(item.size * 1.15) + 512 * 1024 * 1024
        have = free_bytes(work)
        if item.size and have < need:
            raise AudioExtractError(
                f"磁盘空间不足：需要约 {human_size(need)}，可用 {human_size(have)}"
            )

    def _verify_wav(self, dest: Path, item: VideoItem) -> None:
        if not dest.exists() or dest.stat().st_size < 1024:
            raise AudioExtractError(f"抽取结果为空或过小：{dest}")
        size = dest.stat().st_size
        # 16kHz * 2 bytes * 1ch
        minutes = size / (self.audio.sample_rate * 2 * max(1, self.audio.channels)) / 60
        log.info("[%s] 音轨就绪：%s（约 %.0f 分钟）", item.name, human_size(size), minutes)
