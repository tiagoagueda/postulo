"""Which identifiers a person sees, and in what order (#672).

The same design as the main navigation's (`postulo.core.navigation`, #299), for the
registry's schemes instead of its items: two lists on the profile, **what was decided and
never what was offered**.

* ``hidden_identifiers`` holds the scheme keys the person switched *off*, so a scheme a
  plugin adds later is shown for everybody who has not decided about it.
* ``identifier_order`` holds the keys the person has *placed*, in their order. Schemes in
  neither list are drawn after the placed ones, in registration order. The default order
  is stored as nothing, so a later release that changes it reaches everybody who never
  chose.

One arrangement serves people and companies (ISNI and Wikidata identify both): a scheme
that does not identify the subject being drawn simply is not there to be placed.

**Switched off is a way of drawing, never of keeping.** A hidden scheme is not in the
company page's summary and nothing else changes: it stays in the edit forms, the export,
the API and any CV the person chose to print it on.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Case, IntegerField, Value, When

from . import identifiers

#: The two directions a scheme moves in, one place at a time.
DIRECTIONS = ("up", "down")


def default_order() -> list[str]:
    """Every scheme the registry knows, in registration order."""
    return list(identifiers.registry())


def known_keys(value) -> list[str]:
    """A stored list, as far as it can be believed: known schemes, each once, in order.

    What is stored arrives from a form and from an archive, and neither is obliged to hold
    a list of strings. A scheme this instance does not have is passed over.
    """
    if not isinstance(value, list | tuple):
        return []
    registry = identifiers.registry()
    keys: list[str] = []
    for key in value:
        if isinstance(key, str) and key in registry and key not in keys:
            keys.append(key)
    return keys


def complete(order) -> list[str]:
    """Every scheme, ``order`` first and then the ones it does not place."""
    placed = known_keys(order)
    return placed + [key for key in default_order() if key not in placed]


def order_of(profile) -> list[str]:
    return complete(getattr(profile, "identifier_order", None))


def hidden_keys(profile) -> set[str]:
    return set(known_keys(getattr(profile, "hidden_identifiers", None)))


def move(order, key: str, direction: str) -> list[str]:
    """One scheme, one place up or down. Past either end is a no-op, not a wrap."""
    keys = complete(order)
    if key not in keys or direction not in DIRECTIONS:
        return keys
    index = keys.index(key)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(keys):
        keys[index], keys[target] = keys[target], keys[index]
    return keys


def can_move(order, key: str, direction: str) -> bool:
    return move(order, key, direction) != complete(order)


def to_store(order) -> list[str]:
    """What the profile keeps: nothing, when ``order`` is the default order."""
    keys = complete(order)
    return [] if keys == default_order() else keys


def _ranks(profile) -> dict[str, int]:
    return {key: place for place, key in enumerate(order_of(profile))}


def arranged(rows: Iterable, profile) -> list:
    """``rows`` in the person's order of schemes.

    A stable sort, so rows of one scheme (several *Other*) keep the order they came in --
    by value, as the models have always listed them. A row under a scheme the registry no
    longer has goes last.
    """
    ranks = _ranks(profile)
    rows = list(rows)
    return sorted(rows, key=lambda row: ranks.get(row.scheme, len(ranks)))


def shown_and_hidden(rows: Iterable, profile) -> tuple[list, list]:
    """``rows`` arranged, split into those to draw in a summary and those switched off."""
    hidden = hidden_keys(profile)
    ordered = arranged(rows, profile)
    return (
        [row for row in ordered if row.scheme not in hidden],
        [row for row in ordered if row.scheme in hidden],
    )


def order_queryset(queryset, profile):
    """The same arrangement in the database, for a queryset that is paged or made a formset."""
    ranks = _ranks(profile)
    rank = Case(
        *(When(scheme=key, then=Value(place)) for key, place in ranks.items()),
        default=Value(len(ranks)),
        output_field=IntegerField(),
    )
    return queryset.annotate(_scheme_rank=rank).order_by("_scheme_rank", "scheme", "value")
