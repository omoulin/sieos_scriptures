"""Sync notes and highlights with a Church Account (Gospel Library notes service).

The notes service is not a public API. Its shape here follows what the Gospel Library web app
uses (and the reverse-engineered `ldsnotes` project): annotations carry
`highlight.content[] = {uri: "/scriptures/bofm/1-ne/1.p3", color, style, startOffset, endOffset}`
with 1-based word offsets (-1 = whole paragraph), plus `note`, `tags` and `folders`.

Authentication reuses the browser session: the user signs in on churchofjesuschrist.org and
pastes the `Cookie` request header from their browser's developer tools. We never see or store
their password.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .api import BASE, USER_AGENT
from .store import data_dir

NOTES_BASES = [BASE + "/notes/api/v3", BASE + "/notes/api/v2"]
PAGE_SIZE = 500
TYPES = "highlight,journal,reference"


class AuthError(Exception):
    pass


# ---- credentials ------------------------------------------------------------

def _account_path():
    return data_dir() / "account.json"


def load_account():
    try:
        return json.loads(_account_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_account(cookie):
    cookie = cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[len("cookie:"):].strip()
    path = _account_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"cookie": cookie, "saved_at": datetime.now(timezone.utc).isoformat()}, f)


def clear_account():
    _account_path().unlink(missing_ok=True)


# ---- HTTP -------------------------------------------------------------------

class NotesClient:
    def __init__(self, cookie):
        if not cookie:
            raise AuthError("Not connected. Add your session cookie first.")
        self.cookie = cookie
        self.base = None

    def request(self, method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": self.cookie,
            "Origin": BASE,
            "Referer": BASE + "/notes",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError(
                    "The Church Account session was rejected (expired or invalid cookie). "
                    "Sign in again in your browser and paste a fresh cookie."
                ) from e
            raise

    def discover(self):
        """Finds the API version that accepts this session."""
        last = None
        for base in NOTES_BASES:
            try:
                self.request("GET", base + "/annotations?" + urllib.parse.urlencode(
                    {"start": 1, "numberToReturn": 1, "type": TYPES}))
                self.base = base
                return base
            except urllib.error.HTTPError as e:
                last = e
        raise RuntimeError(f"No notes API endpoint answered: {last}")

    def annotations(self):
        if not self.base:
            self.discover()
        start, out = 1, []
        while True:
            page = self.request("GET", self.base + "/annotations?" + urllib.parse.urlencode({
                "start": start, "numberToReturn": PAGE_SIZE, "type": TYPES,
                "notesAsHtml": "false",
            }))
            if isinstance(page, dict):
                page = page.get("annotations") or page.get("items") or []
            out += page or []
            if not page or len(page) < PAGE_SIZE:
                return out
            start += PAGE_SIZE

    def put(self, annotation):
        aid = urllib.parse.quote(annotation["id"])
        try:
            return self.request("PUT", f"{self.base}/annotations/{aid}", annotation)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 405):
                raise
        return self.request("POST", f"{self.base}/annotations", [annotation])

    def delete(self, annotation_id):
        aid = urllib.parse.quote(annotation_id)
        try:
            self.request("DELETE", f"{self.base}/annotations/{aid}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise


# ---- format conversion ------------------------------------------------------

def _ts(iso):
    if not iso:
        return datetime.now(timezone.utc).timestamp()
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


def _split_uri(uri):
    """'/scriptures/bofm/1-ne/1.p3' -> ('/scriptures/bofm/1-ne/1', 'p3')."""
    path, _, anchor = uri.rpartition(".")
    if not path or "/" in anchor:
        return uri, None
    return path, anchor


def remote_to_local(a):
    """Converts a server annotation to a local note dict, or None if unsupported (bookmarks)."""
    if a.get("type") == "bookmark":
        return None
    hl = (a.get("highlight") or {}).get("content") or []
    spans, uri, color, style = [], "", None, None
    for h in hl:
        chapter, anchor = _split_uri(h.get("uri") or "")
        uri = uri or chapter
        if chapter != uri or not anchor:
            continue  # highlights spanning chapters are kept to the first chapter
        s, e = int(h.get("startOffset", -1)), int(h.get("endOffset", -1))
        spans.append({"anchor": anchor, "start": s - 1 if s > 0 else -1,
                      "end": e - 1 if e > 0 else -1})
        color = color or h.get("color")
        style = style or h.get("style")
    note = a.get("note") or {}
    tags = [t.get("name", "") if isinstance(t, dict) else t for t in a.get("tags") or []]
    updated = _ts(a.get("lastUpdated"))
    return {
        "id": a.get("id") or a.get("annotationId"),
        "lang": a.get("locale") or "eng",
        "uri": uri,
        "anchor": spans[0]["anchor"] if spans else None,
        "spans": spans,
        "color": color,
        "style": style,
        "title": note.get("title") or "",
        "content": note.get("content") or "",
        "tags": [t for t in tags if t],
        "created_at": _ts(a.get("created")) if a.get("created") else updated,
        "updated_at": updated,
        "deleted": 1 if a.get("status") in ("trashed", "deleted") else 0,
    }


def local_to_remote(n):
    """Converts a local note row back to the server's shape, preserving unknown remote fields."""
    a = json.loads(n["remote"]) if n.get("remote") else {}
    spans = json.loads(n["spans"]) if isinstance(n["spans"], str) else n["spans"]
    tags = json.loads(n["tags"]) if isinstance(n["tags"], str) else n["tags"]
    old_hl = {h.get("uri"): h for h in (a.get("highlight") or {}).get("content") or []}
    if any(isinstance(t, dict) for t in a.get("tags") or []):
        tags = [{"name": t} for t in tags]  # keep the server's tag shape
    a.update(id=n["id"], locale=n["lang"], lastUpdated=_iso(n["updated_at"]), tags=tags)
    a.setdefault("folders", [])
    if n["title"] or n["content"]:
        a["note"] = {"title": n["title"], "content": n["content"]}
    else:
        a.pop("note", None)
    if spans and n["uri"]:
        content = []
        for s in spans:
            uri = f"{n['uri']}.{s['anchor']}"
            h = dict(old_hl.get(uri, {}))
            h.update(uri=uri, color=n["color"] or "clear", style=n["style"] or "",
                     startOffset=s["start"] + 1 if s["start"] >= 0 else -1,
                     endOffset=s["end"] + 1 if s["end"] >= 0 else -1)
            content.append(h)
        a["type"] = "reference" if a.get("refs") else "highlight"
        a["highlight"] = {"content": content}
    else:
        a["type"] = "journal"
        a.pop("highlight", None)
    return a


# ---- sync -------------------------------------------------------------------

def sync(store, push=False, log=print):
    """Pulls all annotations into the local store; with push=True also uploads local edits."""
    client = NotesClient(load_account().get("cookie"))
    base = client.discover()
    log(f"Connected to {base}")
    stats = {"pulled": 0, "created": 0, "updated": 0, "kept-local": 0, "skipped": 0,
             "pushed": 0, "deleted": 0, "push_errors": 0}

    remote = client.annotations()
    for a in remote:
        note = remote_to_local(a)
        if not note or not note["id"]:
            stats["skipped"] += 1
            continue
        stats["pulled"] += 1
        stats[store.upsert_remote_note(note, a)] += 1
    log(f"Pulled {stats['pulled']} annotations "
        f"({stats['created']} new, {stats['updated']} updated, {stats['skipped']} skipped)")

    if push:
        for n in store.dirty_notes():
            try:
                if n["deleted"]:
                    if n["remote"]:
                        client.delete(n["id"])
                    store.mark_synced(n["id"], purge=True)
                    stats["deleted"] += 1
                else:
                    a = local_to_remote(n)
                    client.put(a)
                    store.mark_synced(n["id"], a)
                    stats["pushed"] += 1
            except urllib.error.HTTPError as e:
                stats["push_errors"] += 1
                log(f"Push failed for {n['id']}: HTTP {e.code} {e.read()[:200]!r}")
        log(f"Pushed {stats['pushed']}, deleted {stats['deleted']}, errors {stats['push_errors']}")
    store.set_setting("last_sync", {"at": _iso(datetime.now(timezone.utc).timestamp()),
                                    "stats": stats})
    return stats
