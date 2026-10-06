"""Every administrators-only route, asked for by somebody who is not one (#422).

`tests/security/test_isolation_sweep.py` excuses the routes that name an account or an
invitation, because nobody owns those: they are for administrators. That is only a reason if
something proves the gate, and the tests it used to cite asked as a member with a GET, or
not at all. This walks every `server:` route and every `accounts:invite_*` route but the
one a stranger is meant to open, and for each one signs in an ordinary member and sends GET
and an empty POST: 403, and nothing changed. An anonymous visitor is sent to sign in.

Take `StaffRequiredMixin` off one of these views and the member's POST reaches it, which
for `person_admin` is a privilege escalation.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, get_resolver, reverse

from postulo.accounts.models import Invite

pytestmark = pytest.mark.django_db

#: A token rather than a staff gate: whoever holds the link may open it.
OPEN_TO_STRANGERS = {"accounts:invite_accept"}

#: The backups page hides itself from everybody but an administrator with a 404 rather than
#: a 403, and names a file in the address rather than a record, so it has its own sweep:
#: `tests/security/test_backups_page.py`.
OWN_SWEEP = ("server:backup",)


def staff_only_routes() -> list[str]:
    """Every name under `server:` and `accounts:invite_*`, as the resolver lists them."""
    found: set[str] = set()

    def walk(resolver, namespace=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                inner = pattern.namespace
                walk(pattern, f"{namespace}{inner}:" if inner else namespace)
            elif pattern.name:
                found.add(f"{namespace}{pattern.name}")

    walk(get_resolver())
    return sorted(
        name
        for name in found
        if (name.startswith("server:") and not name.startswith(OWN_SWEEP))
        or (name.startswith("accounts:invite_") and name not in OPEN_TO_STRANGERS)
    )


ROUTES = staff_only_routes()


@pytest.fixture
def administrator(db):
    return get_user_model().objects.create_user(
        email="boss@example.org", password="not-a-real-password", is_staff=True
    )


@pytest.fixture
def invitation(administrator):
    return Invite.objects.create(created_by=administrator)


def address(name: str, victim, invitation) -> str:
    """The route's address, with whatever it captures filled in with a real record."""
    pk = invitation.pk if name.startswith("accounts:") else victim.pk
    for kwargs in ({}, {"pk": pk}):
        try:
            return reverse(name, kwargs=kwargs)
        except NoReverseMatch:
            continue
    raise AssertionError(f"cannot build an address for {name}")


def snapshot():
    people = get_user_model().objects.order_by("pk")
    return (
        [(p.pk, p.username, p.is_staff, p.is_active, p.is_superuser) for p in people],
        sorted(Invite.objects.values_list("pk", flat=True)),
    )


def test_the_walk_found_the_routes_it_is_about():
    """So that a renamed namespace cannot turn the sweep into one over nothing."""
    for name in (
        "server:person_admin",
        "server:person_active",
        "server:person_delete",
        "server:person_username",
        "accounts:invite_revoke",
    ):
        assert name in ROUTES, f"{name} is not in what the sweep walks: {ROUTES}"


@pytest.mark.parametrize("name", ROUTES)
def test_a_member_is_refused_and_nothing_changes(
    client, user, other_user, administrator, invitation, name
):
    # Another ordinary account is the one asked about, so that nothing would block the
    # change but the gate: it is not the last administrator and not the caller.
    url = address(name, other_user, invitation)
    before = snapshot()
    client.force_login(user)

    for method in (client.get, client.post):
        response = method(url)
        assert response.status_code == 403, (
            f"{name} {url}: {method.__name__.upper()} answered {response.status_code} "
            "to a member who is not an administrator"
        )

    assert snapshot() == before


@pytest.mark.parametrize("name", ROUTES)
def test_a_visitor_is_sent_to_sign_in(client, other_user, invitation, name):
    url = address(name, other_user, invitation)
    for method in (client.get, client.post):
        response = method(url)
        assert response.status_code == 302, f"{name}: {method.__name__.upper()}"
        assert "login" in response["Location"]


def test_a_member_cannot_make_themselves_an_administrator(client, user, administrator):
    """The consequence the gate is there for, asked for directly."""
    client.force_login(user)
    client.post(reverse("server:person_admin", kwargs={"pk": user.pk}))
    client.post(reverse("server:person_username", kwargs={"pk": user.pk}), {"username": "root"})
    user.refresh_from_db()
    assert not user.is_staff
    assert user.username != "root"
