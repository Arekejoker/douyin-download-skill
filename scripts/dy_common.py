#!/usr/bin/env python3
"""douyin-download v3 shared library.

Rules baked in:
- read-only against douyin (no like/comment/follow/publish, no bulk scraping)
- never guess missing metadata: empty stays empty
- explicit status + machine error codes; when unsure, report failure, not success

This module has no side effects on import.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

try:
    import websockets
except ImportError:  # dy.py doctor reports this; import must not crash
    websockets = None

SKILL_VERSION = "3.1"
SCHEMA = "douyin-download.manifest.v1"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1")
DEFAULT_CDP = os.environ.get("DOUYIN_CDP", "http://127.0.0.1:18800")
COOKIE_FILE = os.path.abspath(os.path.expanduser(os.environ.get(
    "DOUYIN_COOKIE_FILE",
    os.path.join(tempfile.gettempdir(), "dy_cookies_netscape.txt"))))


def _default_out_dir():
    """Workspace tmp inside OpenClaw; ~/Downloads/douyin elsewhere.

    Override with DOUYIN_OUT_DIR (also available as `dy.py fetch --out-dir`).
    """
    env = os.environ.get("DOUYIN_OUT_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    oc = os.path.expanduser("~/.openclaw/workspace/tmp")
    if os.path.isdir(os.path.dirname(oc)):
        return oc
    return os.path.expanduser("~/Downloads/douyin")


DEFAULT_OUT_DIR = _default_out_dir()

URL_RE = re.compile(r"https?://[^\s，,。；;）)】\u3000\"'<>]+")
ID_RE = re.compile(r"^\d{15,25}$")
BLOCK_RE = re.compile(r"验证码|滑块验证|安全验证|完成验证|请完成下列验证|verify|captcha", re.I)
LOGIN_RE = re.compile(r"登录后查看|登录后可查看|请先登录|立即登录后")
TPL_RE = re.compile(r"~tplv-([A-Za-z0-9-]+)(?::[A-Za-z0-9._:%-]+)?\.(webp|jpeg|jpg|png|heic)")

FATAL_CODES = {"blocked_by_verification", "login_required", "page_drift",
               "invalid_input", "resolve_failed", "cdp_unavailable"}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def err(code, message, **extra):
    e = {"code": code, "message": message}
    e.update(extra)
    return e


def warn(code, message, **extra):
    w = {"code": code, "message": message}
    w.update(extra)
    return w


# ---------------------------------------------------------------- input parsing

def extract_candidate(text):
    """Return (kind, value) where kind is 'url' | 'id' | None."""
    text = (text or "").strip()
    if ID_RE.match(text):
        return ("id", text)
    m = URL_RE.search(text)
    if m:
        return ("url", m.group(0).rstrip(".,;，。；"))
    m = re.search(r"(v\.douyin\.com/[A-Za-z0-9_-]+)", text)
    if m:
        return ("url", "https://" + m.group(1))
    m = re.search(r"(www\.douyin\.com/(?:share/)?(?:video|note)/\d{15,25})", text)
    if m:
        return ("url", "https://" + m.group(1))
    return (None, None)


def parse_aweme_url(url):
    """Return (aweme_id, type) where type may be None."""
    if not url:
        return (None, None)
    m = re.search(r"/(?:share/)?video/(\d{15,25})", url)
    if m:
        return (m.group(1), "video")
    m = re.search(r"/(?:share/)?note/(\d{15,25})", url)
    if m:
        return (m.group(1), "note")
    m = re.search(r"[?&](?:modal_id|aweme_id|vid)=(\d{15,25})", url)
    if m:
        return (m.group(1), None)
    m = re.search(r"/(video|note)/(\d{15,25})", url)
    if m:
        return (m.group(2), m.group(1))
    return (None, None)


def curl_effective_url(url, ua=IPHONE_UA, timeout=25):
    cmd = ["curl", "-sL", "-o", "/dev/null", "-w", "%{url_effective}",
           "--max-time", str(timeout), "-A", ua, url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def resolve_input(target, short_timeout=25):
    """Resolve any user input (share text / short url / long url / pure id).

    Returns {ok, input, input_kind, aweme_id, type, source_url, resolved_url, errors}.
    Network is only used for short links; pure ids and long urls resolve offline.
    """
    res = {"ok": False, "input": target, "input_kind": None, "aweme_id": None,
           "type": None, "source_url": None, "resolved_url": None, "errors": []}
    kind, value = extract_candidate(target)
    if kind is None:
        res["errors"].append(err("invalid_input", "no douyin url or id found in input"))
        return res
    res["input_kind"] = kind
    if kind == "id":
        res.update(ok=True, aweme_id=value,
                   source_url=f"https://www.douyin.com/video/{value}")
        return res
    res["source_url"] = value
    url = value
    if re.match(r"https?://v\.douyin\.com/", url) or \
       re.match(r"https?://(?:www\.)?iesdouyin\.com/share/", url):
        eff = curl_effective_url(url, timeout=short_timeout)
        if eff:
            res["resolved_url"] = eff
    cand = res["resolved_url"] or url
    aweme_id, typ = parse_aweme_url(cand)
    if not aweme_id and cand != url:
        aweme_id, typ = parse_aweme_url(url)
    if not aweme_id:
        if re.search(r"/share/user/|/user/", cand or ""):
            res["errors"].append(err(
                "unsupported_profile",
                "the link points to a user profile, not a single work; "
                "this skill fetches one work at a time"))
        else:
            res["errors"].append(err(
                "resolve_failed",
                f"could not extract aweme id from {(cand or '')[:120]}"))
        return res
    res.update(ok=True, aweme_id=aweme_id, type=typ)
    return res


# ---------------------------------------------------------------------- CDP

def cdp_http(cdp, path, method="GET", timeout=10):
    req = urllib.request.Request(f"{cdp}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except Exception:
        return body


def cdp_open_tab(cdp, url):
    return cdp_http(cdp, "/json/new?" + url, method="PUT", timeout=15)


def cdp_close_tab(cdp, target_id):
    try:
        cdp_http(cdp, f"/json/close/{target_id}", timeout=5)
    except Exception:
        pass


async def _ws_eval(ws_url, expression, timeout=60):
    async with websockets.connect(ws_url, max_size=100_000_000) as w:
        await w.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                 "params": {"expression": expression,
                                            "returnByValue": True}}))
        while True:
            msg = json.loads(await asyncio.wait_for(w.recv(), timeout=timeout))
            if msg.get("id") == 1:
                res = msg.get("result", {})
                if "exceptionDetails" in res:
                    return None
                return res.get("result", {}).get("value")


def cdp_eval(cdp, ws_url, expression):
    if websockets is None:
        raise RuntimeError("python websockets is not installed")
    return asyncio.run(_ws_eval(ws_url, expression))


JS_EXTRACT = r"""
(() => {
  const out = {ok: true, ready: document.readyState, href: location.href,
    title: document.title || '', desc: '', author: '', published: '',
    stats: {}, music: '', imgs: [], video: '', audio: '', cover: '', poster: '',
    followers: '', authorLikes: '', bodyLen: 0, bodySample: ''};
  try {
    const txt = (el) => el ? ((el.innerText || el.textContent || '').trim()) : '';
    const q = (s) => document.querySelector(s);
    const body = document.body ? (document.body.innerText || '') : '';
    out.bodyLen = body.length;
    out.bodySample = body.slice(0, 800);

    const seen = new Set();
    for (const i of document.querySelectorAll('img')) {
      const u = i.currentSrc || i.src || '';
      if (!/aweme-images|dy-cropcenter|tocdn/.test(u)) continue;
      if (/sc=avatar|sticker_comment|s=profile/.test(u)) continue;
      if (/pcweb_cover/.test(u)) { if (!out.cover) out.cover = u; continue; }
      const key = u.split('?')[0];
      if (seen.has(key)) continue;
      seen.add(key);
      out.imgs.push(u);   // keep query: it carries x-expires + x-signature
    }
    for (const v of document.querySelectorAll('video')) {
      const s = v.currentSrc || v.src || '';
      if (v.poster && !out.poster) out.poster = v.poster;
      if (/ies-music/.test(s)) { if (!out.audio) out.audio = s; continue; }
      if (/aweme\/v1\/play|\.mp4|\.m3u8/.test(s) && !out.video) out.video = s;
    }
    if (!out.cover) out.cover = out.poster || '';

    const d = q('[data-e2e="note-desc"]') || q('[data-e2e="video-desc"]')
           || q('[data-e2e*="desc"]');
    out.desc = txt(d).slice(0, 4000);
    if (!out.desc) {
      const ct = (document.title || '').replace(/\s*-\s*抖音\s*$/, '').trim();
      if (ct) out.desc = ct.split('\n')[0].trim();
    }

    let author = '';
    const ui = q('[data-e2e="user-info"]');
    if (ui) {
      const t = txt(ui);
      const m = t.match(/^([^\s]+)\s*粉丝([\d.]+[万亿]?)\s*获赞([\d.]+[万亿]?)/);
      if (m) { author = m[1]; out.followers = m[2]; out.authorLikes = m[3]; }
      else author = t.split(/\s+/)[0] || '';
    }
    if (!author) {
      const a = q('[data-e2e="video-author-name"]') || q('[data-e2e*="author"]')
             || q('[data-e2e*="user-name"]');
      author = txt(a).replace(/\s+/g, ' ').slice(0, 60);
    }
    out.author = author;

    for (const el of document.querySelectorAll('[data-e2e*=music], [class*=music]')) {
      const t = txt(el).replace(/\s+/g, ' ');
      if (t && t.length < 80) { out.music = t; break; }
    }

    const stat = (sels) => {
      for (const s of sels) {
        const el = q(s);
        if (el && txt(el)) return txt(el).replace(/\s+/g, ' ').slice(0, 40);
      }
      return '';
    };
    out.stats = {
      like: stat(['[data-e2e="video-player-digg"]', '[data-e2e*=like]']),
      comment: stat(['[data-e2e="feed-comment-icon"]', '[data-e2e*=comment]']),
      collect: stat(['[data-e2e="video-player-collect"]', '[data-e2e*=collect]']),
      share: stat(['[data-e2e="video-player-share"]', '[data-e2e*=share]'])
    };
    if (out.followers) out.stats.followers = out.followers;
    if (out.authorLikes) out.stats.author_likes = out.authorLikes;

    const pm = body.match(/发布时间[：:]\s*([^\n]{2,40})/);
    if (pm) out.published = pm[1].trim();
  } catch (e) {
    out.ok = false;
    out.error = String(e);
  }
  return JSON.stringify(out);
})()
"""


def detect_block(data):
    """Return block code only when the page has no media/desc at all."""
    if not data:
        return None
    if data.get("imgs") or data.get("video") or (data.get("desc") or "").strip():
        return None
    s = f"{data.get('title', '')} {data.get('bodySample', '')}"
    if LOGIN_RE.search(s):
        return "login_required"
    if BLOCK_RE.search(s):
        return "blocked_by_verification"
    return None


def _page_ready(data, want):
    if data.get("imgs") or data.get("video") or (data.get("desc") or "").strip():
        return True
    if "audio" in want and (data.get("audio") or data.get("music")):
        return True
    return False


def _fetch_one(cdp, url, want, wait_max, min_wait, extra_video_wait):
    t0 = time.time()
    try:
        tab = cdp_open_tab(cdp, url)
    except Exception as e:
        return {"data": None, "error": "cdp_unavailable",
                "detail": str(e)[:200], "waited_s": 0.0}
    try:
        time.sleep(min_wait)
        data, block_hits, ready_since = None, 0, None
        while True:
            raw = cdp_eval(cdp, tab["webSocketDebuggerUrl"], JS_EXTRACT)
            if raw:
                try:
                    data = json.loads(raw)
                except Exception:
                    pass
            if data:
                if detect_block(data):
                    block_hits += 1
                    if block_hits >= 2:
                        break
                else:
                    block_hits = 0
                    if _page_ready(data, want):
                        if ready_since is None:
                            ready_since = time.time()
                        elapsed = time.time() - t0
                        need_video = "video" in want and not data.get("video")
                        need_imgs = ("image" in want and not data.get("imgs")
                                     and not data.get("video"))
                        if need_video and elapsed < min(wait_max, 8.0):
                            pass  # live-photo video can mount a few seconds late
                        elif need_imgs and elapsed < min(wait_max, 6.0):
                            pass  # images may lazy-load after the shell renders
                        else:
                            break
            if time.time() - t0 >= wait_max:
                break
            time.sleep(1.0)

        # B3: a video url without video_id/aid downloads 200-empty. Wait for playback.
        if data and data.get("video") and ("video_id=" not in data["video"]
                                           or "aid=" not in data["video"]):
            time.sleep(extra_video_wait)
            raw = cdp_eval(cdp, tab["webSocketDebuggerUrl"], JS_EXTRACT)
            if raw:
                try:
                    d2 = json.loads(raw)
                    if d2:
                        data = d2
                except Exception:
                    pass

        waited = round(time.time() - t0, 1)
        if not data:
            return {"data": None, "error": "page_drift",
                    "detail": "Runtime.evaluate returned nothing", "waited_s": waited}
        block = detect_block(data)
        if block and not (data.get("imgs") or data.get("video")):
            return {"data": data, "error": block, "waited_s": waited}
        if not data.get("imgs") and not data.get("video") \
           and not (data.get("desc") or "").strip():
            return {"data": data, "error": "page_drift", "waited_s": waited}
        return {"data": data, "error": None, "waited_s": waited}
    finally:
        cdp_close_tab(cdp, tab["id"])


def fetch_dom(cdp, urls, want, wait_max=15.0, min_wait=2.5, extra_video_wait=2.5):
    """Try urls in order; adaptive poll until ready/timeout. Own tab, always closed."""
    last = None
    for url in urls:
        res = _fetch_one(cdp, url, want, wait_max, min_wait, extra_video_wait)
        last = res
        if res["data"] and not res["error"]:
            return res
        if res["error"] in ("blocked_by_verification", "login_required",
                            "cdp_unavailable"):
            return res
    return last


# ------------------------------------------------------------------ downloads

def curl_body(url, ua=IPHONE_UA, timeout=25, cookie_file=None, referer=None):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", ua]
    if referer:
        cmd += ["-e", referer]
    if cookie_file and os.path.exists(cookie_file):
        cmd += ["-b", cookie_file]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout + 10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def is_real_file(path):
    """A download is real only if non-empty and `file` does not call it text."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        kind = subprocess.run(["file", "-b", path], capture_output=True,
                              text=True, timeout=15).stdout.lower()
    except Exception:
        return True
    bad = ["empty", "ascii text", "unicode text", "utf-8", "json", "html",
           "xml", "text/plain"]
    return not any(b in kind for b in bad)


def download_file(url, dest, referer, cookie_file=COOKIE_FILE, extra_cookie=None,
                  timeout=180, retries=2):
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    cmd = ["curl", "-fsSL", "--max-time", str(timeout),
           "--retry", str(retries), "--retry-delay", "2",
           "-o", dest, "-A", UA]
    if referer:
        cmd += ["-e", referer]
    if cookie_file and os.path.exists(cookie_file):
        cmd += ["-b", cookie_file]
    if extra_cookie:
        cmd += ["-b", extra_cookie]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    except Exception:
        return False
    if r.returncode != 0 or not is_real_file(dest):
        try:
            if os.path.exists(dest) and not is_real_file(dest):
                os.remove(dest)
        except OSError:
            pass
        return False
    return True


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_media(path):
    info = {"path": path, "ok": False,
            "bytes": os.path.getsize(path) if os.path.exists(path) else 0}
    if not os.path.exists(path):
        return info
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_format",
                            "-show_streams", "-of", "json", path],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            j = json.loads(r.stdout or "{}")
            fmt = j.get("format") or {}
            streams = j.get("streams") or []
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            a = next((s for s in streams if s.get("codec_type") == "audio"), None)
            s0 = v or a or {}
            dur = fmt.get("duration")
            info.update(ok=True, format=fmt.get("format_name", ""),
                        codec=s0.get("codec_name", ""),
                        width=v.get("width") if v else None,
                        height=v.get("height") if v else None,
                        duration_s=round(float(dur), 2) if dur else None)
            return info
    except Exception:
        pass
    try:
        kind = subprocess.run(["file", "-b", path], capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        kind = ""
    info.update(ok=is_real_file(path), format=kind[:100])
    return info


def upgrade_image_url(u):
    """Prefer the full-size ~tplv-dy-aweme-images:q75 template. Query untouched."""
    if not u or "~tplv-" not in u:
        return u

    def rep(m):
        name, ext = m.group(1), m.group(2)
        cur = m.group(0)
        if name == "dy-aweme-images" and ":q75" in cur:
            return cur
        return f"~tplv-dy-aweme-images:q75.{ext}"

    return TPL_RE.sub(rep, u, count=1)


def ext_from_url(u):
    m = re.search(r"\.(webp|jpeg|jpg|png|heic|gif)(?:\?|$)", u or "")
    if not m:
        return "webp"
    e = m.group(1)
    return "jpg" if e == "jpeg" else e


# ------------------------------------------------------------------ metadata

def clean_title(t):
    return re.sub(r"\s*-\s*抖音\s*$", "", (t or "").strip())


def topics_from(*texts):
    s = " ".join((t or "") for t in texts)
    out, seen = [], set()
    for m in re.finditer(r"#([^\s#@，。,.;；:：!！?？]+)", s):
        t = "#" + m.group(1)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def build_meta(res, data):
    title = clean_title(data.get("title") or "")
    desc = (data.get("desc") or "").strip()
    if not desc:
        desc = title  # caption lives in document.title on pages without note-desc
    return {
        "aweme_id": res.get("aweme_id"),
        "type": res.get("type"),
        "input": res.get("input"),
        "source_url": res.get("source_url"),
        "resolved_url": res.get("resolved_url"),
        "page_url": data.get("href") or "",
        "title": title,
        "desc": desc,
        "topics": topics_from(title, desc),
        "author": (data.get("author") or "").strip(),
        "stats": {k: v for k, v in (data.get("stats") or {}).items() if v},
        "published": data.get("published") or "",
        "music": data.get("music") or "",
        "urls": {
            "images": list(data.get("imgs") or []),
            "video": data.get("video") or "",
            "audio": data.get("audio") or "",
            "cover": data.get("cover") or "",
        },
    }


def finalize_status(errors, want, acquired):
    codes = {e.get("code") for e in (errors or [])}
    if codes & FATAL_CODES:
        return "failed"
    if not acquired:
        return "failed"
    core = {w for w in want if w in ("text", "image", "video", "audio", "cover")}
    if core and not (core <= acquired):
        return "partial"
    return "success"


# ------------------------------------------------------------- api fallback

TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"


def register_ttwid(timeout=20):
    body = json.dumps({"region": "cn", "aid": 1768, "service": "www.ixigua.com",
                       "union": True, "needFid": False})
    try:
        r = subprocess.run(
            ["curl", "-s", "-i", "--max-time", str(timeout), "-X", "POST",
             "-H", "Content-Type: application/json", "-A", UA,
             "-d", body, TTWID_REGISTER_URL],
            capture_output=True, text=True, timeout=timeout + 10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r'"redirect_url"\s*:\s*"([^"]+)"', r.stdout)
    if not m:
        return None
    redir = m.group(1).replace("\\/", "/")
    try:
        r2 = subprocess.run(["curl", "-s", "-i", "--max-time", str(timeout),
                             "-A", UA, redir],
                            capture_output=True, text=True, timeout=timeout + 10)
    except Exception:
        return None
    m2 = re.search(r"^set-cookie:\s*ttwid=([^;]+)", r2.stdout, re.I | re.M)
    return m2.group(1) if m2 else None


def fetch_detail_api(aweme_id, ttwid=None, timeout=25):
    url = ("https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=%s"
           "&device_platform=webapp&aid=6383&channel=channel_pc_web" % aweme_id)
    cmd = ["curl", "-s", "--max-time", str(timeout), "-A", UA,
           "-e", "https://www.douyin.com/"]
    if os.path.exists(COOKIE_FILE):
        cmd += ["-b", COOKIE_FILE]
    if ttwid:
        cmd += ["-b", "ttwid=" + ttwid]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except Exception:
        return None, ttwid
    if r.returncode != 0 or not r.stdout:
        return None, ttwid
    try:
        j = json.loads(r.stdout)
    except Exception:
        return None, ttwid
    item = j.get("aweme_detail")
    if item:
        return item, ttwid
    if not ttwid:
        t = register_ttwid()
        if t:
            return fetch_detail_api(aweme_id, ttwid=t, timeout=timeout)
    return None, ttwid


def _dig_item_list(j):
    ld = j.get("loaderData") or {}
    for val in ld.values():
        if isinstance(val, dict):
            vir = val.get("videoInfoRes")
            if isinstance(vir, dict):
                lst = vir.get("item_list") or []
                if lst and isinstance(lst[0], dict):
                    return lst[0]
    return None


def fetch_router_item(aweme_id, type_hint=None, timeout=25):
    kinds = [type_hint] if type_hint in ("video", "note") else ["video", "note"]
    for t in kinds:
        url = f"https://www.iesdouyin.com/share/{t}/{aweme_id}/"
        html = curl_body(url, ua=IPHONE_UA, timeout=timeout)
        if not html:
            continue
        m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>",
                      html, re.S)
        if not m:
            continue
        try:
            j = json.loads(m.group(1))
        except Exception:
            raw = m.group(1)
            try:
                j = json.loads(raw[:raw.rfind("}") + 1])
            except Exception:
                continue
        item = _dig_item_list(j)
        if item:
            return item, url
    return None, None


def api_item_to_dom_shape(item, page_url="", type_hint=None):
    """Convert an aweme struct (router data / detail API) to the DOM JS shape."""
    def url_list(obj):
        if not isinstance(obj, dict):
            return []
        return [u for u in (obj.get("url_list") or []) if isinstance(u, str)]

    v = item.get("video") or {}
    images = []
    for im in (item.get("images") or []):
        us = url_list(im)
        if us:
            images.append(us[0])
    play = url_list(v.get("play_addr"))
    video = play[0].replace("playwm", "play") if play else ""
    cover = ""
    for c in (v.get("cover"), v.get("origin_cover"), item.get("cover")):
        us = url_list(c)
        if us:
            cover = us[0]
            break
    music = item.get("music") or {}
    music_txt = " - ".join([x for x in [music.get("title"),
                                        music.get("author")] if x])
    st = item.get("statistics") or {}
    pub = ""
    ct = item.get("create_time")
    if isinstance(ct, (int, float)) and ct > 0:
        pub = datetime.fromtimestamp(ct, timezone.utc).astimezone() \
            .strftime("%Y-%m-%d %H:%M")
    audio = url_list(music.get("play_url"))
    return {
        "ok": True, "ready": "complete", "href": page_url,
        "title": item.get("desc") or "", "desc": item.get("desc") or "",
        "author": ((item.get("author") or {}).get("nickname") or ""),
        "published": pub,
        "stats": {
            "like": str(st.get("digg_count", "") or ""),
            "comment": str(st.get("comment_count", "") or ""),
            "collect": str(st.get("collect_count", "") or ""),
            "share": str(st.get("share_count", "") or ""),
        },
        "music": music_txt, "imgs": images, "video": video,
        "audio": audio[0] if audio else "", "cover": cover, "poster": "",
        "bodyLen": 0, "bodySample": "",
    }


# ------------------------------------------------------------------- doctor

def run_doctor(cdp=DEFAULT_CDP):
    checks = []

    def add(name, ok, detail, required=True):
        checks.append({"name": name, "ok": bool(ok),
                       "required": bool(required), "detail": detail})

    add("python", True, sys.version.split()[0])
    add("websockets", websockets is not None,
        getattr(websockets, "__version__", "missing") if websockets else
        "missing (pip install websockets)")
    for tool, args in (("curl", ["curl", "--version"]),
                       ("ffprobe", ["ffprobe", "-version"]),
                       ("file", ["file", "--version"])):
        path = shutil.which(tool)
        ver = ""
        if path:
            try:
                ver = subprocess.run(args, capture_output=True, text=True,
                                     timeout=10).stdout.splitlines()[0][:80]
            except Exception:
                pass
        add(tool, bool(path), ver or path or "not found")
    ytdlp = shutil.which("yt-dlp")
    yver = ""
    if ytdlp:
        try:
            yver = subprocess.run(["yt-dlp", "--version"], capture_output=True,
                                  text=True, timeout=15).stdout.strip()[:40]
        except Exception:
            pass
        add("yt-dlp", True, f"{yver} (optional; DOM fallback covers video)",
            required=False)
    else:
        add("yt-dlp", False, "not found (optional; DOM fallback covers video)",
            required=False)
    try:
        ver = cdp_http(cdp, "/json/version", timeout=5)
        add("cdp", True, f"{ver.get('Browser', 'unknown')} @ {cdp}")
    except Exception as e:
        if os.path.isdir(os.path.expanduser("~/.openclaw")):
            hint = "OpenClaw: run browserctl ensure, retry in 10s"
        else:
            hint = ("start Chrome with CDP first: google-chrome "
                    "--remote-debugging-port=18800 --user-data-dir=$HOME/.douyin-chrome; "
                    "log in at douyin.com and keep it running")
        add("cdp", False, f"unreachable @ {cdp}: {str(e)[:120]} | {hint}")
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, errors="replace") as f:
                cnt = sum(1 for line in f
                          if line.strip() and not line.startswith("#"))
            age_h = (time.time() - os.path.getmtime(COOKIE_FILE)) / 3600
            add("cookies", True, f"{cnt} cookies, {age_h:.1f}h old ({COOKIE_FILE})",
                required=False)
        except Exception as e:
            add("cookies", False, str(e)[:120], required=False)
    else:
        add("cookies", False,
            f"missing ({COOKIE_FILE}); run: python3 scripts/dy.py cookies",
            required=False)
    try:
        os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
        fd, p = tempfile.mkstemp(dir=DEFAULT_OUT_DIR, prefix=".dy_doctor_")
        os.close(fd)
        os.remove(p)
        add("out_dir", True, DEFAULT_OUT_DIR)
    except Exception as e:
        add("out_dir", False, f"{DEFAULT_OUT_DIR}: {str(e)[:120]}")
    return {
        "ok": all(c["ok"] for c in checks if c["required"]),
        "skill_version": SKILL_VERSION,
        "checks": checks,
        "cookie_file": COOKIE_FILE,
        "out_dir": DEFAULT_OUT_DIR,
    }


# ------------------------------------------------------------------ manifest

def write_manifest(man):
    path = man.get("manifest_path")
    if not path:
        rid = man.get("aweme_id") or "unknown"
        path = os.path.join(man.get("out_dir") or DEFAULT_OUT_DIR,
                            f"dy_{rid}_manifest.json")
        man["manifest_path"] = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return path
