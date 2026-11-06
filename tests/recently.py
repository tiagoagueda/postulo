"""A session that counts as signed in a moment ago, as allauth's reauthentication asks (#740).

`client.force_login` opens a session with no record of *how* the person signed in, so a view
behind `reauthentication_required` sends it to the password page. This writes the record a
typed password leaves, good for allauth's five minutes.
"""

from __future__ import annotations

import time


def write_records(session, user, records: list[dict]) -> None:
    """Put allauth's record of how ``user`` signed in into ``session``, as allauth itself does.

    Since allauth 65.21 the records belong to one account: beside them it keeps whose they are,
    and reading them for anyone else (or for nobody) gives nothing. So a test that writes the
    records by hand writes the owner too, or every check that reads them sees no sign-in at all.
    """
    from allauth.account.internal.flows import login
    from allauth.account.internal.userkit import user_id_to_str

    session[login.AUTHENTICATION_METHODS_SESSION_KEY] = records
    session[login._AUTHENTICATION_METHODS_USER_SESSION_KEY] = user_id_to_str(user)


def signed_in_recently(client, user) -> None:
    client.force_login(user, backend="allauth.account.auth_backends.AuthenticationBackend")
    session = client.session
    write_records(
        session,
        user,
        [{"method": "password", "at": time.time(), "email": user.email, "reauthenticated": True}],
    )
    session.save()


def reauthenticate(client, password: str) -> None:
    """Type the password again on allauth's page, as it asks since 65.20 (#987).

    A form sign-in is no longer taken for that proof; the record it leaves is not marked
    `reauthenticated`, so a view behind `reauthentication_required` sends the person here.
    """
    from django.urls import reverse

    response = client.post(reverse("account_reauthenticate"), {"password": password})
    assert response.status_code == 302, response.content[:300]
