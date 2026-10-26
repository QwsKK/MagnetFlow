# -*- coding: utf-8 -*-
"""一次性脚本：列出并删除 115 上残缺的 .zh.srt（重跑前清障）。用项目自带客户端。"""
import sys

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, r"D:\Agent\115Translate")

import requests

from rawsub.config import load_config
from rawsub.webdav.client import WebDAVClient

cfg = load_config()
client = WebDAVClient(cfg.webdav)
root = cfg.webdav.root

targets = []


def walk(path, depth=0):
    if depth > 3:
        return
    try:
        items = client.listdir(path)
    except Exception as exc:
        print("listdir 失败:", path, exc)
        return
    for it in items:
        if it.name.lower().endswith(".zh.srt"):
            targets.append(it.path)
        elif it.is_dir:
            walk(it.path, depth + 1)


print("扫描", root)
walk(root)
print("找到字幕:")
for t in targets:
    print("  ", t)

for t in targets:
    resp = client._request("DELETE", t)
    print("DELETE", t, "->", resp.status_code)
