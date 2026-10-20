"""Note editor and the Library & Account preferences dialog."""

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .. import api, sync
from ..browser_cookies import firefox_cookie_header
from ..store import data_dir
from ..downloader import DownloadJob
from .reader import COLOR_ALIASES, COLORS

LANGS = {
    "eng": "English", "spa": "Español", "por": "Português", "fra": "Français", "deu": "Deutsch",
    "ita": "Italiano", "rus": "Русский", "jpn": "日本語", "kor": "한국어", "zho": "中文 (繁體)",
    "zhs": "中文 (简体)", "tgl": "Tagalog", "nld": "Nederlands", "ukr": "Українська",
}
STANDARD = [c for c, _ in api.VOLUMES]


def swatch_button(color, tooltip=None, toggle=False):
    btn = (Gtk.ToggleButton if toggle else Gtk.Button)(tooltip_text=tooltip or color.title())
    btn.add_css_class("swatch")
    btn.add_css_class("hl-" + color if color else "swatch-none")
    btn.set_valign(Gtk.Align.CENTER)
    if not color:
        btn.set_icon_name("action-unavailable-symbolic")
    return btn


class NoteDialog(Adw.Dialog):
    def __init__(self, note, quote, reference, on_save, on_delete):
        super().__init__(title="Note", content_width=500, content_height=620)
        self.note, self.on_save, self.on_delete = note, on_save, on_delete

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save.connect("clicked", self._save)
        header.pack_start(cancel)
        header.pack_end(save)
        self.set_default_widget(save)

        page = Adw.PreferencesPage()
        quote_group = Adw.PreferencesGroup(title=reference)
        if quote:
            lbl = Gtk.Label(label=quote, wrap=True, xalign=0, selectable=True,
                            css_classes=["reader-quote", "dim-label"])
            quote_group.add(lbl)
        page.add(quote_group)

        hl_group = Adw.PreferencesGroup(title="Highlight")
        box = Gtk.Box(spacing=8)
        current = COLOR_ALIASES.get(note.get("color"), note.get("color")) or ""
        self.color = current
        first = None
        for c in [""] + COLORS:
            b = swatch_button(c, "No highlight" if not c else None, toggle=True)
            if first:
                b.set_group(first)
            first = first or b
            b.set_active(c == current)
            b.connect("toggled", lambda btn, c=c: btn.get_active() and setattr(self, "color", c))
            box.append(b)
        hl_group.add(box)
        page.add(hl_group)

        fields = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title="Title", text=note.get("title") or "")
        self.tags_row = Adw.EntryRow(title="Tags (comma separated)", text=", ".join(note.get("tags") or []))
        fields.add(self.title_row)
        fields.add(self.tags_row)
        page.add(fields)

        body_group = Adw.PreferencesGroup(title="Note")
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                 left_margin=10, right_margin=10, css_classes=["note-text"])
        self.text.get_buffer().set_text(note.get("content") or "")
        frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=self.text, min_content_height=180))
        body_group.add(frame)
        page.add(body_group)

        del_group = Adw.PreferencesGroup()
        delete = Gtk.Button(label="Delete Highlight and Note", halign=Gtk.Align.CENTER,
                            css_classes=["destructive-action", "pill"])
        delete.connect("clicked", self._confirm_delete)
        del_group.add(delete)
        page.add(del_group)

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_focus(self.text)

    def _save(self, *_):
        buf = self.text.get_buffer()
        self.on_save(self.note, {
            "color": self.color or None,
            "title": self.title_row.get_text().strip(),
            "content": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False),
            "tags": [t.strip() for t in self.tags_row.get_text().split(",") if t.strip()],
        })
        self.close()

    def _confirm_delete(self, *_):
        alert = Adw.AlertDialog(heading="Delete Highlight?",
                                body="The highlight and its note will be removed.")
        alert.add_response("cancel", "Cancel")
        alert.add_response("delete", "Delete")
        alert.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, response):
            if response == "delete":
                self.on_delete(self.note)
                self.close()

        alert.connect("response", done)
        alert.present(self)


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__(title="Library & Account", search_enabled=False)
        self.win = window
        self.app = window.get_application()
        self.store = window.store
        self._timer = None
        self.add(self._library_page())
        self.add(self._account_page())
        self.connect("closed", lambda *_: self._timer and GLib.source_remove(self._timer))
        if self.app.job and self.app.job.status == "running":
            self._watch_job()

    # ---- library ----------------------------------------------------------------

    def _library_page(self):
        page = Adw.PreferencesPage(title="Library", icon_name="folder-download-symbolic")

        lang_group = Adw.PreferencesGroup(
            title="Language", description="Used for reading, search and downloads.")
        codes = list(LANGS) + [c for c in self.store.languages() if c not in LANGS]
        if self.win.lang not in codes:
            codes.append(self.win.lang)
        self.lang_codes = codes
        self.lang_row = Adw.ComboRow(title="Language", model=Gtk.StringList.new(
            [f"{LANGS.get(c, c)}" + ("" if c in LANGS else f" ({c})") for c in codes]))
        self.lang_row.set_selected(codes.index(self.win.lang))
        self.lang_row.connect("notify::selected", self._on_lang)
        lang_group.add(self.lang_row)
        other = Adw.EntryRow(title="Other language code (e.g. nld, swe)", show_apply_button=True)
        other.connect("apply", lambda row: self._set_lang(row.get_text().strip().lower()))
        lang_group.add(other)
        page.add(lang_group)

        self.vol_group = Adw.PreferencesGroup(
            title="Volumes", description="Downloaded from churchofjesuschrist.org for offline reading.")
        self.vol_checks = {}
        self.vol_rows = {}
        for code, title in api.VOLUMES + api.STUDY_HELPS:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            row = Adw.ActionRow(title=title, activatable_widget=check)
            row.add_prefix(check)
            self.vol_checks[code], self.vol_rows[code] = check, row
            self.vol_group.add(row)
        page.add(self.vol_group)
        self._refresh_counts(initial=True)

        actions = Adw.PreferencesGroup()
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.dl_button = Gtk.Button(label="Download Selected", css_classes=["pill", "suggested-action"])
        self.dl_button.connect("clicked", self._start_download)
        self.cancel_button = Gtk.Button(label="Cancel", css_classes=["pill"], visible=False)
        self.cancel_button.connect("clicked", lambda *_: self.app.job and self.app.job.cancelled.set())
        buttons.append(self.dl_button)
        buttons.append(self.cancel_button)
        actions.add(buttons)
        page.add(actions)
        self.progress_group = Adw.PreferencesGroup(visible=False)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress_group.add(self.progress)
        page.add(self.progress_group)
        return page

    def _refresh_counts(self, initial=False):
        lang = self.win.lang
        have = self.store.downloaded_uris(lang)
        vols = {v["code"]: v for v in self.store.volumes(lang)}
        from ..parse import iter_leaves
        for code, row in self.vol_rows.items():
            if code in vols:
                uris = [l["uri"] for l in iter_leaves(vols[code]["toc"])]
                n = sum(u in have for u in uris)
                row.set_subtitle(f"{n} of {len(uris)} pages downloaded")
                complete = n == len(uris)
            else:
                row.set_subtitle("Not downloaded")
                complete = False
            if initial:
                self.vol_checks[code].set_active(code in STANDARD and not complete)

    def _on_lang(self, row, _pspec):
        self._set_lang(self.lang_codes[row.get_selected()])

    def _set_lang(self, code):
        if not code:
            return
        self.win.set_lang(code)
        self._refresh_counts(initial=True)

    def _start_download(self, *_):
        codes = [c for c, chk in self.vol_checks.items() if chk.get_active()]
        if not codes:
            return self.add_toast(Adw.Toast(title="Select at least one volume"))
        if self.app.job and self.app.job.status == "running":
            return self.add_toast(Adw.Toast(title="A download is already running"))
        job = DownloadJob(self.store, self.win.lang, codes)
        self.app.job = job

        def run():
            job.run()
            GLib.idle_add(self.app.download_finished, job)

        threading.Thread(target=run, daemon=True).start()
        self._watch_job()

    def _watch_job(self):
        self.dl_button.set_sensitive(False)
        self.cancel_button.set_visible(True)
        self.progress_group.set_visible(True)
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(400, self._tick)

    def _tick(self):
        job = self.app.job
        if job.total:
            self.progress.set_fraction(job.done / job.total)
            self.progress.set_text(f"{job.done} of {job.total} pages")
        else:
            self.progress.pulse()
            self.progress.set_text(job.message)
        if job.status == "running":
            return True
        self._timer = None
        self.progress.set_text(job.message)
        self.progress.set_fraction(1 if job.status == "done" else self.progress.get_fraction())
        self.dl_button.set_sensitive(True)
        self.cancel_button.set_visible(False)
        self._refresh_counts(initial=True)
        return False

    # ---- account ------------------------------------------------------------------

    def _account_page(self):
        page = Adw.PreferencesPage(title="Church Account", icon_name="avatar-default-symbolic")

        conn = Adw.PreferencesGroup(
            title="Church Account",
            description="Bring your Gospel Library highlights, notes and tags into this app. "
                        "You sign in on the Church's own page; this app never sees your password.")
        self.status_row = Adw.ActionRow(title="Connected")
        disconnect = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER,
                                css_classes=["destructive-action"])
        disconnect.connect("clicked", self._disconnect)
        self.status_row.add_suffix(disconnect)
        conn.add(self.status_row)
        page.add(conn)

        self.signin_group = Adw.PreferencesGroup()
        self.signin_button = Gtk.Button(label="Sign In…", halign=Gtk.Align.CENTER,
                                        css_classes=["pill", "suggested-action"])
        self.signin_button.connect("clicked", lambda *_: self._sign_in())
        self.signin_group.add(self.signin_button)
        page.add(self.signin_group)

        self.other_group = Adw.PreferencesGroup(title="Other Ways to Connect")
        firefox = Adw.ActionRow(title="Use Firefox Session", activatable=True,
                                subtitle="If you are signed in to churchofjesuschrist.org in Firefox")
        firefox.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        firefox.connect("activated", lambda *_: self._use_firefox())
        self.other_group.add(firefox)
        manual = Adw.ExpanderRow(
            title="Paste Session Cookie",
            subtitle="From your browser's developer tools: Network → a request to "
                     "www.churchofjesuschrist.org → Request Headers → Cookie")
        self.cookie_row = Adw.PasswordEntryRow(title="Cookie header value", show_apply_button=True)
        self.cookie_row.connect("apply", lambda row: self._save_cookie(row.get_text()))
        manual.add_row(self.cookie_row)
        self.other_group.add(manual)
        page.add(self.other_group)

        self.privacy_group = Adw.PreferencesGroup(
            description="The session is saved only on this computer (data/account.json and "
                        "data/webkit, readable by you only). Anyone with it can act as you on the "
                        "Church website until it expires. Disconnect removes it.")
        page.add(self.privacy_group)

        self.sync_group = Adw.PreferencesGroup(title="Sync")
        self.pull_row = Adw.ActionRow(title="Download Notes From Account", activatable=True,
                                      subtitle="Copy your Gospel Library highlights and notes here")
        self.pull_row.add_suffix(Gtk.Image(icon_name="folder-download-symbolic"))
        self.pull_row.connect("activated", lambda *_: self._sync(False))
        self.push_row = Adw.ActionRow(title="Two-Way Sync", activatable=True,
                                      subtitle="Experimental: also upload changes made in this app")
        self.push_row.add_suffix(Gtk.Image(icon_name="view-refresh-symbolic"))
        self.push_row.connect("activated", lambda *_: self._confirm_push())
        self.last_row = Adw.ActionRow(title="Last sync", subtitle="Never")
        self.sync_group.add(self.pull_row)
        self.sync_group.add(self.push_row)
        self.sync_group.add(self.last_row)
        page.add(self.sync_group)
        self.log_group = Adw.PreferencesGroup(title="Sync Log", visible=False)
        self.log = Gtk.Label(wrap=True, xalign=0, selectable=True, css_classes=["monospace", "caption"])
        self.log_group.add(self.log)
        page.add(self.log_group)
        self._refresh_account()
        return page

    def _refresh_account(self):
        acct = sync.load_account()
        connected = bool(acct.get("cookie"))
        self.status_row.set_visible(connected)
        if connected:
            saved = acct.get("saved_at")
            self.status_row.set_subtitle(
                "Session saved " + datetime.fromisoformat(saved).astimezone().strftime("%x %X") if saved else "")
        for w in (self.signin_group, self.other_group):
            w.set_visible(not connected)
        self.sync_group.set_visible(connected)
        last = self.store.get_setting("last_sync")
        if last:
            st = last["stats"]
            when = datetime.fromisoformat(last["at"]).astimezone().strftime("%x %X")
            self.last_row.set_subtitle(f"{when}: {st['pulled']} pulled, {st['pushed']} pushed"
                                       + (f", {st['push_errors']} errors" if st["push_errors"] else ""))

    def _connected(self, msg):
        self._refresh_account()
        self.add_toast(Adw.Toast(title=msg, timeout=5))

    def _sign_in(self, then=None):
        self.signin_button.set_sensitive(False)

        def done(ok, msg):
            self.signin_button.set_sensitive(True)
            if msg:
                self._connected(msg)
            if ok and then:
                then()

        start_sign_in(done)

    def _use_firefox(self):
        def run():
            try:
                header, profile = firefox_cookie_header()
                sync.NotesClient(header).discover()
                sync.save_account(header)
                msg = f"Connected using Firefox profile “{profile.name}”"
            except sync.AuthError:
                msg = "Your Firefox session has expired. Sign in again in Firefox, or use Sign In."
            except Exception as e:
                msg = str(e)
            GLib.idle_add(self._connected, msg)

        threading.Thread(target=run, daemon=True).start()

    def _save_cookie(self, cookie):
        cookie = cookie.strip()
        if not cookie:
            return
        self.cookie_row.set_text("")

        def test():
            try:
                sync.NotesClient(cookie).discover()
                sync.save_account(cookie)
                msg = "Connected to your Church Account"
            except Exception as e:
                msg = f"That cookie did not work: {e}"
            GLib.idle_add(self._connected, msg)

        threading.Thread(target=test, daemon=True).start()

    def _disconnect(self, *_):
        sync.clear_account()
        shutil.rmtree(data_dir() / "webkit", ignore_errors=True)
        self._refresh_account()

    def _confirm_push(self):
        alert = Adw.AlertDialog(
            heading="Upload Local Changes?",
            body="Two-way sync sends highlights and notes changed in this app to your Church "
                 "Account. The notes service has no public API, so this is experimental.")
        alert.add_response("cancel", "Cancel")
        alert.add_response("sync", "Sync")
        alert.set_response_appearance("sync", Adw.ResponseAppearance.SUGGESTED)
        alert.connect("response", lambda _d, r: r == "sync" and self._sync(True))
        alert.present(self)

    def _sync(self, push, retried=False):
        lines = []
        self.pull_row.set_sensitive(False)
        self.push_row.set_sensitive(False)
        self.log_group.set_visible(True)
        self.log.set_text("Syncing…")

        def log(line):
            lines.append(line)
            GLib.idle_add(self.log.set_text, "\n".join(lines))

        def run():
            expired = False
            try:
                sync.sync(self.store, push=push, log=log)
                ok, msg = True, "Sync complete"
            except sync.AuthError as e:
                log(f"Error: {e}")
                ok, msg, expired = False, "Session expired", True
            except Exception as e:
                log(f"Error: {e}")
                ok, msg = False, "Sync failed"
            GLib.idle_add(finish, ok, msg, expired)

        def finish(ok, msg, expired):
            self.pull_row.set_sensitive(True)
            self.push_row.set_sensitive(True)
            self._refresh_account()
            self.win.notes_changed()
            if expired and not retried:
                # Renew the session (often automatic thanks to single sign-on), then retry once.
                log("Signing in again…")
                return self._sign_in(then=lambda: self._sync(push, retried=True))
            self.add_toast(Adw.Toast(title=msg, timeout=5))

        threading.Thread(target=run, daemon=True).start()


def start_sign_in(on_done):
    """Runs the sign-in window (lds_scripture.signin) and saves the session it returns.

    on_done(ok, message) is called on the main thread; message is None when cancelled."""
    root = str(Path(__file__).resolve().parents[2])
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
    launcher.setenv("PYTHONPATH", root, True)
    launcher.setenv("LDS_DATA_DIR", str(data_dir()), True)
    try:
        proc = launcher.spawnv(["/usr/bin/python3", "-m", "lds_scripture.signin"])
    except GLib.Error as e:
        return on_done(False, f"Could not open the sign-in window: {e.message}")

    def finished(p, result):
        try:
            _ok, out, err = p.communicate_utf8_finish(result)
        except GLib.Error as e:
            return on_done(False, f"Sign-in failed: {e.message}")
        status = p.get_exit_status()
        if status == 0 and out:
            sync.save_account(json.loads(out)["cookie"])
            return on_done(True, "Signed in to your Church Account")
        if status == 2:
            return on_done(False, "The sign-in window needs WebKitGTK "
                                  "(sudo apt install gir1.2-webkit2-4.1). Use Firefox or paste a cookie instead.")
        on_done(False, None)  # closed by the user

    proc.communicate_utf8_async(None, None, finished)
