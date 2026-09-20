"""The listings page as a configurable table (#160).

The page most like a channel list -- many rows, looked at once, mostly discarded -- drew
its own rows until now, so it had none of what `core/tables.py` gives a list. What is
checked here is the part that was decided rather than inherited: that the workflow stayed a
strip of tabs instead of becoming a column filter, that the buttons deciding about a row
cannot be hidden, that a sweep can discard forty at once and say why, and that a page with
nothing on it does not wear the furniture of a table.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.core import tables
from postulo.jobs.models import Company, DiscardReason, JobPosting, ListingState
from postulo.jobs.tables import ListingsTable

pytestmark = pytest.mark.django_db

LIST = "listings:list"


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science", location="Cambridge")


def listing(user, company, title="Test Engineer", **fields):
    return JobPosting.objects.create(owner=user, company=company, title=title, **fields)


def titles(response) -> list[str]:
    return [row.title for row in response.context["listings"]]


# --------------------------------------------------- it is a table, and a registered one


def test_the_page_draws_the_table_furniture_once_there_is_a_table(client, user, company):
    listing(user, company, title="Test Engineer")
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert '<caption class="sr-only">Listings</caption>' in html, "a table names itself (#260)"
    assert 'id="sort-title"' in html and 'id="sort-closes"' in html, "headers sort"
    assert 'id="filter-title"' in html, "and narrow, per column"
    assert reverse("core:table_settings", args=["listings"]) in html, "Columns follows the account"
    assert reverse("listings:bulk") in html, "and rows can be acted on together"


def test_an_empty_page_wears_none_of_it(client, user):
    """A Columns control, a filter row and a bulk bar over no rows at all is worse than the
    sentence this page used to be, and that sentence is what somebody sees first (#160)."""
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert "Nothing to decide" in html
    assert reverse("core:table_settings", args=["listings"]) not in html
    assert 'id="filter-title"' not in html and reverse("listings:bulk") not in html
    assert reverse("listings:create") in html, "what to do instead is still offered"


def test_an_empty_tab_belonging_to_a_full_account_keeps_it(client, user, company):
    """Not the same thing: forty listings and none discarded is a table with nothing in it
    this week, and taking the controls away would mean they came and went."""
    listing(user, company)
    client.force_login(user)

    html = client.get(reverse(LIST) + "?state=discarded").content.decode()

    assert reverse("core:table_settings", args=["listings"]) in html
    assert "Nothing matches these filters" in html


# ------------------------------------------------------------- the workflow stays a tab


def test_the_state_is_a_column_that_sorts_and_a_tab_that_narrows_but_never_both(
    client, user, company
):
    """Two controls for one question would disagree about which rows are on the page. The
    column shows the state and orders by it; the tabs, which carry the counts, choose."""
    listing(user, company, title="Fresh")
    shortlisted = listing(user, company, title="Kept")
    shortlisted.shortlist()
    binned = listing(user, company, title="Binned")
    binned.discard(DiscardReason.PAY)

    client.force_login(user)
    html = client.get(reverse(LIST) + "?state=all").content.decode()

    assert 'id="sort-state"' in html, "the column sorts"
    assert 'id="filter-state"' not in html, "and narrows nothing: the tabs do that"
    assert 'name="state"' in html, "the open tab travels with the filters"

    # Sorted by what the cell says, not by what the row stores: an applied listing still
    # stores `new`, so ordering on the column would order by something else entirely.
    applied = listing(user, company, title="Sent")
    Application.objects.create(owner=user, posting=applied, status=Status.APPLIED)
    response = client.get(reverse(LIST), {"state": "all", "sort": "state"})
    assert titles(response) == ["Fresh", "Kept", "Sent", "Binned"]


def test_the_usual_tab_does_not_pretend_the_page_is_narrowed(client, user, company):
    listing(user, company)
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()
    assert "Clear filters" not in html

    html = client.get(reverse(LIST) + "?state=discarded").content.decode()
    assert "Clear filters" in html, "a tab that is not the usual one is a filter, and says so"


def test_a_tab_and_a_column_filter_compose(client, user, company):
    other = Company.objects.create(owner=user, name="Black Mesa")
    first = listing(user, company, title="Binned here")
    second = listing(user, other, title="Binned there")
    for item in (first, second):
        item.discard(DiscardReason.LOCATION)
    listing(user, company, title="Still to decide")

    client.force_login(user)
    response = client.get(reverse(LIST), {"state": "discarded", "company": "Aperture"})

    assert titles(response) == ["Binned here"]


# ------------------------------------------------------------------ sorting and narrowing


def test_the_columns_sort_and_narrow(client, user, company):
    today = timezone.localdate()
    listing(user, company, title="Zebra keeper", closes_at=today + dt.timedelta(days=30))
    listing(user, company, title="Antelope keeper", closes_at=today + dt.timedelta(days=2))
    listing(user, company, title="Nobody knows")

    client.force_login(user)

    assert titles(client.get(reverse(LIST), {"sort": "title"})) == [
        "Antelope keeper",
        "Nobody knows",
        "Zebra keeper",
    ]
    # The default: soonest deadline first, and the one with no deadline last rather than
    # first, because `ordering` puts nulls last everywhere.
    assert titles(client.get(reverse(LIST))) == [
        "Antelope keeper",
        "Zebra keeper",
        "Nobody knows",
    ]
    assert titles(client.get(reverse(LIST), {"title": "keeper"})) == [
        "Antelope keeper",
        "Zebra keeper",
    ]
    assert titles(
        client.get(reverse(LIST), {"closes_to": (today + dt.timedelta(days=7)).isoformat()})
    ) == ["Antelope keeper"]


def test_the_salary_column_sorts_by_a_year_within_a_currency(client, user, company):
    """An hourly rate below every annual one is what sorting the raw number does (#224)."""
    listing(
        user, company, title="Hourly", salary_max=90, salary_period="hour", salary_currency="EUR"
    )
    listing(
        user, company, title="Yearly", salary_max=60000, salary_period="year", salary_currency="EUR"
    )

    client.force_login(user)

    # Ascending: 60,000 a year below 90 an hour, which is about 151,000 of the same money.
    # Sorting on the raw figure would have put 90 first and called it the lower salary.
    assert titles(client.get(reverse(LIST), {"sort": "salary"})) == ["Yearly", "Hourly"]
    assert titles(client.get(reverse(LIST), {"sort": "-salary"})) == ["Hourly", "Yearly"]


def test_the_chosen_columns_follow_the_account(client, user, company):
    listing(user, company, title="Test Engineer", source="A board")
    profile = user.profile
    profile.table_settings = {"listings": {"columns": ["title", "source"], "page_size": 25}}
    profile.save(update_fields=["table_settings"])
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert "A board" in html and "Found via" in html
    assert 'data-col="location"' not in html, "a column nobody asked for is not drawn"


def test_the_buttons_that_decide_about_a_row_cannot_be_hidden(client, user, company):
    """The actions are a column of the page, not of the table: a Columns control that could
    take away *Discard* would be a way to make the page useless and not know why (#160)."""
    item = listing(user, company)
    profile = user.profile
    profile.table_settings = {"listings": {"columns": ["title"], "page_size": 25}}
    profile.save(update_fields=["table_settings"])
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert reverse("listings:discard", args=[item.pk]) in html
    assert reverse("listings:apply", args=[item.pk]) in html
    assert "What to do about it" in html, "and the column says what it is"


def test_the_table_is_registered_under_its_own_name():
    assert tables.TABLES["listings"] is ListingsTable
    assert "actions" not in {column.key for column in ListingsTable.columns}


# ------------------------------------------------------------------ deciding in bulk


def test_a_sweep_discards_the_ticked_ones_and_says_why_once(client, user, company):
    """*Look at forty, keep three* is the gesture, and one reason for the sweep is a fair
    description of it; a row that really went for another can be discarded again (#160)."""
    first = listing(user, company, title="One")
    second = listing(user, company, title="Two")
    kept = listing(user, company, title="Three")
    client.force_login(user)

    response = client.post(
        reverse("listings:bulk"),
        {"chosen": [first.pk, second.pk], "bulk-action": "discard", "reason": "pay"},
    )

    assert response.status_code == 302
    for item in (first, second):
        item.refresh_from_db()
        assert item.state == ListingState.DISCARDED
        assert item.discard_reason == DiscardReason.PAY
    kept.refresh_from_db()
    assert kept.state == ListingState.NEW, "an unticked row is not swept up with them"


def test_a_sweep_can_shortlist_and_can_put_them_back(client, user, company):
    item = listing(user, company)
    client.force_login(user)

    client.post(reverse("listings:bulk"), {"chosen": [item.pk], "bulk-action": "shortlist"})
    item.refresh_from_db()
    assert item.state == ListingState.SHORTLISTED

    client.post(reverse("listings:bulk"), {"chosen": [item.pk], "bulk-action": "restore"})
    item.refresh_from_db()
    assert item.state == ListingState.NEW


def test_a_sweep_touches_only_the_rows_that_are_yours(client, user, other_user, company):
    """The rule `core/bulk.py` exists for: the ids are re-scoped and the action works on the
    intersection, so forty ids of which thirty-nine are somebody else's does the one."""
    mine = listing(user, company)
    theirs = JobPosting.objects.create(
        owner=other_user,
        company=Company.objects.create(owner=other_user, name="Theirs"),
        title="Not yours",
    )
    client.force_login(user)

    client.post(
        reverse("listings:bulk"),
        {"chosen": [mine.pk, theirs.pk], "bulk-action": "discard", "reason": "pay"},
    )

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.state == ListingState.DISCARDED
    assert theirs.state == ListingState.NEW


def test_a_sweep_refuses_an_action_it_does_not_have(client, user, company):
    item = listing(user, company)
    client.force_login(user)

    response = client.post(
        reverse("listings:bulk"), {"chosen": [item.pk], "bulk-action": "delete"}, follow=True
    )

    item.refresh_from_db()
    assert item.state == ListingState.NEW
    assert "not something Postulo can do" in response.content.decode()


def test_an_unknown_reason_becomes_other_rather_than_refusing_the_sweep(client, user, company):
    item = listing(user, company)
    client.force_login(user)

    client.post(
        reverse("listings:bulk"),
        {"chosen": [item.pk], "bulk-action": "discard", "reason": "nonsense"},
    )

    item.refresh_from_db()
    assert item.discard_reason == DiscardReason.OTHER


# ------------------------------------------------------------------ editing where it sits


def test_the_role_can_be_renamed_in_its_cell(client, user, company):
    item = listing(user, company, title="Tets Engineer")
    client.force_login(user)
    url = reverse("listings:cell", args=[item.pk, "title"])

    editor = client.get(url).content.decode()
    assert 'name="title"' in editor
    assert reverse("jobs:posting_update", args=[item.pk]) not in editor, "the editor, not the form"

    saved = client.post(url, {"title": "Test Engineer"})

    assert saved.status_code == 200
    item.refresh_from_db()
    assert item.title == "Test Engineer"


def test_a_column_that_is_not_editable_cannot_be_edited_by_address(client, user, company):
    item = listing(user, company)
    client.force_login(user)

    assert client.get(reverse("listings:cell", args=[item.pk, "state"])).status_code == 404


def test_the_row_still_says_which_state_it_is_in(client, user, company):
    """`data-listing-state` was on the row before the page was a table and stays on it."""
    item = listing(user, company)
    item.shortlist()
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert re.search(r'data-listing-state="shortlisted"', html)
