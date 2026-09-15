"""Applications and Board are one page in two shapes, and the switch keeps your filters (#102).

Two views answering "which of my applications am I looking at?" were two places: two
addresses, two entries in the navigation, and a bare link between them that threw the
filters away. Now: one entry, one address, a switch on the page that posts the shape so
it is remembered and comes back with every filter, and the old address redirecting with
whatever it was given. The board still shows only what is still live, and says so when a
filter matches something settled rather than showing an empty board.
"""

from __future__ import annotations

import importlib

import pytest
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.applications.services import change_status
from postulo.core import navigation, tables
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

LIST = "applications:list"
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def two(user):
    aperture = Company.objects.create(owner=user, name="Aperture Science")
    black_mesa = Company.objects.create(owner=user, name="Black Mesa")
    first = Application.objects.create(
        owner=user,
        posting=JobPosting.objects.create(owner=user, company=aperture, title="Test Engineer"),
        status=Status.APPLIED,
    )
    second = Application.objects.create(
        owner=user,
        posting=JobPosting.objects.create(owner=user, company=black_mesa, title="Physicist"),
        status=Status.APPLIED,
    )
    return first, second


def shape_of(response) -> str:
    return response.context["shape"]


def stored(user) -> dict:
    """What is remembered for the applications table, read fresh: the fixture's profile is
    cached from before the request that wrote it."""
    user.profile.refresh_from_db()
    return tables.settings_for(user, "applications")


def cards(response) -> list[int]:
    return [a.pk for column in response.context["columns"] for a in column["applications"]]


# ------------------------------------------------------------------- the switch


def test_the_table_is_the_shape_until_somebody_chooses(client, user, two):
    client.force_login(user)
    response = client.get(reverse(LIST))
    assert shape_of(response) == "table"
    html = response.content.decode()
    assert 'data-shape="table"' in html and "data-shape-switch" in html
    assert 'name="shape" value="board"' in html


def test_choosing_the_board_is_remembered_and_keeps_the_filters(client, user, two):
    first, _second = two
    client.force_login(user)
    page = client.get(reverse(LIST), {"q": "aperture", "sort": "-applied"}).content.decode()
    assert 'name="next" value="/applications/?q=aperture&amp;sort=-applied"' in page

    response = client.post(
        reverse("core:table_settings", args=["applications"]),
        {"shape": "board", "next": "/applications/?q=aperture&sort=-applied"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/applications/?q=aperture&sort=-applied"
    assert stored(user)["shape"] == "board"

    board = client.get(reverse(LIST), {"q": "aperture", "sort": "-applied"})
    assert shape_of(board) == "board"
    assert cards(board) == [first.pk], "the filter came across"
    assert 'data-shape="board"' in board.content.decode()
    assert (
        'name="next" value="/applications/?q=aperture&amp;sort=-applied"' in board.content.decode()
    )

    client.post(
        reverse("core:table_settings", args=["applications"]),
        {"shape": "table", "next": "/applications/?q=aperture"},
    )
    assert shape_of(client.get(reverse(LIST), {"q": "aperture"})) == "table"


def test_the_address_can_ask_for_a_shape_without_changing_the_preference(client, user, two):
    client.force_login(user)
    assert shape_of(client.get(reverse(LIST), {"view": "board"})) == "board"
    assert stored(user) == {}
    assert shape_of(client.get(reverse(LIST))) == "table"
    assert shape_of(client.get(reverse(LIST), {"view": "sideways"})) == "table"

    # A live filter on a page opened by address keeps the shape it was opened in.
    page = client.get(reverse(LIST), {"view": "board"}).content.decode()
    assert 'name="view" value="board"' in page.split("<form", 2)[-1].split("</form>")[0] or (
        'name="view" value="board"' in page
    )


def test_a_shape_nobody_can_choose_is_refused(client, user):
    client.force_login(user)
    client.post(
        reverse("core:table_settings", args=["applications"]),
        {"shape": "sideways", "next": "/applications/"},
    )
    assert stored(user) == {}


def test_saving_the_columns_keeps_the_shape_and_reset_forgets_it(client, user, two):
    client.force_login(user)
    settings_url = reverse("core:table_settings", args=["applications"])
    client.post(settings_url, {"shape": "board", "next": "/applications/"})
    client.post(settings_url, {"show": ["company"], "order": ["company"], "next": "/applications/"})
    assert stored(user)["shape"] == "board"
    assert shape_of(client.get(reverse(LIST))) == "board"

    client.post(settings_url, {"reset": "1", "next": "/applications/"})
    assert shape_of(client.get(reverse(LIST))) == "table"


def test_the_old_address_redirects_and_carries_what_it_was_given(client, user, two):
    client.force_login(user)
    response = client.get("/applications/board/", {"q": "mesa", "status": "applied"})
    assert response.status_code == 302
    location = response["Location"]
    assert location.startswith("/applications/?") and "view=board" in location
    assert "q=mesa" in location and "status=applied" in location

    followed = client.get(location)
    assert shape_of(followed) == "board"
    assert len(cards(followed)) == 1


# --------------------------------------------------------------------- the board


def test_the_board_shows_only_live_columns_and_says_what_is_off_it(client, user, two):
    first, second = two
    change_status(first, Status.REJECTED)
    client.force_login(user)

    board = client.get(reverse(LIST), {"view": "board"})
    statuses = [column["status"] for column in board.context["columns"]]
    assert Status.REJECTED not in statuses and Status.APPLIED in statuses
    assert cards(board) == [second.pk]
    assert "data-off-board" not in board.content.decode(), "nothing was filtered to"

    settled = client.get(reverse(LIST), {"view": "board", "status": "rejected"})
    html = settled.content.decode()
    assert cards(settled) == []
    assert 'data-off-board="1"' in html and "One settled application matches" in html
    assert 'href="/applications/?view=table&amp;status=rejected"' in html, "the table, filter kept"

    closed = client.get(reverse(LIST), {"view": "board", "state": "closed"})
    assert 'data-off-board="1"' in closed.content.decode()


def test_the_count_counts_what_the_shape_shows(client, user, two):
    first, _second = two
    change_status(first, Status.REJECTED)
    client.force_login(user)

    table = client.get(reverse(LIST)).content.decode()
    assert "2 applications" in table
    board = client.get(reverse(LIST), {"view": "board"}).content.decode()
    assert "2 applications" in board, "the count is the filter's, whatever the shape draws"


def test_a_live_filter_swaps_the_board_in_place(client, user, two):
    first, _second = two
    client.force_login(user)
    fragment = client.get(reverse(LIST), {"view": "board", "q": "aperture"}, **HTMX)
    html = fragment.content.decode()
    assert "<html" not in html
    assert 'id="applications-table" data-shape="board"' in html
    assert "data-board-column" in html
    assert cards(fragment) == [first.pk]


# ------------------------------------------------------------------ navigation


def test_board_is_no_longer_a_navigation_item_but_the_shortcut_still_gets_there(client, user):
    assert "board" not in navigation.BY_KEY and "board" not in navigation.HIDEABLE
    assert [item.key for item in navigation.ITEMS][:2] == ["dashboard", "listings"]
    client.force_login(user)
    home = client.get(reverse("core:home")).content.decode()
    assert 'data-nav="board"' not in home
    assert "/applications/?view=board" in home, "the dashboard's shortcut opens the shape"


def test_a_hidden_board_entry_is_forgotten_by_the_migration(user):
    from django.apps import apps

    profile = user.profile
    profile.hidden_nav_items = ["board", "companies"]
    profile.save()
    migration = importlib.import_module(
        "postulo.accounts.migrations.0019_forget_the_board_navigation_item"
    )

    migration.forget_board(apps, None)

    profile.refresh_from_db()
    assert profile.hidden_nav_items == ["companies"]
