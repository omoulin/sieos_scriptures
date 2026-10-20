"""SQLite storage for downloaded scriptures and personal notes."""

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS volumes (
    lang TEXT NOT NULL,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    toc TEXT NOT NULL,             -- JSON tree from parse_manifest
    position INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (lang, code)
);
CREATE TABLE IF NOT EXISTS chapters (
    lang TEXT NOT NULL,
    uri TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    footnotes TEXT NOT NULL,       -- JSON
    audio TEXT NOT NULL,           -- JSON
    content_type TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (lang, uri)
);
CREATE TABLE IF NOT EXISTS verses (
    lang TEXT NOT NULL,
    uri TEXT NOT NULL,
    pid TEXT NOT NULL,
    anchor TEXT NOT NULL,
    num INTEGER,
    text TEXT NOT NULL,
    PRIMARY KEY (lang, uri, pid)
);
CREATE VIRTUAL TABLE IF NOT EXISTS verses_fts USING fts5(
    text, lang UNINDEXED, uri UNINDEXED, pid UNINDEXED, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,           -- local id; equals remote annotationId once synced
    lang TEXT NOT NULL,
    uri TEXT NOT NULL,             -- chapter uri
    anchor TEXT,                   -- first paragraph element id, e.g. p3 (null = chapter note)
    spans TEXT NOT NULL DEFAULT '[]',  -- JSON [{anchor, start, end}], 0-based inclusive word
                                       -- indexes within each paragraph; -1/-1 = whole paragraph
    color TEXT,                    -- null = no highlight
    style TEXT,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    dirty INTEGER NOT NULL DEFAULT 1,  -- changed locally since last sync
    remote TEXT                     -- last raw annotation JSON from the server
);
CREATE INDEX IF NOT EXISTS notes_uri ON notes (uri, deleted);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def data_dir():
    d = Path(os.environ.get("LDS_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


class Store:
    def __init__(self, path=None):
        self.path = str(path or data_dir() / "library.db")
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return c

    # ---- library -------------------------------------------------------

    def save_volume(self, lang, code, title, toc, position):
        with self._write_lock, self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO volumes VALUES (?,?,?,?,?,?)",
                (lang, code, title, json.dumps(toc), position, time.time()),
            )

    def volumes(self, lang):
        rows = self.conn().execute(
            "SELECT code, title, toc FROM volumes WHERE lang=? ORDER BY position", (lang,)
        ).fetchall()
        return [{"code": r["code"], "title": r["title"], "toc": json.loads(r["toc"])} for r in rows]

    def languages(self):
        return [r[0] for r in self.conn().execute("SELECT DISTINCT lang FROM volumes ORDER BY lang")]

    def has_chapter(self, lang, uri):
        return self.conn().execute(
            "SELECT 1 FROM chapters WHERE lang=? AND uri=?", (lang, uri)
        ).fetchone() is not None

    def downloaded_uris(self, lang):
        return {r[0] for r in self.conn().execute("SELECT uri FROM chapters WHERE lang=?", (lang,))}

    def save_chapter(self, ch, verses):
        with self._write_lock, self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?,?,?,?)",
                (ch["lang"], ch["uri"], ch["title"], ch["body"], json.dumps(ch["footnotes"]),
                 json.dumps(ch["audio"]), ch["content_type"], time.time()),
            )
            c.execute("DELETE FROM verses WHERE lang=? AND uri=?", (ch["lang"], ch["uri"]))
            c.execute("DELETE FROM verses_fts WHERE lang=? AND uri=?", (ch["lang"], ch["uri"]))
            for v in verses:
                c.execute(
                    "INSERT OR REPLACE INTO verses VALUES (?,?,?,?,?,?)",
                    (ch["lang"], ch["uri"], v["pid"], v["id"], v["num"], v["text"]),
                )
                if v["kind"] == "verse":
                    c.execute(
                        "INSERT INTO verses_fts (text, lang, uri, pid) VALUES (?,?,?,?)",
                        (v["text"], ch["lang"], ch["uri"], v["pid"]),
                    )

    def chapter(self, lang, uri):
        r = self.conn().execute(
            "SELECT * FROM chapters WHERE lang=? AND uri=?", (lang, uri)
        ).fetchone()
        if not r:
            return None
        return {
            "lang": r["lang"], "uri": r["uri"], "title": r["title"], "body": r["body"],
            "footnotes": json.loads(r["footnotes"]), "audio": json.loads(r["audio"]),
            "content_type": r["content_type"],
        }

    def search(self, lang, query, limit=100):
        terms = [t.replace('"', "") for t in query.split() if t.strip('"')]
        if not terms:
            return []
        match = " ".join(f'"{t}"' for t in terms)
        rows = self.conn().execute(
            """SELECT f.uri, f.pid, v.anchor, v.num, c.title,
                      snippet(verses_fts, 0, '<mark>', '</mark>', '…', 24) AS snip
               FROM verses_fts f
               JOIN verses v ON v.lang=f.lang AND v.uri=f.uri AND v.pid=f.pid
               JOIN chapters c ON c.lang=f.lang AND c.uri=f.uri
               WHERE verses_fts MATCH ? AND f.lang=?
               ORDER BY rank LIMIT ?""",
            (match, lang, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def verse_text(self, lang, uri, anchor):
        r = self.conn().execute(
            "SELECT num, text FROM verses WHERE lang=? AND uri=? AND anchor=?", (lang, uri, anchor)
        ).fetchone()
        return dict(r) if r else None

    def chapter_title(self, lang, uri):
        r = self.conn().execute(
            "SELECT title FROM chapters WHERE lang=? AND uri=?", (lang, uri)).fetchone()
        return r[0] if r else None

    # ---- notes ---------------------------------------------------------

    NOTE_FIELDS = ("lang", "uri", "anchor", "spans", "color", "style", "title", "content", "tags")
    JSON_FIELDS = ("spans", "tags")

    @staticmethod
    def _note_row(r):
        d = dict(r)
        for k in Store.JSON_FIELDS:
            d[k] = json.loads(d[k])
        d.pop("remote", None)
        return d

    def notes_for(self, uri):
        rows = self.conn().execute(
            "SELECT * FROM notes WHERE uri=? AND deleted=0 ORDER BY created_at", (uri,)
        ).fetchall()
        return [self._note_row(r) for r in rows]

    def all_notes(self, include_deleted=False):
        q = "SELECT * FROM notes" + ("" if include_deleted else " WHERE deleted=0")
        rows = self.conn().execute(q + " ORDER BY updated_at DESC").fetchall()
        return [self._note_row(r) for r in rows]

    def get_note(self, note_id, raw=False):
        r = self.conn().execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not r:
            return None
        return dict(r) if raw else self._note_row(r)

    def create_note(self, data):
        now = time.time()
        note = {k: data.get(k) for k in self.NOTE_FIELDS}
        for k in self.JSON_FIELDS:
            note[k] = json.dumps(data.get(k) or [])
        note["title"] = note["title"] or ""
        note["content"] = note["content"] or ""
        if not note["anchor"] and data.get("spans"):
            note["anchor"] = data["spans"][0]["anchor"]
        note.update(id=data.get("id") or str(uuid.uuid4()), created_at=now, updated_at=now)
        cols = ",".join(note)
        with self._write_lock, self.conn() as c:
            c.execute(f"INSERT INTO notes ({cols}) VALUES ({','.join('?' * len(note))})",
                      list(note.values()))
        return self.get_note(note["id"])

    def update_note(self, note_id, data):
        sets, vals = [], []
        for k in self.NOTE_FIELDS:
            if k in data:
                sets.append(f"{k}=?")
                vals.append(json.dumps(data[k]) if k in self.JSON_FIELDS else data[k])
        sets += ["updated_at=?", "dirty=1"]
        vals += [time.time(), note_id]
        with self._write_lock, self.conn() as c:
            c.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id=?", vals)
        return self.get_note(note_id)

    def delete_note(self, note_id):
        with self._write_lock, self.conn() as c:
            c.execute("UPDATE notes SET deleted=1, dirty=1, updated_at=? WHERE id=?",
                      (time.time(), note_id))

    def upsert_remote_note(self, note, remote_raw):
        """Writes a note received from the server, unless the local copy has unsynced edits."""
        existing = self.get_note(note["id"], raw=True)
        if existing and existing["dirty"] and existing["updated_at"] > note["updated_at"]:
            return "kept-local"
        row = {k: note.get(k) for k in self.NOTE_FIELDS}
        for k in self.JSON_FIELDS:
            row[k] = json.dumps(note.get(k) or [])
        row.update(id=note["id"], created_at=note.get("created_at") or note["updated_at"],
                   updated_at=note["updated_at"], deleted=int(note.get("deleted", 0)),
                   dirty=0, remote=json.dumps(remote_raw))
        cols = ",".join(row)
        with self._write_lock, self.conn() as c:
            c.execute(f"INSERT OR REPLACE INTO notes ({cols}) VALUES ({','.join('?' * len(row))})",
                      list(row.values()))
        return "updated" if existing else "created"

    def dirty_notes(self):
        return [dict(r) for r in self.conn().execute("SELECT * FROM notes WHERE dirty=1")]

    def mark_synced(self, note_id, remote_raw=None, purge=False):
        with self._write_lock, self.conn() as c:
            if purge:
                c.execute("DELETE FROM notes WHERE id=?", (note_id,))
            else:
                c.execute("UPDATE notes SET dirty=0, remote=COALESCE(?, remote) WHERE id=?",
                          (json.dumps(remote_raw) if remote_raw else None, note_id))

    # ---- settings ------------------------------------------------------

    def get_setting(self, key, default=None):
        r = self.conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_setting(self, key, value):
        with self._write_lock, self.conn() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
