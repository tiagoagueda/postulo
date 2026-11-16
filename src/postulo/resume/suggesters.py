"""What the suggestion boxes of the career forms offer (#1251).

Registered with `core.suggest`, which names the contract. Each reads what this person
recorded and nothing else: the account is the only thing asked.
"""

from __future__ import annotations

from postulo.core import suggest

from .models import Education

#: The most a box reads before it narrows to what was typed.
AT_MOST = 300


def _own(user, field: str) -> list[str]:
    """The distinct values of an education entry's ``field``, most recent first."""
    seen: dict[str, None] = {}
    for value in (
        Education.objects.for_user(user)
        .exclude(**{field: ""})
        .order_by("-updated_at")
        .values_list(field, flat=True)[: AT_MOST * 2]
    ):
        seen.setdefault(value, None)
    return list(seen)[:AT_MOST]


suggest.register("qualification", lambda user, typed: _own(user, "qualification"))
suggest.register("field-of-study", lambda user, typed: _own(user, "field_of_study"))
