#!/usr/bin/env python3
"""douyin-download v3 single entry point. Prints one JSON object; exits 0 unless failed.

Usage:
  python3 scripts/dy.py resolve "<any share text / url / id>"
  python3 scripts/dy.py fetch   "<any share text / url / id>" \\
      [--want text,image,video,audio,cover] [--route auto|dom|ytdlp] \\
      [--api-fallback] [--out-dir DIR] [--wait 15] [--cdp URL]
  python3 scripts/dy.py cookies [id]
  python3 scripts/dy.py doctor
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dy_common as C  # noqa: E402


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def ensure_cookies(cdp):
    """Return (ok, detail). Creates the cookie file only if it is missing."""
    if os.path.exists(C.COOKIE_FILE):
        return True, "exists"
    try:
        import get_douyin_cookies as GC
        cookies = GC.fetch_cookies(cdp=cdp)
        GC.write_netscape(cookies, C.COOKIE_FILE)
        return True, f"created {len(cookies)} cookies"
    except Exception as e:
        return False, str(e)[:200]


def try_ytdlp(aweme_id, out_dir, timeout=240):
    url = f"https://www.douyin.com/video/{aweme_id}"
    tpl = os.path.join(out_dir, f"dy_{aweme_id}.%(ext)s")
    cmd = ["yt-dlp", "--no-playlist", "--no-mtime", "-q", "--no-warnings",
           "--cookies", C.COOKIE_FILE, "-o", tpl, url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout}s"
    except FileNotFoundError:
        return None, "yt-dlp not installed"
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return None, (tail[-1][:160] if tail else f"exit {r.returncode}")
    cands = sorted(glob.glob(os.path.join(out_dir, f"dy_{aweme_id}.*")))
    cands = [p for p in cands
             if not p.endswith((".part", ".ytdl", ".json", ".txt"))
             and "_manifest" not in p and "_live" not in p
             and "_cover" not in p and "_bgm" not in p
             and not any(f"_{n:02d}." in p for n in range(1, 100))]
    return (cands[0], None) if cands else (None, "no output file")


def fail_manifest(target, out_dir, errors):
    return {
        "schema": C.SCHEMA, "skill_version": C.SKILL_VERSION,
        "fetched_at": C.now_iso(), "status": "failed", "route": None,
        "input": target, "source_url": None, "resolved_url": None,
        "aweme_id": None, "type": None, "title": "", "desc": "", "topics": [],
        "author": "", "stats": {}, "published": "", "music": "", "page_url": "",
        "urls": {"images": [], "video": "", "audio": "", "cover": ""},
        "media": [], "files": [], "errors": errors, "warnings": [],
        "out_dir": out_dir, "elapsed_s": None,
    }


def cmd_resolve(args):
    res = C.resolve_input(args.target)
    print_json(res)
    return 0 if res.get("ok") else 1


def cmd_cookies(args):
    try:
        import get_douyin_cookies as GC
    except Exception as e:
        print_json({"ok": False, "error": f"cannot import get_douyin_cookies: {e}"})
        return 1
    try:
        cookies = GC.fetch_cookies(cdp=args.cdp, vid=args.id)
    except Exception as e:
        print_json({"ok": False, "error": str(e)[:300]})
        return 1
    GC.write_netscape(cookies, C.COOKIE_FILE)
    print_json({"ok": True, "count": len(cookies), "path": C.COOKIE_FILE})
    return 0


def cmd_doctor(args):
    report = C.run_doctor(cdp=args.cdp)
    print_json(report)
    return 0 if report.get("ok") else 1


def cmd_fetch(args):
    t0 = time.time()
    want = {w.strip() for w in args.want.split(",") if w.strip()}
    out_dir = os.path.abspath(os.path.expanduser(args.out_dir))
    os.makedirs(out_dir, exist_ok=True)
    errors, warnings, media = [], [], []

    res = C.resolve_input(args.target)
    if not res.get("ok"):
        man = fail_manifest(args.target, out_dir, res["errors"])
        C.write_manifest(man)
        print_json(man)
        return 1
    aweme_id = res["aweme_id"]

    ok, detail = ensure_cookies(args.cdp)
    if not ok:
        warnings.append(C.warn("cookies_unavailable",
                               f"could not create cookie file: {detail}"))
    elif detail != "exists":
        warnings.append(C.warn("cookies_created", detail))

    meta, route_used, dom_error, dom_err_obj = None, None, None, None

    # 1) Track A: yt-dlp first for videos, DOM is the fallback (2026-09: fragile)
    if res.get("type") == "video" and args.route in ("auto", "ytdlp"):
        path, yerr = try_ytdlp(aweme_id, out_dir)
        if path:
            route_used = "ytdlp"
            media.append({"kind": "video", "path": path, "route": "ytdlp",
                          "source_url": f"https://www.douyin.com/video/{aweme_id}"})
        else:
            item = C.err("ytdlp_failed", f"yt-dlp did not produce a file: {yerr}")
            if args.route == "ytdlp":
                errors.append(item)
            else:
                warnings.append(item)

    # 2) DOM route for metadata + note media (+ video fallback)
    if args.route != "ytdlp":
        if res.get("type") == "note":
            urls = [f"https://www.douyin.com/note/{aweme_id}"]
        elif res.get("type") == "video":
            urls = [f"https://www.douyin.com/video/{aweme_id}"]
        else:
            urls = [f"https://www.douyin.com/video/{aweme_id}",
                    f"https://www.douyin.com/note/{aweme_id}"]
        dom = C.fetch_dom(args.cdp, urls, want, wait_max=args.wait)
        if dom.get("error"):
            dom_error = dom["error"]
            if dom.get("data"):
                meta = C.build_meta(res, dom["data"])
            dom_err_obj = C.err(dom_error,
                                f"DOM extraction failed: {dom_error} "
                                f"(waited {dom.get('waited_s')}s)")
            errors.append(dom_err_obj)
        else:
            meta = C.build_meta(res, dom["data"])
            _, t2 = C.parse_aweme_url(meta.get("page_url") or "")
            if t2:
                res["type"] = t2
                meta["type"] = t2

    # 3) experimental API fallback (off by default)
    if meta is None and args.api_fallback:
        item, src_url = C.fetch_router_item(aweme_id, res.get("type"))
        route = "api-l1"
        if item is None:
            item, _t = C.fetch_detail_api(aweme_id)
            route = "api-l2"
        if item is not None:
            data = C.api_item_to_dom_shape(
                item, src_url or f"https://www.douyin.com/video/{aweme_id}",
                res.get("type"))
            meta = C.build_meta(res, data)
            meta["api_route"] = route
            route_used = route_used or route
            if dom_err_obj is not None:
                errors = [e for e in errors if e is not dom_err_obj]
                warnings.append(C.warn(f"dom_failed_{dom_error}",
                                       "DOM route failed but API fallback worked"))
        else:
            warnings.append(C.warn("api_fallback_failed",
                                   "iesdouyin _ROUTER_DATA and detail API both empty"))

    # 4) downloads
    acquired = set()
    if meta:
        if (meta.get("desc") or "").strip() or (meta.get("title") or "").strip():
            acquired.add("text")
        urls = meta["urls"]
        referer = meta.get("page_url") or f"https://www.douyin.com/note/{aweme_id}"

        got_video = any(m["kind"] == "video" for m in media)
        if "video" in want and not got_video and urls.get("video"):
            vu = urls["video"]
            if ".m3u8" in vu:
                warnings.append(C.warn("m3u8_unsupported",
                                       "video is HLS (m3u8); not downloaded"))
            elif "video_id=" not in vu or "aid=" not in vu:
                errors.append(C.err(
                    "empty_video_url",
                    "video url lacks video_id/aid; download would 200-empty, "
                    "skipped (waited for playback, url stayed incomplete)",
                    url=vu[:160]))
            else:
                dest = os.path.join(out_dir, f"dy_{aweme_id}_live.mp4")
                if C.download_file(vu, dest, referer):
                    media.append({"kind": "video", "path": dest, "route": "dom",
                                  "source_url": vu})
                else:
                    warnings.append(C.warn("download_failed_video",
                                           "video download failed (403 / expired cookie?)",
                                           url=vu[:160]))
        elif "video" in want and not got_video and res.get("type") == "note":
            warnings.append(C.warn("no_video_in_post",
                                   "no live-photo video in DOM; this note is static images"))

        if "image" in want and urls.get("images"):
            for n, u in enumerate(urls["images"], 1):
                dest = os.path.join(out_dir, f"dy_{aweme_id}_{n:02d}.{C.ext_from_url(u)}")
                uu = C.upgrade_image_url(u)
                if uu != u and C.download_file(uu, dest, referer):
                    media.append({"kind": "image", "path": dest, "route": "dom",
                                  "source_url": uu, "origin_url": u,
                                  "url_upgraded": True})
                    continue
                if C.download_file(u, dest, referer):
                    media.append({"kind": "image", "path": dest, "route": "dom",
                                  "source_url": u})
                else:
                    warnings.append(C.warn("download_failed_image",
                                           f"image #{n} failed", url=u[:160]))
        elif "image" in want and res.get("type") == "note":
            warnings.append(C.warn("no_images_in_post",
                                   "no aweme-images found in DOM"))

        au = urls.get("audio")
        if "audio" in want and au:
            dest = os.path.join(out_dir, f"dy_{aweme_id}_bgm.mp3")
            if C.download_file(au, dest, referer):
                media.append({"kind": "audio", "path": dest, "route": "dom",
                              "source_url": au})
            else:
                warnings.append(C.warn("download_failed_audio",
                                       "BGM download failed", url=au[:160]))
        elif "audio" in want and meta.get("music"):
            warnings.append(C.warn("audio_no_url",
                                   f"only music name available: {meta['music']}"))

        if "cover" in want and urls.get("cover"):
            dest = os.path.join(out_dir, f"dy_{aweme_id}_cover.jpg")
            if C.download_file(urls["cover"], dest, referer):
                media.append({"kind": "cover", "path": dest, "route": "dom",
                              "source_url": urls["cover"]})
            else:
                warnings.append(C.warn("download_failed_cover",
                                       "cover download failed"))

    # 5) verify every downloaded file: probe + sha256
    verified = []
    for m in media:
        p = m["path"]
        if not os.path.exists(p):
            warnings.append(C.warn(f"verify_failed_{m['kind']}",
                                   "file missing after download", path=p))
            continue
        pr = C.probe_media(p)
        m["bytes"] = pr.get("bytes")
        m["sha256"] = C.sha256_file(p)
        m["probe"] = pr
        if not pr.get("ok"):
            warnings.append(C.warn(f"verify_failed_{m['kind']}",
                                   "ffprobe/file says this is not real media",
                                   path=p, format=pr.get("format", "")))
            continue
        verified.append(m)
        acquired.add(m["kind"])
    media = verified

    status = C.finalize_status(errors, want, acquired)
    manifest = {
        "schema": C.SCHEMA,
        "skill_version": C.SKILL_VERSION,
        "fetched_at": C.now_iso(),
        "status": status,
        "route": route_used or ("dom" if meta else None),
        "input": args.target,
        "source_url": res.get("source_url"),
        "resolved_url": res.get("resolved_url"),
        "aweme_id": aweme_id,
        "type": (meta or {}).get("type") or res.get("type"),
        "title": (meta or {}).get("title", ""),
        "desc": (meta or {}).get("desc", ""),
        "topics": (meta or {}).get("topics", []),
        "author": (meta or {}).get("author", ""),
        "stats": (meta or {}).get("stats", {}),
        "published": (meta or {}).get("published", ""),
        "music": (meta or {}).get("music", ""),
        "page_url": (meta or {}).get("page_url", ""),
        "urls": (meta or {}).get("urls",
                                 {"images": [], "video": "", "audio": "", "cover": ""}),
        "media": media,
        "files": [m["path"] for m in media],
        "errors": errors,
        "warnings": warnings,
        "out_dir": out_dir,
        "elapsed_s": round(time.time() - t0, 1),
    }
    if meta and meta.get("api_route"):
        manifest["api_route"] = meta["api_route"]
    C.write_manifest(manifest)
    print_json(manifest)
    return 0 if status != "failed" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve", help="resolve any input to aweme_id/type")
    p.add_argument("target")
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("fetch", help="fetch metadata + media for one douyin work")
    p.add_argument("target")
    p.add_argument("--want", default="text,image,video",
                   help="comma list of text,image,video,audio,cover")
    p.add_argument("--route", choices=["auto", "dom", "ytdlp"], default="auto",
                   help="auto: yt-dlp first for videos then DOM fallback")
    p.add_argument("--api-fallback", action="store_true",
                   help="experimental: try iesdouyin/_ROUTER_DATA + detail API")
    p.add_argument("--out-dir", default=C.DEFAULT_OUT_DIR)
    p.add_argument("--wait", type=float, default=15.0,
                   help="max seconds to wait for the page to render")
    p.add_argument("--cdp", default=C.DEFAULT_CDP)
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("cookies", help=f"refresh {C.COOKIE_FILE}")
    p.add_argument("id", nargs="?", default=None)
    p.add_argument("--cdp", default=C.DEFAULT_CDP)
    p.set_defaults(fn=cmd_cookies)

    p = sub.add_parser("doctor", help="environment diagnostics (JSON)")
    p.add_argument("--cdp", default=C.DEFAULT_CDP)
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
