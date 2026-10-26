# -*- coding: utf-8 -*-
"""外科手术式 YAML 编辑：只替换目标键所在行（及多行值块），其余字节级保留。

背景：ruamel 的整文档 round-trip 在「注释 + 空 flow 列表」等组合下存在
确定性再序列化 bug（0.19.1 会把下一个映射键挤到同一行导致 YAML 损坏）。
本模块只用 ruamel *定位*（行号/列号/注释归属），落盘文本由行拼接生成，
永远不会触发 ruamel 的 dump bug；写出的新值统一用 JSON 内联表示（JSON ⊂ YAML）。

对外接口：
    to_plain(obj)                     ruamel/Commented 结构 -> 纯 python
    diff_updates(old, new) -> dict    叶子差异 {dotted_path: new_value}
    apply_text_updates(text, ups)     行级替换（失败抛 SurgicalError）
    verify_values(text, ups) -> [..]  safe_load 后逐路径核对新值
"""
from __future__ import annotations

import json
from io import StringIO

import yaml
from ruamel.yaml import YAML

__all__ = ["SurgicalError", "to_plain", "diff_updates",
           "apply_text_updates", "verify_values"]


class SurgicalError(Exception):
    """无法安全定位/替换目标键，调用方应回退到其他写入策略。"""


# ------------------------------------------------------------------ 纯值工具
def to_plain(obj):
    """CommentedMap/Seq/ScalarStr -> dict/list/scalar（纯 python 可 JSON 化）。"""
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, (int, float, str)):
        return obj if isinstance(obj, (bool, int, float)) else str(obj)
    return str(obj)  # date/Decimal 等：转字符串兜底


def diff_updates(a, b, prefix: str = "") -> dict:
    """返回 b 相对 a 的叶子差异；dict 递归下钻，list/标量作为叶子整体比较。"""
    ups: dict = {}
    if isinstance(a, dict) and isinstance(b, dict):
        for k, v in b.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if k not in a:
                ups[path] = v
            else:
                ups.update(diff_updates(a[k], v, path))
        return ups
    if prefix and a != b:
        ups[prefix] = b
    return ups


# ------------------------------------------------------------------ 行工具
class _DocLines:
    def __init__(self, text: str):
        self.lines = text.splitlines(keepends=True)
        self.ends_lf = text.endswith("\n")

    def render(self) -> str:
        out = "".join(self.lines)
        if self.lines and self.ends_lf and not out.endswith("\n"):
            out += "\n"
        return out


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _is_blank_or_comment(raw: str) -> bool:
    s = raw.strip()
    return (not s) or s.startswith("#")


def _emit_val(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise SurgicalError(f"值无法内联编码：{value!r} ({exc})") from exc


# ------------------------------------------------------------------ 定位
def _load_for_pos(text: str):
    y = YAML(typ="rt")
    y.preserve_quotes = True
    doc = y.load(StringIO(text))
    if doc is None:
        raise SurgicalError("文件为空")
    if not isinstance(doc, dict):
        raise SurgicalError("顶层不是映射")
    return doc


def _unpack_lc(info):
    """兼容 ruamel 各版本 lc.data 结构 -> (key_line, key_col, val_line, val_col)。"""
    if isinstance(info, dict) and "key" in info:
        kp, vp = info["key"], info["value"]
        return kp[0], kp[1], vp[0], vp[1]
    if isinstance(info, (list, tuple)):
        if len(info) == 2 and isinstance(info[0], (list, tuple)):
            (kl, kc), (vl, vc) = info[0], info[1]
            return kl, kc, vl, vc
        if len(info) >= 4 and not isinstance(info[0], (list, tuple)):
            return info[0], info[1], info[2], info[3]
    kl = getattr(info, "key_line", None)
    if kl is not None:
        return kl, getattr(info, "key_col", 0), \
            getattr(info, "value_line", kl), getattr(info, "value_col", 0)
    raise SurgicalError("无法解析 ruamel 位置信息")


def _key_pos_map(parent) -> dict:
    lc = getattr(parent, "lc", None)
    data = getattr(lc, "data", None) if lc is not None else None
    if not data:
        raise SurgicalError("节缺少位置信息（可能整体是 flow 写法）")
    out = {}
    for k, info in data.items():
        try:
            out[str(k)] = _unpack_lc(info)
        except SurgicalError:
            continue
    return out


def _flow_depth(text: str) -> int:
    depth, in_str, quote, esc = 0, False, "", False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str, quote = True, ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                return 0
    return depth


def _tok_line_col(tok):
    ln = getattr(tok, "line", None)
    col = getattr(tok, "col", None)
    if ln is None:
        m = getattr(tok, "start_mark", None)
        if m is not None:
            ln, col = m.line, m.column
    return ln, col


def _eol_comment(parent, key: str) -> str | None:
    """该键所在行的行尾注释文本（不含 #）；跨行注释块只取第一行。"""
    ca = getattr(parent, "ca", None)
    items = getattr(ca, "items", None) if ca is not None else None
    if not items:
        return None
    entry = items.get(key)
    if not entry:
        return None

    def from_tok(tok):
        if tok is None:
            return None
        val = (tok.value or "").strip()
        if not val or not val.startswith("#"):
            return None
        return val.splitlines()[0].lstrip("#").strip() or None

    if len(entry) > 1:
        got = from_tok(entry[1])
        if got:
            return got
    for i in (2, 3):
        if len(entry) > i and entry[i] is not None:
            tok = entry[i]
            ln, _col = _tok_line_col(tok)
            if ln is not None:      # 锚定在键行（同一条注释流）才当作行尾注释
                got = from_tok(tok)
                if got:
                    return got
    return None


# ------------------------------------------------------------------ 替换
def _span_of(kmap: dict, key: str, dl: _DocLines):
    """返回 (mode, key_line, val_line, last_line)。

    mode="same"   值与键同行（单行，或多行 flow）
    mode="block"  值从 val_line 起多行（块列表/块映射/多行标量）
    多行跨度截止于下一个兄弟键行的前一行（并回吐尾部空行/注释行）。
    """
    kl, kc, vl, vc = kmap[key]
    line = dl.lines[kl].rstrip("\r\n") if kl < len(dl.lines) else ""
    colon = line.find(":", kc)
    rest = line[colon + 1:] if colon >= 0 else ""
    stripped = rest.lstrip(" \t")
    if stripped and not stripped.startswith("#"):
        # 值（可能多行 flow）从键行冒号后开始
        depth = _flow_depth(stripped)
        if depth <= 0:
            return "same", kl, kl, kl
        last, total = kl, depth
        i = kl + 1
        while i < len(dl.lines) and i < kl + 80:
            last = i
            total += _flow_depth(dl.lines[i].rstrip("\r\n"))
            if total <= 0:
                break
            i += 1
        return "same", kl, kl, last
    if vl > kl:
        pass
    else:
        return "same", kl, kl, kl          # 空值（null）占键行整行
    siblings = [pos[0] for k2, pos in kmap.items() if pos[0] > kl]
    limit = min(siblings) if siblings else len(dl.lines)
    last = vl
    for i in range(vl + 1, limit):
        raw = dl.lines[i].rstrip("\r\n")
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        ind = _indent_of(raw)
        if ind > kc or (ind == kc and (s == "-" or s.startswith("- "))):
            last = i
        else:
            break
    return "block", kl, vl, last


def _has_path(data, parts: list[str]) -> bool:
    for part in parts[:-1]:
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return False
    return isinstance(data, dict) and parts[-1] in data


def _safe_get(data, parts: list[str]):
    node = data
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _walk(doc, doc_safe, parts: list[str]):
    """定位键：用 ruamel rt 文档（带行号）；rt 静默丢键时用 safe_load 结构判别。"""
    node = doc
    for i, part in enumerate(parts[:-1]):
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        if _has_path(doc_safe, parts):
            raise SurgicalError(
                f"{'.'.join(parts)}：ruamel 解析丢失了节点，无法定位行")
        raise SurgicalError(f"缺少中间层级：{'.'.join(parts[:i + 1])}")
    if not isinstance(node, dict):
        raise SurgicalError("路径终点父节点不是映射")
    return node, parts[-1]


def _apply_one(dl: _DocLines, doc_root, doc_safe, dotted: str, value) -> None:
    parts = dotted.split(".")
    parent, key = _walk(doc_root, doc_safe, parts)
    kmap = _key_pos_map(parent)
    val_str = _emit_val(value)
    comment = _eol_comment(parent, key)
    tail = f"  # {comment}" if comment else ""

    if key not in parent:                     # 追加到本节最后一个键之后
        if not parent:
            raise SurgicalError(f"节 {dotted} 为空，无法确定缩进")
        last_key = str(list(parent.keys())[-1])
        if last_key not in kmap:
            raise SurgicalError(f"锚点键 {last_key} 无位置信息")
        mode, kl, vl, last = _span_of(kmap, last_key, dl)
        indent = " " * kmap[last_key][1]
        new_line = f"{indent}{key}: {val_str}{tail}\n"
        dl.lines.insert(last + 1, new_line)
        return

    if key not in kmap:
        raise SurgicalError(f"{dotted} 键无位置信息")
    kl, kc, vl, _vc = kmap[key]
    if kl >= len(dl.lines):
        raise SurgicalError(f"{dotted} 行号越界")
    line = dl.lines[kl].rstrip("\r\n")
    colon = line.find(":", kc)
    if colon < 0:
        raise SurgicalError(f"{dotted} 找不到冒号")
    prefix = line[:colon + 1]
    mode, kl2, vl2, last = _span_of(kmap, key, dl)
    new_line = f"{prefix} {val_str}{tail}\n"
    if mode == "same":
        dl.lines[kl:last + 1] = [new_line]
    else:
        dl.lines[kl] = new_line
        del dl.lines[vl:last + 1]


def apply_text_updates(text: str, updates: dict, base_order=None) -> str:
    """按 updates（{dotted_path: value}）对 text 做行级替换。

    每替换一个路径就重新解析一次定位，行号永不漂移；每次替换后立即
    yaml.safe_load 预检。任何一步失败抛 SurgicalError（调用方落回原策略）。
    """
    if not updates:
        return text
    order = sorted(updates, key=lambda p: len(p.split(".")))
    dl = _DocLines(text)
    doc0 = _load_for_pos(text)
    try:
        safe0 = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SurgicalError(f"源文本本身无法解析：{exc}") from exc
    safe0 = safe0 if isinstance(safe0, dict) else {}
    for dotted in order:                      # 预检路径可达性
        try:
            _walk(doc0, safe0, dotted.split("."))
        except SurgicalError as exc:
            raise SurgicalError(f"{dotted}: {exc}") from exc
    for dotted in order:
        try:
            cur = dl.render()
            dl_now = _DocLines(cur)
            _apply_one(dl_now, _load_for_pos(cur),
                       yaml.safe_load(cur) or {}, dotted, updates[dotted])
            candidate = dl_now.render()
            yaml.safe_load(candidate)
        except (SurgicalError, yaml.YAMLError, IndexError, ValueError) as exc:
            raise SurgicalError(f"{dotted}: {exc}") from exc
        dl = dl_now
    return dl.render()


def verify_values(text: str, updates: dict) -> list[str]:
    """safe_load 校验写后的值与预期一致，返回不一致的路径列表。"""
    data = yaml.safe_load(text)
    bad = []
    for dotted, want in updates.items():
        node, ok = data, True
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok:
            if isinstance(want, float) and isinstance(node, (int, float)):
                ok = abs(float(node) - want) < 1e-9
            elif isinstance(want, list) and isinstance(node, list):
                ok = [to_plain(x) for x in node] == to_plain(want)
            else:
                ok = node == want
        if not ok:
            bad.append(dotted)
    return bad
