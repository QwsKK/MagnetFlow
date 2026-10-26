# -*- coding: utf-8 -*-
"""离线验证两处采集语义：
1) --limit 是「本次总共处理多少个详情页」，跨列表页累计（不是每页各取 N 条）
2) 一个详情页有多条磁力时，只保留体积最大的一条

不依赖网络：monkeypatch Fetcher / write_outputs，用罐头 HTML 跑 run_site。
用法：.venv/Scripts/python.exe harvester/tests/test_limit_and_pick_largest.py
"""
import hashlib
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARVESTER = HERE.parent
sys.path.insert(0, str(HARVESTER))

import harvest  # noqa: E402

def list_html(page, n=5):
    page = str(page)
    items = "".join(
        '<a class="item" href="/d/%s-%d">ITEM-%s-%d</a>' % (page, i, page, i)
        for i in range(1, n + 1)
    )
    return "<html><body><div id='list'>%s</div></body></html>" % items


def detail_html(tag):
    rows = "".join(
        '<tr><td><a href="magnet:?xt=urn:btih:%s&dn=f%s">f%s</a></td>'
        '<td class="size">%sGB</td></tr>'
        % (hashlib.md5(("%s-%s" % (tag, i)).encode()).hexdigest().ljust(40, "0"),
           i, i, size)
        for i, size in enumerate(("1.00", "5.00", "3.00"))
    )
    return ('<html><body><h1 class="title">TITLE-%s</h1>'
            "<table>%s</table></body></html>" % (tag, rows))


class FakeFetcher:
    """列表页返回 5 条；详情页返回 3 条不同体积的磁力（跨页 hash 不重复）。"""

    def __init__(self, cfg=None):
        self.calls = []
        FakeFetcher.last_calls = self.calls

    def get_text(self, url):
        self.calls.append(url)
        if "/p/" in url or "/search/" in url:
            return list_html("%s" % url.rstrip("/").rsplit("/", 1)[-1].replace("p", ""))
        return detail_html(url.rsplit("/", 1)[-1])


FakeFetcher.last_calls = []


def make_args(limit, pages, keyword=None):
    return Namespace(site="unittest", pages=pages, keyword=keyword, limit=limit,
                     dry_run=True, include_seen=False, list_sites=False)


def run_once(limit, pages=(1, 3), template="http://x/p/{page}", keyword=None,
             cfg_keyword=None, url_prefix=None):
    list_cfg = {"url_template": template, "item_selector": "a.item"}
    if cfg_keyword is not None:
        list_cfg["keyword"] = cfg_keyword
    site_cfg = {
        "name": "unittest",
        "request": {"base_url": url_prefix or "http://x"},
        "list": list_cfg,
        "detail": {"magnet_attr_selectors": ["a[href^='magnet:']"],
                   "title_selector": "h1.title",
                   "magnet_size_selector": "td.size"},
        "filter": {},
        "output": {"dir": "out", "formats": ["json"], "write_latest": False},
    }
    tmpdb = Path(tempfile.gettempdir()) / "mflow_unittest.db"
    if tmpdb.exists():
        tmpdb.unlink()
    defaults = {"request": {}, "filter": {}, "output": {},
                "state": {"db": str(tmpdb)}}

    got = {}
    orig_fetcher, orig_write = harvest.Fetcher, harvest.write_outputs
    harvest.Fetcher = FakeFetcher
    harvest.write_outputs = lambda *a, **kw: (got.__setitem__("groups", a[0]), [])[1]
    try:
        harvest.run_site(site_cfg, defaults, make_args(limit, list(pages), keyword))
    finally:
        harvest.Fetcher, harvest.write_outputs = orig_fetcher, orig_write
    got["urls"] = list(FakeFetcher.last_calls)
    return got


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  <- " + detail) if detail else ""))
    return cond


def main():
    ok = True

    # --- 单元：pick_largest_magnet
    pick = harvest.pick_largest_magnet
    ms = [{"size_gb": 1.0}, {"size_gb": 5.0}, {"size_gb": 3.0}]
    ok &= check("pick_largest 取 5GB", pick(ms)[0]["size_gb"] == 5.0)
    ok &= check("pick_largest 单条原样返回", len(pick([{"size_gb": 2.0}])) == 1)
    ok &= check("pick_largest 未知体积排最后",
                pick([{"size_gb": None}, {"size_gb": 2.0}])[0]["size_gb"] == 2.0)
    ok &= check("pick_largest 全未知取第一条", len(pick([{"size_gb": None}, {"size_gb": None}])) == 1)

    # --- 集成：limit=3，pages 1..3，每页 5 条 -> 只处理 3 个详情页
    res = run_once(limit=3)
    groups = res["groups"]
    ok &= check("limit=3 总共只处理 3 个详情页", len(groups) == 3, "得到 %d" % len(groups))
    ok &= check("每个详情页只留 1 条磁力",
                all(len(g["magnets"]) == 1 for g in groups),
                "每条数=%s" % [len(g["magnets"]) for g in groups])
    ok &= check("保留的是最大那条（5GB）",
                all(g["magnets"][0]["size_gb"] == 5.0 for g in groups),
                "体型=%s" % [g["magnets"][0]["size_gb"] for g in groups])

    # --- 集成：limit=0 = 不限，3 页 x 5 条 = 15 个详情页
    groups = run_once(limit=0)["groups"]
    ok &= check("limit=0 不限（3 页 x5 = 15）", len(groups) == 15, "得到 %d" % len(groups))

    # --- 集成：limit=8 跨页累计（第 1 页 5 条 + 第 2 页 3 条）
    groups = run_once(limit=8)["groups"]
    ok &= check("limit=8 跨页累计正好 8", len(groups) == 8, "得到 %d" % len(groups))

    # --- 关键词进 URL：模板带 {keyword} 时用采集页传入的词
    tpl = "http://x/search/{keyword}/{page}"
    res = run_once(limit=1, pages=(1, 1), template=tpl, keyword=["4k"])
    list_urls = [u for u in res["urls"] if "/search/" in u]
    ok &= check("关键词替换进列表页 URL",
                bool(list_urls) and list_urls[0] == "http://x/search/4k/1",
                "实际=%s" % list_urls[:2])

    # --- 关键词回退到站点配置 list.keyword
    res = run_once(limit=1, pages=(1, 1), template=tpl, cfg_keyword="8k")
    list_urls = [u for u in res["urls"] if "/search/" in u]
    ok &= check("没传关键词时回退 list.keyword",
                bool(list_urls) and list_urls[0] == "http://x/search/8k/1",
                "实际=%s" % list_urls[:2])

    # --- 关键词含空格/中文要 URL 编码
    res = run_once(limit=1, pages=(1, 1), template=tpl, keyword=["中文 4K"])
    list_urls = [u for u in res["urls"] if "/search/" in u]
    ok &= check("关键词 URL 编码",
                bool(list_urls) and "%E4%B8%AD%E6%96%87%204K" in list_urls[0],
                "实际=%s" % list_urls[:2])

    # --- 模板没有 {keyword} 时，关键词仍走「包含关键词」过滤（老行为不回归）
    res = run_once(limit=1, pages=(1, 1), keyword=["ITEM-1"])
    ok &= check("无 {keyword} 模板仍按过滤关键词处理",
                "search" not in " ".join(res["urls"]), "")

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
