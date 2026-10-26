# -*- coding: utf-8 -*-
"""命令行入口。所有子命令都在这里，业务逻辑在各模块里。"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG_NAME, EXAMPLE_CONFIG_NAME, Config, load_config
from .errors import ConfigError, RawSubError
from .logging_setup import setup_logging

log = logging.getLogger("rawsub.cli")


# ================================================================== 帮助函数


def _bootstrap(args: argparse.Namespace) -> Config:
    """初始化日志 + 加载配置。"""
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    level = "DEBUG" if args.verbose else cfg.logging.level
    setup_logging(level=level, log_file=cfg.logging.file, console=cfg.logging.console)
    for warning in _config_warnings(cfg):
        log.warning("%s", warning)
    return cfg


def _config_warnings(cfg: Config) -> list[str]:
    from .config import validate

    return validate(cfg)


def _print_header(text: str) -> None:
    print()
    print("=" * 62)
    print(f" {text}")
    print("=" * 62)


# ================================================================== 子命令：doctor


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    _print_header("115Translate 环境自检")

    ok_all = True

    def check(title: str, ok: bool, detail: str = "") -> bool:
        nonlocal ok_all
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] {title}" + (f"  ->  {detail}" if detail else ""))
        ok_all = ok_all and ok
        return ok

    # 1. Python
    py = sys.version.split()[0]
    check("Python 版本", sys.version_info >= (3, 9), py)

    # 2. ffmpeg / ffprobe
    from .media.ffmpeg import resolve_tools
    from .errors import ToolMissing

    try:
        tools = resolve_tools(cfg)
        check("ffmpeg / ffprobe", True, tools.version())
    except ToolMissing as exc:
        check("ffmpeg / ffprobe", False, str(exc))

    # 3. 显卡
    from .cuda import describe_gpu

    gpu = describe_gpu()
    check("NVIDIA 显卡", "未检测到" not in gpu, gpu)

    # 4. 外部引擎（infer.exe）
    ext_exe = Path(cfg.asr.external_exe) if cfg.asr.external_exe else None
    check("外部引擎 infer.exe", bool(ext_exe and ext_exe.is_file()),
          str(ext_exe) if ext_exe else "未配置 asr.external_exe")

    # 5. WebDAV
    from .webdav import WebDAVClient

    try:
        client = WebDAVClient(cfg.webdav)
        alive = client.healthcheck()
        check("WebDAV 连通性", alive, cfg.webdav.base_url + cfg.webdav.root)
        if alive:
            try:
                entries = client.listdir(cfg.webdav.root)
                check("片库可读", True, f"{cfg.webdav.root} 下有 {len(entries)} 个条目")
            except Exception as exc:  # noqa: BLE001
                check("片库可读", False, str(exc))
    except Exception as exc:  # noqa: BLE001
        check("WebDAV 连通性", False, str(exc))

    # 6. 磁盘
    from .utils import free_bytes, human_size

    free = free_bytes(cfg.workspace.temp_dir)
    check("临时目录可用空间", free > 10 * 1024 ** 3,
          f"{cfg.workspace.temp_dir} 剩余 {human_size(free)}")

    print()
    if ok_all:
        print("  全部检查通过。可以跑 `python run.py scan` 看看片库里有哪些待处理的片子。")
    else:
        print("  有检查项未通过，请先按上面的提示处理（多数问题跑 scripts\\install.ps1 能解决）。")
    print()
    return 0 if ok_all else 1


# ================================================================== 子命令：scan


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .pipeline import Pipeline

    with Pipeline(cfg) as pipe:
        items = pipe.scan(limit=args.limit, force=args.all)
        if not items:
            print("\n没有待处理的影片（可能都已经有中文字幕了）。")
            return 0
        print(f"\n待处理 {len(items)} 部：\n")
        for i, item in enumerate(items, 1):
            size = item.video.size / 1024 ** 3
            stage = item.record.stage
            err = f"  上次失败：{item.record.last_error[:60]}" if item.record.last_error else ""
            print(f" {i:3d}. [{stage:12s}] {item.video.path}  ({size:.2f} GB){err}")
        print()
    return 0


# ================================================================== 子命令：run


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .pipeline import Pipeline

    phases = tuple(args.phases.split(",")) if args.phases else None
    with Pipeline(cfg) as pipe:
        summary = pipe.run(limit=args.limit or cfg.pipeline.max_items_per_run,
                           dry_run=args.dry_run,
                           phases=phases or ("transcribe", "upload"))
    return 0 if summary.failed == 0 else 1


# ================================================================== 子命令：status / reset


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .state import StateDB

    with StateDB(cfg.workspace.state_db) as db:
        counts = db.counts()
        total = sum(counts.values())
        print(f"\n状态库：{cfg.workspace.state_db}")
        print(f"共记录 {total} 部影片\n")
        for stage in ("new", "transcribed", "translated", "done", "failed"):
            print(f"  {stage:14s} {counts.get(stage, 0):5d}")
        if args.failed:
            failed = db.by_stage("failed")
            if failed:
                print("\n失败明细：")
                for rec in failed[:50]:
                    print(f"  - {rec.path}")
                    print(f"      尝试 {rec.attempts} 次，最后错误：{rec.last_error[:100]}")
        print()
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .state import StateDB

    with StateDB(cfg.workspace.state_db) as db:
        if args.all:
            if not args.yes:
                ans = input("这会清空全部处理记录（不会删除已上传的字幕）。确认？[y/N] ")
                if ans.strip().lower() != "y":
                    print("已取消。")
                    return 0
            n = db.reset()
        elif args.failed:
            n = db.reset(only_failed=True)
        elif args.path:
            from .utils import stable_key

            n = db.reset(stable_key(args.path))
        else:
            print("需要指定 --all / --failed / --path")
            return 2
    print(f"已清除 {n} 条记录。")
    return 0


# ================================================================== 子命令：init


def cmd_init(args: argparse.Namespace) -> int:
    """由 config.example.yaml 生成 config.yaml。"""
    root = Path(__file__).resolve().parent.parent
    src = root / EXAMPLE_CONFIG_NAME
    dst = root / DEFAULT_CONFIG_NAME
    if dst.exists() and not args.force:
        print(f"{dst} 已存在，不覆盖（要重新生成请加 --force）")
        return 0
    if not src.exists():
        print(f"找不到模板文件：{src}", file=sys.stderr)
        return 1
    shutil.copy(src, dst)
    print(f"已生成 {dst}")
    print("接下来请编辑它，至少填写 webdav.base_url / username / password / root。")
    return 0


# ================================================================== 参数解析


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="115 网盘视频自动中文字幕流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用组合：\n"
            "  python run.py init                 生成 config.yaml\n"
            "  python run.py doctor               环境自检\n"
            "  python run.py scan --limit 10      看看前 10 部待处理的片子\n"
            "  python run.py run --limit 2        先小批量试跑两部\n"
            "  python run.py run                  正式开跑（睡前挂机）\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"115Translate {__version__}")
    parser.add_argument("-c", "--config", default=None, help=f"配置文件路径（默认 ./{DEFAULT_CONFIG_NAME}）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出 DEBUG 日志")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="从模板生成 config.yaml")
    p.add_argument("--force", action="store_true", help="已存在时覆盖")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="环境自检")
    p.add_argument("--deep", action="store_true", help="实际加载 Whisper 和 llama.cpp 验证")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("scan", help="扫描片库，列出待处理影片")
    p.add_argument("--limit", type=int, default=0, help="最多列出多少部（0=不限）")
    p.add_argument("--all", action="store_true", help="连已处理过的也列出来")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("run", help="跑流水线")
    p.add_argument("--limit", type=int, default=0, help="本轮最多处理多少部（0=按配置）")
    p.add_argument("--dry-run", action="store_true", help="只列计划，不真的跑")
    p.add_argument("--phases", default="", help="只跑指定阶段，逗号分隔，如 transcribe,upload")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="查看处理状态统计")
    p.add_argument("--failed", action="store_true", help="列出失败明细")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("reset", help="清除处理记录（不影响已上传的字幕）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="清空全部")
    g.add_argument("--failed", action="store_true", help="只清失败项，便于下一轮重试")
    g.add_argument("--path", help="只清指定影片（填 WebDAV 路径）")
    p.add_argument("--yes", action="store_true", help="不再二次确认")
    p.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except RawSubError as exc:
        log.error("%s", exc)
        return 1
