"""Converts chapter HTML into plain styled blocks for native rendering (no GTK imports here).

A chapter becomes a list of blocks (paragraphs). Each block has runs of text with style names.
Runs marked `countable` are the ones Gospel Library counts when it numbers words for highlight
offsets: everything except verse numbers and superscripts (footnote letters).
"""

import re
import urllib.parse
from html import escape
from html.parser import HTMLParser

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "figcaption", "tr", "dt", "dd",
              "blockquote", "div", "section", "header", "figure", "table", "ul", "ol"}
SKIP_TAGS = {"script", "style", "video", "audio", "button", "footer", "nav"}
INLINE_STYLES = {"i": "italic", "em": "italic", "b": "bold", "strong": "bold",
                 "small": "smallcaps", "sub": "sub", "u": "underline"}
_WS = re.compile(r"\s+")


class _BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.block_stack = []  # (tag, kind, anchor)
        self.runs = []
        self.inline = []  # stack of (tag, styles added, note, href, countable)
        self.skip = 0

    # -- helpers --
    def _ctx(self):
        styles, note, href, countable = set(), None, None, True
        for _, add, n, h, c in self.inline:
            styles |= add
            note = n or note
            href = h or href
            countable = countable and c
        return styles, note, href, countable

    def _emit(self, text, extra=(), countable=None):
        styles, note, href, ctx_countable = self._ctx()
        self.runs.append({
            "text": text, "styles": frozenset(styles | set(extra)), "note": note, "href": href,
            "countable": ctx_countable if countable is None else countable,
        })

    def _flush(self):
        runs = [r for r in self.runs if r["text"]]
        self.runs = []
        # Collapse whitespace like HTML layout does, across run boundaries.
        prev_space = True
        for r in runs:
            t = _WS.sub(" ", r["text"])
            if prev_space:
                t = t.lstrip(" ")
            if t:
                prev_space = t.endswith(" ")
            r["text"] = t
        while runs and runs[-1]["text"].endswith(" "):
            runs[-1]["text"] = runs[-1]["text"].rstrip(" ")
            if not runs[-1]["text"]:
                runs.pop()
        runs = [r for r in runs if r["text"]]
        if not runs:
            return
        tag, kind, anchor = self.block_stack[-1] if self.block_stack else ("p", "p", None)
        self.blocks.append({"tag": tag, "kind": kind, "anchor": anchor, "runs": runs})

    # -- parser callbacks --
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip or tag in ("img", "hr", "source"):
            return
        if tag == "br":
            return self._emit("\n", countable=False)
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in ("h1",):
                kind = "title"
            elif tag in ("h2", "h3", "h4", "h5", "h6"):
                kind = "heading"
            elif "verse" in cls:
                kind = "verse"
            elif cls:
                kind = cls[0]
            else:
                kind = tag
            anchor = a.get("id") if tag in ("p", "h1", "h2", "h3", "li") and a.get("data-aid") else None
            self.block_stack.append((tag, kind, anchor))
            return
        if tag == "sup":
            if "marker" in cls:
                self.inline.append((tag, {"marker"}, None, None, False))
                if a.get("data-value"):
                    self._emit(a["data-value"])
            else:
                self.inline.append((tag, {"sup"}, None, None, False))
            return
        if tag == "span" and "verse-number" in cls:
            self.inline.append((tag, {"verse-number"}, None, None, False))
            return
        if tag == "a":
            href = a.get("href") or ""
            if "study-note-ref" in cls and href.startswith("#"):
                self.inline.append((tag, {"footnote"}, href[1:], None, True))
            elif href and not href.startswith("#"):
                self.inline.append((tag, {"link"}, None, href, True))
            else:
                self.inline.append((tag, set(), None, None, True))
            return
        self.inline.append((tag, {INLINE_STYLES[tag]} if tag in INLINE_STYLES else set(),
                            None, None, True))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in BLOCK_TAGS:
            self._flush()
            # Pop up to and including the matching block (tolerates sloppy nesting).
            for i in range(len(self.block_stack) - 1, -1, -1):
                if self.block_stack[i][0] == tag:
                    del self.block_stack[i:]
                    break
            return
        for i in range(len(self.inline) - 1, -1, -1):
            if self.inline[i][0] == tag:
                del self.inline[i:]
                break

    def handle_data(self, data):
        if not self.skip and data:
            self._emit(data)


def parse_body(html):
    p = _BodyParser()
    p.feed(html)
    p.close()
    p._flush()
    return p.blocks


# ---- links ------------------------------------------------------------------

SITE = "https://www.churchofjesuschrist.org"


def resolve_link(href, default_lang):
    """Returns ('scripture', lang, uri, anchor) for in-library links or ('web', url)."""
    url = urllib.parse.urlsplit(urllib.parse.urljoin(SITE + "/", href))
    path = url.path
    if url.netloc.endswith("churchofjesuschrist.org"):
        if path.startswith("/study/"):
            path = path[len("/study"):]
        if path.startswith("/scriptures/"):
            q = dict(urllib.parse.parse_qsl(url.query))
            ident = q.get("id") or url.fragment
            anchor = re.split(r"[-,]", ident)[0] if ident else None
            return ("scripture", q.get("lang") or default_lang, path.rstrip("/"), anchor)
    return ("web", urllib.parse.urlunsplit(url))


# ---- Pango markup for footnotes and search snippets -----------------------------

class _MarkupParser(HTMLParser):
    MAP = {"i": "i", "em": "i", "b": "b", "strong": "b", "small": "small", "sup": "sup",
           "sub": "sub", "mark": "b"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open = []
        self.para = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "p":
            if self.para:
                self.out.append("\n")
            self.para = True
        elif tag == "br":
            self.out.append("\n")
        elif tag == "a" and a.get("href"):
            self.out.append(f'<a href="{escape(a["href"], quote=True)}">')
            self.open.append("a")
        elif tag in self.MAP:
            self.out.append(f"<{self.MAP[tag]}>")
            self.open.append(self.MAP[tag])
        else:
            self.open.append(None)

    def handle_endtag(self, tag):
        if tag in ("p", "br"):
            return
        if self.open:
            t = self.open.pop()
            if t:
                self.out.append(f"</{t}>")

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))


def html_to_markup(html):
    p = _MarkupParser()
    p.feed(html)
    out = "".join(p.out)
    while p.open:
        t = p.open.pop()
        if t:
            out += f"</{t}>"
    return out.strip()
