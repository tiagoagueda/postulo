"""Text the Europass editors wrote as HTML, as lines of text.

Both formats hold their long fields -- a job's description, what a course covered, a block of
skills -- as HTML escaped inside the XML or the JSON. A career record holds plain text, so
each reader passes those fields through :func:`_plain` (#563). It lived in ``candidate`` until
the older format's readers needed it too.
"""

from __future__ import annotations

from html.parser import HTMLParser


class _Words(HTMLParser):
    """The words in a fragment of HTML, with its paragraphs and list items kept apart."""

    BREAKS = frozenset(
        {"br", "p", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
    )
    SILENT = frozenset({"script", "style"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.silent = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SILENT:
            self.silent += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SILENT:
            self.silent = max(0, self.silent - 1)
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.silent:
            self.parts.append(data)


def _plain(value: str) -> str:
    """Text the editor wrote as HTML, as lines of text.

    The long fields -- a job's description, what a course covered -- hold HTML, escaped
    inside the XML, and a career record holds plain text: markup kept as it came would
    print on a CV as angle brackets. Paragraphs and list items stay on lines of their own;
    the markup goes. It is read by the standard library's tokenizer, which runs nothing and
    fetches nothing.
    """
    if not value:
        return ""
    if "<" in value:
        words = _Words()
        words.feed(value)
        words.close()
        value = "".join(words.parts)
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line)
