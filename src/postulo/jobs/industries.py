"""Where the list of areas of activity comes from, and why it is a seed rather than a list.

> you shoud looke for a more extenside and general area of activity list

Thirty-two names assembled by hand was not a classification. It had *Gaming* and
*E-commerce* and no *Mining*, no *Water supply*, no *Arts*, nothing at all for a third of
the economy — and the way to fix that is not to keep adding names, because a hand-made list
of two hundred would be two hundred names in thirty-nine languages, carried by this project
for ever (#140).

**So the list is NACE**, the European Union's statistical classification of economic
activities, at **division** level: 87 two-digit codes under 22 sections. That depth is a
deliberate choice between three. Its 21 sections are coarser than the hand-made list ever
was — *Information and communication* was one word for software, telecoms, publishing and
film. Its 615 classes are a form nobody fills in. The divisions are the level where the
names still mean something to the person reading them.

**The translations are the real argument, more than the taxonomy.** Eurostat publishes NACE
in every official EU language, so 87 × 24 names arrive already written by the body that
maintains them. `data/nace-2.1.json` holds them; `data/LICENCE.md` says on whose terms.
Nothing here is a gettext string, because these are *reference data* in the same sense that
a language's own name is: Postulo does not translate them, it reads the translation somebody
else published.

**Where Eurostat publishes no name, the English one stands.** Fifteen of the languages
Postulo speaks are not official EU languages, and inventing NACE names for them would be
this project asserting a classification it does not maintain. A Catalan or Ukrainian reader
sees the English division names — and, much more to the point, types their own word, which
is what almost everybody does anyway.

**NACE classifies the business, not the job, and that difference bites.** Somebody applying
to a bank's software team is applying to *K — Financial and insurance activities*, which is
true of the employer and useless to the applicant. So this is a **seed and never a closed
list**: `Industry` stays one vocabulary per person, in their own words, and the short
familiar names Postulo always offered stay too, first in the list, because *Software* is
what a person types and *Computer programming, consultancy and related activities* is what a
statistician writes. Both are legitimate answers to "what does this company do".

**A name that matches a division keeps its code.** That is the whole of what the standard
buys beyond coverage: a report (#56) can say *62* to an employment office that thinks in
NACE, while the person goes on reading their own word for it. A name nobody recognises has
no code, and that is not a lesser kind of industry.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

DATA = Path(__file__).resolve().parent / "data" / "nace-2.1.json"

#: The language a division is named in when Eurostat publishes no name in the reader's.
FALLBACK = "en"

#: The short, familiar names Postulo has always offered. Kept beside the classification
#: rather than replaced by it: these are the words people actually type, they are already
#: translated with the interface, and dropping them would make the common cases worse in
#: exchange for coverage of the uncommon ones.
STARTER_INDUSTRIES = (
    _("Software"),
    _("Information technology"),
    _("Telecommunications"),
    _("Finance"),
    _("Banking"),
    _("Insurance"),
    _("Consulting"),
    _("Health"),
    _("Pharmaceuticals"),
    _("Biotechnology"),
    _("Education"),
    _("Research"),
    _("Public sector"),
    _("Non-profit"),
    _("Energy"),
    _("Utilities"),
    _("Manufacturing"),
    _("Engineering"),
    _("Construction"),
    _("Automotive"),
    _("Aerospace"),
    _("Transport and logistics"),
    _("Retail"),
    _("E-commerce"),
    _("Hospitality and tourism"),
    _("Food and agriculture"),
    _("Media"),
    _("Advertising and marketing"),
    _("Gaming"),
    _("Real estate"),
    _("Legal"),
    _("Defence"),
)


@lru_cache(maxsize=1)
def classification() -> dict:
    """The vendored file, read once.

    Cached for the life of the process because it is a file that only changes when somebody
    replaces it, and 180 kB of JSON parsed on every company form would be a page slower for
    no reason.
    """
    return json.loads(DATA.read_text(encoding="utf-8"))


def revision() -> str:
    return classification()["revision"]


def languages() -> list[str]:
    """The languages the classification itself is published in."""
    return list(classification()["languages"])


def _reading(language: str = "") -> str:
    """Which of the classification's languages to read, for whoever is reading Postulo.

    ``pt-br`` takes the Portuguese names and ``en-gb`` the English ones: the base language
    is what a classification is published in, and a variant of it is the same words.
    """
    code = (language or get_language() or FALLBACK).lower().replace("_", "-")
    known = set(languages())
    if code in known:
        return code
    base = code.partition("-")[0]
    return base if base in known else FALLBACK


def divisions(language: str = "") -> list[tuple[str, str]]:
    """Every division as ``(code, name)``, in code order, in the reader's language."""
    reading = _reading(language)
    return [
        (code, entry["names"].get(reading) or entry["names"].get(FALLBACK, code))
        for code, entry in classification()["divisions"].items()
    ]


def name_for(code: str, language: str = "") -> str:
    """What a division is called, or empty where nothing here has that code.

    Empty rather than the code, because a code that has gone from a later revision belongs
    to a person's industry that keeps its name — it is not something to print at them.
    """
    entry = classification()["divisions"].get(code)
    if entry is None:
        return ""
    reading = _reading(language)
    return entry["names"].get(reading) or entry["names"].get(FALLBACK, "")


def section_of(code: str) -> str:
    entry = classification()["divisions"].get(code)
    return entry["section"] if entry else ""


@lru_cache(maxsize=32)
def _by_name(reading: str) -> dict[str, str]:
    """``{folded name: code}`` for one language, so a typed name can find its division."""
    return {
        (entry["names"].get(reading) or entry["names"].get(FALLBACK, "")).casefold(): code
        for code, entry in classification()["divisions"].items()
    }


def code_for(name: str, language: str = "") -> str:
    """The division code a typed name matches, in the reader's language or in English.

    Both, and in that order, because somebody reading Postulo in French may paste an English
    division name out of a form they were sent. A name that matches nothing has no code,
    which is the ordinary case and not a failure.
    """
    folded = (name or "").strip().casefold()
    if not folded:
        return ""
    reading = _reading(language)
    return _by_name(reading).get(folded) or _by_name(FALLBACK).get(folded, "")


def suggestions(exclude=(), language: str = "") -> list[str]:
    """What the input offers, minus anything the person already has.

    Postulo's own short names first: a datalist shows its options in order until somebody
    types, and *Software* being visible before *Manufacture of coke and refined petroleum
    products* is the difference between a helpful list and a statistical yearbook.
    """
    taken = {str(name).casefold() for name in exclude}
    offered = [str(name) for name in STARTER_INDUSTRIES]
    offered += [name for _code, name in divisions(language)]
    seen: set[str] = set()
    kept: list[str] = []
    for name in offered:
        folded = name.casefold()
        if folded in taken or folded in seen:
            continue
        seen.add(folded)
        kept.append(name)
    return kept
