# -*- coding: utf-8 -*-
"""
片库扫描器。

职责：递归遍历 WebDAV 片库目录，挑出「视频文件 + 还没有中文字幕」的条目。

判定规则（全部可在 config.yaml -> scan 里调）：
  1. 扩展名在 video_exts 里；
  2. 文件大小 >= min_size_mb（过滤预告片、样片、nfo 同名短片）；
  3. 所在目录里不存在 `<视频主文件名><subtitle_suffix>`。
     为减少请求，同一目录下的文件只做一次 PROPFIND，之后在内存里查表。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable

from ..config import Config
from ..utils import human_size, stable_key
from .client import DavResource, WebDAVClient

log = logging.getLogger("rawsub.scanner")


@dataclass
class VideoItem:
    """一个待处理的视频。"""

    path: str  # WebDAV 绝对路径
    name: str  # 文件名（含扩展名）
    size: int
    last_modified: str | None = None

    # 运行时填充
    key: str = ""
    sub_path: str = ""
    local_audio: str = ""
    local_en_srt: str = ""
    local_zh_srt: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = stable_key(self.path)

    @property
    def stem(self) -> str:
        return PurePosixPath(self.path).stem

    @property
    def dir(self) -> str:
        return str(PurePosixPath(self.path).parent)

    def sibling_subtitle(self, suffix: str) -> str:
        """同目录下、同名换后缀的字幕路径。"""
        return str(PurePosixPath(self.dir) / (self.stem + suffix))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Video {self.path} ({human_size(self.size)})>"


@dataclass
class ScanResult:
    items: list[VideoItem] = field(default_factory=list)
    total_videos: int = 0
    skipped_has_subtitle: int = 0
    skipped_too_small: int = 0
    skipped_excluded: int = 0

    @property
    def pending(self) -> int:
        return len(self.items)


class LibraryScanner:
    def __init__(self, client: WebDAVClient, cfg: Config):
        self.client = client
        self.cfg = cfg
        # 注意：不能叫 self.scan，否则会覆盖下面的 scan() 方法
        self.rules = cfg.scan

    # ------------------------------------------------------------------ 主入口

    def scan(self, verbose: bool = True) -> ScanResult:
        """扫描整个片库，返回待处理列表。"""
        result = ScanResult()
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in self.rules.video_exts}
        exclude_dirs = tuple(self.rules.exclude_dirs)
        name_blocklist = [k.lower() for k in self.rules.exclude_name_keywords]
        min_bytes = int(self.rules.min_size_mb * 1024 * 1024)

        log.info("开始扫描片库：%s（深度上限 %d）", self.cfg.webdav.root, self.rules.max_depth)
        for entry in self.client.iter_tree(
            self.cfg.webdav.root, max_depth=self.rules.max_depth, exclude_dirs=exclude_dirs
        ):
            if entry.suffix not in exts:
                continue
            if name_blocklist and any(k in entry.name.lower() for k in name_blocklist):
                result.skipped_excluded += 1
                continue
            if entry.size and entry.size < min_bytes:
                result.skipped_too_small += 1
                log.debug("跳过（过小 %s）：%s", human_size(entry.size), entry.path)
                continue

            result.total_videos += 1
            item = VideoItem(
                path=entry.path,
                name=entry.name,
                size=entry.size,
                last_modified=entry.last_modified,
            )
            item.sub_path = item.sibling_subtitle(self.rules.subtitle_suffix)

            if self._has_subtitle(entry):
                result.skipped_has_subtitle += 1
                if verbose:
                    log.debug("已有字幕，跳过：%s", item.sub_path)
                continue

            result.items.append(item)

        if verbose:
            log.info(
                "扫描完成：共 %d 个视频，其中 %d 部待处理（%d 部已有字幕，%d 个过小被忽略）",
                result.total_videos,
                result.pending,
                result.skipped_has_subtitle,
                result.skipped_too_small,
            )
        return result

    # ------------------------------------------------------------------ 内部

    def _has_subtitle(self, entry: DavResource) -> bool:
        """判断同目录下是否已有目标字幕。

        优先复用本次遍历已拿到的目录清单（免费），查不到再发一次 PROPFIND。
        """
        sub_path = entry.parent.rstrip("/") + "/" + PurePosixPath(entry.name).stem + self.rules.subtitle_suffix
        try:
            return self.client.exists(sub_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("检查字幕失败（按未处理算）：%s -> %s", sub_path, exc)
            return False

    def list_dir_map(self, directory: str) -> dict[str, DavResource]:
        """列一层目录并以文件名为 key 建索引，便于批量判断。"""
        return {e.name: e for e in self.client.listdir(directory)}

    def find_videos(self, entries: Iterable[DavResource]) -> list[VideoItem]:
        """从一批 DavResource 里筛出视频（供单目录模式复用）。"""
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in self.rules.video_exts}
        out: list[VideoItem] = []
        for entry in entries:
            if entry.is_dir or entry.suffix not in exts:
                continue
            item = VideoItem(
                path=entry.path, name=entry.name, size=entry.size,
                last_modified=entry.last_modified,
            )
            item.sub_path = item.sibling_subtitle(self.rules.subtitle_suffix)
            out.append(item)
        return out
