# -*- coding: utf-8 -*-
"""跑完之后的推送通知（可选）。支持 Server酱 与 Bark。"""
from __future__ import annotations

import logging
from typing import Sequence

import requests

from .config import NotifyConfig

log = logging.getLogger("rawsub.notify")


def send(cfg: NotifyConfig, title: str, body: str) -> None:
    """按配置推送。任何渠道失败都只记日志，不影响主流程。"""
    if not cfg.enabled:
        return

    sent = False
    if cfg.serverchan_key:
        sent |= _serverchan(cfg.serverchan_key, title, body, cfg.timeout)
    if cfg.bark_url:
        sent |= _bark(cfg.bark_url, title, body, cfg.timeout)

    if not sent:
        log.warning("通知已开启但没有配置任何可用渠道（serverchan_key / bark_url）")


def _serverchan(key: str, title: str, body: str, timeout: float) -> bool:
    url = f"https://sctapi.ftqq.com/{key}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": body}, timeout=timeout)
        ok = resp.status_code == 200
        if not ok:
            log.warning("Server酱推送失败：HTTP %s %s", resp.status_code, resp.text[:200])
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("Server酱推送异常：%s", exc)
        return False


def _bark(base_url: str, title: str, body: str, timeout: float) -> bool:
    import urllib.parse

    url = f"{base_url.rstrip('/')}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("Bark 推送异常：%s", exc)
        return False


def format_summary(title: str, lines: Sequence[str]) -> str:
    return "\n\n".join([f"## {title}", *lines])
