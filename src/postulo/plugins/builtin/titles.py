"""What a page's declared titles say about the job, the employer and the place (#267).

A page with no structured data still names itself: in ``<title>``, and in the ``og:title``
and ``twitter:title`` it declares for a link to read. A great many boards pack three answers
into that one line -- "Test Chamber Engineer - Aperture Science - Lisbon | Board", "Job
Application for Test Chamber Engineer at Aperture Science" -- joined with the separators
boards actually use: a hyphen, an en dash, an em dash, a pipe, a middle dot, and "at"
between a job and whoever offers it.

**The parts carry no labels, and nothing here is decided by where a part stands.** One
board writes "Title - Company", the next "Company - Title", a third puts the place second.
So a part is named only when something else vouches for it:

- **the job**, when the page's one heading is that part, or a declared title that is not
  itself a line of parts -- or, failing both, when it is the only part left once everything
  else in the line has been recognised;
- **the employer**, when it follows the job after "at";
- **the place**, when Postulo's own table of cities knows it -- the table the map uses, so
  an instance that has not downloaded it recognises no place here and says nothing rather
  than guess -- and it is the only part the table knows. Plenty of employers share a name
  with a town (Toyota, Nokia, Santander), so a line with two parts the table knows is a
  line in which one of them is probably somebody's name, and neither is taken;
- **the site**, when it is the name the page declares for itself (``og:site_name``), its
  address ("Indeed.com"), or a word of its address ("LinkedIn" on linkedin.com). The site
  is taken out of a title and is not, by that alone, anybody's employer.

A part nothing vouches for is left where it was, and the field it might have filled is left
empty. An empty company costs somebody a second; a wrong one may never be noticed.

The browser extension reads the same line (``postulo-chromium/src/lib/parse.js``), and the
two are meant to split it identically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

#: Between two parts of a title: a hyphen, an en dash, an em dash or a middle dot with space
#: on both sides -- "Front-end" and "2025–2026" are one part each -- or a pipe, which never
#: sits inside anything worth keeping whole.
SEPARATOR = re.compile(r"(\s+[-–—·]\s+|\s*\|\s*)")

#: "at" as a board joins a job to its employer: a word on its own, with space around it.
AT = re.compile(r"\s+at\s+", re.IGNORECASE)

#: A word of a site's address shorter than this is a country, a "www" or a top-level domain,
#: and a title part that happens to be one is not the site signing its line.
SHORTEST_SITE_WORD = 4


@dataclass(frozen=True)
class Reading:
    """What one page's titles were found to say, each empty where nothing vouched for it."""

    title: str = ""
    company: str = ""
    place: str = ""


def plain(text: str) -> str:
    """Spacing as a reader sees it: a run of whitespace is one space."""
    return " ".join((text or "").split())


def _key(text: str) -> str:
    return plain(text).casefold()


def split(text: str) -> list[tuple[str, str]]:
    """A declared title as ``(separator before it, part)`` pairs, in the page's order.

    The first part's separator is empty. A line that starts or ends with a separator ("|
    Board") loses it with the empty part beside it.
    """
    pieces = SEPARATOR.split(plain(text))
    pairs: list[tuple[str, str]] = []
    waiting = ""
    for index, piece in enumerate(pieces):
        if index % 2:
            waiting = waiting or piece.strip()
            continue
        part = piece.strip()
        if part:
            pairs.append((waiting if pairs else "", part))
            waiting = ""
    return pairs


def joined(pairs: list[tuple[str, str]]) -> str:
    """Parts back into one line, each after the separator the page put before it."""
    text = ""
    for separator, part in pairs:
        text = f"{text} {separator or '-'} {part}" if text else part
    return text


def employer(part: str, title: str) -> str:
    """Who is hiring, in a part written "<... the job> at <employer>"; "" otherwise.

    The job has to be named first, so that "at" is read the way a board uses it and not
    wherever the word turns up: "Job Application for Test Chamber Engineer at Aperture
    Science" names Aperture Science, and a line that merely contains "at" names nobody.
    """
    if not title:
        return ""
    found = re.search(
        rf"(?<!\w){re.escape(plain(title))}\s+at\s+(?P<employer>\S.*)$",
        plain(part),
        re.IGNORECASE,
    )
    return found.group("employer").strip() if found else ""


def is_site(part: str, *, site_name: str = "", host: str = "") -> bool:
    """Whether a part is the site signing the line rather than something about the job."""
    key = _key(part)
    if not key:
        return False
    if site_name and key == _key(site_name):
        return True
    labels = [label for label in (host or "").lower().split(".") if label]
    if not labels:
        return False
    address = ".".join(labels)
    if "." in key:
        # "Indeed.com" on pt.indeed.com: a board signing with its address.
        return address == key or address.endswith(f".{key}")
    # "LinkedIn" on www.linkedin.com. Never the last word, which is the top-level domain.
    return key in {label for label in labels[:-1] if len(label) >= SHORTEST_SITE_WORD}


def read(
    *,
    declared: list[str],
    heading: str = "",
    site_name: str = "",
    host: str = "",
    is_place: Callable[[str], bool] = lambda _text: False,
) -> Reading:
    """The job, the employer and the place, from the lines a page declares as its title.

    ``declared`` is in the order the lines are trusted -- ``og:title``, ``twitter:title``,
    ``<title>`` -- ``heading`` is the page's one ``<h1>`` where it has exactly one,
    ``site_name`` what it calls itself, and ``is_place`` asks Postulo's table of cities.
    """
    lines: list[list[tuple[str, str]]] = []
    seen: set[str] = set()
    for text in declared:
        if plain(text) and _key(text) not in seen:
            seen.add(_key(text))
            lines.append(split(text))

    def site(part: str) -> bool:
        return is_site(part, site_name=site_name, host=host)

    # The job, where the page states it on its own: its one heading, or a declared title
    # that is a single part and not the site's name.
    title = plain(heading)
    if not title:
        for pairs in lines:
            if len(pairs) == 1 and not site(pairs[0][1]) and not AT.search(pairs[0][1]):
                title = pairs[0][1]
                break

    # "Job Application for Test Chamber Engineer at Aperture Science".
    company = ""
    for pairs in lines:
        for _separator, part in pairs:
            company = company or employer(part, title)

    # The places the lines name, where the table of cities knows them. The job's own part,
    # the site's and the employer's are not asked about. One is the place; two is a line in
    # which one of them is probably an employer named after a town, and neither is named.
    places: list[str] = []
    for pairs in lines:
        for _separator, part in pairs:
            if site(part) or _key(part) == _key(title) or AT.search(part):
                continue
            if _key(part) in {_key(place) for place in places}:
                continue
            if is_place(part):
                places.append(part)
    place = places[0] if len(places) == 1 else ""

    # No heading to say which part is the job: the part left once the site and the places
    # are taken out, or everything left where more than one is.
    if not title:
        known = {_key(place) for place in places}
        for pairs in lines:
            left = [pair for pair in pairs if not site(pair[1]) and _key(pair[1]) not in known]
            if left:
                title = left[0][1] if len(left) == 1 else joined(left)
                break
    if not title and lines:
        # A line that is nothing but the site's name is still what the page calls itself,
        # and a capture titled with it is a capture somebody can correct.
        title = joined(lines[0])

    return Reading(title=title, company=company, place=place)
