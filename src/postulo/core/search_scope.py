"""What the masthead's search narrows on a page that is not a table (#848).

`cotton/site_search.html` narrows "the table the page is showing" by `q`, and a `Table` says
what its box is called and which other parameters ride along. The Documents lists and the
Calendar are not tables, but they are *the thing the page shows*, and the search should
narrow that first, with everything one button away. This is the little a page needs to say:
the words on the box, the value in it, and the parameters to carry.

There is no live narrowing here: those pages answer a plain GET, which is the whole of the
answer with scripts off, and a form submit is one request more than typing but needs nothing
to be swapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.http import HttpRequest


@dataclass
class SearchScope:
    request: HttpRequest
    search_label: str
    #: The other parameters of the question that a new search keeps: the kind of document
    #: asked for, the period and the shape of the calendar.
    keep: tuple[str, ...] = field(default_factory=tuple)

    @property
    def search(self) -> str:
        return self.request.GET.get("q", "")

    @property
    def search_keeps(self) -> list[tuple[str, str]]:
        return [
            (name, value)
            for name in self.keep
            for value in self.request.GET.getlist(name)
            if value.strip()
        ]
