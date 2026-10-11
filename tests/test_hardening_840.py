"""Columns and Views are at the end of the Listings filter row, and only with a table (#840)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def listed(user):
    company = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=company, title="Tester")
    return user


def page(client, **params):
    return client.get(reverse("listings:list"), params).content.decode()


def filter_row(html: str) -> str:
    """From the row's start to the table that follows it."""
    start = html.index("data-filter-row")
    return html[start : html.index('id="listings-table"', start)]


def controls_of(html: str) -> str:
    """The controls container, up to the table that follows."""
    start = html.index('id="listing-controls"')
    return html[start : html.index('id="listings-table"', start)]


def test_with_a_table_they_sit_at_the_end_of_the_filter_row_beside_apply(client, listed):
    client.force_login(listed)

    row = filter_row(page(client))

    assert "Apply" in row and "Columns" in row and "Views" in row
    assert row.index("Apply") < row.index("Columns")
    assert 'id="listing-controls" class="ms-auto' in row, "aligned to the end"
    title_row = page(client).split("data-filter-row")[0]
    assert "table_settings" not in title_row and "/tables/listings/settings/" not in title_row


def test_they_are_posted_from_forms_of_their_own_outside_the_filter_form(client, listed):
    client.force_login(listed)
    html = page(client)

    filters = html[
        html.index('id="listing-filters"') : html.index(
            "</form>", html.index('id="listing-filters"')
        )
    ]

    assert "Columns" not in filters and "Views" not in filters


def test_the_usual_tab_with_nothing_to_decide_is_the_sentence_alone_and_draws_no_controls(
    client, user
):
    company = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=company, title="Kept", state="discarded")
    client.force_login(user)

    html = page(client)

    assert "Nothing to decide" in html
    controls = controls_of(html)
    assert "Columns" not in controls and "Views" not in controls


def test_a_filter_that_matches_nothing_keeps_the_header_and_the_controls(client, listed):
    client.force_login(listed)

    html = page(client, title="no such role")

    controls = controls_of(html)
    assert "Columns" in controls and "Views" in controls


def test_the_swapped_answer_carries_the_controls_out_of_band(client, listed):
    client.force_login(listed)

    answer = client.get(
        reverse("listings:list"), {"title": "no such role"}, headers={"HX-Request": "true"}
    ).content.decode()

    assert 'id="listings-table"' in answer
    assert 'id="listing-controls"' in answer and 'hx-swap-oob="true"' in answer


def test_no_listings_at_all_draws_no_filter_row(client, user):
    client.force_login(user)

    html = page(client)

    assert "data-filter-row" not in html and "/tables/listings/settings/" not in html
