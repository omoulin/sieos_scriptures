"""Reads the churchofjesuschrist.org session from the user's Firefox profile.

Only cookies that the browser would send to www.churchofjesuschrist.org are read; nothing else
in the profile is touched. Firefox stores cookies unencrypted on Linux. (Chromium-based browsers
encrypt theirs with the desktop keyring, so they are not supported.)
"""

import configparser
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

TARGET_HOST = "www.churchofjesuschrist.org"
FIREFOX_ROOTS = [
    "~/.config/mozilla/firefox",                                  # Firefox 147+ (XDG location)
    "~/.mozilla/firefox",
    "~/snap/firefox/common/.mozilla/firefox",
    "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
    "~/.var/app/org.mozilla.firefox/config/mozilla/firefox",
]


def firefox_profiles():
    """Profile directories that have a cookie database, default profile first."""
    found = []
    for root in FIREFOX_ROOTS:
        root = Path(root).expanduser()
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        defaults = {cfg[s].get("Default") for s in cfg.sections() if s.startswith("Install")}
        for s in cfg.sections():
            if not s.startswith("Profile") or "Path" not in cfg[s]:
                continue
            p = cfg[s]["Path"]
            path = root / p if cfg[s].get("IsRelative", "1") == "1" else Path(p)
            if (path / "cookies.sqlite").is_file():
                found.append((p in defaults or cfg[s].get("Default") == "1", path))
    found.sort(key=lambda t: not t[0])
    return [p for _, p in found]


def _domain_matches(host):
    host = host.lstrip(".")
    return TARGET_HOST == host or TARGET_HOST.endswith("." + host)


def _read(profile):
    """Copies the cookie DB (Firefox keeps it locked and uses WAL) and reads matching cookies."""
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal"):
            src = profile / f"cookies.sqlite{suffix}"
            if src.exists():
                shutil.copy2(src, Path(tmp) / src.name)
        db = sqlite3.connect(Path(tmp) / "cookies.sqlite")
        try:
            rows = db.execute(
                "SELECT host, name, value, path, expiry, lastAccessed FROM moz_cookies "
                "WHERE host LIKE '%churchofjesuschrist.org'").fetchall()
        finally:
            db.close()
    now = time.time()
    cookies, newest = {}, 0
    for host, name, value, path, expiry, last in rows:
        if not _domain_matches(host) or not (path or "/").startswith("/"):
            continue
        # Firefox has stored expiry in seconds and, more recently, in milliseconds.
        exp = expiry / 1000 if expiry and expiry > 1e11 else expiry
        if exp and exp < now:
            continue
        # Prefer the most specific (longest host) cookie when names collide.
        prev = cookies.get(name)
        if prev is None or len(host) > len(prev[0]):
            cookies[name] = (host, value)
        newest = max(newest, last or 0)
    return "; ".join(f"{n}={v}" for n, (_, v) in cookies.items()), newest


def firefox_cookie_header():
    """Returns (cookie_header, profile_path) from the most recently used matching profile."""
    best = (None, None, -1)
    for profile in firefox_profiles():
        try:
            header, last = _read(profile)
        except sqlite3.Error:
            continue
        if header and last > best[2]:
            best = (header, profile, last)
    if not best[0]:
        raise LookupError("No churchofjesuschrist.org session found in Firefox. Sign in at "
                          "churchofjesuschrist.org in Firefox first, or use Sign In.")
    return best[0], best[1]
