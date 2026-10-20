"""Command line: download, login, sync, search; with no command, opens the GNOME app."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import api, sync
from .downloader import DownloadJob
from .store import Store, data_dir

DEFAULT_CODES = [c for c, _ in api.VOLUMES]


def cmd_download(store, args):
    codes = args.volumes or DEFAULT_CODES
    if args.study_helps:
        codes += [c for c, _ in api.STUDY_HELPS]

    def progress(job):
        sys.stdout.write(f"\r{job.done}/{job.total} pages")
        sys.stdout.flush()

    job = DownloadJob(store, args.lang, codes, workers=args.workers, refresh=args.refresh)
    job.run(progress)
    print(f"\n{job.status}: {job.message}")
    for uri, err in job.failed[:10]:
        print(f"  failed {uri}: {err}")
    return 0 if job.status == "done" else 1


def cmd_gui(_store, _args):
    try:
        from .gui.app import main as gui_main
    except ImportError:
        sys.exit("The app needs GTK 4 / libadwaita bindings. Run it with the system Python:\n"
                 "  bin/lds-scripture    (or: /usr/bin/python3 -m lds_scripture)")
    return gui_main()


def cmd_login(_store, args):
    if args.logout:
        sync.clear_account()
        shutil.rmtree(data_dir() / "webkit", ignore_errors=True)
        print("Disconnected.")
        return
    if args.firefox:
        from .browser_cookies import firefox_cookie_header
        try:
            cookie, profile = firefox_cookie_header()
        except LookupError as e:
            sys.exit(str(e))
        print(f"Found a session in Firefox profile {profile.name}.")
    elif args.paste:
        print(__doc_login__)
        cookie = input("Cookie: ").strip()
        if not cookie:
            sys.exit("Nothing saved.")
    else:
        # Sign-in window; it needs the system Python's GTK 3 + WebKitGTK bindings.
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent),
                   LDS_DATA_DIR=str(data_dir()))
        res = subprocess.run(["/usr/bin/python3", "-m", "lds_scripture.signin"],
                             env=env, stdout=subprocess.PIPE, text=True)
        if res.returncode == 2:
            sys.exit("Sign-in window unavailable (needs gir1.2-webkit2-4.1). "
                     "Try: login --firefox, or login --paste")
        if res.returncode != 0:
            sys.exit("Sign-in cancelled.")
        cookie = json.loads(res.stdout)["cookie"]
    try:
        base = sync.NotesClient(cookie).discover()
    except Exception as e:
        sys.exit(f"The session was not accepted: {e}")
    sync.save_account(cookie)
    print(f"Connected. Notes API: {base}")


def cmd_sync(store, args):
    stats = sync.sync(store, push=args.push)
    print(stats)


def cmd_search(store, args):
    for r in store.search(args.lang, " ".join(args.query), limit=args.limit):
        snip = r["snip"].replace("<mark>", "\033[1m").replace("</mark>", "\033[0m")
        print(f"{r['title']}:{r['num'] or ''}  {snip}")


__doc_login__ = """\
Paste a session cookie:
  1. In your browser, sign in at https://www.churchofjesuschrist.org/notes
  2. Open developer tools (F12) > Network, reload the page, select any request to
     www.churchofjesuschrist.org and copy the value of the 'Cookie' request header.
  3. Paste it below. It is stored only in data/account.json (permissions 600).
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="lds_scripture", description=__doc__)
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("download", help="download scriptures for offline use")
    d.add_argument("volumes", nargs="*", help=f"volume codes (default: {' '.join(DEFAULT_CODES)}); "
                   f"study helps: {' '.join(c for c, _ in api.STUDY_HELPS)}")
    d.add_argument("--lang", default="eng", help="language code, e.g. eng, spa, fra, por, deu")
    d.add_argument("--study-helps", action="store_true", help="also download TG, BD, GS and JST")
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--refresh", action="store_true", help="re-download pages already stored")
    d.set_defaults(fn=cmd_download)

    g = sub.add_parser("gui", help="open the GNOME app (the default)")
    g.set_defaults(fn=cmd_gui)

    lg = sub.add_parser("login", help="connect your Church Account (opens a sign-in window)")
    lg.add_argument("--firefox", action="store_true", help="reuse your Firefox session instead")
    lg.add_argument("--paste", action="store_true", help="paste a Cookie header manually")
    lg.add_argument("--logout", action="store_true")
    lg.set_defaults(fn=cmd_login)

    sy = sub.add_parser("sync", help="pull notes from your Church Account")
    sy.add_argument("--push", action="store_true", help="also upload local changes (experimental)")
    sy.set_defaults(fn=cmd_sync)

    se = sub.add_parser("search", help="full-text search downloaded scriptures")
    se.add_argument("query", nargs="+")
    se.add_argument("--lang", default="eng")
    se.add_argument("--limit", type=int, default=20)
    se.set_defaults(fn=cmd_search)

    args = p.parse_args(argv)
    if not args.cmd:
        return cmd_gui(None, args)
    return args.fn(Store(), args)


if __name__ == "__main__":
    sys.exit(main() or 0)
