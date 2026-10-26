# -*- coding: utf-8 -*-
"""把采集器库里的磁力逐条推送到 115「云添加」（离线下载）。

用法（项目根目录）：
    python -m mflow.push115                # 推送全部未推送记录
    python -m mflow.push115 --test         # 只测 cookie 是否有效
    python -m mflow.push115 --limit 10     # 本次最多推 10 条
    python -m mflow.push115 --site example  # 只推指定站点
    python -m mflow.push115 --dry-run      # 只列出将要推的，不真推

工作方式：
    harvester/state/harvest.db 中 pushed=0 的行  ->  p115client 云添加  ->
    成功后置 pushed=1 并记 push_time，失败保持 0 下次重试。

cookie 读取优先级：环境变量 P115_COOKIE  >  config/push115.yaml 的 cookie
（yaml 里写 "${P115_COOKIE}" 也会展开成环境变量值）。

获取 cookie：浏览器登录 https://115.com 后 F12 控制台 document.cookie 复制，
至少要包含 UID 和 SEID 两项。
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from . import paths

ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULTS = {
    "enabled": True,
    "cookie": "",
    "save_path": "",
    "max_per_run": 20,
    "delay_s": 3.0,
    "site": "",
}


def _expand(value: str) -> str:
    return ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value or "")


def load_push_config(path: Path | None = None) -> dict:
    path = path or paths.PUSH_CONFIG
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 读取 {path} 失败：{exc}，使用默认配置")
    cfg["cookie"] = _expand(str(cfg.get("cookie") or ""))
    return cfg


# --------------------------------------------------------------- 115 客户端

def make_client(cookie: str):
    """构造 p115client；缺依赖或未配 cookie 时抛 RuntimeError（带人话提示）。"""
    if not cookie:
        raise RuntimeError(
            "未配置 115 cookie：编辑 config/push115.yaml 填入 cookie，"
            "或设置环境变量 P115_COOKIE")
    try:
        from p115client import P115Client
    except ImportError as exc:
        raise RuntimeError(
            "未安装 p115client：pip install p115client -i "
            "https://pypi.tuna.tsinghua.edu.cn/simple") from exc
    return P115Client(cookie)


def _resp_ok(resp) -> tuple[bool, str]:
    """尽力判断 115 接口返回是否成功（不同接口字段略有差异）。"""
    if not isinstance(resp, dict):
        return True, str(resp)[:120]
    err_no = resp.get("err_no")
    if err_no not in (0, "0", None, ""):
        return False, f"err_no={err_no} {resp.get('error_msg') or ''}".strip()
    state = resp.get("state")
    if state in (False, "false", 0, "0"):
        return False, str(resp)[:160]
    return True, ""


def test_cookie(cookie: str) -> tuple[bool, str]:
    try:
        client = make_client(cookie)
    except RuntimeError as exc:
        return False, str(exc)
    try:
        resp = client.clouddownload_task_cnt()
    except Exception as exc:  # noqa: BLE001
        return False, f"cookie 可能无效或接口变化：{exc}"
    ok, detail = _resp_ok(resp)
    if ok:
        return True, f"cookie 有效，当前离线任务数：{resp.get('count', resp)}"
    return False, detail


# --------------------------------------------------------------- 数据库

def _ensure_push_time_column(con: sqlite3.Connection) -> None:
    try:
        con.execute("ALTER TABLE magnets ADD COLUMN push_time TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在


def fetch_pending(db_path: Path, site: str = "", limit: int = 0) -> list[dict]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = ("SELECT infohash, site, magnet, title, filename, size_gb, source_url, "
           "first_seen FROM magnets WHERE pushed = 0")
    params: list = []
    if site:
        sql += " AND site = ?"
        params.append(site)
    sql += " ORDER BY size_gb DESC, first_seen DESC"
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in con.execute(sql, params)]
    con.close()
    return rows


def _mark_pushed(infohash: str) -> None:
    con = sqlite3.connect(paths.HARVEST_DB)
    _ensure_push_time_column(con)
    con.execute("UPDATE magnets SET pushed = 1, push_time = ? WHERE infohash = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), infohash))
    con.commit()
    con.close()


# --------------------------------------------------------------- 推送主流程

def _resolve_wp_path_id(client, save_path: str) -> int:
    """把 115 网盘路径解析成目录 ID。

    云添加接口（add_task_url）只认 wp_path_id，传 path 会被直接忽略、
    任务落到 115 默认下载目录——这就是「改了保存目录却没生效」的原因。
    """
    p = str(save_path or "").strip().replace("\\", "/")
    if not p:
        raise RuntimeError("保存目录为空")
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    try:
        resp = client.fs_dir_getid(p)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"查询目录失败：{exc}") from exc
    if not isinstance(resp, dict):
        raise RuntimeError(f"查询目录返回异常：{str(resp)[:160]}")
    if not resp.get("state", True):
        raise RuntimeError(f"查询目录失败：{resp.get('error') or str(resp)[:120]}")
    try:
        pid = int(resp.get("id") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        raise RuntimeError(f"115 网盘里找不到目录 {p}（路径要和网盘里完全一致，注意大小写和空格）")
    return pid


def push_all(cfg: dict, limit: int | None = None, site: str | None = None,
             dry_run: bool = False, log=print) -> int:
    """逐条推送。返回退出码。"""
    site = site if site is not None else str(cfg.get("site") or "")
    n_limit = limit if limit is not None else int(cfg.get("max_per_run") or 0)
    delay = float(cfg.get("delay_s") or 3.0)
    save_path = str(cfg.get("save_path") or "").strip()

    rows = fetch_pending(paths.HARVEST_DB, site, n_limit)
    if not rows:
        log("库里没有待推送的磁力（pushed=0 的记录为空）。先跑采集，或放宽筛选条件。")
        return 0

    log(f"待推送 {len(rows)} 条"
        + (f"（站点 {site}）" if site else "")
        + (f"，目标目录 {save_path}" if save_path else "，目标目录 115 默认下载目录"))

    if dry_run:
        for i, r in enumerate(rows, 1):
            log(f"  [{i}/{len(rows)}] {r['title'] or r['filename'] or r['infohash'][:12]}"
                f"  {r['size_gb'] or '?'} GB")
        log("dry-run 结束，没有实际推送。")
        return 0

    client = make_client(str(cfg.get("cookie") or ""))
    # 先把保存目录解析成目录 ID：接口不认路径字符串，不解析就会落默认目录
    wp_path_id = None
    if save_path:
        try:
            wp_path_id = _resolve_wp_path_id(client, save_path)
            log(f"目标目录已确认：{save_path}（目录 ID {wp_path_id}）")
        except RuntimeError as exc:
            log(f"{exc}")
            log("已中止推送：宁可不动，也不要再落到 115 默认目录。改好「保存目录」后再试。")
            return 1
    else:
        log("保存目录留空：任务会进 115 默认下载目录。")
    ok_cnt = fail_cnt = 0
    for i, r in enumerate(rows, 1):
        magnet = r["magnet"]
        name = r["title"] or r["filename"] or r["infohash"][:12]
        label = f"[{i}/{len(rows)}] {name}"
        try:
            payload = {"url": magnet, "aid": 1}
            if wp_path_id:
                payload["wp_path_id"] = wp_path_id
            resp = client.clouddownload_task_add_url(payload)
            ok, detail = _resp_ok(resp)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        if ok:
            ok_cnt += 1
            _mark_pushed(r["infohash"])
            log(f"{label}  ->  已加入云添加")
        else:
            fail_cnt += 1
            log(f"{label}  ->  失败：{detail}（保留待下次重试）")
        if i < len(rows):
            time.sleep(delay)

    log(f"推送完成：成功 {ok_cnt}，失败 {fail_cnt}")
    return 0 if fail_cnt == 0 else 1


# --------------------------------------------------------------- CLI 入口

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="推送未推送磁力到 115 云添加")
    ap.add_argument("--test", action="store_true", help="只测试 cookie 是否有效")
    ap.add_argument("--limit", type=int, default=None, help="本次最多推 N 条")
    ap.add_argument("--site", default=None, help="只推指定站点")
    ap.add_argument("--dry-run", action="store_true", help="只列出不实际推送")
    ap.add_argument("--config", default=None, help="配置文件路径")
    args = ap.parse_args(argv)

    paths.ensure_dirs()
    cfg = load_push_config(Path(args.config) if args.config else None)

    if args.test:
        ok, msg = test_cookie(str(cfg.get("cookie") or ""))
        print(("[OK] " if ok else "[FAIL] ") + msg)
        return 0 if ok else 1

    if not cfg.get("enabled", True):
        print("push115.yaml 里 enabled: false，已跳过推送。")
        return 0

    try:
        return push_all(cfg, limit=args.limit, site=args.site, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"[未推送] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
