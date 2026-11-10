"""Two questions every page asked the database, and what answers them now (#1115).

*Has this instance any account?* opens sign-up on a fresh installation, and was asked while
drawing every page: `SELECT 1 FROM accounts_user LIMIT 1`. *Is this person in a group?*
decides whether the account menu offers *Your groups*, and was a second `EXISTS` on every
full page of somebody signed in.

The first is not asked for somebody signed in, who is an account: nothing is remembered, so
there is nothing to go stale, in this process or in another worker's. A visitor's page asks,
as does everything that lets a sign-up in. The second comes with the profile the request
reads anyway.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from postulo.core import site

pytestmark = pytest.mark.django_db

User = get_user_model()


def asked_for_accounts(captured) -> list[str]:
    asked = 'FROM "accounts_user" LIMIT 1'
    return [q["sql"] for q in captured if q["sql"].rstrip().endswith(asked)]


def asked_for_groups(captured) -> list[str]:
    return [
        q["sql"]
        for q in captured
        if q["sql"].lstrip().startswith('SELECT 1 AS "a" FROM "auth_group" INNER JOIN')
    ]


@pytest.fixture
def closed(settings):
    """Sign-up closed, which is the usual state and the one where emptiness is asked about."""
    settings.POSTULO_REGISTRATION_OPEN = False


# ------------------------------------------------------------------------------ the page


def test_a_page_of_somebody_signed_in_asks_neither(client, user, closed):
    """The dashboard made 20 queries, two of them these: whether the instance has accounts,
    asked of a person who is one, and whether they are in a group (#1115)."""
    client.force_login(user)
    url = reverse("core:home")

    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200

    assert asked_for_accounts(captured) == []
    assert asked_for_groups(captured) == []


@pytest.mark.parametrize("member", [True, False], ids=["in a group", "in none"])
def test_the_account_menu_offers_your_groups_only_to_somebody_in_one(client, user, member):
    """What the question was for is unchanged: the menu's row, to a member and to nobody else."""
    if member:
        user.groups.add(Group.objects.create(name="Careers team"))
    client.force_login(user)

    with CaptureQueriesContext(connection) as captured:
        html = client.get(reverse("core:home")).content.decode()

    assert (f'<a href="{reverse("accounts:groups")}" class="menu-item">' in html) is member
    assert asked_for_groups(captured) == []


def test_joining_a_group_shows_on_the_next_page(client, user):
    """Read with the request's profile, so it is never older than the page it is drawn on."""
    client.force_login(user)
    link = f'href="{reverse("accounts:groups")}"'
    assert link not in client.get(reverse("core:home")).content.decode()

    group = Group.objects.create(name="Careers team")
    user.groups.add(group)
    assert link in client.get(reverse("core:home")).content.decode()

    user.groups.remove(group)
    assert link not in client.get(reverse("core:home")).content.decode()


def test_a_page_drawn_without_the_middleware_still_knows(user, rf):
    """A context built for a request the middleware never saw asks the database, as before."""
    from postulo.core.context_processors import ui

    request = rf.get("/")
    request.user = User.objects.get(pk=user.pk)
    assert not ui(request)["has_groups"]

    request.user.groups.add(Group.objects.create(name="Careers team"))
    assert ui(request)["has_groups"]


def test_a_visitor_is_in_no_group_and_nothing_is_asked(client, user):
    with CaptureQueriesContext(connection) as captured:
        assert client.get(reverse("core:home")).status_code == 200

    assert asked_for_groups(captured) == []


# ------------------------------------------------------------------- who is still asked


def test_a_visitor_to_an_empty_instance_is_offered_sign_up(client, closed):
    """The reason the question exists: somebody has to become the first account, and sign-up
    being closed must not shut the person who installed Postulo out of it."""
    assert not User.objects.exists()

    with CaptureQueriesContext(connection) as captured:
        page = client.get(reverse("core:home")).content.decode()

    assert asked_for_accounts(captured), "a visitor's page asks the database"
    assert reverse("account_signup") in page


def test_a_visitor_is_not_offered_sign_up_once_there_is_an_account(client, user, closed):
    page = client.get(reverse("core:home")).content.decode()

    assert reverse("account_signup") not in page


def test_an_instance_emptied_under_a_running_process_opens_sign_up_again(client, user, closed):
    """Nothing is remembered between requests, so an instance whose last account was deleted,
    by this worker, another one or a shell, opens sign-up on the very next page."""
    assert reverse("account_signup") not in client.get(reverse("core:home")).content.decode()

    User.objects.all().delete()

    assert site.signup_open_now()
    assert reverse("account_signup") in client.get(reverse("core:home")).content.decode()


def test_somebody_signed_in_sees_what_the_setting_says(client, user, settings):
    """For them the page reads the setting alone, which costs no query."""
    from postulo.core.context_processors import ui

    client.force_login(user)
    request = client.get(reverse("core:home")).wsgi_request

    settings.POSTULO_REGISTRATION_OPEN = False
    assert ui(request)["registration_open"] is False
    settings.POSTULO_REGISTRATION_OPEN = True
    assert ui(request)["registration_open"] is True
