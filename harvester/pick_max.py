"""从 harvest.py 的 latest.json 里挑「每个视频体积最大的一条磁力」。

用法（在项目根目录）：
    python pick_max.py                          # 默认读 out/example/latest.json，全部视频
    python pick_max.py --site example -n 10     # 读 out/example/latest.json，只取前 10 个视频
    python pick_max.py --in path/x.json --out-prefix out/example/top

输出（默认写进输入文件同目录，文件名加 -max 后缀）：
    xxx-max.json  分组结构不变，每个视频的 magnets 数组只留体积最大的一条
    xxx-max.csv   一行一条磁力：title, filename, size_gb, magnet, source_url
    xxx-max.txt   纯磁力，一行一条
排序按体积从大到小。体积未知(None)的条目排在最后。
"""
import argparse
import csv
import json
from pathlib import Path


def pick_max(groups: list[dict]) -> list[dict]:
    out = []
    for g in groups:
        magnets = g.get("magnets") or []
        if not magnets:
            continue
        # None 体积当 -1 排最后；同体积时保留原顺序里靠前的
        best = max(magnets, key=lambda m: (m.get("size_gb") if m.get("size_gb") is not None else -1))
        item = {k: v for k, v in g.items() if k != "magnets"}
        item["magnet_count"] = 1
        item["variants"] = len(magnets)  # 原视频共有几条磁力
        item["magnets"] = [best]
        out.append(item)
    # 按最大体积降序排（方便 top N 截断）
    out.sort(key=lambda g: g["magnets"][0].get("size_gb") or -1, reverse=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="每视频只保留体积最大的一条磁力")
    ap.add_argument("--site", default="example", help="站点名（配合默认输入输出路径）")
    ap.add_argument("--in", dest="infile", default="", help="输入 JSON，默认 out/<site>/latest.json")
    ap.add_argument("--out-prefix", default="", help="输出文件名前缀，默认输入同目录 <stem>-max")
    ap.add_argument("-n", "--top", type=int, default=0, help="只保留体积最大的前 N 个视频（0 = 全部）")
    args = ap.parse_args()

    infile = Path(args.infile) if args.infile else Path(f"out/{args.site}/latest.json")
    data = json.loads(infile.read_text(encoding="utf-8"))
    groups = data.get("items") or []

    picked = pick_max(groups)
    if args.top > 0:
        picked = picked[: args.top]

    stem = Path(args.out_prefix) if args.out_prefix else infile.with_name(infile.stem + "-max")
    prefix_dir = Path(str(stem) + "-x").parent  # 保证目录存在
    prefix_dir.mkdir(parents=True, exist_ok=True)
    js, cs, tx = Path(str(stem) + ".json"), Path(str(stem) + ".csv"), Path(str(stem) + ".txt")

    total_magnets = sum(g["magnet_count"] for g in picked)
    payload = {"site": data.get("site"), "count": len(picked), "magnet_count": total_magnets,
               "rule": "per-video max size", "items": picked}
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with cs.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["title", "filename", "size_gb", "variants", "magnet", "source_url"])
        for g in picked:
            m = g["magnets"][0]
            w.writerow([g.get("title", ""), m.get("filename", ""), m.get("size_gb"),
                        g["variants"], m.get("magnet", ""), g.get("source_url", "")])

    tx.write_text("\n".join(g["magnets"][0]["magnet"] for g in picked) + "\n", encoding="utf-8")

    print(f"视频 {len(picked)} 个（原磁力共 {sum(len(g.get('magnets') or []) for g in groups)} 条）")
    for p in (js, cs, tx):
        print(" ->", p)


if __name__ == "__main__":
    main()
