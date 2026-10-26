# -*- coding: utf-8 -*-
"""Minimal CDP helper: open a URL in headless Edge, evaluate JS, print the result.

Usage:
    python tests/cdp_eval.py <url> "<js-expression>" [wait_seconds]

The JS runs in the page context; return a JSON-serialisable value.
Useful for UI smoke checks without the flaky agent-browser daemon.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
PORT = 9224

import websockets


def find_edge():
    import os
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("no Edge/Chrome found")


def ws_url():
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2) as r:
                for t in json.load(r):
                    if t.get("type") == "page":
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("no CDP target")


async def main(url, expr, wait_s, shot=None):
    profile = tempfile.mkdtemp(prefix="mflow-cdp-")
    proc = subprocess.Popen([
        find_edge(), "--headless=new", "--disable-gpu", "--no-first-run",
        "--window-size=1440,1200",
        "--remote-debugging-port=%d" % PORT, "--user-data-dir=" + profile, url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with websockets.connect(ws_url(), max_size=16 * 1024 * 1024) as ws:
            n = {"i": 0}

            async def call(method, params=None):
                n["i"] += 1
                i = n["i"]
                await ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == i:
                        return msg

            await call("Runtime.enable")
            await asyncio.sleep(float(wait_s))
            r = await call("Runtime.evaluate", {
                "expression": expr, "returnByValue": True, "awaitPromise": True})
            res = r.get("result", {})
            if "exceptionDetails" in res:
                print("JS_ERROR:", str(res["exceptionDetails"])[:400])
                return 1
            print(json.dumps(res.get("result", {}).get("value"), ensure_ascii=False, indent=2))
            if shot:
                import base64
                shot_res = await call("Page.captureScreenshot", {"format": "png"})
                data = shot_res.get("result", {}).get("data")
                if data:
                    with open(shot, "wb") as f:
                        f.write(base64.b64decode(data))
                    print("SHOT_SAVED:", shot)
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2],
                              sys.argv[3] if len(sys.argv) > 3 else "6",
                              sys.argv[4] if len(sys.argv) > 4 else None)))
