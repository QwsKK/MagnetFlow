# -*- coding: utf-8 -*-
"""生成冒烟测试用的本地静态站点（模拟一个典型 GBK 资源站结构）。"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
WWWROOT = HERE / "fixtures" / "site"

LIST_TPL = """<!DOCTYPE html>
<html><head><meta charset="gbk"><title>资源列表 第{page}页</title></head>
<body>
<div class="vod-list">
{items}
</div>
</body></html>
"""

LIST_ITEM = '  <div class="item"><a href="{href}" title="{title}">{title}</a></div>'

DETAIL_TPL = """<!DOCTYPE html>
<html><head><meta charset="gbk"><title>{title}</title></head>
<body>
<h1 class="title">{title}</h1>
<div class="info">体积：{size}</div>
<div class="download-box">
{links}
</div>
</body></html>
"""

DETAIL_TEXT_ONLY_TPL = """<!DOCTYPE html>
<html><head><meta charset="gbk"><title>{title}</title></head>
<body>
<h1 class="title">{title}</h1>
<div class="info">体积：{size}</div>
<div class="download-box">
  <p>磁力链接（请复制）：{magnet}</p>
</div>
</body></html>
"""


def fake_hash(seed: str, length: int = 40) -> str:
    base = "0123456789abcdef"
    out = []
    for i, ch in enumerate(seed):
        out.append(base[(ord(ch) + i * 7) % 16])
    while len(out) < length:
        out.append(base[len(out) % 16])
    return "".join(out[:length])


def magnet_for(seed: str, filename: str) -> str:
    return f"magnet:?xt=urn:btih:{fake_hash(seed)}&dn={filename}"


# (详情页文件名, 标题, 体积, 种子文件名, 是否用纯文本磁力)
ENTRIES = [
    ("detail-1.html", "开源纪录片 A 4K 2160p", "12.30GB", "doc-a.4k.mkv", False),
    ("detail-2.html", "开源纪录片 B 1080p", "3.40GB", "doc-b.1080p.mp4", False),
    ("detail-3.html", "开源纪录片 C 预告片", "0.30GB", "doc-c.trailer.mkv", False),
    ("detail-4.html", "开源课程 D 1080p", "8.10GB", "course-d.exe", False),
    ("detail-5.html", "开源讲座 E 720p", "1.20GB", "lecture-e.720p.mkv", True),
    ("detail-6.html", "开源素材集 F 4K", "0.80GB", "footage-f.4k.mov", False),
]

PAGE1 = ENTRIES[:4]
PAGE2 = ENTRIES[4:] + [ENTRIES[0]]  # 末条与第 1 页重复，用于验证去重


def build() -> None:
    WWWROOT.mkdir(parents=True, exist_ok=True)
    for old in WWWROOT.glob("*.html"):
        old.unlink()

    for page, entries in ((1, PAGE1), (2, PAGE2)):
        items = "\n".join(
            LIST_ITEM.format(href=href, title=title) for href, title, *_ in entries
        )
        (WWWROOT / f"list-{page}.html").write_text(
            LIST_TPL.format(page=page, items=items), encoding="gbk"
        )

    for href, title, size, filename, text_only in ENTRIES:
        magnet = magnet_for(href, filename)
        if text_only:
            html = DETAIL_TEXT_ONLY_TPL.format(title=title, size=size, magnet=magnet)
        else:
            html = DETAIL_TPL.format(
                title=title, size=size,
                links=f'  <a href="{magnet}">磁力下载</a>\n'
                      f'  <a href="http://example.com/other">网盘</a>',
            )
        (WWWROOT / href).write_text(html, encoding="gbk")

    print(f"已生成测试站点: {WWWROOT}")
    print(f"  列表页 {2} 个，详情页 {len(ENTRIES)} 个")
    print("  预期：6 条磁力，跨页重复 1 条，"
          "预告片被排除 1 条，.exe 被排除 1 条 -> 新增 4 条")


if __name__ == "__main__":
    build()
