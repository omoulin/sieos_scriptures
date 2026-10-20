"""Church Account sign-in window (separate process: GTK 3 + WebKitGTK 4.1).

The user signs in on the real churchofjesuschrist.org page (including two-step verification).
Once the notes service accepts the browser session, its cookies are written to stdout as JSON
and the window closes. The password is only ever typed into the Church's own page.

Browser data (cookies, local storage) persists in <data>/webkit, so a later sign-in usually
completes by itself while the Church's single sign-on session is still valid.

This runs as its own process because GTK 3 (needed by the installed WebKitGTK) cannot share a
process with the GTK 4 app. Exit status: 0 signed in, 1 cancelled, 2 unavailable.
"""

import json
import os
import sys
import threading
import urllib.parse

import gi

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import GLib, Gtk, WebKit2
except (ValueError, ImportError) as e:
    print(f"WebKitGTK is not available: {e}", file=sys.stderr)
    sys.exit(2)

from . import sync
from .store import data_dir

START_URL = "https://www.churchofjesuschrist.org/notes?lang=eng"
API_URI = "https://www.churchofjesuschrist.org/notes/api/v3/annotations"
CHECK_EVERY_MS = 2500


class SignInWindow(Gtk.Window):
    def __init__(self, profile_dir):
        super().__init__(title="Sign In – Church Account", default_width=540, default_height=780)
        self.signed_in = False
        self.checking = False

        os.makedirs(profile_dir, mode=0o700, exist_ok=True)
        manager = WebKit2.WebsiteDataManager(
            base_data_directory=os.path.join(profile_dir, "data"),
            base_cache_directory=os.path.join(profile_dir, "cache"))
        self.cookies = manager.get_cookie_manager()
        self.cookies.set_persistent_storage(os.path.join(profile_dir, "cookies.sqlite"),
                                           WebKit2.CookiePersistentStorage.SQLITE)
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
        self.view = WebKit2.WebView.new_with_context(context)
        self.view.connect("load-changed", self._on_load_changed)
        self.view.connect("notify::uri", lambda *_: self._update_location())
        self.view.connect("decide-policy", self._on_policy)

        header = Gtk.HeaderBar(show_close_button=True, title="Sign In to Church Account")
        self.set_titlebar(header)
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        self.lock = Gtk.Image.new_from_icon_name("channel-secure-symbolic", Gtk.IconSize.BUTTON)
        header.pack_start(self.lock)

        info = Gtk.Label(xalign=0, wrap=True, margin=10)
        info.set_markup("Sign in on the Church website below. This window closes by itself once "
                        "you are signed in. <small>Your password goes only to the Church.</small>")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(info, False, False, 0)
        box.pack_start(self.view, True, True, 0)
        self.add(box)
        self.connect("destroy", lambda *_: Gtk.main_quit())

        self.view.load_uri(START_URL)
        GLib.timeout_add(CHECK_EVERY_MS, self._poll)

    # The address bar is hidden, so show the current site in the subtitle (and whether it is
    # HTTPS) to make it clear the user is typing into the real Church sign-in page.
    def _update_location(self):
        uri = self.view.get_uri() or ""
        parts = urllib.parse.urlsplit(uri)
        self.get_titlebar().set_subtitle(parts.netloc)
        self.lock.set_visible(parts.scheme == "https")

    def _on_policy(self, _view, decision, kind):
        # Keep the window on Church sign-in pages; open anything else in the user's browser.
        if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            Gtk.show_uri_on_window(self, uri, Gtk.get_current_event_time())
            decision.ignore()
            return True
        return False

    def _on_load_changed(self, _view, event):
        self.spinner.start() if event != WebKit2.LoadEvent.FINISHED else self.spinner.stop()
        if event == WebKit2.LoadEvent.FINISHED:
            self._check()

    def _poll(self):
        if self.signed_in:
            return False
        self._check()
        return True

    def _check(self):
        host = urllib.parse.urlsplit(self.view.get_uri() or "").netloc
        if self.checking or host != "www.churchofjesuschrist.org":
            return
        self.checking = True
        self.cookies.get_cookies(API_URI, None, self._on_cookies)

    def _on_cookies(self, manager, result):
        try:
            cookies = manager.get_cookies_finish(result)
        except GLib.Error:
            self.checking = False
            return
        header = "; ".join(f"{c.get_name()}={c.get_value()}" for c in cookies)
        if not header:
            self.checking = False
            return

        def test():
            try:
                sync.NotesClient(header).discover()
                ok = True
            except Exception:
                ok = False
            GLib.idle_add(self._checked, ok, header)

        threading.Thread(target=test, daemon=True).start()

    def _checked(self, ok, header):
        self.checking = False
        if ok and not self.signed_in:
            self.signed_in = True
            sys.stdout.write(json.dumps({"cookie": header}))
            sys.stdout.flush()
            self.destroy()
        return False


def main():
    profile = os.path.join(str(data_dir()), "webkit")
    win = SignInWindow(profile)
    win.show_all()
    Gtk.main()
    return 0 if win.signed_in else 1


if __name__ == "__main__":
    sys.exit(main())
