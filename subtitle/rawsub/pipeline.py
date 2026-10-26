# -*- coding: utf-8 -*-
"""
流水线编排（external 引擎版）。

    ┌─ 阶段一：转写+翻译 ───────────┐   整片下载 -> 外部引擎 infer.exe
    │  video -> infer.exe -> zh.srt │   黑盒输出即中文字幕，一步到位
    └───────────────────────────────┘
                  ↓
    ┌─ 阶段二：回写 ────────────────┐   PUT 到 115 原目录
    │  zh.srt -> WebDAV             │   秒级完成
    └───────────────────────────────┘

边产边传：一部转写完立刻回写一部；转写过程中按字幕产出增量回填。
预下载（download_ahead）：当前影片在转写（GPU）时，后台线程并行预下载
下一部整片（网络），GPU 无缝衔接不等下载。

每个阶段的进展都写进 SQLite，跑一半关机也不会白干。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .asr.external import ExternalASR
from .asr.srt import Cue, save_srt
from .config import Config
from .errors import PipelineAbort
from .media import AudioExtractor, resolve_tools
from .media.ffmpeg import FfmpegTools, probe_streams, run_ffmpeg
from .notify import send
from .state import (
    STAGE_DONE,
    STAGE_FAILED,
    STAGE_NEW,
    STAGE_TRANSLATED,
    FileRecord,
    StateDB,
    stage_rank,
)
from .utils import ensure_dir, free_bytes, human_duration, human_size, safe_filename, truncate
from .webdav import LibraryScanner, VideoItem, WebDAVClient

log = logging.getLogger("rawsub.pipeline")

PH_TRANSCRIBE = "transcribe"
PH_UPLOAD = "upload"
ALL_PHASES = (PH_TRANSCRIBE, PH_UPLOAD)


# ================================================================== 数据结构


@dataclass
class WorkItem:
    """一部片子在本轮运行中的全部上下文。"""

    video: VideoItem
    record: FileRecord
    en_srt: Path
    zh_srt: Path
    wav: Path
    elapsed: float = 0.0
    error: str = ""
    ok: bool = False
    # 预下载就绪的整片本地路径（转写时直接用，跳过下载等待）；None = 还没预下载
    local_video: Path | None = None
    # 增量回填节流状态（转写过程中边产出边上传）
    _partial_ts: float = 0.0
    _partial_count: int = 0

    @property
    def name(self) -> str:
        return self.video.name


@dataclass
class RunSummary:
    scanned: int = 0
    pending: int = 0
    transcribed: int = 0
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0
    elapsed: float = 0.0
    items: list[WorkItem] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"耗时 {human_duration(self.elapsed)}，"
            f"扫描 {self.scanned} 部 / 待处理 {self.pending} 部 / 跳过 {self.skipped} 部",
            f"转写+翻译 {self.transcribed}，回写 {self.uploaded}，失败 {self.failed}",
        ]
        if self.items:
            lines.append("")
            for item in self.items:
                mark = "OK  " if item.ok else "FAIL"
                tail = f"（{human_duration(item.elapsed)}）" if item.elapsed else ""
                lines.append(f"- [{mark}] {item.name}{tail}")
                if item.error:
                    lines.append(f"       原因：{truncate(item.error, 120)}")
        return "\n".join(lines)


# ================================================================== 流水线


class Pipeline:
    # 预下载结果表兜底默认值（正常在 __init__ 里按实例初始化；
    # 类级仅防 __init__ 被绕过的异常情况，进程内只有一个 Pipeline 实例）
    _prefetch_results: dict = {}

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = StateDB(cfg.workspace.state_db)
        self.client = WebDAVClient(cfg.webdav)
        self.tools: FfmpegTools = resolve_tools(cfg)
        self.extractor = AudioExtractor(cfg, self.client, self.tools)
        self.scanner = LibraryScanner(self.client, cfg)
        self.external_asr = ExternalASR(cfg)
        self._closed = False
        self.last_scan_total = 0  # 上一轮扫描到的视频总数，用于汇总输出
        # 预下载结果表：key -> 已下载好的本地路径（由后台线程写入，主流程收货）
        self._prefetch_results = {}

        ensure_dir(cfg.workspace.temp_dir)
        ensure_dir(cfg.workspace.subtitle_dir)

    # ------------------------------------------------------------------ 资源

    def close(self) -> None:
        if self._closed:
            return
        self.client.close()
        self.db.close()
        self._closed = True

    def __enter__(self) -> "Pipeline":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ 扫描

    def scan(self, limit: int = 0, force: bool = False) -> list[WorkItem]:
        """扫描片库，结合本地状态库得出本轮要处理的条目。"""
        result = self.scanner.scan()
        self.last_scan_total = result.total_videos
        work: list[WorkItem] = []
        skipped = 0

        for video in result.items:
            record = self.db.get(video.key)
            if record is None:
                record = FileRecord(
                    key=video.key, path=video.path, name=video.name,
                    size=video.size, mtime=video.last_modified or "",
                    stage=STAGE_NEW, sub_path=video.sub_path, created_at=time.time(),
                )
                self.db.upsert(record)
                record = self.db.get(video.key) or record

            if record.stage == STAGE_DONE and not force:
                skipped += 1
                continue
            if record.stage == STAGE_FAILED and record.attempts >= self.cfg.pipeline.max_attempts:
                if not self.cfg.pipeline.retry_failed:
                    skipped += 1
                    continue
                log.debug("%s 已达重试上限，本轮仍尝试一次", video.name)

            item = WorkItem(
                video=video,
                record=record,
                en_srt=self.cfg.workspace.subtitle_dir / f"{video.key}.en.srt",
                zh_srt=self.cfg.workspace.subtitle_dir / f"{video.key}.zh.srt",
                wav=self.cfg.workspace.temp_dir / video.key / "audio.wav",
            )
            work.append(item)
            if limit and len(work) >= limit:
                break

        log.info("本轮待处理 %d 部（跳过 %d 部已处理/放弃）", len(work), skipped)
        return work

    # ------------------------------------------------------------------ 主流程

    def run(
        self,
        limit: int = 0,
        dry_run: bool = False,
        phases: tuple[str, ...] = ALL_PHASES,
    ) -> RunSummary:
        started = time.time()
        summary = RunSummary()

        items = self.scan(limit=limit)
        summary.items = items
        summary.scanned = self.last_scan_total
        summary.pending = len(items)
        summary.skipped = max(0, self.last_scan_total - len(items))

        if not items:
            log.info("没有需要处理的影片，本次运行结束")
            summary.elapsed = time.time() - started
            return summary

        if dry_run:
            log.info("[dry-run] 以下是将要处理的影片：")
            for item in items:
                log.info("  - %s（%s）", item.video.path, human_size(item.video.size))
            summary.elapsed = time.time() - started
            return summary

        try:
            if PH_TRANSCRIBE in phases and PH_UPLOAD in phases \
                    and self.cfg.pipeline.upload_immediately:
                # 边产边传：一部转写完立刻回写一部，转写过程中还按字幕产出
                # 增量回填，不用等整批跑完
                self._run_fused(items, summary)
            else:
                if PH_TRANSCRIBE in phases:
                    self.phase_transcribe(items, summary)
                if PH_UPLOAD in phases:
                    self.phase_upload(items, summary)
        except PipelineAbort as exc:
            log.error("流水线中止：%s", exc)

        summary.elapsed = time.time() - started
        summary.failed = sum(1 for i in items if not i.ok and i.error)
        self._finish(summary)
        return summary

    # ------------------------------------------------------------------ 阶段一

    def phase_transcribe(self, items: list[WorkItem], summary: RunSummary) -> None:
        todo = [i for i in items if self._needs_transcribe(i)]
        if not todo:
            log.info("阶段一：没有需要转写的影片")
            return

        log.info("=" * 60)
        log.info("阶段一：转写+翻译 %d 部（外部引擎 %s）",
                 len(todo), Path(self.cfg.asr.external_exe).name)
        log.info("=" * 60)

        for n, item in enumerate(todo, start=1):
            if not self._guard(summary):
                break
            t0 = time.time()
            log.info("[%d/%d] %s", n, len(todo), item.name)
            try:
                self._transcribe_one(item)
                item.elapsed = time.time() - t0
                item.ok = True
                summary.transcribed += 1
            except Exception as exc:  # noqa: BLE001
                item.elapsed = time.time() - t0
                item.error = f"转写失败：{exc}"
                log.error("  转写失败：%s", exc)
                self._fail(item, str(exc))

    def _needs_transcribe(self, item: WorkItem) -> bool:
        # 外部引擎产出即中文字幕（等价于「已翻译」状态）
        if stage_rank(item.record.stage) < stage_rank(STAGE_TRANSLATED):
            return True
        return not item.zh_srt.is_file()

    def _run_fused(self, items: list[WorkItem], summary: RunSummary) -> None:
        """边产边传模式：逐部处理，转写完成立刻回写，不等整批。

        开启 download_ahead 时，当前影片在转写（GPU）期间，后台线程同步
        预下载下一部整片（网络），下载与转写流水线并行。
        """
        todo = [i for i in items
                if self._needs_transcribe(i) or self._needs_upload(i)]
        if not todo:
            log.info("没有需要处理的影片")
            return

        log.info("=" * 60)
        log.info("边产边传模式 %d 部（外部引擎 %s，完成即回写）",
                 len(todo), Path(self.cfg.asr.external_exe).name)
        log.info("=" * 60)

        prefetch_thread: threading.Thread | None = None
        prefetch_key: str | None = None

        for n, item in enumerate(todo, start=1):
            if not self._guard(summary):
                break
            t0 = time.time()
            log.info("[%d/%d] %s", n, len(todo), item.name)
            try:
                # 收货：上一部转写期间预下载的正是这一部，等它落盘即可
                if prefetch_thread is not None and prefetch_key == item.video.key:
                    prefetch_thread.join()
                    prefetch_thread = None
                    prefetch_key = None
                    got = self._prefetch_results.pop(item.video.key, None)
                    if got is not None:
                        item.local_video = got
                        log.info("  预下载已就绪，跳过下载等待")

                if self._needs_transcribe(item):
                    # 趁这部还没开跑，先把下一部需要转写的下载起来
                    if self.cfg.pipeline.download_ahead:
                        prefetch_thread, prefetch_key = self._start_prefetch(todo, n)
                    log.info("  下载并转写+翻译...")
                    self._transcribe_one(item, on_partial=self._make_partial_cb(item))
                    summary.transcribed += 1
                    log.info("  转写完成（%s），立即回写", human_duration(time.time() - t0))
                if self._needs_upload(item):
                    self._upload_one(item)
                    summary.uploaded += 1
                item.elapsed = time.time() - t0
                item.ok = True
            except Exception as exc:  # noqa: BLE001
                item.elapsed = time.time() - t0
                item.error = f"处理失败：{exc}"
                log.error("  失败：%s", exc)
                self._fail(item, str(exc))

        if prefetch_thread is not None:
            log.info("后台预下载仍在进行，退出前不再等待（半截文件支持断点续传）")

    # ------------------------------------------------------------------ 预下载

    def _start_prefetch(self, todo: list[WorkItem], current_idx: int
                        ) -> tuple[threading.Thread | None, str | None]:
        """为下一部需要转写的影片启动后台预下载线程。

        返回 (线程, 预取条目key)；没有需要预取的目标时返回 (None, None)。
        """
        for item in todo[current_idx:]:
            if not self._needs_transcribe(item):
                continue
            if item.local_video is not None:
                continue  # 已经预下载好了（正常不该发生，防御一下）
            work = self.extractor.work_dir(item.video)
            if (work / safe_filename(item.video.name)).is_file():
                continue  # 本地已有完整文件，无需预取
            t = threading.Thread(target=self._prefetch_worker, args=(item,),
                                 daemon=True, name=f"prefetch-{item.video.key[:16]}")
            t.start()
            log.info("  [预下载] 已启动：%s（%s）",
                     item.name, human_size(item.video.size))
            return t, item.video.key
        return None, None

    def _prefetch_worker(self, item: WorkItem) -> None:
        """后台线程入口：把下一部整片提前下载到本地。

        失败只记日志不打断主流程——轮到这部时主流程会正常重试下载。
        """
        try:
            work = self.extractor.work_dir(item.video)
            log.info("  [预下载] %s（%s）...", item.name, human_size(item.video.size))
            last_log = 0.0

            def on_dl(done: int, total: int) -> None:
                nonlocal last_log
                now = time.time()
                if now - last_log >= 20:
                    last_log = now
                    pct = f"{done / total * 100:.0f}%" if total else "?"
                    log.info("  [预下载] %s / %s（%s）",
                             human_size(done), human_size(total), pct)

            local = self._ensure_local_video(item, work, progress=on_dl)
            self._prefetch_results[item.video.key] = local
            log.info("  [预下载] %s 就绪", item.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("  [预下载] %s 失败（轮到时会重试）：%s", item.name, exc)

    def _ensure_local_video(self, item: WorkItem, work: Path,
                            progress=None) -> Path:
        """把整片弄到本地：优先用预下载好的文件，否则现场下载（支持断点续传）。"""
        local_video = item.local_video
        if local_video is not None and local_video.is_file():
            return local_video

        local_video = work / safe_filename(item.video.name)
        if local_video.is_file():
            return local_video

        # 下载写到 .part 暂存文件、完成后才改名——进程中途被杀留下的半截文件
        # 不会误当完整片喂给引擎，下次还能靠 Range 断点续传接着下
        part = local_video.with_suffix(local_video.suffix + ".part")
        need = int(item.video.size * 1.15) + 512 * 1024 * 1024
        have = free_bytes(work)
        if item.video.size and have < need:
            raise RuntimeError(
                f"磁盘空间不足：需要约 {human_size(need)}，可用 {human_size(have)}"
            )
        log.info("  整片下载（%s）...", human_size(item.video.size))
        if progress is None:
            last_log = 0.0

            def progress(done: int, total: int) -> None:
                nonlocal last_log
                now = time.time()
                if now - last_log >= 20:
                    last_log = now
                    pct = f"{done / total * 100:.0f}%" if total else "?"
                    log.info("  下载 %s / %s（%s）",
                             human_size(done), human_size(total), pct)

        self.client.download(item.video.path, part, progress=progress)
        part.rename(local_video)
        return local_video

    def _make_partial_cb(self, item: WorkItem):
        """生成该条目的增量回填回调（带节流），供外部引擎转写过程中调用。"""
        interval = self.cfg.pipeline.partial_upload_interval_s
        if interval <= 0:
            return None

        def cb(cues) -> None:
            now = time.time()
            if now - item._partial_ts < interval:
                return
            if len(cues) <= item._partial_count:
                return
            item._partial_ts = now
            item._partial_count = len(cues)
            line_ending = "\r\n" if self.cfg.output.line_ending == "crlf" else "\n"
            save_srt(cues, item.zh_srt, encoding=self.cfg.output.encoding,
                     line_ending=line_ending)
            data = item.zh_srt.read_bytes()
            self.client.put(item.video.sub_path, data,
                            content_type="application/x-subrip")
            log.info("  增量回填 %d 条 -> %s（%s）",
                     len(cues), item.video.sub_path, human_size(len(data)))

        return cb

    def _transcribe_one(self, item: WorkItem, on_partial=None) -> None:
        """外部引擎：输入视频 -> 直接产出中文字幕。"""
        ext = self.external_asr
        use_video = self.cfg.asr.external_input == "video"
        work = self.extractor.work_dir(item.video)
        output_dir = self.cfg.workspace.subtitle_dir

        if use_video:
            # 整片下载到本地（可能已被预下载线程提前下好），再交给外部工具
            # （与手动拖视频给旧版 bat 完全一致）
            local_video = self._ensure_local_video(item, work)
            local_video = ext.normalize_video_name(local_video)
            input_path, is_video = self._external_input_for(local_video, work)
        else:
            # 只喂抽取的 16kHz wav（省流量；外部工具对裸音频的行为未经验证）
            ensure_dir(item.wav.parent)
            self.extractor.extract(item.video, item.wav)
            input_path, is_video = item.wav, False

        cues = ext.run(input_path, output_dir=output_dir, is_video=is_video,
                       on_partial=on_partial)
        if not cues:
            raise RuntimeError("外部引擎返回空字幕")

        # 黑盒输出已经是中文，直接按最终输出格式落盘为 zh.srt
        line_ending = "\r\n" if self.cfg.output.line_ending == "crlf" else "\n"
        save_srt(cues, item.zh_srt, encoding=self.cfg.output.encoding, line_ending=line_ending)
        self.db.mark_stage(
            item.video.key, STAGE_TRANSLATED,
            zh_srt=str(item.zh_srt),
            duration_s=max((c.end for c in cues), default=0.0),
        )
        log.info("  已生成 %s（%d 条中文）", item.zh_srt.name, len(cues))

        # 清理临时输入（黑盒日志 external-asr.log 保留：排查进度/失败原因有用，
        # 每次运行会被覆盖）
        if not self.cfg.workspace.keep_audio:
            item.wav.unlink(missing_ok=True)
            if use_video:
                input_path.unlink(missing_ok=True)
                if input_path != local_video:
                    # 全景声兜底抽出来的 wav 已喂完引擎，整片也别留着占盘
                    local_video.unlink(missing_ok=True)
        # infer.exe 的原始输出（work/subs/<stem>.srt）与 zh_srt 同内容，删掉避免混乱
        raw_out = output_dir / f"{input_path.stem}.srt"
        if raw_out != item.zh_srt:
            raw_out.unlink(missing_ok=True)

    def _external_input_for(self, local_video: Path, work: Path) -> tuple[Path, bool]:
        """决定喂给外部引擎的输入，返回 (输入路径, 是否视频)。

        faster-whisper 解码时只取容器里的第一条音轨。VR/全景声视频（如 FB360）
        的首条音轨往往是 6~10 声道的空间音频，faster-whisper 重采样阶段会直接崩
        （RuntimeError: Could not allocate AVAudioFifo）。
        检测到首条音轨超过 2 声道时，改为抽取一条 16kHz 单声道 wav
        （优先挑标准立体声混音轨）喂给引擎；普通视频维持整片直喂。
        """
        try:
            streams = probe_streams(self.tools, str(local_video), timeout=60)
        except Exception:  # noqa: BLE001 — 探测失败不拦路，按普通视频处理
            streams = []
        audio = [s for s in streams if s.is_audio]
        if not audio or audio[0].channels <= 2:
            return local_video, True

        first = audio[0]
        pick = next((s for s in audio if 0 < s.channels <= 2), first)
        log.info(
            "  首条音轨 %d 声道（全景声/空间音频），改抽音轨 #%d（%d 声道）为 wav",
            first.channels, pick.index, pick.channels,
        )
        wav = work / f"{local_video.stem}.wav"
        cmd = [
            str(self.tools.ffmpeg), "-nostdin", "-y", "-loglevel", "error",
            "-i", str(local_video),
            "-map", f"0:{pick.index}", "-vn",
            "-ac", str(max(1, self.cfg.audio.channels)),
            "-ar", str(self.cfg.audio.sample_rate),
            "-c:a", "pcm_s16le", "-f", "wav", str(wav),
        ]
        run_ffmpeg(cmd, timeout=7200.0, log_tag="（全景声兜底抽轨）")
        if not wav.is_file() or wav.stat().st_size < 1024:
            raise RuntimeError(f"全景声兜底抽轨结果异常：{wav}")
        return wav, False

    # ------------------------------------------------------------------ 阶段二

    def phase_upload(self, items: list[WorkItem], summary: RunSummary) -> None:
        todo = [i for i in items if self._needs_upload(i)]
        if not todo:
            log.info("阶段三：没有需要回写的字幕")
            return

        log.info("=" * 60)
        log.info("阶段三：回写 %d 份字幕到 115", len(todo))
        log.info("=" * 60)

        for n, item in enumerate(todo, start=1):
            if not self._guard(summary):
                break
            log.info("[%d/%d] %s", n, len(todo), item.name)
            try:
                self._upload_one(item)
                summary.uploaded += 1
                item.ok = True
            except Exception as exc:  # noqa: BLE001
                item.error = f"回写失败：{exc}"
                log.error("  回写失败：%s", exc)
                self._fail(item, str(exc))

    def _upload_one(self, item: WorkItem) -> None:
        """把单部影片的中文字幕回写到 115，成功后标记完成并清理本地文件。"""
        data = item.zh_srt.read_bytes()
        self.client.put(item.video.sub_path, data, content_type="application/x-subrip")
        if self.cfg.output.write_en_srt and item.en_srt.is_file():
            en_remote = item.video.sibling_subtitle(".en.srt")
            self.client.put(en_remote, item.en_srt.read_bytes(),
                            content_type="application/x-subrip")
        self.db.mark_stage(item.video.key, STAGE_DONE)
        log.info("  已上传 %s（%s）", item.video.sub_path, human_size(len(data)))
        self._cleanup(item)

    def _needs_upload(self, item: WorkItem) -> bool:
        if not item.zh_srt.is_file():
            return False
        return stage_rank(item.record.stage) < stage_rank(STAGE_DONE)

    def _cleanup(self, item: WorkItem) -> None:
        if not self.cfg.workspace.keep_en_srt:
            item.en_srt.unlink(missing_ok=True)
        item.zh_srt.unlink(missing_ok=True)
        try:
            item.wav.parent.rmdir()  # 空目录才删得掉
        except OSError:
            pass

    # ------------------------------------------------------------------ 辅助

    def _fail(self, item: WorkItem, error: str) -> None:
        item.record.attempts = self.db.bump_attempt(item.video.key, error, STAGE_FAILED)

    def _guard(self, summary: RunSummary) -> bool:
        """连续失败过多就收手，避免在 115 风控或模型异常时空转一整晚。"""
        limit = self.cfg.pipeline.max_consecutive_failures
        if limit <= 0:
            return True
        streak = 0
        for item in summary.items:
            if item.error:
                streak += 1
                if streak >= limit:
                    raise PipelineAbort(f"连续 {streak} 部失败，停止本轮运行（可查日志定位原因）")
            else:
                streak = 0
        return True

    def _finish(self, summary: RunSummary) -> None:
        log.info("=" * 60)
        log.info("本轮结束\n%s", summary.render())
        log.info("=" * 60)

        if self.cfg.notify.enabled:
            send(
                self.cfg.notify,
                "115Translate 跑完了",
                summary.render(),
            )

        if self.cfg.pipeline.shutdown_after_run:
            self.shutdown(self.cfg.pipeline.shutdown_delay_s)

    @staticmethod
    def shutdown(delay: float = 60.0) -> None:
        import subprocess
        import sys

        if sys.platform != "win32":
            log.warning("非 Windows 系统，跳过自动关机")
            return
        log.warning("将在 %.0f 秒后关机（可在命令行执行 shutdown /a 取消）", delay)
        subprocess.run(["shutdown", "/s", "/t", str(int(delay))], capture_output=True)
