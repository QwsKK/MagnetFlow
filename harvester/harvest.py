# -*- coding: utf-8 -*-
# 刻意不写 shebang：Windows 的 py 启动器遇到 "#!/usr/bin/env python" 会去 PATH 里
# 搜 python.exe，很容易命中微软商店的别名存根，结果是静默失败或退出码 9009。
# 不写 shebang 时 py 直接用注册表里的默认版本，反而是最稳的。
# 运行方式：py harvest.py ... | .\run.cmd ... | .\run.ps1 ...
"""配置驱动的静态站点磁力采集器。

用法:
    py harvest.py --list-sites
    py harvest.py --site mysite
    py harvest.py --site mysite --pages 1 5 --keyword "4K"
    py harvest.py --site mysite --dry-run --limit 10

设计要点:
    1. 站点规则全部写在 sites/*.yaml，脚本本身不含任何站点特有逻辑。
    2. infohash 级去重 + SQLite 增量，重复运行只会输出新链接。
    3. 结果里 pushed 字段为 0 的记录，就是后续接 115 云添加时的待推送队列。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, unquote, urljoin

import requests
import yaml
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
SITES_DIR = ROOT / "sites"
CONFIG_FILE = ROOT / "config" / "default.yaml"

MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>\\)\]}]+", re.I)
# 40 位 hex 分支必须排在 32 位 base32 前面，否则 40 位哈希会被截断成 32 位
INFOHASH_RE = re.compile(
    r"urn:btih:([0-9a-zA-Z]{40}(?![0-9a-zA-Z])|[0-9a-zA-Z]{32})", re.I
)
SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB|KIB|MIB|GIB|TIB|K|M|G|T)(?![A-Za-z])", re.I
)

# 文档里 <meta charset="..."> / <meta http-equiv="Content-Type" content="...charset=...">
# 自报的编码。声明优先于配置里的猜测。
META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I
)

SIZE_UNIT_TO_GB = {
    "K": 1 / 1024**2, "KB": 1 / 1024**2, "KIB": 1 / 1024**2,
    "M": 1 / 1024, "MB": 1 / 1024, "MIB": 1 / 1024,
    "G": 1.0, "GB": 1.0, "GIB": 1.0,
    "T": 1024.0, "TB": 1024.0, "TIB": 1024.0,
}

log = logging.getLogger("harvest")


# --------------------------------------------------------------- 基础工具

def deep_merge(base: dict, override: dict) -> dict:
    """把 override 合并进 base，dict 递归，其余直接覆盖。"""
    out = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def infohash_of(magnet: str) -> str:
    """从磁力链接里取 infohash 作为去重主键。"""
    match = INFOHASH_RE.search(magnet)
    if match:
        return match.group(1).lower()
    return "raw:" + str(abs(hash(magnet)))


def magnet_display_name(magnet: str) -> str:
    """解析磁力链接的 dn 参数（种子内文件名）。"""
    if "?" not in magnet:
        return ""
    try:
        query = parse_qs(magnet.split("?", 1)[1], keep_blank_values=True)
        return unquote((query.get("dn") or [""])[0]).strip()
    except Exception:
        return ""


def parse_size_gb(text: str) -> float | None:
    """从文本里抓第一个体积值并换算成 GB。"""
    if not text:
        return None
    match = SIZE_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return round(value * SIZE_UNIT_TO_GB[match.group(2).upper()], 3)


def guess_extension(text: str) -> str:
    """取文件名末尾的扩展名。注意不能取第一个点号后缀——
    'movie.4k.mkv' 的第一个后缀是 '4k'，会被扩展名白名单误杀。"""
    if not text:
        return ""
    name = re.split(r"[\\/]", text.strip())[-1].strip().lower()
    match = re.search(r"\.([a-z][a-z0-9]{1,4})\s*$", name)
    return match.group(1) if match else ""


# --------------------------------------------------------------- 抓取

def declared_charset(raw: bytes) -> str:
    """从页面头部的 <meta> 里读文档自报的编码，读不到返回空串。"""
    match = META_CHARSET_RE.search(raw[:4096])
    if not match:
        return ""
    return match.group(1).decode("ascii", errors="ignore")


def _try_decode(raw: bytes, enc: str) -> str | None:
    """严格解码，失败返回 None（宽松解码只会把乱码藏起来，不要）。"""
    try:
        return raw.decode(enc, errors="strict")
    except (UnicodeDecodeError, LookupError):
        return None


def looks_mojibake(text: str, enc: str) -> bool:
    """判断文本是否像是「UTF-8 字节被误按 enc 解出来」的乱码。

    思路：把文本按 enc 还原回字节，如果这批字节恰好是合法的 UTF-8，
    那原文几乎可以确定是 UTF-8，只是被 enc（通常是 gbk）错误解码了。
    """
    if enc.lower().replace("_", "").replace("-", "") == "utf8":
        return False
    try:
        raw = text.encode(enc, errors="strict")
        fixed = raw.decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
        return False
    return any(ord(ch) > 127 for ch in fixed)


def decode_html(raw: bytes, preferred: str = "", fallback: str = "") -> tuple[str, str]:
    """返回 (文本, 实际使用的编码)。

    候选顺序：文档 <meta> 自报 > 配置/响应头给定 > utf-8 > 兜底 > gbk。
    某个候选解出来像乱码就换下一个，避免配置写错编码时静默产出乱码。
    """
    declared = declared_charset(raw)
    tried: list[str] = []
    for enc in (declared, preferred, "utf-8", fallback, "gbk"):
        if not enc or enc.lower() in tried:
            continue
        tried.append(enc.lower())
        text = _try_decode(raw, enc)
        if text is None:
            continue
        if not looks_mojibake(text, enc):
            return text, enc
        log.warning("按 %s 解码疑似乱码，换用其他编码重试", enc)
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


class Fetcher:
    """带限速、重试、编码纠偏的 HTTP 客户端。"""

    def __init__(self, cfg: dict):
        self.timeout = float(cfg.get("timeout", 15))
        self.encoding = str(cfg.get("encoding", "auto")).lower()
        delay = cfg.get("delay", [1.0, 2.5])
        if not isinstance(delay, (list, tuple)):
            delay = [delay, delay]
        self.delay_range = (float(delay[0]), float(delay[1]))
        self._last_request = 0.0

        self.session = requests.Session()
        retry = Retry(
            total=int(cfg.get("retries", 3)),
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(cfg.get("headers") or {})

    def _throttle(self) -> None:
        wait = random.uniform(*self.delay_range)
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request = time.monotonic()

    def get_text(self, url: str) -> str:
        self._throttle()
        log.debug("GET %s", url)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()

        preferred = "" if self.encoding in ("", "auto") else self.encoding
        if not preferred:
            # 响应头没给 charset 时 requests 会回落 ISO-8859-1，这个猜测没有价值
            header_enc = resp.encoding or ""
            preferred = "" if header_enc.lower() == "iso-8859-1" else header_enc

        text, used = decode_html(resp.content, preferred, resp.apparent_encoding or "")
        log.debug("GET %s -> 按 %s 解码 %d 字节", url, used, len(resp.content))
        return text

    def get_ajax(self, url: str, referer: str = "") -> str:
        """取 XHR 接口返回的 HTML 片段。

        和 get_text 的差别在请求头：异步接口普遍校验 Referer，
        有的还会认 X-Requested-With，缺了就返回空串或整页 HTML。
        """
        self._throttle()
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if referer:
            headers["Referer"] = referer
        log.debug("GET(ajax) %s", url)
        resp = self.session.get(url, timeout=self.timeout, headers=headers)
        resp.raise_for_status()

        preferred = "" if self.encoding in ("", "auto") else self.encoding
        if not preferred:
            header_enc = resp.encoding or ""
            preferred = "" if header_enc.lower() == "iso-8859-1" else header_enc

        text, used = decode_html(resp.content, preferred, resp.apparent_encoding or "")
        log.debug("GET(ajax) %s -> 按 %s 解码 %d 字节", url, used, len(resp.content))
        return text


# --------------------------------------------------------------- 解析

def iter_list_urls(list_cfg: dict, pages_override: list[int] | None) -> list[str]:
    urls = list(list_cfg.get("urls") or [])
    template = list_cfg.get("url_template")
    if template:
        start, end = (pages_override or list_cfg.get("pages") or [1, 1])[:2]
        urls += [template.format(page=p) for p in range(int(start), int(end) + 1)]
    return urls


def parse_list_page(html: str, page_url: str, list_cfg: dict) -> Iterator[dict]:
    soup = BeautifulSoup(html, "html.parser")
    selector = list_cfg.get("item_selector")
    if not selector:
        raise ValueError("站点配置缺少 list.item_selector")
    attr = list_cfg.get("item_attr") or "href"
    title_selector = list_cfg.get("item_title_selector") or ""

    for node in soup.select(selector):
        href = node.get(attr)
        if not href:
            continue
        title = ""
        if title_selector:
            sub = node.select_one(title_selector)
            if sub:
                title = sub.get_text(strip=True)
        if not title:
            title = node.get_text(strip=True) or node.get("title", "")
        yield {"url": urljoin(page_url, href), "list_title": title.strip()}


def _entry_from_node(node: Any, detail_cfg: dict,
                     fallback_size_gb: float | None) -> dict:
    """从一个带 magnet: href 的 <a> 元素构造一条结构化磁力记录。

    体积的取值顺序（能取到就停）：
      1. detail.magnet_size_selector 指定的同行单元格（最准）
      2. <a> 自身的文本（有些站点直接把 "16.10GB" 写在 <a> 里）
      3. 整行 <tr> 的文本
      4. 页面级体积（详情页里找到的第一个体积值）
    """
    # bs4 解析属性时已经还原过 HTML 实体，这里只需再解一次百分号编码
    magnet = unquote((node.get("href") or "").strip())
    filename = magnet_display_name(magnet)
    row = node.find_parent("tr")

    size_gb = None
    size_selector = detail_cfg.get("magnet_size_selector") or ""
    if size_selector and row is not None:
        sub = row.select_one(size_selector)
        if sub is not None:
            size_gb = parse_size_gb(sub.get_text(" "))
    if size_gb is None:
        size_gb = parse_size_gb(node.get_text(" "))
    if size_gb is None and row is not None:
        size_gb = parse_size_gb(row.get_text(" "))
    if size_gb is None:
        size_gb = fallback_size_gb

    if not filename:
        text = node.get_text(strip=True)
        # 体积文本不是文件名，别把 "16.10GB" 当成名字和扩展名去过滤
        filename = "" if parse_size_gb(text) is not None else text

    return {
        "infohash": infohash_of(magnet),
        "magnet": magnet,
        "filename": filename.strip(),
        "size_gb": size_gb,
        "extension": guess_extension(filename),
    }


def extract_magnet_entries(html: str, detail_cfg: dict,
                           fallback_size_gb: float | None = None) -> list[dict]:
    """提取详情页里的全部磁力，返回结构化列表，按 infohash 去重。

    优先走 DOM（能带上每条磁力自己的体积），DOM 取不到才退回正则扫全文。
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    seen: set[str] = set()

    for selector in detail_cfg.get("magnet_attr_selectors") or []:
        for node in soup.select(selector):
            href = (node.get("href") or "").strip()
            if not href.lower().startswith("magnet:"):
                continue
            entry = _entry_from_node(node, detail_cfg, fallback_size_gb)
            if entry["infohash"] in seen:
                continue
            seen.add(entry["infohash"])
            entries.append(entry)

    if entries:
        return entries

    if not detail_cfg.get("fallback_to_regex", True):
        return []

    found = MAGNET_RE.findall(soup.get_text(" "))
    if not found:
        # 走原始 HTML 时要先还原实体：onclick="window.open('magnet:?xt=...&amp;dn=x')"
        # 里的 &amp; 不解出来的话，dn 参数会变成 "amp;xxx"
        found = [unescape(m) for m in MAGNET_RE.findall(html)]

    for magnet in found:
        key = infohash_of(magnet)
        if key in seen:
            continue
        seen.add(key)
        # 正则捞到的没有 DOM 位置，只能退回页面级体积
        filename = magnet_display_name(magnet)
        entries.append({
            "infohash": key,
            "magnet": magnet,
            "filename": filename,
            "size_gb": fallback_size_gb,
            "extension": guess_extension(filename),
        })
    return entries


def extract_page_vars(html: str, patterns: dict) -> dict:
    """按配置里的正则，从页面内联 <script> 里取值，供 AJAX 接口拼 URL。

    目标形如 `var gid = 69777671478;` / `var uc = 0;` / `var img = '/pics/x.jpg';`。
    取不到就给空串，让调用方在拼 URL 时直接暴露问题，而不是静默拼出坏链接。
    """
    values: dict[str, str] = {}
    for name, pattern in (patterns or {}).items():
        match = re.search(pattern, html)
        values[name] = match.group(1) if match else ""
    return values


def build_ajax_url(template: str, detail_html: str, ajax_cfg: dict) -> str:
    """把 detail.ajax.url_template 里的 {gid} {uc} ... 换成页面里取到的值。"""
    values = extract_page_vars(detail_html, ajax_cfg.get("vars") or {})
    rand_max = int(ajax_cfg.get("random_floor") or 0)
    if rand_max:
        # 站点用 floor 当缓存击穿参数，随便给个随机数即可
        values["floor"] = str(random.randint(1, rand_max))
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError(
            f"AJAX 模板里的变量 {exc} 页面中没有取到，已取到: {sorted(values)}"
        ) from exc


def augment_with_ajax(detail_html: str, page_url: str,
                      ajax_cfg: dict, fetcher: Fetcher) -> str:
    """详情页磁力若是 XHR 异步加载的，把接口返回的片段追加到详情 HTML 后面。

    追加而不是替换：详情 HTML 本身还要用来取标题和体积，替换会把这些信息弄丢。
    接口失败时原样返回，不让单个详情页的失败影响整轮采集。
    """
    template = ajax_cfg.get("url_template")
    if not template:
        log.warning("detail.ajax.enabled 为 true 但缺少 url_template，跳过")
        return detail_html

    try:
        url = build_ajax_url(template, detail_html, ajax_cfg)
    except ValueError as exc:
        log.warning("AJAX 接口地址拼接失败 %s: %s", page_url, exc)
        return detail_html

    referer = page_url if ajax_cfg.get("referer", True) else ""
    try:
        fragment = fetcher.get_ajax(url, referer)
    except Exception as exc:
        log.warning("AJAX 接口请求失败 %s: %s", url, exc)
        return detail_html

    if not MAGNET_RE.search(fragment):
        log.warning("AJAX 接口未返回磁力片段 %s（%d 字节）", url, len(fragment))
    return detail_html + "\n" + fragment


def parse_detail(html: str, detail_cfg: dict, list_title: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    selector = detail_cfg.get("title_selector")
    if selector:
        node = soup.select_one(selector)
        if node:
            title = node.get_text(strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    if not title:
        title = list_title

    size_selector = detail_cfg.get("size_selector") or ""
    size_gb = None
    if size_selector:
        node = soup.select_one(size_selector)
        if node:
            size_gb = parse_size_gb(node.get_text(" "))
    if size_gb is None:
        size_gb = parse_size_gb(soup.get_text(" ")[:20000])

    return {"title": title.strip(), "size_gb": size_gb}


# --------------------------------------------------------------- 过滤

def apply_filter(item: dict, rules: dict) -> tuple[bool, str]:
    """返回 (是否保留, 被过滤的原因)。"""
    title = (item.get("title") or "").lower()
    haystack = " ".join([title, (item.get("filename") or "").lower()])

    exclude = [k.lower() for k in rules.get("exclude_keywords") or [] if k]
    for kw in exclude:
        if kw in haystack:
            return False, f"命中排除词: {kw}"

    include = [k.lower() for k in rules.get("include_keywords") or [] if k]
    if include and not any(kw in haystack for kw in include):
        return False, "未命中包含词"

    allowed = [e.lower().lstrip(".") for e in rules.get("allowed_extensions") or []]
    if allowed:
        ext = (item.get("extension") or "").lower()
        if ext and ext not in allowed:
            return False, f"扩展名不匹配: {ext}"

    min_gb = float(rules.get("min_size_gb") or 0)
    max_gb = float(rules.get("max_size_gb") or 0)
    size = item.get("size_gb")
    if size is None:
        if str(rules.get("unknown_size", "keep")).lower() == "drop":
            return False, "无法识别体积"
    else:
        if min_gb and size < min_gb:
            return False, f"体积过小: {size}GB"
        if max_gb and size > max_gb:
            return False, f"体积过大: {size}GB"

    return True, ""


# --------------------------------------------------------------- 存储

class Store:
    """SQLite 去重表。pushed 字段留给后续接入 115。"""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS magnets (
                infohash   TEXT PRIMARY KEY,
                site       TEXT NOT NULL,
                magnet     TEXT NOT NULL,
                title      TEXT,
                filename   TEXT,
                size_gb    REAL,
                extension  TEXT,
                source_url TEXT,
                first_seen TEXT,
                pushed     INTEGER DEFAULT 0
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_magnets_site ON magnets(site)"
        )
        self.conn.commit()

    def known(self, site: str) -> set[str]:
        cur = self.conn.execute(
            "SELECT infohash FROM magnets WHERE site = ?", (site,)
        )
        return {row[0] for row in cur.fetchall()}

    def insert(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        payload = [
            (
                r["infohash"], r["site"], r["magnet"], r.get("title", ""),
                r.get("filename", ""), r.get("size_gb"), r.get("extension", ""),
                r.get("source_url", ""), r["first_seen"], 0,
            )
            for r in rows
        ]
        self.conn.executemany(
            "INSERT OR IGNORE INTO magnets "
            "(infohash, site, magnet, title, filename, size_gb, extension, "
            "source_url, first_seen, pushed) VALUES (?,?,?,?,?,?,?,?,?,?)",
            payload,
        )
        self.conn.commit()
        return self.conn.total_changes

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------- 输出

def flatten_groups(groups: list[dict]) -> list[dict]:
    """把「一个详情页一个对象」的分组结果摊平成每行一条磁力。

    数据库去重和 csv/txt 导出都是按磁力一行一条来做的，
    分组结构（json）和平铺结构（csv/txt）之间靠这个函数转换。
    """
    rows: list[dict] = []
    for group in groups:
        for magnet in group.get("magnets") or []:
            rows.append({
                "infohash": magnet["infohash"],
                "site": group.get("site", ""),
                "magnet": magnet["magnet"],
                "title": group.get("title", ""),
                "filename": magnet.get("filename", ""),
                "size_gb": magnet.get("size_gb"),
                "extension": magnet.get("extension", ""),
                "source_url": group.get("source_url", ""),
                "first_seen": group.get("first_seen", ""),
            })
    return rows


def total_magnets(groups: list[dict]) -> int:
    return sum(len(g.get("magnets") or []) for g in groups)


def pick_largest_magnet(magnets: list[dict]) -> list[dict]:
    """同一个详情页常挂多条磁力（不同清晰度/字幕），默认只保留体积最大的一条。

    体积未知（None）的排在已知体积之后；全都未知就取第一条，行为可预期。
    站点 YAML 里 `detail.pick_largest: false` 可关掉这个行为。
    """
    if len(magnets) <= 1:
        return magnets

    def size_key(m: dict) -> tuple:
        size = m.get("size_gb")
        known = size is not None
        return (known, float(size) if known else -1.0)

    return [max(magnets, key=size_key)]


def write_outputs(items: list[dict], site: str, out_dir: Path,
                  formats: list[str], write_latest: bool,
                  dump_detail: bool = False,
                  detail_pages: list[dict] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    written: list[Path] = []
    columns = ["infohash", "title", "filename", "size_gb", "extension",
               "magnet", "source_url", "first_seen"]
    generated_at = datetime.now().isoformat(timespec="seconds")

    # json 按详情页分组（一个详情一个对象，磁力在 magnets 数组里），
    # csv/txt 是二维结构，摊平成每行一条磁力
    rows = flatten_groups(items)

    payload = {
        "site": site,
        "generated_at": generated_at,
        "count": len(items),
        "magnet_count": total_magnets(items),
        "items": items,
    }

    def dump_json(path: Path, data: dict) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if "json" in formats:
        path = out_dir / f"magnets-{stamp}.json"
        dump_json(path, payload)
        written.append(path)
        if write_latest:
            latest = out_dir / "latest.json"
            dump_json(latest, payload)
            written.append(latest)

    if "csv" in formats:
        path = out_dir / f"magnets-{stamp}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)
        if write_latest:
            latest = out_dir / "latest.csv"
            with latest.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            written.append(latest)

    if "txt" in formats:
        path = out_dir / f"magnets-{stamp}.txt"
        path.write_text(
            "\n".join(row["magnet"] for row in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        written.append(path)
        if write_latest:
            latest = out_dir / "latest.txt"
            latest.write_text(
                "\n".join(row["magnet"] for row in rows) + ("\n" if rows else ""),
                encoding="utf-8",
            )
            written.append(latest)

    if dump_detail and detail_pages is not None:
        # 详情页原始 HTML 全量落盘，方便事后补选择器 / 排错，不参与去重与过滤
        detail_payload = {
            "site": site,
            "generated_at": generated_at,
            "count": len(detail_pages),
            "items": detail_pages,
        }
        path = out_dir / f"detail-{stamp}.json"
        dump_json(path, detail_payload)
        written.append(path)
        if write_latest:
            latest = out_dir / "latest_detail.json"
            dump_json(latest, detail_payload)
            written.append(latest)

    return written


# --------------------------------------------------------------- 主流程

def run_site(site_cfg: dict, defaults: dict, args: argparse.Namespace) -> int:
    site = site_cfg.get("name", "unnamed")
    req_cfg = deep_merge(defaults.get("request", {}), site_cfg.get("request", {}))
    list_cfg = site_cfg.get("list", {})
    detail_cfg = site_cfg.get("detail", {})
    filter_cfg = deep_merge(defaults.get("filter", {}), site_cfg.get("filter", {}))
    out_cfg = deep_merge(defaults.get("output", {}), site_cfg.get("output", {}))

    # 列表页模板里带 {keyword} 的站点（如 /search/<关键词>/<页码> 这类搜索页），
    # 关键词直接拼进搜索 URL；采集页临时传的关键词优先于 list.keyword。
    # 模板里没有 {keyword} 的站点退回老行为：关键词并进「包含关键词」过滤白名单。
    template = str(list_cfg.get("url_template") or "")
    keyword_in_url = "{keyword}" in template
    if keyword_in_url:
        kw = (args.keyword[0].strip() if args.keyword else
              str(list_cfg.get("keyword") or "").strip())
        if kw:
            tpl = template.replace("{keyword}", quote(kw, safe=""))
        else:
            tpl = template.replace("/{keyword}", "").replace("{keyword}", "")
            log.warning("列表页模板含 {keyword}，但关键词和 list.keyword 都是空的")
        list_cfg = dict(list_cfg)
        list_cfg["url_template"] = tpl
        log.info("搜索关键词：%s", kw or "(空)")
    if args.keyword and not keyword_in_url:
        keywords = list(filter_cfg.get("include_keywords") or [])
        keywords += [k for k in args.keyword if k not in keywords]
        filter_cfg["include_keywords"] = keywords

    fetcher = Fetcher(req_cfg)
    base_url = str(req_cfg.get("base_url", "")).rstrip("/")
    store = Store(ROOT / defaults.get("state", {}).get("db", "state/harvest.db"))

    known = set() if args.include_seen else store.known(site)
    log.info("站点 %s：历史已收录 %d 条", site, len(known))

    page_urls = iter_list_urls(list_cfg, args.pages)
    log.info("待抓列表页 %d 个", len(page_urls))

    dump_detail = bool(out_cfg.get("dump_detail_html", False))
    # collected 里每个元素是一个详情页分组，磁力在 group["magnets"] 列表里
    collected: list[dict] = []
    detail_pages: list[dict] = []
    seen_this_run: set[str] = set()
    stats = {"detail": 0, "magnet": 0, "dup": 0, "filtered": 0, "error": 0}
    # limit 是「本次总共处理多少个详情页」，跨所有列表页累计，而不是每页各取 N 条
    remaining = max(0, int(args.limit or 0))

    for page_url in page_urls:
        if remaining <= 0 and args.limit:
            log.info("已达到本次上限 %d 个详情页，停止翻页", args.limit)
            break
        if base_url and page_url.startswith("/"):
            page_url = base_url + page_url
        try:
            html = fetcher.get_text(page_url)
        except Exception as exc:
            stats["error"] += 1
            log.error("列表页抓取失败 %s: %s", page_url, exc)
            continue

        entries = list(parse_list_page(html, page_url, list_cfg))
        log.info("列表页 %s -> 条目 %d", page_url, len(entries))
        if remaining:
            entries = entries[:remaining]

        for entry in entries:
            if remaining:
                remaining -= 1
            try:
                detail_html = fetcher.get_text(entry["url"])
            except Exception as exc:
                stats["error"] += 1
                log.warning("详情页抓取失败 %s: %s", entry["url"], exc)
                continue
            stats["detail"] += 1

            # 磁力若是 XHR 异步加载的，先把接口返回的片段并进详情 HTML 再提取
            ajax_cfg = detail_cfg.get("ajax") or {}
            if ajax_cfg.get("enabled"):
                detail_html = augment_with_ajax(
                    detail_html, entry["url"], ajax_cfg, fetcher
                )

            meta = parse_detail(detail_html, detail_cfg, entry["list_title"])
            # 页面级体积只作为最后兜底，每条磁力优先用自己的体积
            magnets = extract_magnet_entries(detail_html, detail_cfg,
                                             meta["size_gb"])
            stats["magnet"] += len(magnets)
            # 一个详情页有多条磁力时，只留体积最大的一条（站点 YAML 可关）
            if detail_cfg.get("pick_largest", True) and len(magnets) > 1:
                before = len(magnets)
                magnets = pick_largest_magnet(magnets)
                best = magnets[0]
                log.info("详情页 %d 条磁力，只取最大的一条（%s）",
                         before,
                         ("%.2fGB" % best["size_gb"]) if best.get("size_gb") is not None else "体积未知")

            if dump_detail:
                detail_pages.append({
                    "source_url": entry["url"],
                    "list_title": entry["list_title"],
                    "title": meta["title"],
                    "size_gb": meta["size_gb"],
                    "magnet_count": len(magnets),
                    "magnets": magnets,
                    "html": detail_html,
                })

            kept: list[dict] = []
            for magnet in magnets:
                ih = magnet["infohash"]
                if ih in known or ih in seen_this_run:
                    stats["dup"] += 1
                    continue

                title = meta["title"] or magnet["filename"] or entry["list_title"]
                probe = {
                    "title": title,
                    "filename": magnet["filename"],
                    "extension": magnet["extension"],
                    "size_gb": magnet["size_gb"],
                }
                keep, reason = apply_filter(probe, filter_cfg)
                if not keep:
                    stats["filtered"] += 1
                    log.debug("过滤 %s (%s)", title[:40], reason)
                    continue

                kept.append(magnet)
                known.add(ih)
                seen_this_run.add(ih)

            if not kept:
                continue

            title = meta["title"] or kept[0]["filename"] or entry["list_title"]
            group = {
                "site": site,
                "title": title,
                "source_url": entry["url"],
                "magnet_count": len(kept),
                "magnets": kept,
                "first_seen": datetime.now().isoformat(timespec="seconds"),
            }
            collected.append(group)
            sizes = [m["size_gb"] for m in kept if m["size_gb"] is not None]
            log.info("新增 %s | %d 条磁力 | %sGB",
                     title[:48], len(kept),
                     "%.2f" % max(sizes) if sizes else "-")

    if collected and not args.dry_run:
        rows = flatten_groups(collected)
        store.insert(rows)
        log.info("写入数据库 %d 条", len(rows))

    out_dir = ROOT / str(out_cfg.get("dir", "out")) / site
    formats = [f.lower() for f in (out_cfg.get("formats") or ["json", "txt"])]
    written = write_outputs(
        collected, site, out_dir, formats, bool(out_cfg.get("write_latest", True)),
        dump_detail=dump_detail, detail_pages=detail_pages,
    )
    store.close()

    print()
    print(f"[{site}] 抓取详情页 {stats['detail']}，发现磁力 {stats['magnet']}，"
          f"重复 {stats['dup']}，过滤 {stats['filtered']}，"
          f"新增 {total_magnets(collected)} 条（{len(collected)} 个详情页），"
          f"失败 {stats['error']}")
    if args.dry_run:
        print("dry-run 模式：未写入数据库")
    for path in written:
        print(f"  -> {path.relative_to(ROOT)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="配置驱动的静态站点磁力采集器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  py harvest.py --list-sites\n"
               "  py harvest.py --site example --pages 1 5\n"
               "  py harvest.py --site example --keyword 4K --dry-run\n",
    )
    parser.add_argument("--site", help="站点配置名（sites/<name>.yaml）")
    parser.add_argument("--list-sites", action="store_true", help="列出所有站点配置")
    parser.add_argument("--pages", nargs=2, type=int, metavar=("START", "END"),
                        help="临时覆盖配置里的页码区间")
    parser.add_argument("--keyword", action="append", metavar="KW",
                        help="追加包含关键词，可重复")
    parser.add_argument("--limit", type=int,
                        help="本次总共最多处理多少个详情页（跨所有列表页累计），0/省略 = 不限")
    parser.add_argument("--dry-run", action="store_true", help="只跑不写数据库")
    parser.add_argument("--include-seen", action="store_true",
                        help="忽略历史去重，重新输出全部（仍会写回数据库）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # 不要强制 UTF-8：Windows 控制台默认 GBK，强改会让输出被按错误编码解码而乱码。
        # 跟随控制台原生编码（tty 下 Python 走 Unicode API，管道下走 locale 编码），
        # 只放宽错误处理，遇到编码不了的字符打问号而不是崩掉。
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)
    defaults = load_yaml(CONFIG_FILE) if CONFIG_FILE.exists() else {}

    log_cfg = defaults.get("log", {})
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_file = log_cfg.get("file")
    if log_file:
        path = ROOT / log_file
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else
        getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers,
    )

    if args.list_sites:
        rows = []
        for path in sorted(SITES_DIR.glob("*.yaml")):
            cfg = load_yaml(path)
            rows.append((path.stem, cfg.get("name", "?"),
                         "启用" if cfg.get("enabled") else "关闭"))
        if not rows:
            print("sites/ 目录下还没有配置文件，复制 sites/example.yaml 改一份")
            return 0
        print(f"{'配置文件':<24}{'站点名':<20}状态")
        for name, site, status in rows:
            print(f"{name:<24}{site:<20}{status}")
        return 0

    if not args.site:
        build_parser().print_help()
        return 1

    site_path = SITES_DIR / f"{args.site}.yaml"
    if not site_path.exists():
        log.error("找不到站点配置: %s", site_path)
        return 1

    site_cfg = load_yaml(site_path)
    if not site_cfg.get("enabled"):
        log.error("站点 %s 未启用，把配置里的 enabled 改成 true", args.site)
        return 1

    return run_site(site_cfg, defaults, args)


if __name__ == "__main__":
    sys.exit(main())
