# -*- coding: utf-8 -*-
"""
WebDAV 客户端。

只依赖 requests，实现了流水线真正需要的四个动作：
  PROPFIND  列目录（递归扫描片库）
  GET       下载（支持 Range 断点续传，用于「整片下载」兜底策略）
  PUT       上传（回写 .zh.srt）
  MKCOL     建目录（上传前确保父目录存在）

认证支持 auto / basic / digest / none。auto 模式下先按 basic 试，
遇到 401 且服务端提供 digest 挑战时自动切换。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote, unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import WebDAVConfig
from ..errors import WebDAVAuthError, WebDAVError

log = logging.getLogger("rawsub.webdav")

_PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontentlength/>
    <d:getcontenttype/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>
"""

DAV_NS = "{DAV:}"
ProgressCb = Callable[[int, int], None]


@dataclass
class DavResource:
    """一次 PROPFIND 返回的条目。"""

    path: str  # 相对 WebDAV 根的绝对路径，已 URL 解码，如 /电影/a.mkv
    name: str
    is_dir: bool
    size: int = 0
    content_type: str | None = None
    last_modified: str | None = None

    @property
    def parent(self) -> str:
        return self.path.rsplit("/", 1)[0] or "/"

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    def __repr__(self) -> str:  # pragma: no cover
        kind = "DIR " if self.is_dir else "FILE"
        return f"<{kind} {self.path} {self.size}>"


class WebDAVClient:
    def __init__(self, cfg: WebDAVConfig):
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self._base_path = urlsplit(self.base_url).path.rstrip("/") or ""
        self._session = self._build_session()
        self._auth_mode = cfg.auth

    # -------------------------------------------------------------- 基础设施

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.cfg.max_retries,
            connect=self.cfg.max_retries,
            read=self.cfg.max_retries,
            status=self.cfg.max_retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(
                {"GET", "PUT", "PROPFIND", "HEAD", "MKCOL", "DELETE"}
            ),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.verify = self.cfg.verify_ssl
        if self.cfg.auth != "none":
            session.auth = self._make_auth(self.cfg.auth)
        return session

    def _make_auth(self, mode: str):
        user, pwd = self.cfg.username, self.cfg.password
        if mode == "digest":
            from requests.auth import HTTPDigestAuth

            return HTTPDigestAuth(user, pwd)
        from requests.auth import HTTPBasicAuth

        return HTTPBasicAuth(user, pwd)

    def url(self, remote_path: str) -> str:
        """把远端路径（可能含中文/空格）拼成完整 URL。"""
        if not remote_path.startswith("/"):
            remote_path = "/" + remote_path
        encoded = "/".join(quote(seg, safe="~()*!.'-_") for seg in remote_path.split("/"))
        return f"{self.base_url}{encoded}"

    def _decode_href(self, href: str) -> str:
        """把响应里的 href 还原成「相对 WebDAV 根」的路径。"""
        href = unquote(href)
        # 情况一：绝对路径 /dav/电影/a.mkv
        if self._base_path and href.startswith(self._base_path + "/"):
            href = href[len(self._base_path) :]
        elif self._base_path and href == self._base_path:
            href = "/"
        # 情况二：完整 URL http://host:5244/dav/电影/a.mkv
        elif href.startswith(self.base_url):
            href = href[len(self.base_url) :]
        if not href.startswith("/"):
            href = "/" + href
        return href

    def _request(self, method: str, remote_path: str, **kwargs) -> requests.Response:
        url = self.url(remote_path)
        kwargs.setdefault("timeout", self.cfg.timeout)
        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise WebDAVError(f"{method} {remote_path} 网络异常：{exc}", url=url) from exc

        # auto 模式：被 401 且服务端要求 digest 时切换
        if resp.status_code == 401 and self._auth_mode == "auto":
            challenge = (resp.headers.get("WWW-Authenticate") or "").lower()
            if "digest" in challenge:
                log.info("服务端要求 Digest 认证，自动切换")
                self._auth_mode = "digest"
                self._session.auth = self._make_auth("digest")
                try:
                    resp = self._session.request(method, url, **kwargs)
                except requests.RequestException as exc:
                    raise WebDAVError(f"{method} {remote_path} 网络异常：{exc}", url=url) from exc

        if resp.status_code in (401, 403):
            raise WebDAVAuthError(
                f"{method} {remote_path} 认证/权限失败（HTTP {resp.status_code}）。"
                "请检查 webdav.username / password，以及该账号对目标目录的读写权限。",
                status_code=resp.status_code,
                url=url,
            )
        return resp

    # -------------------------------------------------------------- 列目录

    def propfind(self, remote_path: str = "/", depth: int = 1) -> list[DavResource]:
        import xml.etree.ElementTree as ET

        resp = self._request(
            "PROPFIND",
            remote_path,
            headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
            data=_PROPFIND_BODY.encode("utf-8"),
        )
        if resp.status_code == 404:
            raise WebDAVError(f"目录不存在：{remote_path}", status_code=404, url=resp.url)
        if resp.status_code not in (207, 200):
            raise WebDAVError(
                f"PROPFIND {remote_path} 失败：HTTP {resp.status_code}",
                status_code=resp.status_code,
                url=resp.url,
            )

        text = resp.content.decode("utf-8", errors="replace")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise WebDAVError(f"PROPFIND 响应不是合法 XML：{exc}", url=resp.url) from exc

        results: list[DavResource] = []
        for response_el in root.findall(f"{DAV_NS}response"):
            href_el = response_el.find(f"{DAV_NS}href")
            if href_el is None or not href_el.text:
                continue
            path = self._decode_href(href_el.text).rstrip("/") or "/"

            is_dir = False
            size = 0
            content_type = None
            last_modified = None
            display_name = None

            for propstat in response_el.findall(f"{DAV_NS}propstat"):
                prop = propstat.find(f"{DAV_NS}prop")
                if prop is None:
                    continue
                res_type = prop.find(f"{DAV_NS}resourcetype")
                if res_type is not None and res_type.find(f"{DAV_NS}collection") is not None:
                    is_dir = True
                length = prop.find(f"{DAV_NS}getcontentlength")
                if length is not None and length.text:
                    try:
                        size = int(length.text)
                    except ValueError:
                        size = 0
                ctype = prop.find(f"{DAV_NS}getcontenttype")
                if ctype is not None and ctype.text:
                    content_type = ctype.text
                lm = prop.find(f"{DAV_NS}getlastmodified")
                if lm is not None and lm.text:
                    last_modified = lm.text
                dn = prop.find(f"{DAV_NS}displayname")
                if dn is not None and dn.text:
                    display_name = dn.text

            name = display_name or (path.rsplit("/", 1)[-1] or "/")
            name = unquote(name)
            results.append(
                DavResource(
                    path=path,
                    name=name,
                    is_dir=is_dir,
                    size=size,
                    content_type=content_type,
                    last_modified=last_modified,
                )
            )
        return results

    def listdir(self, remote_path: str = "/") -> list[DavResource]:
        """列一层目录，自动排除自身。"""
        items = self.propfind(remote_path, depth=1)
        target = ("/" + remote_path.strip("/")).rstrip("/") or "/"
        return [i for i in items if i.path.rstrip("/") != target]

    def exists(self, remote_path: str) -> bool:
        resp = self._request("PROPFIND", remote_path, headers={"Depth": "0"},
                             data=_PROPFIND_BODY.encode("utf-8"))
        if resp.status_code in (200, 207):
            return True
        if resp.status_code == 404:
            return False
        raise WebDAVError(
            f"exists({remote_path}) 失败：HTTP {resp.status_code}",
            status_code=resp.status_code,
            url=resp.url,
        )

    def iter_tree(self, remote_path: str = "/", max_depth: int = 8,
                  exclude_dirs: tuple[str, ...] = ()) -> Iterator[DavResource]:
        """深度优先遍历目录树，产出所有非目录条目。"""
        excl = {d.lower() for d in exclude_dirs}
        stack: list[tuple[str, int]] = [(remote_path, 0)]
        seen: set[str] = set()
        while stack:
            current, depth = stack.pop()
            key = current.rstrip("/") or "/"
            if key in seen:
                continue
            seen.add(key)
            try:
                entries = self.listdir(current)
            except WebDAVError as exc:
                log.warning("列目录失败，跳过：%s（%s）", current, exc)
                continue
            for entry in entries:
                if entry.is_dir:
                    if entry.name.lower() in excl:
                        continue
                    if depth + 1 < max_depth:
                        stack.append((entry.path, depth + 1))
                else:
                    yield entry

    # -------------------------------------------------------------- 下载

    def download(
        self,
        remote_path: str,
        dest: Path,
        progress: ProgressCb | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Path:
        """流式下载到 dest，支持断点续传。返回实际文件路径。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        resume = dest.stat().st_size if dest.exists() else 0
        headers = {"Range": f"bytes={resume}-"} if resume else {}
        resp = self._request("GET", remote_path, headers=headers, stream=True)
        if resp.status_code == 416:  # Range 越界，说明本地文件已完整
            return dest
        if resp.status_code not in (200, 206):
            raise WebDAVError(
                f"GET {remote_path} 失败：HTTP {resp.status_code}",
                status_code=resp.status_code,
                url=resp.url,
            )

        # 服务端忽略了 Range 请求时不能续写，否则文件会变成两份拼接
        if resp.status_code == 200:
            resume = 0
        mode = "ab" if resume else "wb"
        total = int(resp.headers.get("Content-Length", 0) or 0) + resume
        done = resume
        with dest.open(mode) as fh:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        return dest

    # -------------------------------------------------------------- 上传

    def put(self, remote_path: str, data: bytes | str, content_type: str = "text/plain; charset=utf-8") -> None:
        """上传文件。路径父目录不存在时自动建。"""
        if isinstance(data, str):
            data = data.encode("utf-8")
        resp = self._request(
            "PUT", remote_path, data=data, headers={"Content-Type": content_type}
        )
        if resp.status_code == 409:  # 父目录不存在
            parent = remote_path.rsplit("/", 1)[0] or "/"
            log.debug("父目录缺失，尝试创建：%s", parent)
            self._request("MKCOL", parent)
            resp = self._request(
                "PUT", remote_path, data=data, headers={"Content-Type": content_type}
            )
        if resp.status_code not in (200, 201, 204):
            raise WebDAVError(
                f"PUT {remote_path} 失败：HTTP {resp.status_code}（115 可能触发了上传风控，稍后重试）",
                status_code=resp.status_code,
                url=resp.url,
            )
        log.debug("已上传 %s（%d 字节）", remote_path, len(data))

    def healthcheck(self) -> bool:
        """探活：能对根目录 PROPFIND 通就认为可用。

        用独立的短超时请求，不走上面的重试适配器——探活失败时
        没必要再自动重试三轮，把报错时间拖长。
        """
        try:
            resp = requests.request(
                "PROPFIND",
                self.url("/"),
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
                data=_PROPFIND_BODY.encode("utf-8"),
                auth=self._session.auth,
                timeout=min(self.cfg.timeout, 15.0),
                verify=self.cfg.verify_ssl,
            )
            return resp.status_code in (200, 207)
        except requests.RequestException as exc:
            log.debug("WebDAV 探活失败：%s", exc)
            return False

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "WebDAVClient":  # pragma: no cover
        return self

    def __exit__(self, *exc) -> None:  # pragma: no cover
        self.close()
