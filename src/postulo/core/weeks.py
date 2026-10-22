"""Which day a week starts on, for whoever the work is being done for (#859).

The locale says it (`FIRST_DAY_OF_WEEK`), and a person may say otherwise: somebody reading
English in a country whose weeks start on a Sunday, or the other way round. The choice is
held for the rest of the request by `core.preferences.apply`, beside the time zone and the
language it sits with, and read by the one function that works a week out -- so the week
view, the month grid and the reports all agree, and a task run for nobody in particular falls
back to the locale's.

Numbered as Django numbers it: 0 is Sunday, 1 is Monday, up to 6, Saturday.
"""

from __future__ import annotations

from contextvars import ContextVar

from django.utils import formats

_chosen: ContextVar[int | None] = ContextVar("first_weekday", default=None)


def choose(day: int | None) -> None:
    """Hold a person's choice (``None`` for the language's own) for what follows."""
    _chosen.set(day if isinstance(day, int) and 0 <= day <= 6 else None)


def first_day() -> int:
    """0 (Sunday) to 6: the person's choice, else the locale's, else Monday."""
    chosen = _chosen.get()
    if chosen is not None:
        return chosen
    try:
        return int(formats.get_format("FIRST_DAY_OF_WEEK")) % 7
    except (TypeError, ValueError):
        return 1


#: The languages whose readers number their weeks (#933): a Swede books "vecka 42", a German
#: "KW 42". A date picker in one of them draws the ISO 8601 number beside each week; nobody
#: else is shown a column they never use. By the language's primary subtag, because the habit
#: is the language's wherever it is read. Kept here rather than in ``config/formats``: Django
#: reads only the format names it knows from those modules, and an invented one is returned
#: verbatim (see that package's docstring).
NUMBERED_WEEKS = frozenset({"da", "de", "fi", "fo", "is", "nb", "nn", "no", "sv"})


def numbers_weeks(language: str) -> bool:
    """Whether a reader of ``language`` (a BCP 47 tag) numbers the weeks of the year."""
    return (language or "").split("-")[0].lower() in NUMBERED_WEEKS
