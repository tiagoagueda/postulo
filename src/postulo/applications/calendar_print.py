"""The Calendar, printed: a PDF of the month, week, day or agenda on screen, set by a theme (#342).

The Calendar could not be printed properly. Printing was the browser's dialog over the page:
the month's two entries a cell and "and N more", a *New reminder* button in every cell, the
legend and the navigation, all on paper, and nothing set the paper on its side. The
templates plugin was asked for "a template for printing calendar views" and there was no
kind for one to set.

**The view on screen, asked once more.** The view, the day, the kinds shown, an agenda's
last day and the search are read from the address by `agenda.asked_for`, which is what the
page reads them with, so the print is the period the person was looking at.

**Every entry of the period.** The month's two-a-cell limit is the screen's; on paper a day
holds what it has. Nothing that is a control prints -- no buttons, no legend, no navigation.

**One set of plain values** (#330). The entries are `agenda.Item`s, the values a calendar
plugin reads, through `agenda.printed_items`: the page, the feed, a plugin and the print
all read the one place that decides what a deadline is and which ones are over.

**One theme kind for the four views** (`themes.Kind.CALENDAR`), handed the same shape for
each, remembered per person as a report's and a list's are (#340), in the language and the
direction of the document. A month is a grid, and so with nothing chosen it is drawn on the
person's paper turned on its side, where the theme prints on that (#344), as a list is.

**A download, never filed**, as a list is (#341): a calendar printed for one's own desk is
not something an employer received (#236).
"""

from __future__ import annotations

import datetime as dt

from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from . import agenda

#: The views drawn on their side when nobody chose a paper: a month is seven columns wide,
#: and the others are lists of days.
ON_ITS_SIDE = frozenset({"month"})


def offered_languages() -> list[tuple[str, str]]:
    """The languages a calendar may be printed in: the instance's own, as for a list."""
    from postulo.core import list_export

    return list_export.offered_languages()


def language_for(user, asked: str = "") -> str:
    """The language it is printed in: the one asked for where it is offered, else the one a
    document with no language of its own is written in, as for a list (#341)."""
    from postulo.core import list_export

    return list_export.language_for(user, asked)


def theme_for(user, asked: str = "") -> str:
    """The theme it is set in: the one asked for where it sets calendars, else the one this
    person last chose for a calendar (`themes.remembered`), else `plain`."""
    from postulo.documents import themes

    theme = themes.find(asked) if asked else None
    if theme is not None and theme.sets(themes.Kind.CALENDAR):
        return theme.name
    return themes.remembered(getattr(user, "profile", None), themes.Kind.CALENDAR)


def paper_for(theme: str, user, view: str, asked: str = ""):
    """The paper it is drawn on. Raises `CannotRender` for one asked for that the theme does
    not print on.

    Nothing asked is the person's default; for a month, that paper turned on its side, where
    the theme prints on that, because a month is seven columns across.
    """
    from postulo.documents import paper, paper_choice, themes

    if asked:
        return paper_choice.for_document(theme, themes.Kind.CALENDAR, user, asked)
    if view in ON_ITS_SIDE:
        wished = paper_choice.personal_default(user)
        turned = paper.Paper(wished.size, paper.Orientation.LANDSCAPE)
        if turned in paper.offered(theme, themes.Kind.CALENDAR):
            return turned
    return paper_choice.for_document(theme, themes.Kind.CALENDAR, user)


# --------------------------------------------------------------------- the period itself


def _entry(item: agenda.Item) -> dict:
    return {
        "kind": item.kind,
        "said": item.said,
        "title": item.title,
        "detail": item.detail,
        "all_day": item.all_day,
        "starts_at": item.starts_at,
        "ends_at": item.ends_at,
        "over": item.over,
        "state": item.state,
    }


def _matches(item: agenda.Item, needle: str) -> bool:
    # The page's own rule for its search (`agenda.narrow`): the title and the second line.
    return needle in f"{item.title} {item.detail}".casefold()


def as_context(user, asked: agenda.Asked, *, today: dt.date | None = None, period=None) -> dict:
    """The period as a theme's template is handed it: plain values and nothing else.

    Strings, numbers, booleans, dates and moments, in lists and mappings -- never a model or
    an `Item`, so the names below are the whole contract, written in the wiki's *Writing a
    plugin*. The same keys for every view: ``weeks`` is the month's grid (and the week's one
    row), empty for a day and an agenda; ``days`` is every day of the period in order, each
    with its entries, the empty ones too. Built in the language and the zone in force.

    ``period`` is `agenda.span`'s answer where the caller worked it out before putting the
    document's language in force: the week starts on the day the page's starts on.
    """
    today = today or timezone.localdate()
    on, start, end, until = period or agenda.span(asked.view, asked.on, asked.until)
    items = agenda.printed_items(user, start, end, kinds=asked.kinds)
    needle = asked.search.strip().casefold()
    if needle:
        items = tuple(item for item in items if _matches(item, needle))
    on_day: dict[dt.date, list[dict]] = {}
    for item in items:
        on_day.setdefault(item.day, []).append(_entry(item))
    days = [
        {
            "date": date,
            "outside": asked.view == "month" and date.month != on.month,
            "today": date == today,
            "entries": on_day.get(date, []),
        }
        for date in agenda.days_between(start, end)
    ]
    grid = asked.view in {"month", "week"}
    return {
        "view": asked.view,
        "view_label": str(agenda.VIEW_LABELS[asked.view]),
        "title": str(agenda.period_title(asked.view, on, start, until)),
        "person": user.display_name if hasattr(user, "display_name") else str(user),
        "produced_at": timezone.localtime(),
        "start": start,
        "last": end - dt.timedelta(days=1),
        "kinds": [str(label) for kind, label, _tone in agenda.KINDS if kind in asked.kinds],
        "narrowed": asked.kinds != agenda.ALL_KINDS,
        "search": asked.search.strip(),
        "weekdays": ([formats.date_format(day["date"], "l") for day in days[:7]] if grid else []),
        "weeks": [days[index : index + 7] for index in range(0, len(days), 7)] if grid else [],
        "days": days,
        "count": len(items),
    }


def title_of(context: dict) -> str:
    """What the document is called, in the language it is written in."""
    return str(_("Calendar · %(period)s") % {"period": context["title"]})


def filename(asked: agenda.Asked) -> str:
    """A name that says which period it is without being opened."""
    if asked.view == "month":
        return f"postulo-calendar-{asked.on:%Y-%m}.pdf"
    _on, start, _end, _until = agenda.span(asked.view, asked.on, asked.until)
    return f"postulo-calendar-{asked.view}-{start:%Y-%m-%d}.pdf"


def draw(request, *, theme: str = "", language: str = "", paper: str = ""):
    """The calendar the request's address asks for, set as a document: ``(html, theme,
    language)``, the last two being what it was drawn in. Raises `CannotRender` for a paper
    the theme does not print on, before anything is read."""
    from django.template.loader import render_to_string

    from postulo.core import languages
    from postulo.documents import paper as papers
    from postulo.documents import theme_assets, themes

    user = request.user
    asked = agenda.asked_for(request.GET)
    theme = theme_for(user, theme)
    language = language_for(user, language)
    chosen = paper_for(theme, user, asked.view, paper)
    # The period as the page drew it, in the reader's language: which weekday a week starts
    # on is the locale's, and the print is of the week on screen.
    period = agenda.span(asked.view, asked.on, asked.until)
    # A file handed to a renderer in this process, never a page a browser is served, so no
    # policy reads it and its styles need no nonce -- as for a list's (#341).
    nonce = None
    with languages.override(language):
        context = as_context(user, asked, period=period)
        html = render_to_string(
            themes.template_for(theme, themes.Kind.CALENDAR),
            {
                **context,
                "document_language": language,
                "document_direction": languages.direction(language),
                "document_title": title_of(context),
                "csp_nonce": nonce,
                "paper": chosen,
                theme_assets.CONTEXT_KEY: theme_assets.Inliner(themes.find(theme)),
            },
        )
    return papers.with_paper(html, chosen, nonce=nonce), theme, language
