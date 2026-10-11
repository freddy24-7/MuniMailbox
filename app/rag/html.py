"""Turn a web page into plain text with its headings kept (FR-15).

Standard library only. Navigation, scripts and footers are dropped; the text of the
page's main content is returned as lines, headings marked with `#` so the chunker can
split on them.
"""

import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "nav", "header", "footer", "svg", "form", "button", "noscript", "aside"}
_VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "base", "col"}
_BLOCK = {"p", "li", "div", "section", "article", "tr", "ul", "ol", "table", "dd", "dt", "main"}
_HEADING = re.compile(r"h[1-6]$")


class _TextExtractor(HTMLParser):
    def __init__(self, container: tuple[str, str | None]) -> None:
        super().__init__(convert_charrefs=True)
        self._container_tag, self._container_id = container
        self._depth = 0  # > 0 while inside the content container
        self._skip_depth = 0
        self._buffer: list[str] = []
        self._prefix = ""
        self.lines: list[str] = []
        self.title = ""
        self._in_title = False

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        if text:
            self.lines.append(self._prefix + text)
        self._buffer, self._prefix = [], ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if self._depth == 0:
            is_container = tag == self._container_tag and (
                self._container_id is None or dict(attrs).get("id") == self._container_id
            )
            if is_container:
                self._depth = 1
            return
        if tag in _VOID:
            if tag == "br":
                self._buffer.append(" ")
            return
        self._depth += 1
        if self._skip_depth or tag in _SKIP:
            self._skip_depth += 1
            return
        if tag in _BLOCK or _HEADING.match(tag):
            self._flush()
            if _HEADING.match(tag):
                self._prefix = "#" * int(tag[1]) + " "
            elif tag == "li":
                self._prefix = "- "

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._depth == 0 or tag in _VOID:
            return
        if self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK or _HEADING.match(tag):
            self._flush()
        self._depth -= 1
        if self._depth == 0:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._depth and not self._skip_depth:
            self._buffer.append(data)


def _content_container(html: str) -> tuple[str, str | None]:
    if re.search(r"<article[\s>]", html):
        return ("article", None)
    if re.search(r"<main[\s>]", html):
        return ("main", None)
    if re.search(r'<div[^>]*\bid="main"', html):
        return ("div", "main")
    return ("body", None)


def extract(html: str) -> tuple[str, str]:
    """Returns (title, text). The title is the page's first heading, or its <title>."""
    parser = _TextExtractor(_content_container(html))
    parser.feed(html)
    parser.close()
    first_heading = next((line for line in parser.lines if line.startswith("# ")), None)
    title = first_heading[2:] if first_heading else parser.title.split("|")[0].strip()
    return title, "\n".join(parser.lines)
