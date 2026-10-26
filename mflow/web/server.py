# -*- coding: utf-8 -*-
"""MagnetFlow Web 控制台后端（FastAPI）。

启动：  python -m mflow.web.server [--port 8420] [--host 127.0.0.1] [--no-browser]
界面是零依赖的静态单页（mflow/web/static），所有接口都是同源 JSON。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime
from io import StringIO
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from .. import paths
from .. import tasks as tasks_mod
from .. import yamlkit
from ..push115 import _resolve_wp_path_id, load_push_config, test_cookie as _p115_test_cookie

VERSION = "0.2.0"
DEFAULT_PORT = 8420
STATIC_DIR = Path(__file__).resolve().parent / "static"
BACKUP_DIR = paths.LOGS_DIR / "config_backups"

MANAGER = tasks_mod.MANAGER
PY = None  # 延迟取（避免 import 期做 venv 探测副作用）


def _py() -> str:
    global PY
    if PY is None:
        PY = paths.python_exe()
    return PY


# ============================================================ 配置读写
from ruamel.yaml import YAML  # noqa: E402


def _new_ryaml() -> "YAML":
    """必须每个文档新建实例：ruamel 单实例跨 load/dump 会泄漏注释/对齐状态
    （FastAPI 线程池并发下尤其危险，曾导致 config.yaml 被写坏）。"""
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    return y


def _read_plain(path: Path) -> dict:
    """safe_load，读不到返回 {}。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _load_rt(path: Path):
    """ruamel 保留注释加载；文件不存在/解析失败时退化为新建普通 dict。"""
    try:
        doc = _new_ryaml().load(path.read_text(encoding="utf-8"))
        if doc is None:
            doc = {}
        return doc
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _dump_rt(doc) -> str:
    buf = StringIO()
    _new_ryaml().dump(doc, buf)
    return buf.getvalue()


def _dump_safe(doc) -> str:
    """序列化并用 PyYAML 校验；校验不过直接 500，绝不把坏文本落盘。"""
    text = _dump_rt(doc)
    try:
        yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        try:
            paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            (paths.LOGS_DIR / "broken_dump_preview.yaml").write_text(
                text, encoding="utf-8")
        except OSError:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"序列化校验失败（未保存，预览见 logs/broken_dump_preview.yaml）：{exc}",
        ) from exc
    return text


def _backup_save(path: Path, new_text: str) -> None:
    """写前备份到 logs/config_backups/，再原子落盘。"""
    if path.exists():
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S.%f")
            shutil.copy2(path, BACKUP_DIR / f"{path.name}.{stamp}.bak")
            # 只保留最近 30 份备份
            old = sorted(BACKUP_DIR.glob(f"{path.name}.*.bak"))
            for stale in old[:-30]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)


def _save_doc(path: Path, doc, old_plain) -> None:
    """外科手术落盘：按叶子差异做行级替换（mflow/yamlkit），注释与版式原样保留；
    定位不了的路径回退 ruamel 整文档序列化（同样带 PyYAML 校验，坏文本不落盘）。"""
    ups = yamlkit.diff_updates(old_plain, yamlkit.to_plain(doc))
    if not ups:
        return
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        text = yamlkit.apply_text_updates(original, ups)
        bad = yamlkit.verify_values(text, ups)
        if bad:
            raise yamlkit.SurgicalError(f"值校验失败：{bad}")
    except yamlkit.SurgicalError as exc:
        try:
            paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(paths.LOGS_DIR / "yamlkit_fallback.log", "a",
                      encoding="utf-8") as fh:
                fh.write(f"{datetime.now().isoformat()} {path.name} "
                         f"{exc} {sorted(ups)}\n")
        except OSError:
            pass
        _backup_save(path, _dump_safe(doc))
        return
    _backup_save(path, text)


def _getp(data, dotted: str, default=None):
    node = data
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return default
    return node


def _setp(doc, dotted: str, value) -> None:
    """在 ruamel/doc 字典上按 a.b.c 路径赋值，缺的中间层自动补 dict。"""
    parts = dotted.split(".")
    node = doc
    for p in parts[:-1]:
        if isinstance(node, list):
            idx = int(p)
            while len(node) <= idx:
                node.append(None)
            if not isinstance(node[idx], (dict, list)):
                node[idx] = {}
            node = node[idx]
        else:
            if p not in node or not isinstance(node[p], (dict, list)):
                node[p] = {}
            node = node[p]
    last = parts[-1]
    if isinstance(node, list):
        idx = int(last)
        while len(node) <= idx:
            node.append(None)
        node[idx] = value
    else:
        node[last] = value


def _to_list(value, sep=True):
    """textarea/逗号输入 -> 字符串列表。"""
    if isinstance(value, list):
        return [str(v) for v in value]
    s = str(value or "")
    parts = s.replace("，", ",").split("\n") if not sep else s.replace("，", ",").replace(",", "\n").split("\n")
    return [p.strip() for p in parts if p.strip()]


def _coerce(key: str, spec: dict, value):
    t = spec.get("type", "str")
    try:
        if t == "bool":
            return bool(value)
        if t == "int":
            return max(int(float(value or 0)), spec.get("min", 0))
        if t == "float":
            return max(float(value or 0), spec.get("min", 0.0))
        if t == "enum":
            v = str(value or "").strip()
            if v not in spec.get("options", []):
                raise ValueError(f"{key} 的值「{v}」不在允许范围内")
            return v
        if t == "strlist":
            return _to_list(value)
        if t in ("password", "textarea", "str"):
            return str(value or "").strip() if t != "textarea" else str(value or "").rstrip()
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"字段 {key} 输入无效：{exc}") from exc
    return str(value or "")


def _form_get(source: dict, schema: list[dict]) -> dict:
    out = {}
    for spec in schema:
        path = spec.get("path", spec["key"])
        default = spec.get("default", "")
        val = _getp(source, path, default)
        if spec.get("type") == "strlist" and not isinstance(val, list):
            val = []
        out[spec["key"]] = val
    return out


def _form_put(doc, source_schema: list[dict], updates: dict,
              post: dict | None = None) -> None:
    """按 schema 把 updates 写进 ruamel doc；post 处理特殊路径。"""
    for spec in source_schema:
        key = spec["key"]
        if key not in updates:
            continue
        path = spec.get("path", key)
        if post and key in post.get("skip", []):
            continue
        value = _coerce(key, spec, updates[key])
        _setp(doc, path, value)
    if post:
        for path, value in post.get("sets", {}).items():
            _setp(doc, path, value)


# ============================================================ 表单 schema（单一事实来源）
def _S(key, label, type_="str", group="常规", **kw) -> dict:
    d = {"key": key, "label": label, "type": type_, "group": group}
    d.update(kw)
    return d


PUSH_SCHEMA = [
    _S("enabled", "启用推送", "bool", "推送 115", default=True,
       help="关闭后「一键全流程」会跳过推送环节"),
    _S("cookie", "115 Cookie", "password", "推送 115",
       help="最省事：点「扫码登录」，手机 115 App 扫一下自动填入；也可粘贴浏览器 document.cookie 整串，"
            "或 115 App 抓包的 UID=...; CID=...; SEID=...; KID=...；写 ${P115_COOKIE} 则走环境变量"),
    _S("save_path", "保存目录", "str", "推送 115", default="",
       help="115 网盘内的落盘目录，如 /云下载/学习资料/常规（要与网盘里完全一致；保存后点「测试保存目录」验证，"
            "解析不到目录会中止推送，不会偷偷落到默认目录）；留空 = 115 默认目录"),
    _S("max_per_run", "单次最多推送", "int", "推送 115", default=20, min=1,
       help="防止一次把整个库灌爆"),
    _S("delay_s", "每条间隔秒", "float", "推送 115", default=3.0, min=0,
       help="对 115 接口客气一点，0 = 不等待"),
    _S("site", "限定站点", "str", "推送 115", default="",
       help="留空 = 推送所有站点的未推条目"),
]

HARVEST_SCHEMA = [
    _S("request.timeout", "请求超时（秒）", "int", "网络", default=15, min=1),
    _S("delay_min", "请求间隔下限（秒）", "float", "网络", default=1.0, min=0,
       path="_delay_min", help="随机等待区间，别把自己 IP 送进黑名单"),
    _S("delay_max", "请求间隔上限（秒）", "float", "网络", default=2.5, min=0,
       path="_delay_max"),
    _S("request.retries", "重试次数", "int", "网络", default=3, min=0),
    _S("request.encoding", "页面编码", "enum", "网络", default="auto",
       options=["auto", "utf-8", "gbk", "gb2312", "big5"]),
    _S("ua", "浏览器 UA", "textarea", "网络", path="request.headers.User-Agent",
       default="", help="被 Cloudflare 拦时换成自己浏览器真实的 UA"),
    _S("accept_lang", "Accept-Language", "str", "网络",
       path="request.headers.Accept-Language", default="zh-CN,zh;q=0.9"),
    _S("output.dir", "导出目录", "str", "输出", default="out",
       help="相对 harvester 目录"),
    _S("output.write_latest", "维护 latest 快照", "bool", "输出", default=True,
       help="每次采集后刷新 latest.json / latest.txt"),
    _S("output.dump_detail_html", "保留详情页 HTML", "bool", "输出", default=False,
       help="调试选择器时打开，文件会比较大"),
    _S("log.level", "日志级别", "enum", "输出", default="INFO",
       options=["DEBUG", "INFO", "WARNING", "ERROR"]),
]

SUBTITLE_SCHEMA = [
    _S("webdav.base_url", "WebDAV 地址", "str", "WebDAV 连接",
       help="OpenList 的 WebDAV 根，不带尾部斜杠"),
    _S("webdav.username", "用户名", "str", "WebDAV 连接"),
    _S("webdav.password", "密码", "password", "WebDAV 连接"),
    _S("webdav.root", "片库目录", "str", "WebDAV 连接",
       help="脚本只扫这个目录，须与推送 115 的落盘目录对应"),
    _S("webdav.auth", "认证方式", "enum", "WebDAV 连接", default="basic",
       options=["auto", "basic", "digest", "none"]),
    _S("webdav.timeout", "超时（秒）", "int", "WebDAV 连接", default=60, min=5),
    _S("scan.min_size_mb", "最小体积（MB）", "int", "扫描规则", default=100, min=0,
       help="小于此体积视为预告/样片"),
    _S("scan.max_depth", "递归深度上限", "int", "扫描规则", default=8, min=1),
    _S("scan.subtitle_suffix", "完成判定后缀", "str", "扫描规则", default=".zh.srt",
       help="存在同名+此后缀的字幕就跳过"),
    _S("scan.exclude_name_keywords", "文件名排除词", "strlist", "扫描规则",
       default=[], help="每行一个，文件名包含即跳过"),
    _S("asr.device", "转写设备", "enum", "转写引擎", default="cuda",
       options=["cuda", "cpu"], help="没有 N 卡选 cpu"),
    _S("asr.external_exe", "infer.exe 路径", "str", "转写引擎",
       help="一体化转写+翻译引擎的可执行文件"),
    _S("asr.external_input", "喂给引擎的内容", "enum", "转写引擎", default="video",
       options=["video", "audio"], help="video=整片（推荐）；audio=只喂 wav，行为未验证"),
    _S("asr.external_timeout_s", "单部转写时限（秒）", "int", "转写引擎",
       default=14400, min=60),
    _S("tools.ffmpeg", "ffmpeg 路径", "str", "转写引擎",
       default="bin/ffmpeg/ffmpeg.exe"),
    _S("tools.fallback_to_path", "找不到时用 PATH 里的 ffmpeg", "bool", "转写引擎",
       default=True),
    _S("pipeline.max_items_per_run", "每轮最多处理几部", "int", "流水线",
       default=0, min=0, help="0 = 不限；挂机建议 15~20"),
    _S("pipeline.max_attempts", "单部最大失败次数", "int", "流水线", default=3, min=1),
    _S("pipeline.max_consecutive_failures", "连败几部后中止本轮", "int", "流水线",
       default=3, min=1),
    _S("pipeline.retry_failed", "自动重试失败条目", "bool", "流水线", default=True),
    _S("pipeline.upload_immediately", "完成即回写字幕", "bool", "流水线", default=True),
    _S("pipeline.download_ahead", "预下载下一部", "bool", "流水线", default=True,
       help="GPU 转当前片时后台先下下一部"),
    _S("pipeline.shutdown_after_run", "跑完自动关机", "bool", "流水线", default=False),
    _S("workspace.state_db", "进度数据库", "str", "工作目录与日志",
       default="data/state.db", help="换库=换片库；相对 subtitle 目录"),
    _S("workspace.keep_en_srt", "保留英文原文字幕", "bool", "工作目录与日志",
       default=True),
    _S("logging.level", "日志级别", "enum", "工作目录与日志", default="INFO",
       options=["DEBUG", "INFO", "WARNING", "ERROR"]),
    _S("notify.enabled", "启用跑完通知", "bool", "通知", default=False),
    _S("notify.serverchan_key", "Server酱 SendKey", "password", "通知", default=""),
    _S("notify.bark_url", "Bark URL", "str", "通知", default=""),
]

SITE_SCHEMA = [
    _S("name", "站点标识", "str", "基本信息", help="决定输出目录和数据库分站记录"),
    _S("enabled", "启用采集", "bool", "基本信息", default=True),
    _S("request.base_url", "站点根地址", "str", "基本信息",
       help="相对链接按它补全"),
    _S("request.encoding", "页面编码", "enum", "基本信息", default="auto",
       options=["auto", "utf-8", "gbk", "gb2312", "big5"], help="国内老站乱码就改 gbk"),
    _S("cookie_header", "访问 Cookie", "textarea", "基本信息",
       path="request.headers.Cookie", default="",
       help="需要登录/过年龄验证的站点，把浏览器 Cookie 整串贴进来"),
    _S("list.keyword", "搜索关键词", "str", "列表页", default="",
       help="列表页地址里写了 {keyword} 时会被替换成它（如 /search/8k/1）；采集页临时填的关键词优先于这里"),
    _S("list.url_template", "列表页地址", "str", "列表页",
       help="页码位置写 {page}，关键词位置写 {keyword}，如 https://site/search/{keyword}/{page}"),
    _S("pages_start", "起始页", "int", "列表页", default=1, min=1, path="_pages[0]"),
    _S("pages_end", "结束页", "int", "列表页", default=3, min=1, path="_pages[1]"),
    _S("list.item_selector", "条目链接选择器", "str", "列表页",
       help="指向详情页的 <a> 的 CSS 选择器"),
    _S("list.item_title_selector", "条目标题选择器", "str", "列表页",
       default="", help="留空则从 <a> 文本/属性自动猜"),
    _S("detail.magnet_attr_selectors", "磁力链接选择器", "strlist", "详情页",
       default=[], help="每行一个 CSS 选择器，取 href 以 magnet: 开头的链接"),
    _S("detail.title_selector", "详情页标题选择器", "str", "详情页", default=""),
    _S("detail.magnet_size_selector", "磁力体积单元格", "str", "详情页", default="",
       help="磁力表里「文件大小」那一列的选择器"),
    _S("filter.include_keywords", "包含关键词", "strlist", "过滤", default=[],
       help="每行一个，留空 = 不限制"),
    _S("filter.exclude_keywords", "排除关键词", "strlist", "过滤",
       default=[], help="每行一个，如 预告 / 样片 / 广告"),
    _S("filter.allowed_extensions", "允许的扩展名", "strlist", "过滤",
       default=[], help="逗号或每行一个，如 mkv, mp4；留空 = 不限"),
    _S("filter.min_size_gb", "最小体积（GB）", "float", "过滤", default=0, min=0),
    _S("filter.max_size_gb", "最大体积（GB）", "float", "过滤", default=0, min=0),
    _S("filter.unknown_size", "体积未知时", "enum", "过滤", default="keep",
       options=["keep", "drop"]),
]


# ============================================================ FastAPI 应用
app = FastAPI(title="MagnetFlow Console", version=VERSION)
tasks_mod.paths = paths  # 保底


@app.get("/api/system/status")
def api_status():
    drive = Path(paths.ROOT.drive + "\\") if Path(paths.ROOT).drive else None
    free = shutil.disk_usage(drive).free / 2**30 if drive else None
    active = [t.to_dict(brief=True) for t in MANAGER.active_tasks()]
    return {
        "app": "MagnetFlow", "version": VERSION,
        "port": _SERVER.get("port", DEFAULT_PORT),
        "python": _venv_python_version(),
        "root": str(paths.ROOT),
        "disk_free_gb": round(free, 1) if free is not None else None,
        "busy": bool(active), "active": active,
    }


_VENV_PY_VER: str | None = None


def _venv_python_version() -> str:
    global _VENV_PY_VER
    if _VENV_PY_VER is None:
        try:
            import subprocess
            out = subprocess.run([_py(), "-c", "import platform;print(platform.python_version())"],
                                 capture_output=True, text=True, timeout=8)
            _VENV_PY_VER = out.stdout.strip() or "?"
        except Exception:
            _VENV_PY_VER = "?"
    return _VENV_PY_VER


def _db(path: Path, ro: bool = True) -> sqlite3.Connection:
    if not path.exists():
        raise HTTPException(503, f"数据库不存在：{path}")
    uri = f"file:{path.as_posix()}?mode=ro" if ro else f"file:{path.as_posix()}"
    con = sqlite3.connect(uri if ro else path, timeout=5, uri=ro)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=4000")
    return con


# ---------------------------------------------------------------- 总览
@app.get("/api/overview")
def api_overview():
    # 采集
    sites_cfg = []
    total = pending = today = 0
    for yf in sorted(paths.SITES_DIR.glob("*.yaml")):
        if yf.name.startswith("_"):        # 下划线开头 = 内部/测试站点，不在界面展示
            continue
        cfg = _read_plain(yf)
        sites_cfg.append({"name": str(cfg.get("name") or yf.stem),
                          "enabled": bool(cfg.get("enabled")), "file": yf.name})
    if paths.HARVEST_DB.exists():
        con = _db(paths.HARVEST_DB)
        total = con.execute("SELECT COUNT(*) FROM magnets").fetchone()[0]
        pending = con.execute(
            "SELECT COUNT(*) FROM magnets WHERE COALESCE(pushed,0)=0").fetchone()[0]
        today = con.execute(
            "SELECT COUNT(*) FROM magnets WHERE date(first_seen)=date('now','localtime')"
        ).fetchone()[0]
        con.close()
    # 最新导出文件
    last_export = None
    for f in paths.HARVEST_OUT_DIR.glob("*/latest.json") if paths.HARVEST_OUT_DIR.exists() else []:
        ts = f.stat().st_mtime
        if last_export is None or ts > last_export["mtime"]:
            last_export = {"site": f.parent.name, "mtime": ts}
    # 推送
    push_cfg = load_push_config()
    installed = importlib.util.find_spec("p115") is not None
    cookie = str(push_cfg.get("cookie") or "")
    # 字幕
    stages: dict[str, int] = {}
    if paths.SUBTITLE_DB.exists():
        con = _db(paths.SUBTITLE_DB)
        for row in con.execute("SELECT stage, COUNT(*) c FROM files GROUP BY stage"):
            stages[row["stage"]] = row["c"]
        con.close()
    sub_cfg = _read_plain(paths.SUBTITLE_CONFIG)
    exe = str(_getp(sub_cfg, "asr.external_exe") or "")
    engine_ok = bool(exe) and Path(exe).exists()
    last_by_kind: dict[str, dict] = {}
    for h in MANAGER.history:
        last_by_kind.setdefault("harvest" if h["kind"] in ("harvest", "pick_max") else h["kind"], h)
    return {
        "harvester": {
            "sites": sites_cfg, "sites_enabled": sum(1 for s in sites_cfg if s["enabled"]),
            "magnet_total": total, "magnet_pending": pending, "magnet_today": today,
            "last_export": last_export,
        },
        "push": {
            "installed": installed, "cookie_set": bool(cookie),
            "enabled": bool(push_cfg.get("enabled")),
            "save_path": str(push_cfg.get("save_path") or ""),
            "pending": pending,
        },
        "subtitle": {
            "stages": stages,
            "video_total": sum(stages.values()),
            "done": stages.get("done", 0),
            "failed": stages.get("failed", 0),
            "pending": stages.get("new", 0) + stages.get("failed", 0),
            "engine_ok": engine_ok, "engine_path": exe,
            "device": str(_getp(sub_cfg, "asr.device") or "cuda"),
            "webdav_root": str(_getp(sub_cfg, "webdav.root") or ""),
        },
        "last_tasks": {k: {s: v for s, v in h.items() if s != "kind"}
                       for k, h in last_by_kind.items()},
        "active": [t.to_dict(brief=True) for t in MANAGER.active_tasks()],
    }


# ---------------------------------------------------------------- 磁力库
_SORTS = {"first_seen": "first_seen", "size": "size_gb", "size_gb": "size_gb",
          "title": "title"}


@app.get("/api/magnets")
def api_magnets(q: str = "", site: str = "", pushed: str = "all",
                page: int = 1, size: int = 50,
                sort: str = "first_seen", order: str = "desc"):
    size = max(5, min(size, 500))
    page = max(page, 1)
    col = _SORTS.get(sort, "first_seen")
    direction = "ASC" if str(order).lower().startswith("a") else "DESC"
    where, args = [], []
    if q:
        where.append("(title LIKE ? OR filename LIKE ? OR infohash = ?)")
        like = f"%{q}%"
        args += [like, like, q.strip().lower()]
    if site:
        where.append("site = ?")
        args.append(site)
    if pushed in ("0", "1"):
        where.append("COALESCE(pushed,0) = ?")
        args.append(int(pushed))
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    con = _db(paths.HARVEST_DB)
    try:
        total = con.execute(f"SELECT COUNT(*) FROM magnets{wsql}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM magnets{wsql} ORDER BY {col} {direction}, infohash "
            f"LIMIT ? OFFSET ?", [*args, size, (page - 1) * size]).fetchall()
        items = [dict(r) for r in rows]
    finally:
        con.close()
    return {"total": total, "page": page, "size": size,
            "pages": max(1, -(-total // size)), "items": items}


@app.get("/api/magnets/facets")
def api_magnet_facets():
    con = _db(paths.HARVEST_DB)
    try:
        sites = [dict(r) for r in con.execute(
            "SELECT site, COUNT(*) c FROM magnets GROUP BY site ORDER BY c DESC")]
        total = sum(s["c"] for s in sites)
        pending = con.execute(
            "SELECT COUNT(*) FROM magnets WHERE COALESCE(pushed,0)=0").fetchone()[0]
    finally:
        con.close()
    return {"sites": sites, "total": total, "pending": pending}


@app.post("/api/magnets/bulk")
def api_magnets_bulk(payload: dict):
    hashes = [str(h) for h in payload.get("hashes") or [] if h][:1000]
    action = str(payload.get("action") or "")
    if not hashes or action not in ("pushed", "unpushed", "delete"):
        raise HTTPException(400, "参数：hashes[] + action(pushed|unpushed|delete)")
    con = _db(paths.HARVEST_DB, ro=False)
    try:
        if action == "delete":
            con.executemany("DELETE FROM magnets WHERE infohash = ?",
                            [(h,) for h in hashes])
        else:
            val = 1 if action == "pushed" else 0
            con.executemany("UPDATE magnets SET pushed = ? WHERE infohash = ?",
                            [(val, h) for h in hashes])
        con.commit()
        return {"affected": len(hashes), "action": action}
    finally:
        con.close()


# ---------------------------------------------------------------- 站点规则
def _site_files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for yf in sorted(paths.SITES_DIR.glob("*.yaml")):
        if yf.name.startswith("_"):        # 内部/测试站点不进界面
            continue
        name = str(_read_plain(yf).get("name") or yf.stem)
        out[name] = yf
    return out


@app.get("/api/sites")
def api_sites():
    files = _site_files()
    counts: dict[str, int] = {}
    if paths.HARVEST_DB.exists():
        con = _db(paths.HARVEST_DB)
        counts = {r["site"]: r["c"] for r in con.execute(
            "SELECT site, COUNT(*) c FROM magnets GROUP BY site")}
        con.close()
    items = []
    for name, yf in files.items():
        cfg = _read_plain(yf)
        items.append({
            "name": name, "file": yf.name, "enabled": bool(cfg.get("enabled")),
            "base_url": str(_getp(cfg, "request.base_url") or ""),
            "url_template": str(_getp(cfg, "list.url_template") or ""),
            "pages": _getp(cfg, "list.pages") or [1, 1],
            "has_ajax": bool(_getp(cfg, "detail.ajax.enabled")),
            "db_count": counts.get(name, 0),
        })
    return {"sites": items}


@app.get("/api/sites/{name}")
def api_site_get(name: str):
    files = _site_files()
    if name not in files:
        raise HTTPException(404, f"站点不存在：{name}")
    yf = files[name]
    cfg = _read_plain(yf)
    values = _form_get(cfg, SITE_SCHEMA)
    pages = _getp(cfg, "list.pages") or [1, 1]
    sel = _getp(cfg, "detail.magnet_attr_selectors") or []
    values["pages_start"] = int(pages[0] if len(pages) > 0 else 1)
    values["pages_end"] = int(pages[1] if len(pages) > 1 else pages[0] if pages else 3)
    values["detail.magnet_attr_selectors"] = [str(s) for s in sel]
    return {"name": name, "file": yf.name, "values": values,
            "schema": SITE_SCHEMA, "raw": yf.read_text(encoding="utf-8"),
            "ajax_note": "AJAX 取磁力表等高级规则请在下方「原始 YAML」里改"}


@app.put("/api/sites/{name}")
def api_site_put(name: str, payload: dict):
    files = _site_files()
    if name not in files:
        raise HTTPException(404, f"站点不存在：{name}")
    yf = files[name]
    doc = _load_rt(yf)
    old_plain = yamlkit.to_plain(doc)
    updates = payload.get("values") if isinstance(payload, dict) else payload
    if not isinstance(updates, dict):
        raise HTTPException(400, "body 需要 {values: {...}} 或直接字段对象")
    skip = ["name", "pages_start", "pages_end", "detail.magnet_attr_selectors"]
    _form_put(doc, [s for s in SITE_SCHEMA if s["key"] not in skip], updates)
    if "name" in updates and str(updates["name"]).strip():
        _setp(doc, "name", str(updates["name"]).strip())
    ps = _coerce("pages_start", {"type": "int", "min": 1}, updates.get("pages_start", 1))
    pe = _coerce("pages_end", {"type": "int", "min": 1}, updates.get("pages_end", ps))
    if pe < ps:
        ps, pe = pe, ps
    if "pages_start" in updates or "pages_end" in updates:
        _setp(doc, "list.pages", [ps, pe])
    if "detail.magnet_attr_selectors" in updates:
        sels = _to_list(updates["detail.magnet_attr_selectors"])
        if not sels:
            raise HTTPException(400, "磁力链接选择器至少要填一行")
        _setp(doc, "detail.magnet_attr_selectors", sels)
    _save_doc(yf, doc, old_plain)
    return {"ok": True, "saved": str(yf)}


@app.post("/api/sites/{name}/enabled")
def api_site_enabled(name: str, payload: dict):
    files = _site_files()
    if name not in files:
        raise HTTPException(404, f"站点不存在：{name}")
    yf = files[name]
    doc = _load_rt(yf)
    old_plain = yamlkit.to_plain(doc)
    _setp(doc, "enabled", bool(payload.get("enabled")))
    _save_doc(yf, doc, old_plain)
    return {"ok": True, "name": name, "enabled": bool(payload.get("enabled"))}


@app.get("/api/sites/{name}/raw")
def api_site_raw_get(name: str):
    files = _site_files()
    if name not in files:
        raise HTTPException(404, f"站点不存在：{name}")
    try:
        return {"path": str(files[name]), "text": files[name].read_text(encoding="utf-8")}
    except OSError:
        return {"path": str(files[name]), "text": ""}


@app.put("/api/sites/{name}/raw")
def api_site_raw(name: str, payload: dict):
    files = _site_files()
    if name not in files:
        raise HTTPException(404, f"站点不存在：{name}")
    text = str(payload.get("text") or "")
    try:
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, dict):
            raise ValueError("顶层必须是映射（key: value）")
        yaml.dump(parsed, allow_unicode=True, sort_keys=False)
    except (yaml.YAMLError, ValueError) as exc:
        raise HTTPException(400, f"YAML 语法有问题：{exc}") from exc
    _backup_save(files[name], text)
    return {"ok": True}


# ---------------------------------------------------------------- 配置表单
_SETTINGS_MAP = {
    "push115": (lambda: paths.PUSH_CONFIG, PUSH_SCHEMA),
    "harvester": (lambda: paths.HARVESTER_DIR / "config" / "default.yaml", HARVEST_SCHEMA),
    "subtitle": (lambda: paths.SUBTITLE_CONFIG, SUBTITLE_SCHEMA),
}


@app.get("/api/settings/{which}")
def api_settings_get(which: str):
    if which not in _SETTINGS_MAP:
        raise HTTPException(404, "未知的配置项")
    path_fn, schema = _SETTINGS_MAP[which]
    data = _read_plain(path_fn())
    values = _form_get(data, schema)
    extra = {}
    if which == "harvester":
        delay = _getp(data, "request.delay") or [1.0, 2.5]
        values["delay_min"] = float(delay[0])
        values["delay_max"] = float(delay[1])
    if which == "push115":
        # 只看 cookie 的值本身，别看整个文件——注释里提到 ${P115_COOKIE} 不算
        cookie_val = str(values.get("cookie") or "").strip()
        extra["env_cookie"] = bool(os.environ.get("P115_COOKIE"))
        extra["uses_env_placeholder"] = cookie_val.startswith("${") and cookie_val.endswith("}")
    return {"values": values, "schema": schema, "path": str(path_fn()), **extra}


@app.put("/api/settings/{which}")
def api_settings_put(which: str, payload: dict):
    if which not in _SETTINGS_MAP:
        raise HTTPException(404, "未知的配置项")
    path_fn, schema = _SETTINGS_MAP[which]
    updates = payload.get("values") if isinstance(payload, dict) else payload
    if not isinstance(updates, dict):
        raise HTTPException(400, "body 需要 {values: {...}}")
    target = path_fn()
    doc = _load_rt(target)
    old_plain = yamlkit.to_plain(doc)
    if which == "harvester":
        rest = {k: v for k, v in updates.items() if k not in ("delay_min", "delay_max")}
        _form_put(doc, [s for s in schema if s["key"] not in
                        ("delay_min", "delay_max")], rest)
        dmin = _coerce("delay_min", {"type": "float", "min": 0}, updates.get("delay_min", 1.0))
        dmax = _coerce("delay_max", {"type": "float", "min": 0}, updates.get("delay_max", 2.5))
        if dmax < dmin:
            dmin, dmax = dmax, dmin
        if any(k in updates for k in ("delay_min", "delay_max")):
            _setp(doc, "request.delay", [dmin, dmax])
        _save_doc(target, doc, old_plain)
        return {"ok": True, "saved": str(target)}
    _form_put(doc, schema, updates)
    _save_doc(target, doc, old_plain)
    return {"ok": True, "saved": str(target)}


@app.get("/api/settings/{which}/raw")
def api_settings_raw(which: str):
    if which not in _SETTINGS_MAP:
        raise HTTPException(404, "未知的配置项")
    target = _SETTINGS_MAP[which][0]()
    try:
        return {"path": str(target), "text": target.read_text(encoding="utf-8")}
    except OSError:
        return {"path": str(target), "text": ""}


@app.put("/api/settings/{which}/raw")
def api_settings_raw_put(which: str, payload: dict):
    if which not in _SETTINGS_MAP:
        raise HTTPException(404, "未知的配置项")
    target = _SETTINGS_MAP[which][0]()
    text = str(payload.get("text") or "")
    try:
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, dict):
            raise ValueError("顶层必须是映射")
    except (yaml.YAMLError, ValueError) as exc:
        raise HTTPException(400, f"YAML 语法有问题：{exc}") from exc
    _backup_save(target, text)
    return {"ok": True}


_QR_SESSIONS: dict[str, dict] = {}     # 115 扫码登录会话：uid -> {payload, ts}


def _p115_anon_client():
    """构造一个未登录的 p115client：只用来拿二维码 / 查扫码状态。"""
    try:
        from p115client import P115Client
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
        raise HTTPException(400, "未安装 p115client：pip install p115client -i "
                                 "https://pypi.tuna.tsinghua.edu.cn/simple") from exc
    return P115Client("")


@app.post("/api/115/qrcode/start")
def api_115_qrcode_start():
    """取一张 115 登录二维码（PNG base64），5 分钟内有效。"""
    client = _p115_anon_client()
    try:
        resp = client.login_qrcode_token(app="web")
        data = dict((resp or {}).get("data") or {})
        data.pop("qrcode", None)
        uid = str(data.get("uid") or "")
        if not uid:
            raise RuntimeError(f"115 返回异常：{str(resp)[:160]}")
        # 注意：p115client 0.0.9 的 login_qrcode() 会把 payload 丢掉，
        # 必须显式 params={"uid": uid}，否则拿到的是 uid 为空的废码（扫了会失败）
        png = client.login_qrcode(uid, app="web", params={"uid": uid})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络/接口异常统一转 502
        raise HTTPException(502, f"获取二维码失败：{exc}") from exc

    now = time.time()
    for k in [k for k, v in _QR_SESSIONS.items() if now - v["ts"] > 600]:
        _QR_SESSIONS.pop(k, None)                       # 顺手清理过期会话
    _QR_SESSIONS[uid] = {"payload": data, "ts": now}
    import base64
    return {"uid": uid, "expires_in": 300,
            "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}


@app.get("/api/115/qrcode/status")
def api_115_qrcode_status(uid: str):
    """轮询扫码结果：waiting / confirmed(带 cookie) / expired。"""
    item = _QR_SESSIONS.get(uid)
    if not item:
        raise HTTPException(404, "二维码已失效，请重新获取")
    if time.time() - item["ts"] > 300:
        _QR_SESSIONS.pop(uid, None)
        return {"status": "expired", "msg": "二维码已过期，请重新获取"}
    client = _p115_anon_client()
    try:
        res = client.login_qrcode_scan_result(uid, app="web")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"查询登录状态失败：{exc}") from exc

    data = (res or {}).get("data") or {}
    cookie = {str(k).upper(): str(v) for k, v in (data.get("cookie") or {}).items()}
    if cookie.get("UID") and cookie.get("SEID"):
        cookie_str = "; ".join(f"{k}={cookie[k]}" for k in ("UID", "CID", "SEID", "KID")
                               if cookie.get(k))
        _QR_SESSIONS.pop(uid, None)
        return {"status": "confirmed", "cookie": cookie_str,
                "user_id": cookie.get("UID")}
    msg = str((res or {}).get("message") or (res or {}).get("error") or "")
    # 40101017 = 还没扫码（115 的「老乡验证失败」）；已扫码未确认时 msg 会变成等待确认
    return {"status": "waiting", "msg": msg or "等待手机扫码"}


# ---------------------------------------------------------------- 连通性测试
@app.post("/api/test/cookie")
def api_test_cookie(payload: dict):
    cookie = str(payload.get("cookie") or load_push_config().get("cookie") or "")
    if not cookie:
        return {"ok": False, "msg": "还没填 Cookie（登录后 F12 执行 document.cookie 复制整串）"}
    try:
        ok, msg = _p115_test_cookie(cookie)
        return {"ok": ok, "msg": msg}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "msg": f"测试异常：{exc}"}


@app.post("/api/test/webdav")
def api_test_webdav(payload: dict):
    import requests
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    cfg = _read_plain(paths.SUBTITLE_CONFIG)
    base = str(payload.get("base_url") or _getp(cfg, "webdav.base_url") or "").rstrip("/")
    root = str(payload.get("root") or _getp(cfg, "webdav.root") or "")
    user = str(payload.get("username") or _getp(cfg, "webdav.username") or "")
    pwd = str(payload.get("password") or _getp(cfg, "webdav.password") or "")
    auth_mode = str(payload.get("auth") or _getp(cfg, "webdav.auth") or "basic")
    if not base:
        raise HTTPException(400, "缺少 WebDAV 地址")
    url = base + (root if root.startswith("/") else "/" + root if root else "")

    def probe(auth):
        return requests.request(
            "PROPFIND", url, auth=auth, headers={"Depth": "1", "Accept": "text/xml"},
            timeout=(6, 12), verify=False)
    try:
        auth = (HTTPBasicAuth(user, pwd) if auth_mode in ("basic", "auto")
                else HTTPDigestAuth(user, pwd) if auth_mode == "digest" else None)
        resp = probe(auth)
        if auth_mode == "auto" and resp.status_code == 401:
            resp = probe(HTTPDigestAuth(user, pwd))
        if resp.status_code in (207, 200):
            return {"ok": True, "msg": f"目录可读写（HTTP {resp.status_code}）：{url}"}
        if resp.status_code == 401:
            return {"ok": False, "msg": "认证失败：用户名或密码不对"}
        if resp.status_code == 403:
            return {"ok": False, "msg": "403：账号对这个目录没权限"}
        if resp.status_code == 404:
            return {"ok": False, "msg": f"目录不存在：{url}（检查 root 配置）"}
        return {"ok": False, "msg": f"HTTP {resp.status_code}：{resp.text[:120]}"}
    except requests.exceptions.SSLError as exc:
        return {"ok": False, "msg": f"证书问题：{exc}（可在高级 YAML 里关 verify_ssl）"}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "msg": f"连不上 {url}：{type(exc).__name__}"}


@app.post("/api/test/savedir")
def api_test_savedir(payload: dict):
    """校验「保存目录」在 115 网盘里真实存在，并返回目录 ID。

    云添加接口只认 wp_path_id，路径写错时会静默落到默认目录，所以推送前值得先测。
    """
    cfg = load_push_config()
    path = str(payload.get("path") or "").strip() or str(cfg.get("save_path") or "").strip()
    cookie = str(payload.get("cookie") or "").strip() or str(cfg.get("cookie") or "").strip()
    if not cookie:
        return {"ok": False, "msg": "还没配置 Cookie：先点「扫码登录」再测"}
    if not path:
        return {"ok": True, "msg": "保存目录留空 —— 任务会进 115 默认下载目录", "default": True}
    try:
        from p115client import P115Client
    except ImportError:
        return {"ok": False, "msg": "未安装 p115client"}
    try:
        pid = _resolve_wp_path_id(P115Client(cookie), path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}
    return {"ok": True, "msg": f"目录存在：{path}", "path_id": pid}


@app.post("/api/test/engine")
def api_test_engine(payload: dict):
    exe = str(payload.get("exe") or "").strip()
    if not exe:
        return {"ok": False, "msg": "还没填 infer.exe 路径"}
    p = Path(exe)
    if not p.exists():
        return {"ok": False, "msg": f"文件不存在：{exe}"}
    if p.suffix.lower() != ".exe":
        return {"ok": True, "msg": f"文件存在，但可能不是可执行文件：{exe}"}
    return {"ok": True, "msg": f"引擎存在（{p.stat().st_size / 2**20:.0f} MB）：{exe}"}


# ---------------------------------------------------------------- 任务
def _start(kind: str, argv: list, cwd: Path, label: str = "") -> dict:
    try:
        task = MANAGER.start(kind, argv, cwd, label)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "task": task.to_dict(brief=True)}


@app.post("/api/tasks/harvest")
def api_task_harvest(payload: dict):
    argv = [_py(), str(paths.HARVEST_SCRIPT)]
    site = str(payload.get("site") or "").strip()
    if site:
        argv += ["--site", site]
    pages = payload.get("pages") or []
    try:
        if site and len(pages) >= 2 and int(pages[0]) >= 1:
            argv += ["--pages", str(int(pages[0])), str(max(int(pages[1]), int(pages[0])))]
    except (TypeError, ValueError):
        pass
    kw = str(payload.get("keyword") or "").strip()
    if kw:
        argv += ["--keyword", kw]
    try:
        limit = int(payload.get("limit") or 0)
        if limit > 0:
            argv += ["--limit", str(limit)]
    except (TypeError, ValueError):
        pass
    if payload.get("dry_run"):
        argv += ["--dry-run"]
    return _start("harvest", argv, paths.HARVESTER_DIR)


@app.post("/api/tasks/push")
def api_task_push(payload: dict):
    argv = [_py(), str(paths.ROOT / "run.py"), "push"]
    try:
        limit = int(payload.get("limit") or 0)
        if limit > 0:
            argv += ["--limit", str(limit)]
    except (TypeError, ValueError):
        pass
    site = str(payload.get("site") or "").strip()
    if site:
        argv += ["--site", site]
    if payload.get("dry_run"):
        argv += ["--dry-run"]
    return _start("push", argv, paths.ROOT)


_SUBTITLE_CMDS = {
    "run": [], "scan": ["scan"], "status": ["status"], "doctor": ["doctor"],
    "retry_failed": ["run", "--retry-failed"],
}


@app.post("/api/tasks/subtitle")
def api_task_subtitle(payload: dict):
    command = str(payload.get("command") or "run")
    if command not in _SUBTITLE_CMDS:
        raise HTTPException(400, f"不支持的命令：{command}")
    argv = [_py(), str(paths.SUBTITLE_RUN), *_SUBTITLE_CMDS[command]]
    if command in ("run", "retry_failed"):
        try:
            limit = int(payload.get("limit") or 0)
            if limit > 0:
                argv += ["--limit", str(limit)]
        except (TypeError, ValueError):
            pass
    return _start("subtitle", argv, paths.SUBTITLE_DIR)


@app.post("/api/tasks/chain")
def api_task_chain(payload: dict):
    argv = [_py(), str(paths.ROOT / "run.py"), "chain"]
    for flag, key in (("skip_harvest", "--skip-harvest"), ("skip_push", "--skip-push"),
                      ("skip_subtitle", "--skip-subtitle"), ("dry_run", "--dry-run")):
        if payload.get(flag):
            argv.append(flag and key)
    try:
        limit = int(payload.get("limit") or 0)
        if limit > 0:
            argv += ["--limit", str(limit)]
    except (TypeError, ValueError):
        pass
    try:
        hlimit = int(payload.get("harvest_limit") or 0)
        if hlimit > 0:
            argv += ["--harvest-limit", str(hlimit)]
    except (TypeError, ValueError):
        pass
    return _start("chain", argv, paths.ROOT)


@app.post("/api/tasks/doctor")
def api_task_doctor(payload: dict | None = None):
    return _start("doctor", [_py(), str(paths.ROOT / "run.py"), "doctor"], paths.ROOT)


@app.post("/api/tasks/pick-max")
def api_task_pick_max(payload: dict):
    site = str(payload.get("site") or "").strip()
    cands = []
    if site:
        cands = [paths.HARVEST_OUT_DIR / site / "latest.json"]
    elif paths.HARVEST_OUT_DIR.exists():
        cands = sorted(paths.HARVEST_OUT_DIR.glob("*/latest.json"))
    latest = next((c for c in cands if c.exists()), None)
    if not latest:
        raise HTTPException(400, "还没有导出文件（先跑一次采集）")
    return _start("pick_max", [_py(), str(paths.PICK_MAX_SCRIPT),
                               "--in", str(latest)], paths.HARVESTER_DIR)


@app.get("/api/tasks")
def api_tasks_list():
    items = MANAGER.list_tasks()
    # 服务重启后内存里的任务没了、只剩 history：标记 gone，前端据此禁用「日志」按钮
    for it in items:
        if MANAGER.get(it.get("id")) is None:
            it["gone"] = True
    return {"tasks": items}


@app.get("/api/tasks/{task_id}")
def api_task_get(task_id: str):
    task = MANAGER.get(task_id)
    if not task:
        hist = next((h for h in MANAGER.history if h["id"] == task_id), None)
        if hist:
            return {"task": hist, "gone": True}
        raise HTTPException(404, "任务不存在")
    return {"task": task.to_dict()}


@app.get("/api/tasks/{task_id}/log")
def api_task_log(task_id: str, after: int = -1):
    task = MANAGER.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在（服务重启后历史日志只保留在文件里）")
    return {"lines": task.new_lines(after), "seq": task.seq,
            "dropped": task.dropped, "status": task.status,
            "exit_code": task.exit_code, "paused": task.paused,
            "progress": dict(task.progress)}


@app.post("/api/tasks/{task_id}/action")
def api_task_action(task_id: str, payload: dict):
    action = str(payload.get("action") or "")
    task = MANAGER.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if action == "stop":
        if not MANAGER.stop(task_id):
            raise HTTPException(409, "任务已经不在运行了")
        return {"ok": True, "action": "stop"}
    if action in ("pause", "resume"):
        want_paused = action == "pause"
        if task.paused != want_paused:
            res = MANAGER.pause_resume(task_id)
            if res is None:
                raise HTTPException(409, "任务当前状态下无法挂起/恢复")
        return {"ok": True, "paused": task.paused}
    raise HTTPException(400, f"未知操作：{action}")


# ---------------------------------------------------------------- 导出文件与目录
@app.get("/api/exports")
def api_exports():
    items = []
    if paths.HARVEST_OUT_DIR.exists():
        for pat in ("*/*.json", "*/*.csv", "*/*.txt"):
            for f in paths.HARVEST_OUT_DIR.glob(pat):
                st = f.stat()
                items.append({"name": f"{f.parent.name}/{f.name}", "path": str(f),
                              "size_kb": round(st.st_size / 1024, 1),
                              "mtime": st.st_mtime})
    items.sort(key=lambda x: -x["mtime"])
    return {"items": items[:100]}


_PATHS_NAMED = {
    "exports": lambda: paths.HARVEST_OUT_DIR,
    "logs": lambda: paths.LOGS_DIR,
    "config": lambda: paths.CONFIG_DIR,
    "subtitle_work": lambda: paths.SUBTITLE_DIR / "work",
    "subtitle_logs": lambda: paths.SUBTITLE_DIR / "logs",
    "subtitle_data": lambda: paths.SUBTITLE_DIR / "data",
    "root": lambda: paths.ROOT,
}


@app.get("/api/system/paths")
def api_paths_named():
    out = {}
    for key, fn in _PATHS_NAMED.items():
        p = fn()
        out[key] = {"path": str(p), "exists": p.exists()}
    return out


@app.post("/api/system/open")
def api_open(payload: dict):
    key = str(payload.get("key") or "")
    if key not in _PATHS_NAMED:
        raise HTTPException(400, f"未知的目录：{key}")
    p = _PATHS_NAMED[key]()
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise HTTPException(404, f"目录不存在：{p}")
    try:
        os.startfile(str(p))  # noqa: S606 - 本机控制台服务
        return {"ok": True, "path": str(p)}
    except OSError as exc:
        raise HTTPException(500, f"打开失败：{exc}") from exc


# ---------------------------------------------------------------- 静态前端
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/")
    def _no_static():
        return {"hint": f"前端未构建：请把静态文件放到 {STATIC_DIR}"}


# ---------------------------------------------------------------- 启动入口
_SERVER = {"port": DEFAULT_PORT}


def main(argv=None) -> int:
    import uvicorn
    ap = argparse.ArgumentParser(description="MagnetFlow Web 控制台")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MFLOW_PORT", DEFAULT_PORT)))
    ap.add_argument("--host", default=os.environ.get("MFLOW_HOST", "127.0.0.1"),
                    help="填 0.0.0.0 可局域网访问")
    ap.add_argument("--no-browser", action="store_true", help="不自动弹浏览器")
    args = ap.parse_args(argv)
    _SERVER["port"] = args.port
    paths.ensure_dirs()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # pythonw（无控制台）下 stdout/stderr 是 None，print 与 uvicorn 默认日志都会炸
    headless = sys.stdout is None or sys.stderr is None
    if not headless:
        print(f"MagnetFlow 控制台已启动： {url}")
    kwargs = {"log_level": "warning"} if not headless else {"log_config": None}
    uvicorn.run(app, host=args.host, port=args.port, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
