"""HTML parsing helpers: volume manifests into TOC trees, chapter bodies into verses."""

import re
from html.parser import HTMLParser

from .api import normalize_uri


class _ManifestParser(HTMLParser):
    """Turns nested <ul class="doc-map"><li>... into a tree of groups and leaf links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"title": "", "children": []}
        self.stack = [self.root]  # groups
        self.li_stack = []  # (kind, node) for open <li>
        self.capture = None  # ('label'|'title'|'description', target dict)
        self.in_label = False
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "li":
            self.li_stack.append({"type": a.get("data-content-type", ""), "group": None})
        elif tag == "h2" and "label" in cls and self.li_stack:
            group = {"title": "", "type": self.li_stack[-1]["type"], "children": []}
            self.stack[-1]["children"].append(group)
            self.stack.append(group)
            self.li_stack[-1]["group"] = group
            self.in_label = True
        elif tag == "a" and a.get("href") and self.li_stack:
            self.current_link = {
                "uri": normalize_uri(a["href"]),
                "type": self.li_stack[-1]["type"],
                "title": "",
                "description": "",
            }
        elif tag == "p" and "title" in cls:
            if self.current_link is not None:
                self.capture = ("title", self.current_link)
            elif self.in_label:
                self.capture = ("title", self.stack[-1])
        elif tag == "p" and "description" in cls and self.current_link is not None:
            self.capture = ("description", self.current_link)

    def handle_endtag(self, tag):
        if tag == "p":
            self.capture = None
        elif tag == "h2":
            self.in_label = False
        elif tag == "a" and self.current_link is not None:
            link = self.current_link
            self.current_link = None
            if link["type"] != "table-of-contents":
                self.stack[-1]["children"].append(link)
        elif tag == "li" and self.li_stack:
            li = self.li_stack.pop()
            if li["group"] is not None and self.stack[-1] is li["group"]:
                self.stack.pop()

    def handle_data(self, data):
        if self.capture:
            key, target = self.capture
            target[key] = (target.get(key, "") + data).strip()


def parse_manifest(html):
    p = _ManifestParser()
    p.feed(html)
    return p.root["children"]


def iter_leaves(tree):
    for node in tree:
        if "children" in node:
            yield from iter_leaves(node["children"])
        else:
            yield node


class _VerseParser(HTMLParser):
    """Extracts plain text of each paragraph that carries a data-aid (verses, headings, intros)."""

    SKIP = {"sup"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.verses = []
        self.cur = None
        self.depth = 0
        self.skip = 0
        self.in_num = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cur is None:
            if tag == "p" and a.get("data-aid"):
                cls = a.get("class") or ""
                self.cur = {
                    "pid": a["data-aid"],
                    "id": a.get("id", ""),
                    "kind": "verse" if "verse" in cls.split() else (cls or "p"),
                    "num": None,
                    "text": "",
                }
                self.depth = 1
            return
        self.depth += 1
        if tag in self.SKIP:
            self.skip += 1
        if tag == "span" and "verse-number" in (a.get("class") or ""):
            self.in_num = self.depth

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if self.in_num == self.depth:
            self.in_num = 0
        self.depth -= 1
        if self.depth == 0:
            self.cur["text"] = re.sub(r"\s+", " ", self.cur["text"]).strip()
            self.verses.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.cur is None or self.skip:
            return
        if self.in_num:
            num = data.strip()
            if num.isdigit():
                self.cur["num"] = int(num)
            return
        self.cur["text"] += data


def parse_verses(body_html):
    p = _VerseParser()
    p.feed(body_html)
    return p.verses


_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.I | re.S)
_ON_ATTR_RE = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def sanitize(html):
    return _ON_ATTR_RE.sub("", _SCRIPT_RE.sub("", html))
