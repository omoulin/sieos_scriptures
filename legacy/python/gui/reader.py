"""Native chapter view: a read-only GtkTextView with footnotes, links and highlights."""

import re

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..render import parse_body

COLORS = ["yellow", "green", "blue", "red", "purple", "orange", "pink", "gray"]
# Gospel Library color names that map onto the palette above.
COLOR_ALIASES = {"dark_blue": "blue", "brown": "orange", "grey": "gray"}
PALETTE = {
    False: {"yellow": "#fbe68a", "green": "#bfe6b3", "blue": "#b9d9f5", "red": "#f6b7b0",
            "purple": "#d9c3f0", "orange": "#f9cf9f", "pink": "#f6c1dd", "gray": "#d6d4cf"},
    True: {"yellow": "#6b5a14", "green": "#2f5a29", "blue": "#23486b", "red": "#6e2c26",
           "purple": "#4c3466", "orange": "#6d4519", "pink": "#683250", "gray": "#4a4843"},
}

ITALIC = {"style": Pango.Style.ITALIC}
BLOCK_STYLES = {
    "p": {"pixels_below_lines": 12},
    "verse": {"pixels_below_lines": 12},
    "title": {"scale": 1.7, "weight": 700, "justification": Gtk.Justification.CENTER,
              "pixels_above_lines": 8, "pixels_below_lines": 10},
    "subtitle": {**ITALIC, "justification": Gtk.Justification.CENTER, "pixels_below_lines": 14},
    "intro": {**ITALIC, "pixels_below_lines": 16},
    "title-number": {"scale": 1.25, "weight": 600, "justification": Gtk.Justification.CENTER,
                     "pixels_above_lines": 14, "pixels_below_lines": 12},
    "study-summary": {**ITALIC, "scale": 0.94, "pixels_below_lines": 22},
    "heading": {"scale": 1.2, "weight": 700, "pixels_above_lines": 18, "pixels_below_lines": 8},
    "label": {"scale": 1.1, "weight": 700, "pixels_above_lines": 14, "pixels_below_lines": 8},
}
BLOCK_ALIASES = {"study-intro": "intro", "entry": "p"}
DIM_BLOCKS = {"intro", "study-summary"}
SPAN_STYLES = {
    "italic": ITALIC,
    "bold": {"weight": 700},
    "underline": {"underline": Pango.Underline.SINGLE},
    "smallcaps": {"variant": Pango.Variant.SMALL_CAPS, "scale": 0.92},
    "sup": {"rise": 6 * Pango.SCALE, "scale": 0.7},
    "sub": {"rise": -3 * Pango.SCALE, "scale": 0.7},
    "verse-number": {"weight": 700, "scale": 0.78},
    "marker": {"rise": 7 * Pango.SCALE, "scale": 0.62, **ITALIC},
    "link": {"underline": Pango.Underline.SINGLE},
}


def rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec)
    return c


class ReaderView(Gtk.ScrolledWindow):
    """Shows one chapter. Hooks (set by the window):
    on_footnote(note_id, rect), on_link(href), on_note(note_id), on_selection(spans, rect)."""

    def __init__(self):
        super().__init__(vexpand=True, hexpand=True)
        self.view = Gtk.TextView(
            editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=28, bottom_margin=120, left_margin=28, right_margin=28,
            pixels_inside_wrap=4,
        )
        self.view.add_css_class("reader")
        self.view.update_property([Gtk.AccessibleProperty.LABEL], ["Chapter text"])
        self.set_child(Adw.ClampScrollable(maximum_size=780, tightening_threshold=620,
                                           child=self.view))
        self.buffer = self.view.get_buffer()
        self.on_footnote = self.on_link = self.on_note = self.on_selection = None

        self.paragraphs = []   # {anchor, num, start, end, words: [(start, end)]}
        self.by_anchor = {}
        self.regions = []      # (start, end, kind, payload) for footnotes and links
        self.note_regions = []  # (start, end, note_id)
        self.countable = bytearray()
        self.marks = []
        self._last_top = None  # last known top paragraph, for when the view is hidden (other tab)
        self._create_tags()
        self.get_vadjustment().connect("value-changed", lambda *_: self._remember_top())

        sm = Adw.StyleManager.get_default()
        sm.connect("notify::dark", lambda *_: self._update_colors())
        sm.connect("notify::accent-color-rgba", lambda *_: self._update_colors())
        self._update_colors()

        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", self._on_event)
        self.view.add_controller(legacy)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.view.add_controller(motion)

    # ---- tags ---------------------------------------------------------------

    def _create_tags(self):
        # Creation order sets priority: block < span < highlight.
        for kind, props in BLOCK_STYLES.items():
            self.buffer.create_tag("b-" + kind, **props)
        for name, props in SPAN_STYLES.items():
            self.buffer.create_tag("s-" + name, **props)
        for c in COLORS:
            self.buffer.create_tag("hl-" + c)
        self.buffer.create_tag("hl-clear", underline=Pango.Underline.SINGLE)
        self.buffer.create_tag("noted", underline=Pango.Underline.DOUBLE)
        self.buffer.create_tag("flash")

    def _update_colors(self):
        sm = Adw.StyleManager.get_default()
        dark = sm.get_dark()
        table = self.buffer.get_tag_table()
        for c, spec in PALETTE[dark].items():
            table.lookup("hl-" + c).set_property("background-rgba", rgba(spec))
        accent = sm.get_accent_color_rgba()
        dim = rgba("rgba(255,255,255,0.62)" if dark else "rgba(0,0,0,0.58)")
        for kind in DIM_BLOCKS:
            table.lookup("b-" + kind).set_property("foreground-rgba", dim)
        table.lookup("s-verse-number").set_property("foreground-rgba", dim)
        table.lookup("s-marker").set_property("foreground-rgba", accent)
        table.lookup("s-link").set_property("foreground-rgba", accent)
        table.lookup("noted").set_property("underline-rgba", accent)
        soft = Gdk.RGBA()
        soft.red, soft.green, soft.blue, soft.alpha = accent.red, accent.green, accent.blue, 0.18
        table.lookup("flash").set_property("paragraph-background-rgba", soft)

    # ---- loading ------------------------------------------------------------

    def load(self, chapter, notes):
        buf = self.buffer
        for m in self.marks:
            buf.delete_mark(m)
        self.marks = []
        buf.set_text("")
        self.paragraphs, self.by_anchor, self.regions = [], {}, []
        self._last_top = None
        countable = bytearray()
        blocks = parse_body(chapter["body"])
        for bi, block in enumerate(blocks):
            kind = BLOCK_ALIASES.get(block["kind"], block["kind"])
            btag = "b-" + (kind if kind in BLOCK_STYLES else "p")
            start = buf.get_char_count()
            offsets, chars, num = [], [], None
            for run in block["runs"]:
                text = run["text"]
                off = buf.get_char_count()
                tags = [btag] + ["s-" + s for s in run["styles"] if s in SPAN_STYLES]
                buf.insert_with_tags_by_name(buf.get_end_iter(), text, *tags)
                end = off + len(text)
                countable += bytes([1 if run["countable"] else 0]) * len(text)
                if run["countable"]:
                    chars.append(text)
                    offsets.extend(range(off, end))
                if "verse-number" in run["styles"] and text.strip().isdigit():
                    num = int(text.strip())
                if run["note"]:
                    kind_ = "marker" if "marker" in run["styles"] else "footnote"
                    self.regions.append((off, end, kind_, run["note"]))
                elif run["href"]:
                    self.regions.append((off, end, "link", run["href"]))
            joined = "".join(chars)
            words = [(offsets[m.start()], offsets[m.end() - 1] + 1)
                     for m in re.finditer(r"\S+", joined)]
            para = {"anchor": block["anchor"], "num": num, "start": start,
                    "end": buf.get_char_count(), "words": words}
            self.paragraphs.append(para)
            if block["anchor"]:
                self.by_anchor[block["anchor"]] = para
                self.marks.append(buf.create_mark(None, buf.get_iter_at_offset(start), True))
                para["mark"] = self.marks[-1]
            if bi < len(blocks) - 1:
                buf.insert_with_tags_by_name(buf.get_end_iter(), "\n", btag)
                countable += b"\x01"
        self.countable = countable
        buf.place_cursor(buf.get_start_iter())
        self.apply_notes(notes)
        GLib.idle_add(lambda: self.get_vadjustment().set_value(0) and False)

    def apply_notes(self, notes):
        buf = self.buffer
        s, e = buf.get_bounds()
        for name in ["hl-" + c for c in COLORS] + ["hl-clear", "noted"]:
            buf.remove_tag_by_name(name, s, e)
        self.note_regions = []
        for n in notes:
            color = COLOR_ALIASES.get(n.get("color"), n.get("color"))
            for span in n.get("spans") or []:
                rng = self._span_range(span)
                if not rng:
                    continue
                a, b = buf.get_iter_at_offset(rng[0]), buf.get_iter_at_offset(rng[1])
                if color in COLORS or color == "clear":
                    buf.apply_tag_by_name("hl-" + color, a, b)
                if n.get("content") or n.get("title") or not color:
                    buf.apply_tag_by_name("noted", a, b)
                self.note_regions.append((rng[0], rng[1], n["id"]))

    def _span_range(self, span):
        p = self.by_anchor.get(span.get("anchor"))
        if not p or not p["words"]:
            return None
        words = p["words"]
        first = 0 if span["start"] < 0 else span["start"]
        last = len(words) - 1 if span["end"] < 0 else min(span["end"], len(words) - 1)
        if first > last:
            return None
        return words[first][0], words[last][1]

    # ---- navigation helpers ---------------------------------------------------

    def scroll_to(self, anchor, flash=True):
        p = self.by_anchor.get(anchor)
        if not p:
            return
        self._last_top = anchor

        def do():
            self.view.scroll_to_mark(p["mark"], 0.0, True, 0.0, 0.25)
            return False

        GLib.timeout_add(60, do)
        if flash:
            a, b = self.buffer.get_iter_at_offset(p["start"]), self.buffer.get_iter_at_offset(p["end"])
            self.buffer.apply_tag_by_name("flash", a, b)
            GLib.timeout_add(1600, lambda: self.buffer.remove_tag_by_name(
                "flash", *self.buffer.get_bounds()) and False)

    def top_anchor(self):
        return (self._visible_top() if self.get_mapped() else None) or self._last_top

    def _remember_top(self):
        if self.get_mapped():
            self._last_top = self._visible_top() or self._last_top

    def _visible_top(self):
        rect = self.view.get_visible_rect()
        ok, it = self.view.get_iter_at_location(rect.x + 10, rect.y + 10)
        if not ok:
            return None
        off = it.get_offset()
        for p in self.paragraphs:
            if p["anchor"] and p["end"] >= off:
                return p["anchor"]
        return None

    def verse_range_label(self, spans):
        nums = [self.by_anchor[s["anchor"]]["num"] for s in spans
                if s["anchor"] in self.by_anchor and self.by_anchor[s["anchor"]]["num"]]
        if not nums:
            return ""
        return str(nums[0]) if nums[0] == nums[-1] else f"{nums[0]}–{nums[-1]}"

    def text_for(self, spans):
        """Readable text of note spans (without verse numbers or footnote letters)."""
        parts = []
        for s in spans:
            rng = self._span_range(s)
            if rng:
                parts.append(self._clean_text(*rng))
        return " … ".join(p for p in parts if p)

    def _clean_text(self, start, end):
        text = self.buffer.get_text(self.buffer.get_iter_at_offset(start),
                                    self.buffer.get_iter_at_offset(end), True)
        kept = "".join(ch for i, ch in enumerate(text, start)
                       if i < len(self.countable) and self.countable[i])
        return re.sub(r"[ \t]+", " ", kept).strip()

    # ---- selection ------------------------------------------------------------

    def selection(self):
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            return None
        s, e = bounds[0].get_offset(), bounds[1].get_offset()
        spans = []
        for p in self.paragraphs:
            if not p["anchor"] or p["end"] <= s or p["start"] >= e:
                continue
            idx = [i for i, (ws, we) in enumerate(p["words"]) if ws < e and we > s]
            if idx:
                spans.append({"anchor": p["anchor"], "start": idx[0], "end": idx[-1]})
        return {"spans": spans, "text": self._clean_text(s, e), "start": s, "end": e} if spans else None

    def clear_selection(self):
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        self.buffer.select_range(it, it)

    def rect_for_offsets(self, start, end):
        """Widget-relative rectangle around [start, end) (first line if it wraps)."""
        a = self.view.get_iter_location(self.buffer.get_iter_at_offset(start))
        b = self.view.get_iter_location(self.buffer.get_iter_at_offset(max(start, end - 1)))
        x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, a.x, a.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.height = x, y, a.height
        rect.width = max(1, b.x + b.width - a.x) if b.y == a.y else max(1, a.width)
        return rect

    # ---- events ----------------------------------------------------------------

    def _hit(self, off):
        for s, e, kind, payload in self.regions:
            if s <= off < e and kind == "marker":
                return kind, payload, (s, e)
        for s, e, nid in reversed(self.note_regions):
            if s <= off < e:
                return "note", nid, (s, e)
        for s, e, kind, payload in self.regions:
            if s <= off < e:
                return kind, payload, (s, e)
        return None

    def _on_event(self, _ctrl, event):
        t = event.get_event_type()
        if t == Gdk.EventType.BUTTON_RELEASE and event.get_button() == 1 or t == Gdk.EventType.TOUCH_END:
            GLib.idle_add(self._after_release)
        return False

    def _after_release(self):
        sel = self.selection()
        if sel:
            if self.on_selection:
                self.on_selection(sel, self.rect_for_offsets(sel["start"], sel["end"]))
            return False
        off = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        # The cursor lands between characters; also try the character to the left.
        hit = self._hit(off) or (self._hit(off - 1) if off else None)
        if not hit:
            return False
        kind, payload, (s, e) = hit
        if kind == "note" and self.on_note:
            self.on_note(payload)
        elif kind in ("marker", "footnote") and self.on_footnote:
            self.on_footnote(payload, self.rect_for_offsets(s, e))
        elif kind == "link" and self.on_link:
            self.on_link(payload)
        return False

    def _on_motion(self, _ctrl, x, y):
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.view.get_iter_at_location(bx, by)
        hit = self._hit(it.get_offset()) if ok else None
        self.view.set_cursor_from_name("pointer" if hit else "text")
