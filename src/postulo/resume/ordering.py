"""The order of the entries in each career section, owned by the person (#203).

Every entry carries an ``order`` number, and until #203 the arrows on the overview nudged
that number by one: pressing *up* on an entry at 0 left it at 0, pressing *down* once put
it behind every other entry at 0 wherever it had been, and two entries at 3 and 5 needed
two presses to pass each other, the first of them invisible. Experience, education and
certifications were sorted by date first besides, so there the arrows changed nothing a
person could see unless two entries shared a date. The number box on every entry's form
was the one control that reliably did anything -- and it asked for an integer that meant
"lower first".

The model the dashboard's widgets already follow (#124): the arrows move an entry past
its neighbour, *up* swapping with the one drawn above it and *down* with the one below,
and the section is renumbered densely afterwards so the numbers are exactly what the
page shows. The dated sections take the number too, seeded from their dates once by the
migration, so the person owns the order everywhere; a new dated entry lands where its
date would have put it and is the person's to move from there. The number box is hidden
unless *Settings > Appearance* says otherwise, which is an accessibility choice for
somebody who cannot use the arrows or would rather type.
"""

from __future__ import annotations

import datetime as dt

#: The dated sections, and the date that decided their order before the person did: what
#: a new entry is placed by, so that adding your latest job still puts it first.
DATE_FIELDS: dict[str, str] = {
    "Experience": "start_date",
    "Education": "end_date",
    "Certification": "issued_on",
}

DIRECTIONS = ("up", "down")


def siblings(item) -> list:
    """Every entry in the same section of the same person's record, as the page draws them."""
    return list(type(item).objects.for_user(item.owner).order_by("order", "pk"))


def renumber(items: list) -> None:
    """Dense numbers in the given order, writing only the rows whose number changes."""
    for position, entry in enumerate(items):
        if entry.order != position:
            entry.order = position
            entry.save(update_fields=["order", "updated_at"])


def move(item, direction: str) -> bool:
    """Swap the entry with its neighbour above or below. ``False`` when there is none."""
    if direction not in DIRECTIONS:
        return False
    items = siblings(item)
    index = next((i for i, entry in enumerate(items) if entry.pk == item.pk), None)
    if index is None:
        return False
    other = index - 1 if direction == "up" else index + 1
    if other < 0 or other >= len(items):
        return False
    items[index], items[other] = items[other], items[index]
    renumber(items)
    return True


def newest_first_key(item, date_field: str):
    """Ongoing -- no date -- before everything, then the newest date first."""
    date = getattr(item, date_field, None)
    return (date is None, date or dt.date.min)


def place_new(item) -> None:
    """Where a just-added entry goes: by its date in a dated section, last in the rest.

    Adding your latest job should still put it first, as the date ordering used to; in a
    section with no date to read, a new entry goes at the end, which is where the page
    said nothing and a person looks last. Either way the arrows own it from here.
    """
    others = [entry for entry in siblings(item) if entry.pk != item.pk]
    date_field = DATE_FIELDS.get(type(item).__name__)
    position = len(others)
    if date_field is not None:
        mine = newest_first_key(item, date_field)
        for index, entry in enumerate(others):
            if newest_first_key(entry, date_field) < mine:
                position = index
                break
    others.insert(position, item)
    renumber(others)
