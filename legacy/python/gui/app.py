"""GNOME application entry point."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..store import Store  # noqa: E402
from .reader import PALETTE  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "io.github.ldsscripture.Scriptures"

CSS = """
textview.reader { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, "Noto Serif",
                  "DejaVu Serif", Georgia, serif; }
.reader-quote { font-family: "Noto Serif", "DejaVu Serif", serif; font-style: italic; }
.note-text { font-family: "Noto Serif", "DejaVu Serif", serif; font-size: 12pt; }
.footnote-text { font-family: "Noto Serif", "DejaVu Serif", serif; font-size: 12pt; }
.chapter-btn { min-width: 38px; padding-left: 0; padding-right: 0; }
.swatch { min-width: 24px; min-height: 24px; padding: 0; border-radius: 999px;
          border: 1px solid alpha(currentColor, 0.18); }
.swatch:checked { outline: 2px solid var(--accent-color); outline-offset: 2px; }
.swatch-none { background: none; }
"""


def _swatch_css(dark):
    return "\n".join(f".swatch.hl-{c} {{ background: {v}; }}" for c, v in PALETTE[dark].items())


class ScripturesApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name("Scriptures")
        self.store = None
        self.job = None
        self.font_provider = Gtk.CssProvider()
        self.swatch_provider = Gtk.CssProvider()

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.store = Store()
        display = Gdk.Display.get_default()
        base = Gtk.CssProvider()
        base.load_from_string(CSS)
        for p in (base, self.swatch_provider, self.font_provider):
            Gtk.StyleContext.add_provider_for_display(display, p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        sm = Adw.StyleManager.get_default()
        sm.connect("notify::dark", lambda *_: self._update_swatches())
        self._update_swatches()

        for name, cb, accels in [
            ("quit", lambda *_: self.quit(), ["<Control>q"]),
            ("preferences", lambda *_: self.active_window_or_new().show_preferences(), ["<Control>comma"]),
            ("about", self._about, []),
        ]:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            self.set_accels_for_action(f"app.{name}", accels)

    def _update_swatches(self):
        self.swatch_provider.load_from_string(_swatch_css(Adw.StyleManager.get_default().get_dark()))

    def set_reader_font_size(self, pt):
        self.font_provider.load_from_string(f"textview.reader {{ font-size: {pt}pt; }}")

    def active_window_or_new(self):
        win = self.get_active_window()
        if not win:
            win = MainWindow(self)
            win.connect("close-request", lambda w: w.save_position() or False)
        return win

    def do_activate(self):
        self.active_window_or_new().present()

    def download_finished(self, job):
        win = self.get_active_window()
        if win:
            if job.lang == win.lang:
                win.reload_library()
            win.toast(job.message)
        return False

    def _about(self, *_):
        about = Adw.AboutDialog(
            application_name="Scriptures", application_icon=APP_ID, version=__version__,
            developer_name="lds_scripture",
            comments="Read the scriptures offline, with footnotes, search, highlights and notes "
                     "synced from your Church Account.",
            website="https://www.churchofjesuschrist.org/study/scriptures",
            license_type=Gtk.License.CUSTOM,
            license="Scripture text and study helps © Intellectual Reserve, Inc. "
                    "Downloaded from churchofjesuschrist.org for personal study.")
        about.present(self.get_active_window())


def main():
    return ScripturesApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
