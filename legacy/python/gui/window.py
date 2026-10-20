"""Main window: library sidebar, tabbed chapter readers, search and notes."""

import threading
from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import api
from ..downloader import fetch_and_store_chapter
from ..render import html_to_markup, resolve_link
from .dialogs import NoteDialog, PreferencesDialog, swatch_button
from .reader import COLOR_ALIASES, COLORS, PALETTE, ReaderView


def _label(text, *classes, **kw):
    return Gtk.Label(label=text, xalign=0, wrap=True, css_classes=list(classes), **kw)


def _row(title, on_activate, subtitle=None, icon=None, arrow=False, markup=False):
    """A sidebar row; on_activate(new_tab) runs when it is clicked or activated."""
    box = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4)
    if icon:
        box.append(Gtk.Image(icon_name=icon))
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=2)
    t = _label(title)
    t.set_use_markup(markup)
    texts.append(t)
    if subtitle:
        s = _label(subtitle, "caption", "dim-label")
        s.set_use_markup(markup)
        texts.append(s)
    box.append(texts)
    if arrow:
        box.append(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
    row = Gtk.ListBoxRow(child=box)
    row.on_activate = on_activate
    return row


def _chapter_number(leaf):
    last = leaf["uri"].rsplit("/", 1)[-1]
    return last if last.isdigit() else None


def _dot(color):
    return PALETTE[Adw.StyleManager.get_default().get_dark()][color]


class Tab:
    """One open chapter: its own reader, back history, and loading/error/welcome pages."""

    def __init__(self, win):
        self.reader = ReaderView()
        self.reader.on_footnote = win.show_footnote
        self.reader.on_link = win.follow_link
        self.reader.on_note = win.edit_note_by_id
        self.reader.on_selection = win.show_selection_menu
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.tab = self  # lets the window find the Tab from an AdwTabPage
        self.stack.add_named(self.reader, "reader")
        self.stack.add_named(Adw.StatusPage(title="Loading…", child=Adw.Spinner(
            width_request=32, height_request=32)), "loading")
        self.error_page = Adw.StatusPage(icon_name="network-offline-symbolic", title="Not Available Offline")
        self.stack.add_named(self.error_page, "error")
        self.welcome = Adw.StatusPage(icon_name="accessories-dictionary-symbolic", title="Scriptures")
        self.stack.add_named(self.welcome, "welcome")
        self.current = None
        self.history = []
        self.pending = None  # (lang, uri, anchor) to load when the tab is first shown
        self.page = None


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=1200, default_height=860,
                         title="Scriptures")
        self.set_size_request(360, 294)  # required for breakpoints to collapse the layout
        self.store = app.store
        self.lang = self.store.get_setting("lang", "eng")
        self.leaves, self.leaf_index = [], {}
        self.media = None
        self._ctrl = False

        # ---- content: header + tabs ----
        self.window_title = Adw.WindowTitle(title="Scriptures")
        header = Adw.HeaderBar(title_widget=self.window_title)
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back (Alt+←)",
                                   action_name="win.back", visible=False)
        header.pack_start(self.back_btn)
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("New Tab", "win.new-tab")
        section.append("Close Tab", "win.close-tab")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Larger Text", "win.zoom-in")
        section.append("Smaller Text", "win.zoom-out")
        section.append("Reset Text Size", "win.zoom-reset")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Library & Account", "app.preferences")
        section.append("Keyboard Shortcuts", "win.show-help-overlay")
        section.append("About Scriptures", "app.about")
        menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main Menu", primary=True))
        nav = Gtk.Box(css_classes=["linked"])
        nav.append(Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Previous Chapter (Alt+↑)",
                              action_name="win.prev-chapter"))
        nav.append(Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Next Chapter (Alt+↓)",
                              action_name="win.next-chapter"))
        header.pack_end(nav)
        self.audio_btn = Gtk.Button(icon_name="audio-headphones-symbolic", tooltip_text="Listen (streams online)",
                                    visible=False)
        self.audio_btn.connect("clicked", self.play_audio)
        header.pack_end(self.audio_btn)

        self.tabs = Adw.TabView()
        self.tabs.connect("notify::selected-page", lambda *_: self._on_tab_switched())
        self.tabs.connect("close-page", self._on_close_page)
        tab_bar = Adw.TabBar(view=self.tabs, autohide=False)
        tab_bar.set_end_action_widget(Gtk.Button(icon_name="tab-new-symbolic", css_classes=["flat"],
                                                 tooltip_text="New Tab (Ctrl+T)", action_name="win.new-tab"))

        self.media_bar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        content_view = Adw.ToolbarView(content=self.tabs, top_bar_style=Adw.ToolbarStyle.RAISED)
        content_view.add_top_bar(header)
        content_view.add_top_bar(tab_bar)
        content_view.add_bottom_bar(self.media_bar)
        self.content_page = Adw.NavigationPage(title="Scriptures", child=content_view, tag="content")

        # ---- sidebar ----
        self.nav = Adw.NavigationView()
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="Library", child=self.nav),
            content=self.content_page, min_sidebar_width=270, max_sidebar_width=360,
            sidebar_width_fraction=0.27)
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self._build_popovers()
        self._install_actions()
        self._apply_zoom()
        self.reload_library()
        self._restore_session()

    # ---- tabs ------------------------------------------------------------------------

    @property
    def tab(self):
        page = self.tabs.get_selected_page()
        return page.get_child().tab if page else None

    @property
    def current(self):
        tab = self.tab
        return tab.current if tab else None

    @property
    def reader(self):
        return self.tab.reader

    def all_tabs(self):
        return [self.tabs.get_nth_page(i).get_child().tab for i in range(self.tabs.get_n_pages())]

    def new_tab(self, lang=None, uri=None, anchor=None, select=True):
        tab = Tab(self)
        tab.page = self.tabs.append(tab.stack)
        if uri:
            tab.pending = (lang or self.lang, api.normalize_uri(uri), anchor)
            tab.page.set_title(self.store.chapter_title(lang or self.lang, tab.pending[1]) or "Loading…")
        else:
            tab.page.set_title("New Tab")
            self._show_welcome(tab)
        if select:
            self.tabs.set_selected_page(tab.page)
            self._on_tab_switched()
        return tab

    def _on_tab_switched(self):
        tab = self.tab
        if not tab:
            return
        for pop in (self.fn_popover, self.sel_popover):
            if pop.get_parent() is not tab.reader.view:
                pop.popdown()
                if pop.get_parent():
                    pop.unparent()
                pop.set_parent(tab.reader.view)
        if tab.pending:
            lang, uri, anchor = tab.pending
            tab.pending = None
            self.open_chapter(lang, uri, anchor, history=False, flash=False, tab=tab)
        self._sync_header()

    def _on_close_page(self, view, page):
        if view.get_n_pages() == 1:
            # Keep one tab open: closing the last one just empties it.
            tab = page.get_child().tab
            tab.current, tab.history = None, []
            page.set_title("New Tab")
            self._show_welcome(tab)
            self._sync_header()
            view.close_page_finish(page, False)
            return True
        tab = page.get_child().tab
        for pop in (self.fn_popover, self.sel_popover):
            if pop.get_parent() is tab.reader.view:
                pop.popdown()
                pop.unparent()
        return False

    def _sync_header(self):
        ch = self.current
        if ch:
            i = self.leaf_index.get(ch["uri"])
            self.window_title.set_title(ch["title"])
            self.window_title.set_subtitle(self.leaves[i]["volume"]["title"] if i is not None else "")
            self.content_page.set_title(ch["title"])
            self.set_title(f"{ch['title']} – Scriptures")
        else:
            self.window_title.set_title("Scriptures")
            self.window_title.set_subtitle("")
            self.content_page.set_title("Scriptures")
            self.set_title("Scriptures")
        self.audio_btn.set_visible(bool(ch and ch.get("audio")))
        self._update_nav_actions()

    def save_position(self):
        """Remembers open tabs (and where each one is scrolled) for the next start."""
        tabs = []
        for tab in self.all_tabs():
            if tab.pending:
                lang, uri, anchor = tab.pending
                tabs.append({"lang": lang, "uri": uri, "anchor": anchor})
            elif tab.current:
                tabs.append({"lang": tab.current["lang"], "uri": tab.current["uri"],
                             "anchor": tab.reader.top_anchor()})
        selected = self.tabs.get_page_position(self.tabs.get_selected_page()) if self.tabs.get_selected_page() else 0
        self.store.set_setting("session", {"tabs": tabs, "selected": min(selected, max(0, len(tabs) - 1))})

    def _restore_session(self):
        session = self.store.get_setting("session")
        entries = [t for t in (session or {}).get("tabs", []) if self.store.has_chapter(t["lang"], t["uri"])]
        if not entries:
            last = self.store.get_setting("last_read")
            if last and self.store.has_chapter(last["lang"], last["uri"]):
                entries = [last]
        if not entries:
            self.new_tab()
            return
        for t in entries:
            self.new_tab(t["lang"], t["uri"], t.get("anchor"), select=False)
        selected = (session or {}).get("selected", 0)
        self.tabs.set_selected_page(self.tabs.get_nth_page(min(selected, len(entries) - 1)))
        self._on_tab_switched()

    # ---- actions ------------------------------------------------------------------

    def _install_actions(self):
        def add(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            self.add_action(a)
            return a

        self.back_action = add("back", self.go_back)
        self.prev_action = add("prev-chapter", lambda: self.step_chapter(-1))
        self.next_action = add("next-chapter", lambda: self.step_chapter(1))
        add("new-tab", self._new_tab_action)
        add("close-tab", lambda: self.tabs.close_page(self.tabs.get_selected_page()))
        add("search", self.focus_search)
        add("show-library", lambda: self.split.set_show_content(False))
        add("zoom-in", lambda: self._zoom(1))
        add("zoom-out", lambda: self._zoom(-1))
        add("zoom-reset", lambda: self._zoom(0))
        add("show-help-overlay", self._show_shortcuts)
        app = self.get_application()
        for action, accels in {
            "win.back": ["<Alt>Left"], "win.prev-chapter": ["<Alt>Up"],
            "win.next-chapter": ["<Alt>Down"], "win.search": ["<Control>f"],
            "win.new-tab": ["<Control>t"], "win.close-tab": ["<Control>w"],
            "win.zoom-in": ["<Control>plus", "<Control>equal"], "win.zoom-out": ["<Control>minus"],
            "win.zoom-reset": ["<Control>0"], "win.show-help-overlay": ["<Control>question"],
        }.items():
            app.set_accels_for_action(action, accels)
        self._update_nav_actions()

    def _new_tab_action(self):
        self.new_tab()
        if self.split.get_collapsed():
            self.split.set_show_content(False)

    def _update_nav_actions(self):
        tab = self.tab
        history = bool(tab and tab.history)
        self.back_action.set_enabled(history)
        self.back_btn.set_visible(history)
        i = self.leaf_index.get(self.current["uri"]) if self.current else None
        self.prev_action.set_enabled(i is not None and i > 0)
        self.next_action.set_enabled(i is not None and i < len(self.leaves) - 1)

    def _zoom(self, step):
        size = self.store.get_setting("font_size", 14)
        size = 14 if step == 0 else max(9, min(30, size + step))
        self.store.set_setting("font_size", size)
        self._apply_zoom()

    def _apply_zoom(self):
        self.get_application().set_reader_font_size(self.store.get_setting("font_size", 14))

    def _show_shortcuts(self):
        rows = [("Search", "Ctrl+F"), ("New tab", "Ctrl+T"), ("Close tab", "Ctrl+W"),
                ("Open in new tab", "Ctrl+click or middle-click"),
                ("Next / previous tab", "Ctrl+Page Down / Page Up"),
                ("Previous chapter", "Alt+↑"), ("Next chapter", "Alt+↓"), ("Back", "Alt+←"),
                ("Larger / smaller text", "Ctrl+ + / Ctrl+ −"), ("Library & Account", "Ctrl+,"),
                ("Quit", "Ctrl+Q")]
        group = Adw.PreferencesGroup()
        for title, keys in rows:
            row = Adw.ActionRow(title=title)
            row.add_suffix(Gtk.Label(label=keys, css_classes=["dim-label"]))
            group.add(row)
        page = Adw.PreferencesPage()
        page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        Adw.Dialog(title="Keyboard Shortcuts", child=view, content_width=440, content_height=620).present(self)

    # ---- "open in new tab" clicks ---------------------------------------------------------

    def _track_modifiers(self, widget, on_middle=None):
        """Records Ctrl on press (so the following click can open a new tab); handles middle-click."""
        press = Gtk.GestureClick(button=0)
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def pressed(g, _n, x, y):
            self._ctrl = bool(g.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)
            if g.get_current_button() == 2 and on_middle:
                on_middle(x, y)
                g.set_state(Gtk.EventSequenceState.CLAIMED)

        press.connect("pressed", pressed)
        widget.add_controller(press)

    def _take_ctrl(self):
        ctrl, self._ctrl = self._ctrl, False
        return ctrl

    def _nav_list(self):
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        lb.connect("row-activated", lambda _lb, row: row.on_activate(self._take_ctrl()))

        def middle(_x, y):
            row = lb.get_row_at_y(int(y))
            if row:
                row.on_activate(True)

        self._track_modifiers(lb, middle)
        return lb

    def _chapter_button(self, label, tooltip, uri):
        b = Gtk.Button(label=label, tooltip_text=tooltip, css_classes=["chapter-btn"])
        if self.current and self.current["uri"] == uri:
            b.add_css_class("suggested-action")
        b.connect("clicked", lambda _b: self.open_chapter(self.lang, uri, new_tab=self._take_ctrl()))
        self._track_modifiers(b, lambda *_: self.open_chapter(self.lang, uri, new_tab=True))
        return b

    # ---- library / sidebar ------------------------------------------------------------

    def set_lang(self, lang):
        if lang == self.lang:
            return
        self.lang = lang
        self.store.set_setting("lang", lang)
        self.reload_library()
        if self.current:
            uri = self.current["uri"]
            if self.store.has_chapter(lang, uri) or self.leaf_index.get(uri) is not None:
                self.open_chapter(lang, uri, self.reader.top_anchor(), history=False, flash=False)

    def reload_library(self):
        self.volumes = self.store.volumes(self.lang)
        self.leaves, self.leaf_index = [], {}

        def walk(nodes, vol):
            for n in nodes:
                if "children" in n:
                    walk(n["children"], vol)
                else:
                    self.leaf_index[n["uri"]] = len(self.leaves)
                    self.leaves.append({**n, "volume": vol})

        for v in self.volumes:
            walk(v["toc"], v)
        self.nav.replace([self._root_page()])
        for tab in self.all_tabs():
            if not tab.current and not tab.pending:
                self._show_welcome(tab)
        self._update_nav_actions()

    def _sidebar_page(self, title, child, tag=None, top=None):
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        if top:
            view.add_top_bar(top)
        view.set_content(Gtk.ScrolledWindow(child=child, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return Adw.NavigationPage(title=title, child=view, tag=tag)

    def _root_page(self):
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search scriptures", margin_start=12,
                                            margin_end=12, margin_bottom=6)
        self.search_entry.connect("activate", lambda e: self.search(e.get_text()))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lb = self._nav_list()
        if not self.volumes:
            status = Adw.StatusPage(
                icon_name="folder-download-symbolic", title="No Scriptures Yet",
                description="Download volumes to read them offline.", css_classes=["compact"])
            btn = Gtk.Button(label="Download…", halign=Gtk.Align.CENTER,
                             css_classes=["pill", "suggested-action"], action_name="app.preferences")
            status.set_child(btn)
            box.append(status)
        for v in self.volumes:
            lb.append(_row(v["title"], lambda _new, v=v: self.nav.push(self._group_page(v["title"], v["toc"])),
                           arrow=True))
        lb.append(_row("Notes &amp; Highlights", lambda _new: self.nav.push(self._notes_page()),
                       icon="document-edit-symbolic", arrow=True, markup=True))
        box.append(lb)
        return self._sidebar_page("Library", box, tag="root", top=self.search_entry)

    def _group_page(self, title, nodes):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        leaves = [n for n in nodes if "children" not in n]
        numbered = len(leaves) > 1 and len(leaves) == len(nodes) and all(map(_chapter_number, leaves))
        if numbered:
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               min_children_per_line=4, max_children_per_line=8,
                               row_spacing=6, column_spacing=6, margin_start=12, margin_end=12,
                               margin_top=6, margin_bottom=12)
            for leaf in leaves:
                flow.append(self._chapter_button(_chapter_number(leaf), leaf["title"], leaf["uri"]))
            box.append(flow)
        else:
            lb = self._nav_list()
            for n in nodes:
                if "children" in n:
                    lb.append(_row(n["title"] or "Contents",
                                   lambda _new, n=n: self.nav.push(self._group_page(n["title"], n["children"])),
                                   arrow=True))
                else:
                    lb.append(_row(n["title"], lambda new, u=n["uri"]: self.open_chapter(self.lang, u, new_tab=new)))
            box.append(lb)
        return self._sidebar_page(title or "Contents", box)

    def focus_search(self):
        self.split.set_show_content(False)
        self.nav.pop_to_tag("root")
        self.search_entry.grab_focus()

    def search(self, query):
        query = query.strip()
        if not query:
            return
        results = self.store.search(self.lang, query)
        lb = self._nav_list()
        for r in results:
            ref = r["title"] + (f":{r['num']}" if r["num"] else "")
            lb.append(_row(f"<b>{GLib.markup_escape_text(ref)}</b>",
                           lambda new, r=r: self.open_chapter(self.lang, r["uri"], r["anchor"], new_tab=new),
                           subtitle=html_to_markup(r["snip"]), markup=True))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if not results:
            box.append(Adw.StatusPage(icon_name="edit-find-symbolic", title="No Results",
                                      description="Only downloaded volumes are searched.",
                                      css_classes=["compact"]))
        box.append(lb)
        page = self._sidebar_page(f"“{query}”", box, tag="search")
        if self.nav.find_page("search"):
            self.nav.pop_to_tag("root")
        self.nav.push(page)

    def _notes_page(self):
        entry = Gtk.SearchEntry(placeholder_text="Filter notes and tags", margin_start=12,
                                margin_end=12, margin_bottom=6)
        lb = self._nav_list()
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.append(lb)
        page = self._sidebar_page("Notes & Highlights", holder, tag="notes", top=entry)

        def fill():
            while (child := lb.get_first_child()) is not None:
                lb.remove(child)
            f = entry.get_text().lower()
            shown = 0
            for n in self.store.all_notes():
                title = self.store.chapter_title(n["lang"], n["uri"]) if n["uri"] else None
                verse = self.store.verse_text(n["lang"], n["uri"], n["anchor"]) if n["anchor"] else None
                hay = " ".join([n["title"], n["content"], title or "", verse["text"] if verse else "",
                                *n["tags"]]).lower()
                if f and f not in hay:
                    continue
                shown += 1
                ref = (title or n["uri"] or "Journal entry") + (f":{verse['num']}" if verse and verse["num"] else "")
                color = COLOR_ALIASES.get(n["color"], n["color"])
                dot = f'<span foreground="{_dot(color)}">●</span> ' if color in COLORS else ""
                preview = n["title"] or n["content"] or (verse["text"] if verse else "")
                tags = "  ".join("#" + t for t in n["tags"])
                sub = GLib.markup_escape_text(preview[:140]) + (
                    f"\n<small>{GLib.markup_escape_text(tags)}</small>" if tags else "")
                when = datetime.fromtimestamp(n["updated_at"]).strftime("%x")
                if n["uri"]:
                    activate = (lambda new, n=n: self.open_chapter(n["lang"], n["uri"], n["anchor"], new_tab=new))
                else:
                    activate = (lambda _new, n=n: self.edit_note(n))
                lb.append(_row(f"{dot}<b>{GLib.markup_escape_text(ref)}</b>  <small>{when}</small>",
                               activate, subtitle=sub, markup=True))
            if not shown:
                lb.append(_row("No notes yet. Select text while reading to highlight it.", lambda _new: None))

        entry.connect("search-changed", lambda *_: fill())
        page.connect("showing", lambda *_: fill())
        return page

    # ---- reading --------------------------------------------------------------------

    def _show_welcome(self, tab):
        tab.welcome.set_description(
            "Choose a chapter from the library. Ctrl+click opens it in a new tab." if self.volumes else
            "Download the scriptures to read them offline.")
        if not self.volumes:
            tab.welcome.set_child(Gtk.Button(label="Download Scriptures…", halign=Gtk.Align.CENTER,
                                             css_classes=["pill", "suggested-action"],
                                             action_name="app.preferences"))
        else:
            tab.welcome.set_child(None)
        tab.stack.set_visible_child_name("welcome")

    def open_chapter(self, lang, uri, anchor=None, history=True, flash=True, tab=None, new_tab=False):
        uri = api.normalize_uri(uri)
        if new_tab:
            # Opens in the background so the user can keep reading; switch with a tab click.
            self.new_tab(lang, uri, anchor, select=False)
            self.toast("Opened in a new tab")
            return
        tab = tab or self.tab
        cur = tab.current
        if history and cur and cur["uri"] != uri:
            tab.history.append((cur["lang"], cur["uri"], tab.reader.top_anchor()))
            del tab.history[:-100]
        if lang != self.lang:
            self.lang = lang
            self.store.set_setting("lang", lang)
            self.reload_library()
        if cur and cur["uri"] == uri and cur["lang"] == lang:
            self.split.set_show_content(True)
            if anchor:
                tab.reader.scroll_to(anchor, flash)
            return
        chapter = self.store.chapter(lang, uri)
        if chapter:
            return self._show_chapter(tab, chapter, anchor, flash)
        tab.stack.set_visible_child_name("loading")
        tab.page.set_loading(True)
        self.split.set_show_content(True)

        def fetch():
            try:
                fetch_and_store_chapter(self.store, lang, uri)
                GLib.idle_add(lambda: self._show_chapter(tab, self.store.chapter(lang, uri), anchor, flash) and False)
            except Exception as e:
                GLib.idle_add(self._show_error, tab, uri, lang, str(e))

        threading.Thread(target=fetch, daemon=True).start()

    def _show_error(self, tab, uri, lang, message):
        tab.page.set_loading(False)
        tab.page.set_title("Not Available")
        tab.error_page.set_description(
            f"{GLib.markup_escape_text(message)}\n\nDownload this volume from Library &amp; Account, "
            f"or <a href=\"{api.BASE}/study{uri}?lang={lang}\">open it online</a>.")
        tab.stack.set_visible_child_name("error")

    def _show_chapter(self, tab, chapter, anchor=None, flash=True):
        selected = tab is self.tab
        if selected:
            self.stop_audio()
        tab.current = chapter
        tab.reader.load(chapter, self.store.notes_for(chapter["uri"]))
        tab.stack.set_visible_child_name("reader")
        i = self.leaf_index.get(chapter["uri"])
        tab.page.set_title(chapter["title"])
        tab.page.set_tooltip(self.leaves[i]["volume"]["title"] if i is not None else chapter["title"])
        tab.page.set_loading(False)
        self.store.set_setting("last_read", {"lang": chapter["lang"], "uri": chapter["uri"]})
        if anchor:
            tab.reader.scroll_to(anchor, flash)
        if selected:
            self._sync_header()
            self.split.set_show_content(True)
            tab.reader.view.grab_focus()

    def step_chapter(self, delta):
        cur = self.current
        i = self.leaf_index.get(cur["uri"]) if cur else None
        if i is not None and 0 <= i + delta < len(self.leaves):
            self.open_chapter(cur["lang"], self.leaves[i + delta]["uri"], history=False)

    def go_back(self):
        tab = self.tab
        if tab and tab.history:
            lang, uri, anchor = tab.history.pop()
            self.open_chapter(lang, uri, anchor, history=False, flash=False)
            self._update_nav_actions()

    def follow_link(self, href, new_tab=False):
        target = resolve_link(href, self.lang)
        if target[0] == "scripture":
            _, lang, uri, anchor = target
            self.open_chapter(lang, uri, anchor, new_tab=new_tab)
        else:
            Gtk.UriLauncher(uri=target[1]).launch(self, None, None, None)

    # ---- audio ------------------------------------------------------------------------

    def play_audio(self, *_):
        audio = (self.current or {}).get("audio") or []
        if not audio:
            return
        self.stop_audio()
        self.media = Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(audio[0]["mediaUrl"]))
        self.media.connect("notify::error", self._on_media_error)
        controls = Gtk.MediaControls(media_stream=self.media, margin_start=12, margin_end=12,
                                     margin_top=4, margin_bottom=4)
        self.media_bar.set_child(controls)
        self.media_bar.set_reveal_child(True)
        self.media.play()

    def _on_media_error(self, media, _pspec):
        if media.get_error():
            self.toast(f"Audio unavailable: {media.get_error().message}")
            self.stop_audio()

    def stop_audio(self):
        if self.media:
            self.media.pause()
            self.media = None
        self.media_bar.set_reveal_child(False)

    # ---- footnotes, selection, notes -----------------------------------------------------

    def _build_popovers(self):
        # One pair of popovers, moved to whichever tab's reader is showing.
        self.fn_popover = Gtk.Popover(position=Gtk.PositionType.BOTTOM)
        self.fn_title = _label("", "heading")
        self.fn_body = _label("", "footnote-text", max_width_chars=48, selectable=False)
        self.fn_body.set_use_markup(True)
        self.fn_body.connect("activate-link", self._on_footnote_link)
        self._track_modifiers(self.fn_body)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6,
                      margin_bottom=6, margin_start=6, margin_end=6)
        box.append(self.fn_title)
        box.append(self.fn_body)
        self.fn_popover.set_child(box)

        self.sel_popover = Gtk.Popover(position=Gtk.PositionType.TOP, has_arrow=True)
        bar = Gtk.Box(spacing=6, margin_top=2, margin_bottom=2, margin_start=2, margin_end=2)
        for c in COLORS:
            b = swatch_button(c, f"Highlight {c}")
            b.connect("clicked", lambda _b, c=c: self._create_from_selection(color=c))
            bar.append(b)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        note = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Add Note", css_classes=["flat"])
        note.connect("clicked", lambda *_: self._create_from_selection(edit=True))
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy with Reference", css_classes=["flat"])
        copy.connect("clicked", self._copy_selection)
        bar.append(note)
        bar.append(copy)
        self.sel_popover.set_child(bar)
        self.selection = None

    def show_footnote(self, note_id, rect):
        fn = (self.current or {}).get("footnotes", {}).get(note_id)
        if not fn:
            return
        self.fn_title.set_text(f"{fn.get('marker', '')}  {fn.get('context', '')}".strip())
        self.fn_body.set_markup(html_to_markup(fn.get("text", "")))
        self.fn_popover.set_pointing_to(rect)
        self.fn_popover.popup()

    def _on_footnote_link(self, _label, href):
        new_tab = self._take_ctrl()
        if not new_tab:
            self.fn_popover.popdown()
        self.follow_link(href, new_tab=new_tab)
        return True

    def show_selection_menu(self, selection, rect):
        self.selection = selection
        self.sel_popover.set_pointing_to(rect)
        self.sel_popover.popup()

    def _reference(self, spans):
        verses = self.reader.verse_range_label(spans)
        return self.current["title"] + (f":{verses}" if verses else "")

    def _copy_selection(self, *_):
        self.sel_popover.popdown()
        if self.selection:
            ref = self._reference(self.selection["spans"])
            self.get_clipboard().set(f"{self.selection['text']} ({ref})")
            self.toast("Copied")
        self.reader.clear_selection()

    def _create_from_selection(self, color=None, edit=False):
        self.sel_popover.popdown()
        sel = self.selection
        if not sel or not self.current:
            return
        note = self.store.create_note({
            "lang": self.current["lang"], "uri": self.current["uri"], "spans": sel["spans"],
            "color": color, "title": "", "content": "", "tags": [],
        })
        self.reader.clear_selection()
        self.notes_changed()
        if edit:
            self.edit_note(note)

    def edit_note_by_id(self, note_id):
        note = self.store.get_note(note_id)
        if note:
            self.edit_note(note)

    def edit_note(self, note):
        on_page = self.current and note["uri"] == self.current["uri"]
        quote = self.reader.text_for(note["spans"]) if on_page else ""
        ref = self._reference(note["spans"]) if on_page else (
            self.store.chapter_title(note["lang"], note["uri"]) or "Journal entry")
        NoteDialog(note, quote, ref, self._save_note, self._delete_note).present(self)

    def _save_note(self, note, changes):
        self.store.update_note(note["id"], changes)
        self.notes_changed()

    def _delete_note(self, note):
        self.store.delete_note(note["id"])
        self.notes_changed()
        self.toast("Highlight deleted")

    def notes_changed(self):
        # The same chapter may be open in several tabs.
        for tab in self.all_tabs():
            if tab.current:
                tab.reader.apply_notes(self.store.notes_for(tab.current["uri"]))
        page = self.nav.get_visible_page()
        if page and page.get_tag() == "notes":
            page.emit("showing")

    # ---- misc ---------------------------------------------------------------------------

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def show_preferences(self):
        PreferencesDialog(self).present(self)
