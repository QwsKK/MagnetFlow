# -*- coding: utf-8 -*-
"""
ffmpeg / ffprobe 的薄封装：定位可执行文件、拼带认证的 URL、探测流信息。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from ..config import Config
from ..errors import ToolFailure, ToolMissing

log = logging.getLogger("rawsub.ffmpeg")

USER_AGENT = "115Translate/1.0 (ffmpeg)"


@dataclass
class FfmpegTools:
    ffmpeg: Path
    ffprobe: Path

    @property
    def ffmpeg_dir(self) -> Path:
        return self.ffmpeg.parent

    def version(self) -> str:
        try:
            out = subprocess.run(
                [str(self.ffmpeg), "-version"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace",
            )
            return out.stdout.splitlines()[0] if out.stdout else "unknown"
        except Exception as exc:  # noqa: BLE001
            return f"unknown ({exc})"


@dataclass
class StreamInfo:
    index: int  # 容器内的绝对 stream index
    codec_type: str  # audio / video / subtitle
    language: str = ""
    codec_name: str = ""
    channels: int = 0
    title: str = ""

    @property
    def is_audio(self) -> bool:
        return self.codec_type == "audio"


def resolve_tools(cfg: Config) -> FfmpegTools:
    """按配置找到 ffmpeg / ffprobe；找不到时回退 PATH，再找不到就报错。"""
    candidates: dict[str, Path | None] = {"ffmpeg": None, "ffprobe": None}

    for name, configured in (("ffmpeg", cfg.tools.ffmpeg), ("ffprobe", cfg.tools.ffprobe)):
        p = Path(configured)
        if p.is_file():
            candidates[name] = p.resolve()
        elif cfg.tools.fallback_to_path:
            found = shutil.which(name)
            if found:
                candidates[name] = Path(found)
                log.debug("%s 走 PATH：%s", name, found)

    missing = [k for k, v in candidates.items() if v is None]
    if missing:
        raise ToolMissing(
            "、".join(missing) + " 未找到。请运行 scripts\\install.ps1 安装，"
            "或在 config.yaml 的 tools 段填写正确路径。"
        )
    return FfmpegTools(ffmpeg=candidates["ffmpeg"], ffprobe=candidates["ffprobe"])  # type: ignore[arg-type]


def auth_url(base_url: str, remote_path: str, username: str, password: str) -> str:
    """把账号密码编进 URL，供 ffmpeg 直接以 HTTP(S) 方式读取 WebDAV 文件。

    用户名/密码会做百分号编码，所以含 `@`、`:`、`/` 之类的特殊字符也能用。
    """
    split = urlsplit(base_url)
    path = remote_path if remote_path.startswith("/") else "/" + remote_path
    encoded_path = "/".join(quote(seg, safe="~()*!.'-_") for seg in path.split("/"))
    netloc = split.netloc
    if username:
        user = quote(username, safe="")
        pwd = quote(password or "", safe="")
        netloc = f"{user}:{pwd}@{split.netloc}"
    return f"{split.scheme}://{netloc}{split.path.rstrip('/')}{encoded_path}"


def probe_streams(
    tools: FfmpegTools,
    source: str,
    timeout: float = 60.0,
    extra_input_args: list[str] | None = None,
) -> list[StreamInfo]:
    """用 ffprobe 列出容器内的流。失败返回空列表（交给调用方按默认音轨处理）。"""
    cmd: list[str] = [str(tools.ffprobe), "-v", "error"]
    cmd += extra_input_args or []
    cmd += [
        "-analyzeduration", "0",
        "-probesize", "10M",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels:stream_tags=language,title",
        "-print_format", "json",
        source,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        log.warning("ffprobe 超时（%.0fs），按默认音轨处理：%s", timeout, source)
        return []
    except OSError as exc:
        log.warning("ffprobe 无法执行：%s", exc)
        return []

    if proc.returncode != 0 or not proc.stdout.strip():
        log.debug("ffprobe 无有效输出（code=%s）：%s", proc.returncode, proc.stderr[:300])
        return []

    try:
        payload: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("ffprobe 输出不是合法 JSON")
        return []

    streams: list[StreamInfo] = []
    for raw in payload.get("streams", []):
        tags = raw.get("tags") or {}
        streams.append(
            StreamInfo(
                index=int(raw.get("index", -1)),
                codec_type=raw.get("codec_type", ""),
                language=(tags.get("language") or "").lower(),
                codec_name=raw.get("codec_name", ""),
                channels=int(raw.get("channels") or 0),
                title=(tags.get("title") or "").lower(),
            )
        )
    return streams


def pick_audio_stream(
    streams: list[StreamInfo],
    prefer: list[str],
    avoid: list[str],
    forced_index: int | None = None,
) -> int | None:
    """挑一条音轨，返回它在容器里的绝对 stream index。

    挑选顺序：
      1. 显式指定 forced_index（第 N 条音轨，从 0 起）；
      2. 语言在 prefer 列表里的，按 prefer 顺序优先；
      3. 非 avoid 语言的第一条；
      4. 实在没有信息，就第一条音轨。
    """
    audio = [s for s in streams if s.is_audio]
    if not audio:
        return None

    if forced_index is not None:
        if 0 <= forced_index < len(audio):
            return audio[forced_index].index
        log.warning("配置的音轨序号 %d 越界（共 %d 条），改用自动挑选", forced_index, len(audio))

    for lang in prefer:
        for s in audio:
            if s.language == lang.lower():
                return s.index

    for s in audio:
        if s.language and s.language not in [a.lower() for a in avoid]:
            return s.index

    return audio[0].index


def run_ffmpeg(cmd: list[str], timeout: float | None = None, log_tag: str = "") -> None:
    """执行 ffmpeg，非零退出码时抛 ToolFailure（带 stderr 尾巴，便于排错）。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure(f"ffmpeg 超时（{timeout}s）{log_tag}") from exc
    except OSError as exc:
        raise ToolFailure(f"ffmpeg 无法执行：{exc}") from exc

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise ToolFailure(
            f"ffmpeg 失败（code={proc.returncode}）{log_tag}\n" + "\n".join(tail),
            returncode=proc.returncode,
            stderr=proc.stderr or "",
        )
