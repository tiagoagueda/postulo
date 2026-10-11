"""A session that counts as signed in a moment ago, as allauth's reauthentication asks (#740).

`client.force_login` opens a session with no record of *how* the person signed in, so a view
behind `reauthentication_required` sends it to the password page. This writes the record a
typed password leaves, good for allauth's five minutes.
"""

from __future__ import annotations

import time


def signed_in_recently(client, user) -> None:
    from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY

    client.force_login(user, backend="allauth.account.auth_backends.AuthenticationBackend")
    session = client.session
    session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {"method": "password", "at": time.time(), "email": user.email}
    ]
    session.save()
