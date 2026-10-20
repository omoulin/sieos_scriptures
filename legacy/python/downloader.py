"""Downloads volumes (table of contents + every chapter) into the local store."""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import api
from .parse import iter_leaves, parse_manifest, parse_verses, sanitize

ALL_CODES = [c for c, _ in api.VOLUMES + api.STUDY_HELPS]


def fetch_and_store_chapter(store, lang, uri):
    ch = api.fetch_chapter(uri, lang)
    ch["body"] = sanitize(ch["body"])
    store.save_chapter(ch, parse_verses(ch["body"]))
    return ch


class DownloadJob:
    """Downloads one or more volumes for a language; progress is readable from other threads."""

    def __init__(self, store, lang, codes, workers=4, refresh=False):
        self.store = store
        self.lang = lang
        self.codes = codes
        self.workers = workers
        self.refresh = refresh
        self.total = 0
        self.done = 0
        self.failed = []
        self.status = "pending"
        self.message = ""
        self.cancelled = threading.Event()

    def snapshot(self):
        return {
            "lang": self.lang, "codes": self.codes, "total": self.total, "done": self.done,
            "failed": len(self.failed), "status": self.status, "message": self.message,
        }

    def run(self, on_progress=None):
        self.status = "running"
        try:
            pending = []
            for code in self.codes:
                position = ALL_CODES.index(code) if code in ALL_CODES else len(ALL_CODES)
                self.message = f"Reading table of contents for {code}"
                title, html = api.fetch_manifest(f"/scriptures/{code}", self.lang)
                toc = parse_manifest(html)
                self.store.save_volume(self.lang, code, title, toc, position)
                have = set() if self.refresh else self.store.downloaded_uris(self.lang)
                pending += [leaf["uri"] for leaf in iter_leaves(toc) if leaf["uri"] not in have]
            pending = list(dict.fromkeys(pending))
            self.total = len(pending)
            self.message = f"Downloading {self.total} pages"
            with ThreadPoolExecutor(self.workers) as pool:
                futures = {pool.submit(self._one, uri): uri for uri in pending}
                for fut in as_completed(futures):
                    if fut.exception():
                        self.failed.append((futures[fut], str(fut.exception())))
                    self.done += 1
                    if on_progress:
                        on_progress(self)
            self.status = "cancelled" if self.cancelled.is_set() else "done"
            self.message = f"Downloaded {self.done - len(self.failed)} of {self.total} pages"
            if self.failed:
                self.message += f" ({len(self.failed)} failed; run again to retry)"
        except Exception as e:  # surfaced to the UI / CLI
            self.status = "error"
            self.message = str(e)
        return self

    def _one(self, uri):
        if self.cancelled.is_set():
            raise RuntimeError("cancelled")
        fetch_and_store_chapter(self.store, self.lang, uri)
