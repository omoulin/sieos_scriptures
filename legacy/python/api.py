"""Client for the public Gospel Library content API used by churchofjesuschrist.org/study."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.churchofjesuschrist.org"
CONTENT_URL = BASE + "/study/api/v3/language-pages/type/content"
DYNAMIC_URL = BASE + "/study/api/v3/language-pages/type/dynamic"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 lds-scripture-offline/0.1"
)

# Standard works, in library order.
VOLUMES = [
    ("ot", "Old Testament"),
    ("nt", "New Testament"),
    ("bofm", "Book of Mormon"),
    ("dc-testament", "Doctrine and Covenants"),
    ("pgp", "Pearl of Great Price"),
]
# Study helps are large; downloaded only on request.
STUDY_HELPS = [
    ("tg", "Topical Guide"),
    ("bd", "Bible Dictionary"),
    ("gs", "Guide to the Scriptures"),
    ("jst", "Joseph Smith Translation"),
]


class ApiError(Exception):
    pass


class RateLimiter:
    """Spaces requests at least `interval` seconds apart across threads."""

    def __init__(self, interval):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


_limiter = RateLimiter(0.15)


def get_json(url, params=None, headers=None, retries=3, timeout=30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        _limiter.wait()
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise ApiError(f"HTTP {e.code} for {url}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise ApiError(f"Failed to fetch {url}: {last}")


def normalize_uri(href):
    """'/study/scriptures/bofm/1-ne/1?lang=eng#p3' -> '/scriptures/bofm/1-ne/1'."""
    path = urllib.parse.urlsplit(href).path
    if path.startswith("/study/"):
        path = path[len("/study"):]
    return path.rstrip("/") or "/"


def fetch_chapter(uri, lang):
    data = get_json(CONTENT_URL, {"lang": lang, "uri": uri})
    content = data.get("content") or {}
    meta = data.get("meta") or {}
    return {
        "uri": uri,
        "lang": lang,
        "title": meta.get("title") or uri,
        "body": content.get("body") or "",
        "footnotes": content.get("footnotes") or {},
        "audio": meta.get("audio") or [],
        "content_type": (meta.get("pageAttributes") or {}).get("data-content-type", ""),
    }


def fetch_manifest(uri, lang):
    """Returns (title, body_html) of a volume's table of contents."""
    data = get_json(DYNAMIC_URL, {"lang": lang, "uri": uri})
    page = (data.get("content") or {})
    inner = page.get("content") or {}
    title = (page.get("meta") or {}).get("title") or (data.get("toc") or {}).get("title") or uri
    return title, inner.get("body") or ""
