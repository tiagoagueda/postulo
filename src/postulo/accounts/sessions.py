"""Sessions that belong to a person, found by who they are signed in as (#750).

Django keeps a session as one row of encoded data with no column for the account, so
"all of somebody's sessions" is a question the table cannot answer by itself. It matters
for two reasons: a held import (a parsed CV, a spreadsheet, a vCard) lives in the session row
and outlives the account it was for, and deleting an account should end the sessions it was
signed in on.
"""

from __future__ import annotations

from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone


def keys_of(user) -> list[str]:
    """The keys of the live sessions signed in as ``user``."""
    store = SessionStore()
    wanted = str(user.pk)
    found = []
    for key, data in Session.objects.filter(expire_date__gt=timezone.now()).values_list(
        "session_key", "session_data"
    ):
        # `decode` answers an empty dict for a row it cannot read, which is not this person's.
        decoded = store.decode(data)
        if str(decoded.get("_auth_user_id", "")) == wanted:
            found.append(key)
    return found


def end_all_of(user) -> int:
    """Delete every session signed in as ``user``, held imports and all. How many went."""
    keys = keys_of(user)
    return Session.objects.filter(session_key__in=keys).delete()[0] if keys else 0


def clear_expired() -> int:
    """Delete the sessions past their expiry, and what they held (#751). How many went."""
    return Session.objects.filter(expire_date__lt=timezone.now()).delete()[0]
