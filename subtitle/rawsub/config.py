# -*- coding: utf-8 -*-
"""
配置层。

- 用 dataclass 描述全部可配置项，带默认值，缺字段直接用默认。
- YAML 里只写需要改的部分，缺失字段自动补默认。
- 类型标注为 ``Path`` 的字段会自动相对「项目根目录」解析成绝对路径。
- 敏感项（WebDAV 密码等）支持 ``${ENV_VAR}`` 占位符，从环境变量读取。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import yaml

from .errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_CONFIG_NAME = "config.yaml"
EXAMPLE_CONFIG_NAME = "config.example.yaml"


# ================================================================== 配置结构


@dataclass
class WebDAVConfig:
    base_url: str = "http://127.0.0.1:5244/dav"
    username: str = ""
    password: str = ""
    root: str = "/"
    auth: str = "auto"  # auto | basic | digest | none
    verify_ssl: bool = False
    timeout: float = 60.0
    max_retries: int = 3


@dataclass
class ScanConfig:
    video_exts: list[str] = field(
        default_factory=lambda: [".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"]
    )
    subtitle_suffix: str = ".zh.srt"
    min_size_mb: float = 100.0
    max_depth: int = 8
    exclude_dirs: list[str] = field(
        default_factory=lambda: ["@eaDir", ".trashed", "Extras", "extras", "Sample"]
    )
    exclude_name_keywords: list[str] = field(default_factory=list)


@dataclass
class WorkspaceConfig:
    temp_dir: Path = Path("work/tmp")
    subtitle_dir: Path = Path("work/subs")
    state_db: Path = Path("data/state.db")
    keep_audio: bool = False
    keep_en_srt: bool = True
    # 每部翻译完成后在这里留一份「原文 -> 中译」对照记录（Markdown）
    record_dir: Path = Path("work/records")


@dataclass
class ToolsConfig:
    ffmpeg: Path = Path("bin/ffmpeg/ffmpeg.exe")
    ffprobe: Path = Path("bin/ffmpeg/ffprobe.exe")
    # 若上面的路径不存在，会回退到 PATH 里的 ffmpeg/ffprobe
    fallback_to_path: bool = True


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    strategy: str = "auto"  # auto | stream | download
    track_index: int | None = None  # None = 自动挑选音轨
    prefer_languages: list[str] = field(
        default_factory=lambda: ["eng", "en", "jpn", "ja", "kor", "ko", "und"]
    )
    avoid_languages: list[str] = field(
        default_factory=lambda: ["chi", "zho", "zh", "yue", "cmn"]
    )
    # 流式抽取的看门狗：输出文件这么久没增长就判定卡死，切换策略
    stall_timeout_s: float = 180.0
    stream_max_duration_s: float = 7200.0
    download_retries: int = 2


@dataclass
class AsrConfig:
    # 转写+翻译由外部引擎（旧版 AI_src infer.exe）一体化完成，
    # 输出即中文字幕；内置 faster-whisper + LLM 翻译已移除
    device: str = "cuda"  # cuda | cpu（透传给外部引擎）
    # 外部工具路径，如 D:\BaiduNetdiskDownload\AI_src\AI_src\infer.exe
    external_exe: Path | None = None
    # 喂给外部工具的输入：video = 整片下载后喂视频（与手动拖视频完全一致）；
    #                     audio = 只喂本地抽取的 16kHz wav（省流量，但外部工具
    #                     对裸音频的行为未经验证，遇到问题切回 video）
    external_input: str = "video"
    # 单部处理超时（外部工具是黑盒，长片给足时间）
    external_timeout_s: float = 14400.0
    # 透传给外部工具的额外参数（不含位置参数）
    external_extra_args: list[str] = field(default_factory=list)


@dataclass
class OutputConfig:
    encoding: str = "utf-8-sig"  # 带 BOM，播放器/Windows 兼容性最好
    line_ending: str = "crlf"  # crlf | lf
    write_en_srt: bool = False


@dataclass
class PipelineConfig:
    max_items_per_run: int = 0  # 0 = 不限
    max_attempts: int = 3
    max_consecutive_failures: int = 3
    retry_failed: bool = True
    # true = 一部转写完立刻回写一部（不等整批），且转写过程中按字幕产出增量回填
    upload_immediately: bool = True
    # 增量回填节流：字幕每增长这么多秒才 PUT 一次（115 是整文件覆盖上传）；
    # 0 = 关闭转写过程中的增量回填，只保留「一部完成立刻回写」
    partial_upload_interval_s: float = 60.0
    # true = 趁当前影片在转写时，后台线程预下载下一部整片；
    # 下载（网络）与转写（GPU）并行，GPU 不用等下一部的下载时间。0 部待转写时无开销
    download_ahead: bool = True
    shutdown_after_run: bool = False
    shutdown_delay_s: float = 60.0


@dataclass
class NotifyConfig:
    enabled: bool = False
    serverchan_key: str = ""
    bark_url: str = ""
    timeout: float = 15.0


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Path = Path("logs/run.log")
    console: bool = True


@dataclass
class Config:
    webdav: WebDAVConfig = field(default_factory=WebDAVConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # 非 YAML 字段
    root: Path = Path(".")
    config_path: Path = Path("config.yaml")

    # -------------------------------------------------------------- 便捷属性

    @property
    def temp_dir(self) -> Path:
        return self.workspace.temp_dir

    @property
    def subtitle_dir(self) -> Path:
        return self.workspace.subtitle_dir


# ================================================================== 加载逻辑


def _expand_env(value: Any) -> Any:
    """把字符串里的 ${VAR} 替换成环境变量值。"""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _resolve_path(value: Any, root: Path) -> Path:
    p = Path(str(value))
    return p if p.is_absolute() else (root / p)


def _convert(hint: Any, raw: Any, root: Path) -> Any:
    """按 dataclass 字段的类型标注，把 YAML 里的原始值转成目标类型。"""
    if is_dataclass(hint):
        return _build_dataclass(hint, raw if isinstance(raw, dict) else {}, root)

    origin = get_origin(hint)
    if origin is list:
        (inner,) = get_args(hint) or (str,)
        return [_convert(inner, item, root) for item in (raw or [])]

    if hint is Path:
        return _resolve_path(raw, root)

    if hint is bool and isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    if hint is int and isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return raw

    if hint is float and isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return raw

    return raw


def _build_dataclass(cls: type, data: dict, root: Path):
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data or data[f.name] is None:
            continue
        kwargs[f.name] = _convert(hints.get(f.name, Any), _expand_env(data[f.name]), root)
    return cls(**kwargs)


def load_config(path: str | Path | None = None, root: str | Path | None = None) -> Config:
    """加载配置。

    :param path: config.yaml 路径；None 时自动在调用方目录下查找
    :param root: 项目根目录，用于解析相对路径；None 时取配置文件所在目录
    """
    config_path = Path(path) if path else _find_config()
    project_root = Path(root) if root else config_path.resolve().parent

    if not config_path.is_file():
        example = project_root / EXAMPLE_CONFIG_NAME
        hint = f"请先复制 {example} 为 {config_path.name} 并填写。" if example.exists() else ""
        raise ConfigError(f"配置文件不存在：{config_path}。{hint}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件不是合法 YAML：{config_path}\n{exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件顶层必须是字典：{config_path}")

    cfg = _build_dataclass(Config, raw, project_root)
    cfg.root = project_root
    cfg.config_path = config_path.resolve()
    validate(cfg)
    return cfg


def _find_config() -> Path:
    """在「当前目录」和「本文件上级目录」里找 config.yaml。"""
    candidates = [
        Path.cwd() / DEFAULT_CONFIG_NAME,
        Path(__file__).resolve().parent.parent / DEFAULT_CONFIG_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


# ================================================================== 校验


def validate(cfg: Config, strict: bool = False) -> list[str]:
    """检查配置是否可用。返回警告列表；严重问题直接抛 ConfigError。"""
    warnings: list[str] = []

    if not cfg.webdav.base_url:
        raise ConfigError("webdav.base_url 不能为空")
    cfg.webdav.base_url = cfg.webdav.base_url.rstrip("/")
    if not cfg.webdav.root.startswith("/"):
        cfg.webdav.root = "/" + cfg.webdav.root.lstrip("/")
    cfg.webdav.root = "/" + cfg.webdav.root.strip("/")

    if cfg.webdav.auth not in {"auto", "basic", "digest", "none"}:
        raise ConfigError(f"webdav.auth 取值非法：{cfg.webdav.auth}")

    if cfg.audio.strategy not in {"auto", "stream", "download"}:
        raise ConfigError(f"audio.strategy 取值非法：{cfg.audio.strategy}")

    if cfg.asr.device not in {"cuda", "cpu"}:
        raise ConfigError(f"asr.device 取值非法：{cfg.asr.device}")

    # 转写+翻译由外部引擎一体化完成（内置 faster-whisper / LLM 翻译已移除）
    if not cfg.asr.external_exe:
        raise ConfigError(
            "必须配置 asr.external_exe"
            "（如 D:\\BaiduNetdiskDownload\\AI_src\\AI_src\\infer.exe）"
        )
    if not Path(cfg.asr.external_exe).exists():
        raise ConfigError(f"asr.external_exe 不存在：{cfg.asr.external_exe}")
    if cfg.asr.external_input not in {"video", "audio"}:
        raise ConfigError(f"asr.external_input 取值非法：{cfg.asr.external_input}")

    if cfg.output.line_ending not in {"crlf", "lf"}:
        raise ConfigError(f"output.line_ending 取值非法：{cfg.output.line_ending}")

    if strict:
        missing = [n for n in ("username", "password") if not getattr(cfg.webdav, n)]
        if missing and cfg.webdav.auth != "none":
            warnings.append(f"WebDAV 未配置：{', '.join(missing)}")

    return warnings
