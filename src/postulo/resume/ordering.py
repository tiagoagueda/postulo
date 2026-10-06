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

from django.utils import timezone

#: The dated sections, and the date that decided their order before the person did: what
#: a new entry is placed by, so that adding your latest job still puts it first.
DATE_FIELDS: dict[str, str] = {
    "Experience": "start_date",
    "Education": "end_date",
    "Certification": "issued_on",
    "Honour": "awarded_on",
    "Membership": "start_date",
    "Course": "end_date",
}

DIRECTIONS = ("up", "down")

#: How many rows one statement writes when a file adds a section's worth at once.
BATCH = 200


def siblings(item) -> list:
    """Every entry in the same section of the same person's record, as the page draws them.

    The default answer, and the right one for a career section. It is not the only shape a
    row of arrows has: a CV's entries are one list per CV rather than one per model, so the
    caller there hands the neighbours in itself through ``among`` (#235).
    """
    return list(type(item).objects.for_user(item.owner).order_by("order", "pk"))


def renumber(items: list) -> None:
    """Dense numbers in the given order, writing only the rows whose number changes."""
    for position, entry in enumerate(items):
        if entry.order != position:
            entry.order = position
            entry.save(update_fields=["order", "updated_at"])


def move(item, direction: str, *, among=None) -> bool:
    """Swap the entry with its neighbour above or below. ``False`` when there is none.

    ``among`` is the list the page drew, for a row of arrows whose neighbours are not simply
    every entry of that model: on a CV it is that CV's entries, in the order shown.
    """
    if direction not in DIRECTIONS:
        return False
    items = list(among) if among is not None else siblings(item)
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


def place_many(model, owner, items: list) -> None:
    """Add unsaved entries, in the order given, each where it belongs in the owner's section.

    What an importer calls (#618): into a section with nothing in it, in the order the file
    gives them, which is the order their owner put them in somewhere else; into a section
    that has entries, as an entry typed by hand would land (#203) -- by its date where the
    section has one, last where it has not, and the person's to move from there. Dense
    numbers afterwards, as `renumber` leaves them.

    Written in two statements rather than one for each entry: what is new, and the places
    of what was there and has moved. A file may hold a few hundred entries of a kind, and a
    request that asked the database about each would hold it for as long as somebody
    else's file cared to make it. ``bulk_create`` calls no ``save``, so whatever a save
    works out is for the caller to do before.
    """
    standing = list(model.objects.for_user(owner).order_by("order", "pk"))
    dated = DATE_FIELDS.get(model.__name__) if standing else None
    sequence = list(standing)
    for item in items:
        place = len(sequence)
        if dated is not None:
            mine = newest_first_key(item, dated)
            for index, other in enumerate(sequence):
                if newest_first_key(other, dated) < mine:
                    place = index
                    break
        sequence.insert(place, item)

    now = timezone.now()
    added, moved = [], []
    for place, item in enumerate(sequence):
        if item.pk is None:
            item.order = place
            added.append(item)
        elif item.order != place:
            item.order, item.updated_at = place, now
            moved.append(item)
    model.objects.bulk_create(added, batch_size=BATCH)
    model.objects.bulk_update(moved, ["order", "updated_at"], batch_size=BATCH)
