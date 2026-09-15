"""HTML -> Edvibe content sections.

The hermes/esl handouts are standalone HTML lesson guides: a <title>, a single
<h1> document heading, then <h2> SECTION blocks with <h3> sub-parts. We split
the body into blocks of sanitised HTML that fit an Edvibe Note (Type 22).

Split level heuristic:
  1. count heading levels (h1..h4) across the document;
  2. drop a leading lone <h1> (the document title);
  3. split level = the smallest heading level that occurs >= 2 times (fallback h2).
"""
import html.parser
import re


class _Probe(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.levels = []
        self.in_title = False
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True
        elif tag.lower() in ("h1", "h2", "h3", "h4"):
            self.levels.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


_SKIP_TAGS = {"style", "script", "head", "nav", "header", "footer", "iframe", "svg", "form"}


class _Splitter(html.parser.HTMLParser):
    def __init__(self, split_level, drop_first_h1=False):
        super().__init__(convert_charrefs=True)
        self.split_level = split_level
        self.drop_first_h1 = drop_first_h1
        self.seen_h1 = False
        self.current = []            # raw chunks of the open section body
        self.pending_title = None    # title of the open section
        self.lead = ""               # intro text before the first section heading
        self.sections = []
        self._skip = 0
        self._in_heading = False
        self._heading_buf = []
        self._dropped_h1 = False

    # ------------------------------------------------------------- helpers
    def _open(self, tag, attrs, self_closing=False):
        if not attrs:
            return f"<{tag}{'/>' if self_closing else '>'}"
        parts = []
        for k, v in attrs:
            kl = k.lower()
            if kl.startswith("on") or kl == "style":
                continue
            parts.append(k if v is None else f'{k}="{html.escape(str(v), quote=True)}"')
        return f"<{tag} " + " ".join(parts) + ("/>" if self_closing else ">")

    def _finish_heading(self):
        if self._in_heading:
            self.pending_title = re.sub(r"\s+", " ", "".join(self._heading_buf)).strip()
            self._in_heading = False
            self._heading_buf = []

    def _flush(self):
        self._finish_heading()
        body = "".join(self.current).strip()
        if body and self.pending_title:
            if self.lead:
                body = self.lead + "\n" + body
                self.lead = ""
            self.sections.append({"title": self.pending_title, "html": body})
        elif body:
            # intro text before the first real section heading
            self.lead = (self.lead + "\n" + body).strip()
        self.current = []
        self.pending_title = None

    # ------------------------------------------------------------- parsing
    def handle_starttag(self, tag, attrs):
        low = tag.lower()
        if low in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if low in ("html", "body"):
            return
        if low in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(low[1])
            if self.drop_first_h1 and not self.seen_h1 and level == 1:
                # document title heading: capture text, emit no section
                self.seen_h1 = True
                self.current = []          # drop preamble junk before it
                self._in_heading = True
                self._heading_buf = []
                self._dropped_h1 = True
                return
            if level <= self.split_level:
                self._flush()
                self._in_heading = True
                self._heading_buf = []
            else:
                self.current.append(self._open(low, attrs))
            return
        self.current.append(self._open(low, attrs))

    def handle_endtag(self, tag):
        low = tag.lower()
        if low in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if low in ("html", "body"):
            return
        if low in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self._in_heading:
                self._finish_heading()
                if self._dropped_h1:
                    self._dropped_h1 = False
                    self.pending_title = None  # never emit the doc title as a section
            else:
                self.current.append(f"</{low}>")
            return
        self.current.append(f"</{low}>")

    def handle_startendtag(self, tag, attrs):
        low = tag.lower()
        if self._skip or low in ("html", "body"):
            return
        self.current.append(self._open(low, attrs, self_closing=True))

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_heading:
            self._heading_buf.append(data)
        else:
            self.current.append(data)

    def close(self):
        super().close()
        self._flush()
        if self.lead:
            self.sections.append({"title": "", "html": self.lead})


def _choose_split(levels):
    counts = {1: 0, 2: 0, 3: 0}
    for lv in levels:
        if lv in counts:
            counts[lv] += 1
    for lv in (1, 2, 3):
        if counts[lv] >= 2:
            return lv
    return 2


def html_to_sections(path, min_len=40):
    """Parse a standalone handout HTML file.

    Returns (title, [{"title": str, "html": str}, ...]).
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    probe = _Probe()
    probe.feed(raw)
    probe.close()
    title = re.sub(r"\s+", " ", "".join(probe.title)).strip()

    levels = probe.levels
    drop_first_h1 = bool(levels and levels[0] == 1 and levels.count(1) == 1)
    split_level = _choose_split(levels[1:] if drop_first_h1 else levels)

    sp = _Splitter(split_level=split_level, drop_first_h1=drop_first_h1)
    sp.feed(raw)
    sp.close()

    sections = []
    for sec in sp.sections:
        body = sec["html"]
        if not sec["title"] and len(re.sub(r"<[^>]+>", "", body)) < min_len:
            continue
        sections.append(sec)
    return title, sections
