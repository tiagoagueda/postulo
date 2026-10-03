"""Values read out of the address or a form, which may be anything the sender typed."""

from __future__ import annotations


def as_pk(value) -> int | None:
    """A primary key out of something typed into a URL, or nothing at all (#409).

    A lookup on `pk=` raises `ValueError` for a value that is not a number, and a
    parameter that only preselects a field must not cost the person the whole page.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
