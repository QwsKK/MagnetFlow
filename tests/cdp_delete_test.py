# -*- coding: utf-8 -*-
"""Drive headless Edge over CDP to test the library delete button end-to-end.

ASCII-only source except the Chinese selectors (file is utf-8 with header).
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = "http://127.0.0.1:8420/#/library"
PORT = 9223

import websockets


def get_ws_url():
    for _ in range(40):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2) as r:
                targets = json.load(r)
            for t in targets:
                if t.get("type") == "page" and "127.0.0.1:8420" in t.get("url", ""):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    return None


async def main():
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
        "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=%TEMP%\\mflow-cdp-test" if False else
        "--user-data-dir=" + (__import__("tempfile").gettempdir() + "\\mflow-cdp-test"),
        URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = get_ws_url()
        if not ws_url:
            print("FAIL: no CDP target")
            return 1
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            mid = {"n": 0}

            async def call(method, params=None):
                mid["n"] += 1
                m = mid["n"]
                await ws.send(json.dumps({"id": m, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == m:
                        return msg

            async def js(expr):
                r = await call("Runtime.evaluate", {
                    "expression": expr, "returnByValue": True, "awaitPromise": True})
                res = r.get("result", {}).get("result", {})
                if "exceptionDetails" in r.get("result", {}):
                    return {"error": str(r["result"]["exceptionDetails"])[:200]}
                return res.get("value")

            await call("Page.enable")
            # wait until rows rendered
            for _ in range(40):
                n = await js("document.querySelectorAll('.grid-table tbody tr').length")
                if isinstance(n, int) and n > 0:
                    break
                await asyncio.sleep(0.3)
            print("rows:", n)

            before = await js("""(() => {
                const tr = [...document.querySelectorAll('.grid-table tbody tr')]
                  .find(r => r.textContent.includes('_ui_test'));
                return tr ? 'found' : 'not-found';
            })()""")
            print("test row:", before)

            clicked = await js("""(() => {
                const tr = [...document.querySelectorAll('.grid-table tbody tr')]
                  .find(r => r.textContent.includes('_ui_test'));
                if (!tr) return 'no-row';
                const b = tr.querySelector('.cell-act .iconbtn');
                if (!b) return 'no-btn';
                b.click();
                return 'clicked';
            })()""")
            print("click:", clicked)
            await asyncio.sleep(0.6)

            modal = await js("""(() => {
                const root = document.querySelector('#modal-root');
                if (!root || root.classList.contains('hidden')) return 'no-modal';
                const t = document.querySelector('.modal-title');
                const detail = document.querySelector('.modal-list');
                return JSON.stringify({title: t && t.textContent,
                                       detail: detail && detail.textContent.slice(0, 60)});
            })()""")
            print("modal:", modal)

            confirmed = await js("""(() => {
                const btns = [...document.querySelectorAll('#modal-box .btn')];
                const ok = btns.find(b => b.textContent.trim() === '删除');
                if (!ok) return 'no-ok-btn:' + btns.map(b=>b.textContent.trim()).join('|');
                ok.click();
                return 'confirmed';
            })()""")
            print("confirm:", confirmed)
            await asyncio.sleep(2.0)

            after = await js("""(() => {
                const rows = [...document.querySelectorAll('.grid-table tbody tr')];
                const toast = document.querySelector('.toast .t-msg');
                const info = document.querySelector('.pager-info');
                return JSON.stringify({
                  stillThere: rows.some(r => r.textContent.includes('_ui_test')),
                  rows: rows.length,
                  toast: toast && toast.textContent,
                  pager: info && info.textContent
                });
            })()""")
            print("after:", after)
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
