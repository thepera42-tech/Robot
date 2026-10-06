"""
agent_browser.py — the robot's "browser for the AI".

Two engines behind ONE API (/browse/<op>), so the rest of the system never cares which is running:

  lite  Pure standard library. Fetches pages server-side, turns them into readable text with
        numbered links, searches the web, downloads files. Runs ANYWHERE, including Termux on
        the Android TV. Cannot click or run JavaScript.

  full  A real Chromium driven by Playwright (headless by default). Live screenshots, click, type,
        scroll, back/forward, tabs, downloads — it is a normal browser the AI operates like an
        agent. Needs:  pip install playwright && playwright install chromium   (laptop/PC only;
        Chromium does not run under Termux).

Where it runs:
  * Everything on one machine:         just start backend.py (engine = auto: full if Playwright is
                                       installed, otherwise lite).
  * TV is the kiosk, laptop is the     On the laptop:  python agent_browser.py   (port 5006,
    browser:                           set BROWSER_HOST=0.0.0.0 and BROWSER_TOKEN=some-secret)
                                       On the TV:      BROWSER_REMOTE=http://<laptop-ip>:5006
                                                       BROWSER_TOKEN=some-secret
                                       The TV backend forwards every /browse call to the laptop.

Environment:
  BROWSER_ENGINE         auto (default) | full | lite
  BROWSER_REMOTE         URL of a laptop running this file standalone
  BROWSER_TOKEN          shared secret (sent as X-Browser-Token); strongly recommended off-localhost
  BROWSER_HEADLESS       1 (default) | 0 to watch Chromium on the machine's own screen
  BROWSER_DOWNLOAD_DIR   default: ./downloads
  BROWSER_MAX_DOWNLOAD_MB  default 200
  BROWSER_ALLOW_PRIVATE  1 to let the browser reach LAN/localhost addresses (default blocked: a web
                         page, or an AI that read one, must not be able to poke your router/TV/NAS)
"""
import hmac
import ipaddress
import json
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from flask import Blueprint, Response, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.environ.get("BROWSER_DOWNLOAD_DIR") or os.path.join(BASE_DIR, "downloads")
ENGINE_PREF = os.environ.get("BROWSER_ENGINE", "auto").strip().lower()
REMOTE = os.environ.get("BROWSER_REMOTE", "").strip().rstrip("/")
TOKEN = os.environ.get("BROWSER_TOKEN", "").strip()
HEADLESS = os.environ.get("BROWSER_HEADLESS", "1") != "0"
ALLOW_PRIVATE = os.environ.get("BROWSER_ALLOW_PRIVATE", "") == "1"
MAX_DOWNLOAD = int(os.environ.get("BROWSER_MAX_DOWNLOAD_MB", "200")) * 1024 * 1024
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

bp = Blueprint("agent_browser", __name__)
_log = None


class BrowseError(Exception):
    pass


# ------------------------------------------------------------------ safety

def _ip_blocked(ip_text):
    ip = ipaddress.ip_address(ip_text.split("%")[0])
    return (not ip.is_global) or ip.is_multicast


_host_cache = {}


def host_allowed(host):
    """True if every address the host resolves to is public. Cached for 60 s."""
    if ALLOW_PRIVATE:
        return True
    now = time.time()
    hit = _host_cache.get(host)
    if hit and now - hit[1] < 60:
        return hit[0]
    try:
        infos = socket.getaddrinfo(host, None)
        ok = bool(infos) and not any(_ip_blocked(i[4][0]) for i in infos)
    except socket.gaierror:
        ok = True          # unresolvable: the fetch itself will fail with a clear error
    _host_cache[host] = (ok, now)
    return ok


def check_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BrowseError("Only http and https addresses are allowed.")
    if not parsed.hostname:
        raise BrowseError("That address has no host name.")
    if not host_allowed(parsed.hostname):
        raise BrowseError("Blocked: that address is on a private or local network.")
    return url


def normalize_url(url):
    url = str(url or "").strip()
    if not url:
        raise BrowseError("No address given.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    return check_url(url)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_GuardedRedirect)


def safe_filename(name):
    name = urllib.parse.unquote(str(name or "")).split("/")[-1].split("\\")[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")[:120]
    return name or "download"


# ------------------------------------------------------------------ lite engine

def fetch(url, max_bytes=2_000_000, headers=None, timeout=15):
    check_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        **(headers or {}),
    })
    try:
        resp = _opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise BrowseError("That site answered with HTTP %s." % exc.code)
    except BrowseError:
        raise
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise BrowseError("Couldn't reach that site: %s" % getattr(exc, "reason", exc))
    ctype = resp.headers.get("Content-Type", "")
    raw = resp.read(max_bytes + 1)[:max_bytes]
    return resp.geturl(), ctype, raw


def decode(raw, ctype=""):
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb"charset=[\"']?([\w-]+)", raw[:2048])
    enc = (m.group(1).decode() if m and isinstance(m.group(1), bytes) else (m.group(1) if m else "utf-8"))
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _Reader(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "header", "footer", "ul", "ol",
             "table", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "form", "nav", "main"}
    NO_END_BREAK = {"li", "tr", "br"}        # these already break before the next one starts

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.links = base, [], []
        self.skip, self.title, self._in_title = 0, "", False
        self._href, self._ltxt = None, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.out.append("# ")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("javascript:", "#", "mailto:", "tel:")):
                self._href, self._ltxt = urllib.parse.urljoin(self.base, href), []

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            text = re.sub(r"\s+", " ", "".join(self._ltxt)).strip()
            if text and len(self.links) < 80:
                self.links.append({"i": len(self.links) + 1, "text": text[:100], "url": self._href})
                self.out.append(" [%d]" % len(self.links))
            self._href = None
        if tag in self.BLOCK and tag not in self.NO_END_BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self._in_title:
            self.title += data
            return                                   # the title is metadata, not page text
        self.out.append(data)
        if self._href is not None:
            self._ltxt.append(data)


def read_page(url, max_chars=6000):
    final, ctype, raw = fetch(normalize_url(url))
    if "html" not in ctype.lower() and not ctype.lower().startswith("text/"):
        return {"url": final, "title": final.rsplit("/", 1)[-1], "text": "",
                "links": [], "note": "Not a web page (%s). Use download_file to save it." % (ctype or "unknown type")}
    parser = _Reader(final)
    parser.feed(decode(raw, ctype))
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return {"url": final, "title": re.sub(r"\s+", " ", parser.title).strip(), "text": text[:max_chars],
            "truncated": len(text) > max_chars, "links": parser.links}


# ---- web search (scrapes DuckDuckGo's no-JS page, falls back to Bing). Scrapers break when the
# ---- sites change their markup; the full engine is the robust path.

class _DDG(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self._cur, self._mode = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "a" and "result__a" in cls:
            self._cur = {"title": "", "url": _unwrap_ddg(a.get("href", "")), "snippet": ""}
            self.results.append(self._cur)
            self._mode = "title"
        elif tag in ("a", "div") and "result__snippet" in cls and self._cur:
            self._mode = "snippet"

    def handle_endtag(self, tag):
        if tag in ("a", "div"):
            self._mode = None

    def handle_data(self, data):
        if self._cur and self._mode:
            self._cur[self._mode] += data


def _unwrap_ddg(href):
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    return urllib.parse.unquote(q["uddg"][0]) if "uddg" in q else href


class _Bing(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self._in_item, self._cur, self._mode, self._depth = [], False, None, None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and "b_algo" in (a.get("class") or ""):
            self._in_item, self._cur = True, {"title": "", "url": "", "snippet": ""}
            self.results.append(self._cur)
        elif self._in_item and tag == "a" and a.get("href", "").startswith("http") and not self._cur["url"]:
            self._cur["url"], self._mode = _unwrap_bing(a["href"]), "title"
        elif self._in_item and tag == "p":
            self._mode = "snippet"

    def handle_endtag(self, tag):
        if tag in ("a", "p"):
            self._mode = None
        if tag == "li":
            self._in_item = False

    def handle_data(self, data):
        if self._in_item and self._mode and self._cur is not None:
            self._cur[self._mode] += data


def _unwrap_bing(href):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    u = (q.get("u") or [""])[0]
    if u.startswith("a1"):
        import base64
        try:
            pad = "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(u[2:] + pad).decode()
        except Exception:
            pass
    return href


def web_search(query, limit=6):
    query = str(query or "").strip()[:200]
    if not query:
        raise BrowseError("Empty search.")
    results = []
    for url, parser_cls in (("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query), _DDG),
                            ("https://www.bing.com/search?q=" + urllib.parse.quote_plus(query), _Bing)):
        try:
            _, ctype, raw = fetch(url)
            parser = parser_cls()
            parser.feed(decode(raw, ctype))
            results = [r for r in parser.results if r["url"].startswith("http")]
        except BrowseError:
            results = []
        if results:
            break
    if not results:
        raise BrowseError("Search returned nothing (the search engine may be blocking this machine).")
    return [{"title": re.sub(r"\s+", " ", r["title"]).strip(), "url": r["url"],
             "snippet": re.sub(r"\s+", " ", r["snippet"]).strip()[:240]} for r in results[:limit]]


# ---- music: YouTube search -> video IDs for the kiosk's embedded player

def _walk(node, key, found):
    if isinstance(node, dict):
        if key in node:
            found.append(node[key])
        for v in node.values():
            _walk(v, key, found)
    elif isinstance(node, list):
        for v in node:
            _walk(v, key, found)


def parse_youtube_results(text, limit=5):
    marker = re.search(r"ytInitialData\s*=\s*", text)
    if not marker:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(text, marker.end())
    except ValueError:
        return []
    renderers = []
    _walk(data, "videoRenderer", renderers)
    out = []
    for r in renderers:
        vid = r.get("videoId")
        if not vid or any(o["video_id"] == vid for o in out):
            continue
        title = "".join(run.get("text", "") for run in (r.get("title") or {}).get("runs", []))
        channel = "".join(run.get("text", "") for run in (r.get("ownerText") or {}).get("runs", []))
        length = (r.get("lengthText") or {}).get("simpleText", "")
        out.append({"video_id": vid, "title": title, "channel": channel, "length": length})
        if len(out) >= limit:
            break
    return out


def music_search(query, limit=5):
    query = str(query or "").strip()[:150]
    if not query:
        raise BrowseError("Empty music search.")
    url = "https://www.youtube.com/results?search_query=%s&sp=EgIQAQ%%3D%%3D" % urllib.parse.quote_plus(query)
    _, ctype, raw = fetch(url, max_bytes=3_000_000, headers={"Cookie": "CONSENT=YES+1; SOCS=CAI"})
    results = parse_youtube_results(decode(raw, ctype), limit)
    if not results:
        raise BrowseError("Couldn't find that on YouTube.")
    return results


# ---- downloads

def _unique_path(name):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    stem, ext = os.path.splitext(safe_filename(name))
    path, n = os.path.join(DOWNLOAD_DIR, stem + ext), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(DOWNLOAD_DIR, "%s (%d)%s" % (stem, n, ext))
    return path


def download(url):
    url = normalize_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        resp = _opener.open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        raise BrowseError("Download failed: HTTP %s." % exc.code)
    except BrowseError:
        raise
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise BrowseError("Download failed: %s" % getattr(exc, "reason", exc))
    disp = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disp, re.I)
    name = m.group(1) if m else (urllib.parse.urlparse(resp.geturl()).path.rsplit("/", 1)[-1] or "download")
    path, size = _unique_path(name), 0
    try:
        with open(path, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise BrowseError("File is larger than the %d MB download limit." % (MAX_DOWNLOAD // 1048576))
                fh.write(chunk)
    except BaseException:
        if os.path.exists(path):
            os.remove(path)
        raise
    return {"name": os.path.basename(path), "bytes": size, "content_type": resp.headers.get("Content-Type", "")}


def list_downloads():
    if not os.path.isdir(DOWNLOAD_DIR):
        return []
    items = []
    for name in sorted(os.listdir(DOWNLOAD_DIR)):
        full = os.path.join(DOWNLOAD_DIR, name)
        if os.path.isfile(full):
            items.append({"name": name, "bytes": os.path.getsize(full)})
    return items


# ------------------------------------------------------------------ full engine (Playwright)

_COLLECT_JS = """() => {
  document.querySelectorAll('[data-omni-i]').forEach(e => e.removeAttribute('data-omni-i'));
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[onclick],summary';
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    i += 1; el.setAttribute('data-omni-i', i);
    out.push({i, tag: el.tagName.toLowerCase(), type: el.type || '',
      text: (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || el.title || '').trim().replace(/\\s+/g,' ').slice(0, 80),
      href: el.tagName === 'A' ? el.href : '' });
    if (i >= 60) break;
  }
  return out;
}"""


class FullBrowser:
    """Playwright's sync API is bound to the thread that created it, so one worker thread owns the
    browser and every operation is queued to it."""

    def __init__(self):
        self.q = queue.Queue()
        self.thread = None
        self.ready = threading.Event()
        self.error = None
        self.downloads = []
        self._start_lock = threading.Lock()

    # -- lifecycle
    def start(self):
        with self._start_lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="agent-browser", daemon=True)
                self.thread.start()
        self.ready.wait(60)
        if self.error:
            raise BrowseError("The full browser could not start: %s" % self.error)

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            args = []
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                args.append("--no-sandbox")
            browser = pw.chromium.launch(headless=HEADLESS, args=args)
            ctx = browser.new_context(viewport={"width": 1280, "height": 720}, accept_downloads=True)

            def guard(route):
                url = route.request.url
                if url.startswith(("http://", "https://")):
                    host = urllib.parse.urlparse(url).hostname or ""
                    if not host_allowed(host):
                        return route.abort()
                route.continue_()

            ctx.route("**/*", guard)
            holder = {"page": ctx.new_page()}

            def adopt(page):                       # popups / target=_blank become the active tab
                holder["page"] = page
                page.on("download", self._on_download)
            holder["page"].on("download", self._on_download)
            ctx.on("page", adopt)
        except Exception as exc:                   # not installed, no browser binary, no display...
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.ready.set()
            return
        self.ready.set()
        while True:
            try:
                fn, box = self.q.get(timeout=0.05)
            except queue.Empty:
                try:
                    holder["page"].wait_for_timeout(120)     # lets Playwright process events while idle
                except Exception:
                    pass
                continue
            try:
                box["result"] = fn(holder["page"])
            except Exception as exc:
                box["error"] = exc
            box["done"].set()

    def _on_download(self, dl):
        try:
            path = _unique_path(dl.suggested_filename)
            dl.save_as(path)
            self.downloads.append({"name": os.path.basename(path), "bytes": os.path.getsize(path)})
        except Exception:
            pass

    def call(self, fn, timeout=45):
        self.start()
        box = {"done": threading.Event()}
        self.q.put((fn, box))
        if not box["done"].wait(timeout):
            raise BrowseError("The browser took too long to respond.")
        if "error" in box:
            exc = box["error"]
            raise exc if isinstance(exc, BrowseError) else BrowseError("%s" % str(exc).splitlines()[0][:200])
        return box["result"]


_full = FullBrowser()
_full_probe = {"checked": False, "ok": False, "error": None}


def full_available():
    if not _full_probe["checked"]:
        _full_probe["checked"] = True
        try:
            import playwright  # noqa: F401
            _full_probe["ok"] = True
        except ImportError:
            _full_probe["error"] = "Playwright is not installed (pip install playwright && playwright install chromium)."
    return _full_probe["ok"] and _full.error is None


def local_engine():
    if ENGINE_PREF == "lite":
        return "lite"
    if ENGINE_PREF == "full":
        return "full"
    return "full" if full_available() else "lite"


def _settle(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
        page.wait_for_load_state("networkidle", timeout=2500)
    except Exception:
        pass


def _snapshot(page, max_chars=3500):
    try:
        text = page.inner_text("body", timeout=3000)
    except Exception:
        text = ""
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return {"url": page.url, "title": page.title(), "text": text[:max_chars],
            "truncated": len(text) > max_chars, "elements": page.evaluate(_COLLECT_JS)}


def f_open(p):
    url = normalize_url(p.get("url"))

    def go(page):
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _settle(page)
        return _snapshot(page)
    return _full.call(go, 60)


def f_state(_p):
    return _full.call(lambda page: _snapshot(page))


def f_click(p):
    def do(page):
        if p.get("index") is not None:
            page.click('[data-omni-i="%d"]' % int(p["index"]), timeout=5000)
        else:
            page.mouse.click(float(p["x"]), float(p["y"]))
        _settle(page)
        return _snapshot(page)
    return _full.call(do)


def f_type(p):
    text = str(p.get("text", ""))[:2000]

    def do(page):
        if p.get("index") is not None:
            page.fill('[data-omni-i="%d"]' % int(p["index"]), text, timeout=5000)
        else:
            page.keyboard.type(text, delay=15)
        if p.get("submit"):
            page.keyboard.press("Enter")
            _settle(page)
        return _snapshot(page)
    return _full.call(do)


def f_key(p):
    key = str(p.get("key", ""))[:30]
    if not re.match(r"^[A-Za-z0-9+_ ]+$", key):
        raise BrowseError("Unsupported key.")

    def do(page):
        page.keyboard.press(key)
        _settle(page)
        return _snapshot(page)
    return _full.call(do)


def f_scroll(p):
    dy = max(-3000, min(3000, int(p.get("dy", 600))))

    def do(page):
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(250)
        return _snapshot(page)
    return _full.call(do)


def f_nav(p):
    action = p.get("action")
    if action not in ("back", "forward", "reload"):
        raise BrowseError("Unknown navigation action.")

    def do(page):
        {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action](timeout=20000)
        _settle(page)
        return _snapshot(page)
    return _full.call(do, 40)


def screenshot_local():
    return _full.call(lambda page: page.screenshot(type="jpeg", quality=55), 20)


# ------------------------------------------------------------------ dispatcher (local or remote)

LOCAL_OPS = {
    "search": lambda p: {"results": web_search(p.get("query"))},
    "read": lambda p: read_page(p.get("url"), int(p.get("max_chars", 6000))),
    "music": lambda p: {"results": music_search(p.get("query"))},
    "download": lambda p: download(p.get("url")),
}
FULL_OPS = {"open": f_open, "state": f_state, "click": f_click, "type": f_type,
            "key": f_key, "scroll": f_scroll, "nav": f_nav}


def _remote_request(path, payload=None, method="POST", want_bytes=False):
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["X-Browser-Token"] = TOKEN
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    req = urllib.request.Request(REMOTE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=70) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error")
        except Exception:
            msg = None
        raise BrowseError(msg or "The remote browser answered HTTP %s." % exc.code)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise BrowseError("Can't reach the remote browser at %s (%s)." % (REMOTE, getattr(exc, "reason", exc)))
    return body if want_bytes else json.loads(body.decode())


def capabilities():
    if REMOTE:
        try:
            caps = _remote_request("/browse/capabilities", method="GET")
            caps["remote"] = REMOTE
            return caps
        except BrowseError as exc:
            return {"engine": "lite", "can_click": False, "remote": REMOTE, "remote_error": str(exc),
                    "download_dir": DOWNLOAD_DIR}
    engine = local_engine()
    err = None
    if engine == "full" and not full_available():
        engine, err = "lite", _full_probe["error"] or (_full.error and "Chromium failed to start: " + _full.error)
    return {"engine": engine, "can_click": engine == "full", "remote": None,
            "full_error": err or _full_probe["error"], "download_dir": DOWNLOAD_DIR}


def dispatch(op, payload):
    """Single entry point used by both the HTTP routes and the AI's tools."""
    payload = payload or {}
    if op in ("search", "read", "music"):            # stateless, always done locally (TV has internet)
        return LOCAL_OPS[op](payload)
    if REMOTE:
        return _remote_request("/browse/" + op, payload)
    if op == "download":
        return LOCAL_OPS[op](payload)
    if op in FULL_OPS:
        if local_engine() != "full" or not full_available():
            raise BrowseError("Clicking and typing need the full browser (Playwright + Chromium). "
                              "Only reading, searching and downloading are available right now.")
        return FULL_OPS[op](payload)
    raise BrowseError("Unknown browser operation.")


def screenshot():
    if REMOTE:
        return _remote_request("/browse/screenshot", method="GET", want_bytes=True)
    if local_engine() != "full" or not full_available():
        raise BrowseError("No live view in lite mode.")
    return screenshot_local()


# ------------------------------------------------------------------ HTTP routes

def _auth_ok():
    return not TOKEN or hmac.compare_digest(request.headers.get("X-Browser-Token", ""), TOKEN)


@bp.before_request
def _guard():
    # Only enforce the token when THIS process is a remote browser server (standalone mode).
    if STANDALONE and not _auth_ok():
        return jsonify({"error": "Bad or missing browser token."}), 403


STANDALONE = False


@bp.route("/browse/capabilities", methods=["GET"])
def r_caps():
    return jsonify(capabilities())


@bp.route("/browse/screenshot", methods=["GET"])
def r_shot():
    try:
        return Response(screenshot(), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})
    except BrowseError as exc:
        return jsonify({"error": str(exc)}), 409


@bp.route("/browse/downloads", methods=["GET"])
def r_downloads():
    items = list_downloads()
    if REMOTE:
        try:
            items = _remote_request("/browse/downloads", method="GET").get("downloads", [])
        except BrowseError:
            items = []
    return jsonify({"downloads": items, "dir": DOWNLOAD_DIR})


@bp.route("/browse/downloads/<path:name>", methods=["GET"])
def r_download_file(name):
    return send_from_directory(DOWNLOAD_DIR, safe_filename(name), as_attachment=True)


@bp.route("/browse/<op>", methods=["POST"])
def r_op(op):
    try:
        return jsonify(dispatch(op, request.get_json(silent=True) or {}))
    except BrowseError as exc:
        return jsonify({"error": str(exc)}), 400
    except (ValueError, TypeError, KeyError):
        return jsonify({"error": "Bad request."}), 400


def init(app, log):
    global _log
    _log = log
    app.register_blueprint(bp)
    caps = capabilities()
    log.info("Agent browser: engine=%s%s", caps["engine"], " (remote %s)" % REMOTE if REMOTE else "")


if __name__ == "__main__":      # standalone "browser server" for a laptop that serves the TV
    from flask import Flask
    try:
        from flask_cors import CORS
    except ImportError:
        CORS = None
    STANDALONE = True
    app = Flask(__name__)
    if CORS:
        CORS(app)
    app.register_blueprint(bp)
    host = os.environ.get("BROWSER_HOST", "127.0.0.1")
    port = int(os.environ.get("BROWSER_PORT", "5006"))
    if host != "127.0.0.1" and not TOKEN:
        print("WARNING: listening on %s with no BROWSER_TOKEN — anyone on the network can drive this browser." % host)
    print("Agent browser server on http://%s:%d  (engine: %s)" % (host, port, capabilities()["engine"]))
    app.run(host=host, port=port, threaded=True)
