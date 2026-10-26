# -*- coding: utf-8 -*-
"""一键全流程：采集（全部启用站点） -> 推送 115 -> 字幕流水线。

用法（项目根目录）：
    python run.py chain                    # 完整链路
    python run.py chain --limit 5          # 字幕本次只处理 5 部
    python run.py chain --skip-push        # 跳过推送
    python run.py chain --skip-subtitle    # 跳过字幕
    python run.py chain --dry-run          # 采集试跑（不入库不导出）

每一步都是独立子进程，日志原样透传；采集/推送某一步失败会告警但
不中断后续步骤（字幕失败则整体退出码非 0）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from . import paths
from .push115 import load_push_config


def _banner(text: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} | INFO     | chain | {text}",
          flush=True)


def _run_step(argv: list[str], cwd: Path, label: str) -> int:
    """跑一个子步骤，stdout 透传给调用方。"""
    _banner(f"—— 开始：{label} ——" + (f"  (cwd={cwd})" if cwd else ""))
    env_extra = {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    import os
    env = {**os.environ, **env_extra}
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
    except OSError as exc:
        _banner(f"启动失败：{exc}")
        return 99
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\r\n"), flush=True)
    code = proc.wait()
    _banner(f"—— 结束：{label}（退出码 {code}）——")
    return code


def enabled_sites() -> list[str]:
    """sites/ 下所有 enabled: true 的站点名（按文件名）。"""
    out = []
    if paths.SITES_DIR.exists():
        for p in sorted(paths.SITES_DIR.glob("*.yaml")):
            if p.name.startswith("_"):
                continue
            try:
                cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if isinstance(cfg, dict) and cfg.get("enabled"):
                out.append(p.stem)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一键全流程")
    ap.add_argument("--limit", type=int, default=0, help="字幕本次最多处理 N 部")
    ap.add_argument("--harvest-limit", type=int, default=0, help="采集磁力上限（透传 harvest.py）")
    ap.add_argument("--skip-harvest", action="store_true", help="跳过采集")
    ap.add_argument("--skip-push", action="store_true", help="跳过推送 115")
    ap.add_argument("--skip-subtitle", action="store_true", help="跳过字幕流水线")
    ap.add_argument("--dry-run", action="store_true", help="采集试跑模式")
    args = ap.parse_args(argv)

    py = paths.python_exe()
    paths.ensure_dirs()
    worst = 0
    t0 = time.time()

    if not args.skip_harvest:
        sites = enabled_sites()
        if not sites:
            _banner("sites/ 下没有启用的站点，跳过采集")
        for site in sites:
            harv_args = [paths.HARVEST_SCRIPT, "--site", site]
            if args.dry_run:
                harv_args.append("--dry-run")
            if args.harvest_limit:
                harv_args += ["--limit", str(args.harvest_limit)]
            code = _run_step([py] + [str(a) for a in harv_args],
                             paths.HARVESTER_DIR, f"采集 {site}")
            if code != 0:
                _banner(f"[警告] 采集 {site} 未正常结束（{code}），继续后续步骤")

    if not args.skip_push:
        cfg = load_push_config()
        if not cfg.get("enabled", True):
            _banner("push115.yaml 中 enabled: false，跳过推送")
        elif not str(cfg.get("cookie") or "").strip():
            _banner("[提示] 未配置 115 cookie，跳过推送（去 设置页 或 config/push115.yaml 配置）")
        else:
            code = _run_step([py, "-m", "mflow.push115"], paths.ROOT, "推送 115")
            if code != 0:
                _banner(f"[警告] 推送步骤未正常结束（{code}），继续字幕")

    if not args.skip_subtitle:
        sub_args = [paths.SUBTITLE_RUN, "run"]
        if args.limit:
            sub_args += ["--limit", str(args.limit)]
        code = _run_step([py] + [str(a) for a in sub_args],
                         paths.SUBTITLE_DIR, "字幕流水线")
        if code != 0:
            _banner(f"[错误] 字幕流水线未正常结束（退出码 {code}）")
            worst = code

    _banner(f"全流程结束，用时 {time.time() - t0:.0f}s")
    return worst


if __name__ == "__main__":
    sys.exit(main())
