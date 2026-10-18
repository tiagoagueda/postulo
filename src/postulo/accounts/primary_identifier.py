"""The primary identifier of a person (#922): one at most, and one whenever there are any.

Beside the registry's view (`accounts.identifiers`) and not in it, because the model imports
that module and these functions import the model.
"""

from __future__ import annotations

from .models import PersonIdentifier


def set_primary(profile, row) -> None:
    """Make ``row`` the profile's primary identifier, and the only one (#922).

    Cleared first and set second: the partial unique index means two of them cannot exist
    even for the length of a statement. Written with ``update`` so that nothing but the flag
    is touched.
    """
    PersonIdentifier.objects.filter(profile=profile, is_primary=True).exclude(pk=row.pk).update(
        is_primary=False
    )
    PersonIdentifier.objects.filter(pk=row.pk).update(is_primary=True)
    row.is_primary = True


def ensure_one_primary(profile):
    """The profile keeps the primary identifier it has; failing that, its first becomes it.

    What saving the rows does when nobody said which is the primary, and what taking the
    primary off does, so that the two ways of losing it cannot disagree about who inherits
    it. Nothing when the profile holds no identifier.
    """
    rows = PersonIdentifier.objects.filter(profile=profile)
    wanted = rows.filter(is_primary=True).first() or rows.first()
    if wanted is not None and not wanted.is_primary:
        set_primary(profile, wanted)
    return wanted
