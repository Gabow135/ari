"""Minimal HTML-to-text using only the stdlib, so email bodies are readable
without pulling a dependency. Not a full renderer: it drops script/style, turns
block tags into line breaks, and decodes entities."""
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "head", "title"}
_BLOCK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
          "table", "ul", "ol", "blockquote"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t\n]*", "\n\n", text)
    return text.strip()
