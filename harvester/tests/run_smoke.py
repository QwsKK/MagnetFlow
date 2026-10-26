# -*- coding: utf-8 -*-
"""端到端冒烟测试：本地静态站点 -> 采集 -> 去重 -> 过滤 -> 导出。

    py tests\run_smoke.py
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

ROOT = HERE.parent
WWWROOT = HERE / "fixtures" / "site"
PORT = 8765

# 子进程跟随控制台编码输出，这里统一成 UTF-8 便于按 utf-8 解码断言，
# 避免不同终端代码页导致测试结果不稳定。
os.environ["PYTHONIOENCODING"] = "utf-8"

import make_fixtures  # noqa: E402
import harvest  # noqa: E402

FAILURES: list[str] = []


def flat(items: list[dict]) -> list[dict]:
    """把按详情页分组的记录摊平，方便按磁力逐条断言。"""
    rows = []
    for group in items:
        for magnet in group.get("magnets") or []:
            rows.append({**magnet, "title": group.get("title", "")})
    return rows


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  PASS  {message}")
    else:
        print(f"  FAIL  {message}")
        FAILURES.append(message)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs) -> None:  # 静音访问日志
        pass


def start_server():
    handler = functools.partial(QuietHandler, directory=str(WWWROOT))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def run_harvest(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "harvest.py", "--site", "_local_test", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def reset_state() -> None:
    """把真实库临时挪走（而不是删掉），测试结束后 restore_state 换回来。"""
    shutil.rmtree(ROOT / "out" / "local_test", ignore_errors=True)
    db = ROOT / "state" / "harvest.db"
    bak = ROOT / "state" / "harvest.db.real"
    if db.exists():
        if bak.exists():
            db.unlink()          # 上次测试崩溃残留：这次以备份为准
        else:
            shutil.move(str(db), str(bak))


def restore_state() -> None:
    shutil.rmtree(ROOT / "out" / "local_test", ignore_errors=True)
    db = ROOT / "state" / "harvest.db"
    bak = ROOT / "state" / "harvest.db.real"
    if bak.exists():
        if db.exists():
            db.unlink()          # fixture 库让位给真实库
        shutil.move(str(bak), str(db))


def main() -> int:
    print("1) 生成测试站点")
    make_fixtures.build()

    print(f"2) 启动本地服务 127.0.0.1:{PORT}")
    httpd = start_server()

    print("3) 清空历史状态")
    reset_state()

    try:
        print("4) 首次采集")
        proc = run_harvest()
        print(proc.stderr.strip()[-600:] or proc.stdout.strip()[-600:])
        check(proc.returncode == 0, "首次采集退出码为 0")
        check("新增 4" in proc.stdout, "首次采集新增 4 条")

        latest = ROOT / "out" / "local_test" / "latest.json"
        check(latest.exists(), "生成 latest.json")
        if latest.exists():
            data = json.loads(latest.read_text(encoding="utf-8"))
            items = data["items"]
            check(data["count"] == 4, f"latest.json 记录 4 个详情页（实际 {data['count']}）")
            check(data["magnet_count"] == 4,
                  f"latest.json 磁力总数 4（实际 {data['magnet_count']}）")

            rows = flat(items)
            titles = [i["title"] for i in rows]
            check(not any("预告" in t for t in titles), "排除词生效：无预告片")
            exts = [i["extension"] for i in rows]
            check("exe" not in exts, f"扩展名过滤生效：{exts}")

            sizes = sorted((i["size_gb"] for i in rows), reverse=True)
            check(sizes == [12.3, 3.4, 1.2, 0.8], f"GBK 页面体积解析正确：{sizes}")

            check(all(i["magnet"].startswith("magnet:") for i in rows),
                  "磁力链接格式正确")
            check(len({i["infohash"] for i in rows}) == 4, "infohash 唯一")

            check(all(len(i["infohash"]) == 40 for i in rows), "infohash 长度 40")
            check(any(i["filename"] == "lecture-e.720p.mkv" for i in rows),
                  "正则兜底提取生效（纯文本磁力）")

        txt = ROOT / "out" / "local_test" / "latest.txt"
        check(txt.exists() and len(txt.read_text(encoding="utf-8").strip().splitlines()) == 4,
              "latest.txt 输出 4 行")

        csv_file = ROOT / "out" / "local_test" / "latest.csv"
        check(csv_file.exists(), "生成 latest.csv")

        detail_dump = ROOT / "out" / "local_test" / "latest_detail.json"
        check(detail_dump.exists(), "生成 latest_detail.json（详情页 HTML 转存）")
        if detail_dump.exists():
            dump = json.loads(detail_dump.read_text(encoding="utf-8"))
            pages = dump["items"]
            # 两页共 7 次详情页抓取（第 2 页末条与第 1 页首条同页，会重复抓一次）
            check(dump["count"] == 7, f"详情页 HTML 转存 7 次抓取（实际 {dump['count']}）")
            check(len({p["source_url"] for p in pages}) == 6,
                  "覆盖 6 个不同详情页")
            check(all("<html>" in p["html"] for p in pages),
                  "每条记录都含完整详情页 HTML")
            check(all(p["source_url"].startswith("http://127.0.0.1:8765/detail-")
                      for p in pages), "详情页来源 URL 正确")
            check(all(p["magnet_count"] == 1 for p in pages),
                  "每页解析出 1 条磁力")

        print("5) 二次采集（验证去重与增量）")
        proc2 = run_harvest()
        check(proc2.returncode == 0, "二次采集退出码为 0")
        check("新增 0" in proc2.stdout, "二次采集新增 0 条（去重生效）")

        print("6) 关键词过滤")
        proc3 = run_harvest("--keyword", "4K", "--include-seen", "--dry-run")
        check("新增 2" in proc3.stdout or "新增 1" in proc3.stdout,
              f"关键词 4K 过滤生效（输出: {proc3.stdout.strip().splitlines()[-1] if proc3.stdout.strip() else '空'}）")

        print("7) --list-sites")
        proc4 = subprocess.run(
            [sys.executable, "harvest.py", "--list-sites"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        check(proc4.returncode == 0 and "_local_test" in proc4.stdout,
              "--list-sites 正常列出站点")

        print("8) 编码纠偏")
        # 日文样本只按 UTF-8 编码，不去碰 GBK（部分假名/符号 GBK 编不了）
        jp = "【VR】【8K】受付嬢の神対応"
        cn = "开源纪录片 VR 8K 2160p"
        utf8_page = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{jp}</title></head><body></body></html>"
        ).encode("utf-8")
        gbk_page = (
            '<!DOCTYPE html><html><head><meta charset="gbk">'
            f"<title>{cn}</title></head><body></body></html>"
        ).encode("gbk")

        text, _ = harvest.decode_html(utf8_page, preferred="gbk")
        check(jp in text, "UTF-8 页面配 gbk 时自动纠偏（不再出乱码）")

        text, used = harvest.decode_html(gbk_page, preferred="gbk")
        check(cn in text, f"GBK 页面按 gbk 正常解码（{used}）")

        text, _ = harvest.decode_html(gbk_page, preferred="")
        check(cn in text, "未配置编码时靠 meta 声明识别 GBK")

        text, _ = harvest.decode_html(utf8_page, preferred="")
        check(jp in text, "未配置编码时靠 meta 声明识别 UTF-8")

        no_meta = utf8_page.replace(b'<meta charset="utf-8">', b"")
        text, _ = harvest.decode_html(no_meta, preferred="gbk")
        check(jp in text, "无 meta 声明时靠乱码检测纠偏")

        check(not harvest.looks_mojibake(cn, "gbk"), "正常中文不会被误判成乱码")

        print("9) 多磁力分组与逐条体积")
        # 同一个详情页两行磁力：第一行的体积单元格里也是个 magnet 链接（同 infohash），
        # 第二行的体积是纯文本
        multi = """<table id="magnet-table">
          <tr style="font-weight:bold;"><td>名稱</td><td>檔案大小</td><td>分享日期</td></tr>
          <tr>
            <td width="70%%"><a href="magnet:?xt=urn:btih:6cd66b80f5327b733e15bf9c302ffdbe081c61d0&amp;dn=kiwvr-893">kiwvr-893</a><a class="btn">高清</a></td>
            <td width="20%%"><a style="color:#333" rel="nofollow" href="magnet:?xt=urn:btih:6cd66b80f5327b733e15bf9c302ffdbe081c61d0&amp;dn=kiwvr-893">16.10GB</a></td>
            <td width="10%%">2026-08-30</td>
          </tr>
          <tr>
            <td width="70%%"><a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&amp;dn=movie.2160p.mkv">movie.2160p.mkv</a></td>
            <td width="20%%">3.40GB</td>
            <td width="10%%">2026-08-29</td>
          </tr>
        </table>"""
        cfg = {
            "magnet_attr_selectors": ["a[href^='magnet:']"],
            "magnet_size_selector": "td:nth-of-type(2)",
        }
        entries = harvest.extract_magnet_entries(multi, cfg, None)
        check(len(entries) == 2,
              f"同一详情页 2 条磁力（重复 href 已按 infohash 去重，实际 {len(entries)}）")
        sizes = [e["size_gb"] for e in entries]
        check(sizes == [16.1, 3.4], f"逐条体积正确：{sizes}")
        names = [e["filename"] for e in entries]
        check(names == ["kiwvr-893", "movie.2160p.mkv"], f"文件名正确：{names}")
        check(entries[0]["extension"] == "" and entries[1]["extension"] == "mkv",
              f"扩展名按各自文件名识别：{[e['extension'] for e in entries]}")

        # 体积写在 <a> 文本里、且没有体积单元格选择器时也要能取到
        only_link = ('<table><tr><td><a href="magnet:?xt=urn:btih:'
                     '6cd66b80f5327b733e15bf9c302ffdbe081c61d0&amp;dn=kiwvr-893">'
                     '16.10GB</a></td></tr></table>')
        one = harvest.extract_magnet_entries(
            only_link, {"magnet_attr_selectors": ["a[href^='magnet:']"]}, None)
        check(len(one) == 1 and one[0]["size_gb"] == 16.1,
              "体积写在 <a> 文本里时也能取到")
        check(one[0]["filename"] == "kiwvr-893",
              f"体积文本不会被误当成文件名（实际 {one[0]['filename']!r}）")

        # 分组 -> 摊平
        group = {"site": "s", "title": "T", "source_url": "u",
                 "magnets": entries, "first_seen": "t"}
        check(harvest.total_magnets([group]) == 2, "total_magnets 统计磁力条数")
        rows = harvest.flatten_groups([group])
        check(len(rows) == 2 and all(r["title"] == "T" for r in rows),
              "flatten_groups 摊平成每行一条磁力并继承详情页标题")
        check(rows[0]["size_gb"] == 16.1 and rows[1]["size_gb"] == 3.4,
              "摊平后体积仍是各自的值")
    finally:
        httpd.shutdown()
        httpd.server_close()
        restore_state()

    print()
    if FAILURES:
        print(f"共 {len(FAILURES)} 项失败：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
