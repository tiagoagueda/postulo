"""Forty captures without forty page loads (#179).

Deciding is three answers -- keep, discard, keep-and-apply -- and most captures get the
second one in under a second. The review page moves on by itself when asked, the list
discards several at once, and a default in the form is visibly a default.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs.capture_views import NOT_ON_THE_PAGE, next_pending
from postulo.jobs.models import Capture, CaptureStatus

pytestmark = pytest.mark.django_db

DECISION = {"company_name": "Black Mesa", "salary_currency": "EUR", "salary_period": "year"}


def capture(user, title: str, **data) -> Capture:
    return Capture.objects.create(
        owner=user,
        url=f"https://example.org/{title.lower().replace(' ', '-')}",
        data={"title": title, "company_name": "Black Mesa", **data},
    )


@pytest.fixture
def queue(user):
    """Three waiting, oldest first in the list they were made in; the list shows newest first."""
    return [capture(user, "First"), capture(user, "Second"), capture(user, "Third")]


# --------------------------------------------------------------------- the queue


def test_next_is_the_next_older_one_and_wraps_at_the_end(user, queue):
    first, second, third = queue
    assert next_pending(user, after=third) == second, "newest first, so next is the older one"
    assert next_pending(user, after=second) == first
    assert next_pending(user, after=first) == third, "the end wraps to what is still waiting"

    third.status = CaptureStatus.DISCARDED
    third.save(update_fields=["status"])
    assert next_pending(user, after=first) == second, "a decided one is not in the queue"


def test_the_page_says_how_many_are_waiting_and_offers_the_next(client, user, queue):
    _first, second, third = queue
    client.force_login(user)

    html = client.get(reverse("jobs:capture_review", args=[third.pk])).content.decode()

    assert "Waiting after this one: 2." in html
    assert f'href="{second.get_absolute_url()}"' in html, "skip goes to the next older one"
    assert "Save and next" in html and "Discard and next" in html


def test_the_last_one_offers_no_next(client, user):
    only = capture(user, "Only")
    client.force_login(user)

    html = client.get(reverse("jobs:capture_review", args=[only.pk])).content.decode()

    assert "The last one waiting." in html
    assert "Save and next" not in html and "Discard and next" not in html
    assert "data-key-save-next" in html, "the keys fall back to the plain buttons"


# ------------------------------------------------------------------ and next


def test_save_and_next_records_the_listing_and_moves_on(client, user, queue):
    _first, second, third = queue
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_review", args=[third.pk]),
        {**DECISION, "title": "Third", "next": "1"},
    )

    third.refresh_from_db()
    assert third.status == CaptureStatus.ACCEPTED and third.posting is not None
    assert response.status_code == 302
    assert response.url == second.get_absolute_url(), "the next capture, not the listing"


def test_save_and_next_on_the_last_one_goes_back_to_the_list(client, user):
    only = capture(user, "Only")
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_review", args=[only.pk]), {**DECISION, "title": "Only", "next": "1"}
    )

    assert response.url == reverse("listings:list")


def test_discard_and_next_moves_on(client, user, queue):
    _first, second, third = queue
    client.force_login(user)

    response = client.post(reverse("jobs:capture_discard", args=[third.pk]), {"next": "1"})

    third.refresh_from_db()
    assert third.status == CaptureStatus.DISCARDED
    assert response.url == second.get_absolute_url()


def test_plain_save_and_discard_still_go_where_they_went(client, user, queue):
    _first, second, third = queue
    client.force_login(user)

    saved = client.post(reverse("jobs:capture_review", args=[third.pk]), {**DECISION, "title": "T"})
    third.refresh_from_db()
    assert saved.url == third.posting.get_absolute_url()

    discarded = client.post(reverse("jobs:capture_discard", args=[second.pk]))
    assert discarded.url == reverse("listings:list")


# --------------------------------------------------------------- bulk discard


def test_the_ticked_ones_are_discarded_together(client, user, other_user, queue):
    first, second, third = queue
    theirs = capture(other_user, "Theirs")
    second.status = CaptureStatus.ACCEPTED
    second.save(update_fields=["status"])
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_discard_selected"),
        {"selected": [str(first.pk), str(second.pk), str(third.pk), str(theirs.pk), "x"]},
    )

    assert response.status_code == 302
    for row in (first, second, third, theirs):
        row.refresh_from_db()
    assert first.status == third.status == CaptureStatus.DISCARDED
    assert second.status == CaptureStatus.ACCEPTED, "only what was still waiting"
    assert theirs.status == CaptureStatus.PENDING, "never somebody else's"


def test_the_list_offers_a_checkbox_per_capture_and_one_button(client, user, queue):
    client.force_login(user)

    html = client.get(reverse("listings:list")).content.decode()

    assert html.count('name="selected"') == 3
    assert html.count('form="capture-bulk"') == 3, "the boxes belong to the bulk form by name"
    assert "Discard the ticked ones" in html


def test_nothing_ticked_discards_nothing(client, user, queue):
    client.force_login(user)
    client.post(reverse("jobs:capture_discard_selected"), {})
    assert Capture.objects.filter(status=CaptureStatus.PENDING).count() == 3


# ----------------------------------------------------------- visible defaults


def test_a_currency_the_page_did_not_state_is_marked_as_a_default(client, user):
    stated = capture(
        user, "Stated", salary_min="40000", salary_currency="USD", salary_period="year"
    )
    unstated = capture(user, "Unstated")
    client.force_login(user)

    form = client.get(reverse("jobs:capture_review", args=[unstated.pk])).context["form"]
    assert form.fields["salary_currency"].help_text == NOT_ON_THE_PAGE
    assert form.fields["salary_period"].help_text == NOT_ON_THE_PAGE
    assert form.initial["salary_currency"] == "EUR", "a select needs a default"

    form = client.get(reverse("jobs:capture_review", args=[stated.pk])).context["form"]
    assert form.fields["salary_currency"].help_text != NOT_ON_THE_PAGE, "read off the page"
