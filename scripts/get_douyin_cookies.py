#!/usr/bin/env python3
"""Extract douyin cookies from a running CDP browser -> Netscape format.

Usage: python3 get_douyin_cookies.py [id] [--cdp URL] [--out PATH] [--json]
Output: <tmpdir>/dy_cookies_netscape.txt (0600); env DOUYIN_COOKIE_FILE / --out to change.

Reuses an existing douyin tab if present; otherwise opens the id page or homepage.
Never wipes cookies, never starts a browser process (CDP is owned by the user or
by OpenClaw's browserctl service).

Importable API: fetch_cookies(cdp=..., vid=...) -> list[dict]
               netscape_lines(cookies) -> list[str]
               write_netscape(cookies, path) -> path
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.request

import websockets

DEFAULT_CDP = os.environ.get("DOUYIN_CDP", "http://127.0.0.1:18800")
DEFAULT_OUT = os.path.abspath(os.path.expanduser(os.environ.get(
    "DOUYIN_COOKIE_FILE",
    os.path.join(tempfile.gettempdir(), "dy_cookies_netscape.txt"))))
COOKIE_URLS = ["https://www.douyin.com"]


def _http_json(cdp, path, method="GET"):
    req = urllib.request.Request(f"{cdp}{path}", method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def open_tab(cdp, url):
    return _http_json(cdp, "/json/new?" + url, method="PUT")


def close_tab(cdp, target_id):
    try:
        urllib.request.urlopen(f"{cdp}/json/close/{target_id}", timeout=5).read()
    except Exception:
        pass


async def _get_cookies(ws_url):
    async with websockets.connect(ws_url, max_size=50_000_000) as w:
        await w.send(json.dumps({"id": 1, "method": "Network.getCookies",
                                 "params": {"urls": COOKIE_URLS}}))
        while True:
            r = json.loads(await w.recv())
            if r.get("id") == 1:
                return r.get("result", {}).get("cookies", [])


def fetch_cookies(cdp=DEFAULT_CDP, vid=None, wait_video=9.0, wait_home=8.0,
                  wait_existing=1.0):
    """One-shot: find/open a douyin tab, read cookies, close only our own tab."""
    tabs = [t for t in _http_json(cdp, "/json") if t.get("type") == "page"]
    tab = next((t for t in tabs if "douyin.com" in t.get("url", "")), None)
    opened = None
    if tab is None:
        kind = "video" if (vid or "").isdigit() and len(vid or "") > 15 else "note"
        url = f"https://www.douyin.com/{kind}/{vid}" if vid else "https://www.douyin.com"
        tab = open_tab(cdp, url)
        opened = tab["id"]
        time.sleep(wait_video if vid else wait_home)
    else:
        time.sleep(wait_existing)
    try:
        return asyncio.run(_get_cookies(tab["webSocketDebuggerUrl"]))
    finally:
        if opened:
            close_tab(cdp, opened)


def netscape_lines(cookies):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dom = c.get("domain", ".douyin.com")
        lines.append("\t".join([
            dom,
            "TRUE" if str(dom).startswith(".") else "FALSE",
            c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(int(c.get("expirationDate") or (time.time() + 86400 * 30))),
            c["name"],
            str(c["value"]),
        ]))
    return lines


def write_netscape(cookies, path=DEFAULT_OUT):
    with open(path, "w") as f:
        f.write("\n".join(netscape_lines(cookies)) + "\n")
    os.chmod(path, 0o600)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", nargs="?", default=None)
    ap.add_argument("--cdp", default=DEFAULT_CDP)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        cookies = fetch_cookies(cdp=a.cdp, vid=a.id)
    except Exception as e:
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)[:300]}))
        else:
            print(f"FAIL {e}", file=sys.stderr)
        return 1
    write_netscape(cookies, a.out)
    if a.json:
        print(json.dumps({"ok": True, "count": len(cookies), "path": a.out},
                         ensure_ascii=False))
    else:
        print(f"OK {len(cookies)} cookies -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
