"""What the suggestion boxes of core's own forms offer (#1251).

Registered with `core.suggest`. The tags a person has made are theirs; nothing of anybody
else's is read.
"""

from __future__ import annotations

from . import suggest
from .models import Tag


def tags(user, typed: str):
    """The person's tags by name; the box offers the ones not yet typed in its own list."""
    return Tag.objects.for_user(user).order_by("name").values_list("name", flat=True)[:300]


suggest.register("tag", tags)
