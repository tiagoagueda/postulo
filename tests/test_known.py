"""Has this advert been captured before? Told, not refused (#178).

Nothing checked, until this, whether a posting had been captured already. The check is a
question asked in time -- on the form before anything is fetched, on the review screen, and
through the API for the extension -- and never a constraint: a second capture is the
person's to want.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.core.addresses import same_url
from postulo.jobs.known import known
from postulo.jobs.models import Capture, CaptureStatus, Company, JobPosting

pytestmark = pytest.mark.django_db

POSTING = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Research Engineer",
    "hiringOrganization": {"name": "Black Mesa"},
    "description": "<p>Science.</p>",
}
PAGE = (
    f'<html><head><script type="application/ld+json">{json.dumps(POSTING)}</script></head></html>'
)


def a_listing(
    user, url="https://www.example.org/jobs/42/", title="Research Engineer", company="Black Mesa"
):
    owner_company, _ = Company.objects.get_or_create(owner=user, name=company)
    return JobPosting.objects.create(owner=user, company=owner_company, title=title, url=url)


def a_capture(
    user, url="https://example.org/jobs/42", status=CaptureStatus.PENDING, title="Research Engineer"
):
    return Capture.objects.create(
        owner=user, url=url, status=status, data={"title": title, "company_name": "Black Mesa"}
    )


# -------------------------------------------------------------- the address


@pytest.mark.parametrize(
    "spelling",
    [
        "https://www.example.org/jobs/42/",
        "http://example.org/jobs/42",
        "HTTPS://EXAMPLE.ORG/JOBS/42/",
        "https://www.example.org/jobs/42",
    ],
)
def test_one_address_however_it_is_spelled(spelling):
    assert same_url(spelling) == "example.org/jobs/42"


def test_the_query_is_part_of_the_address_and_nothing_is_an_address():
    assert same_url("https://board.example/view?id=7") == "board.example/view?id=7"
    assert same_url("https://board.example/view?id=8") != same_url(
        "https://board.example/view?id=7"
    )
    assert same_url("") == "" and same_url("not a url") == ""


# -------------------------------------------------------------- the question


def test_a_listing_at_the_address_is_found_however_it_was_spelled(user):
    listing = a_listing(user, "https://www.example.org/jobs/42/")

    seen = known(user, "http://example.org/jobs/42")

    assert seen.listings == [listing]
    assert not seen.captures and not seen.similar
    assert seen


def test_a_capture_still_waiting_is_found_and_the_capture_being_reviewed_is_not(user):
    waiting = a_capture(user, "https://example.org/jobs/42")
    a_capture(user, "https://example.org/jobs/42", status=CaptureStatus.DISCARDED)
    reviewed = a_capture(user, "https://example.org/jobs/42/")

    seen = known(user, "https://www.example.org/jobs/42", except_capture=reviewed.pk)

    assert seen.captures == [waiting], "discarded ones and the one being reviewed do not count"


def test_the_same_title_at_the_same_company_is_a_softer_match(user):
    elsewhere = a_listing(user, "https://board.example/view?id=7")

    seen = known(user, "https://board.example/view?id=8", "research engineer", "BLACK MESA")

    assert seen.similar == [elsewhere], "case aside, and at another address"
    assert not seen.listings
    assert not known(user, "https://board.example/view?id=8", "Research Engineer", "Aperture")


def test_an_address_match_is_not_reported_twice_as_similar(user):
    listing = a_listing(user)

    seen = known(user, "https://example.org/jobs/42", "Research Engineer", "Black Mesa")

    assert seen.listings == [listing] and seen.similar == []


def test_nothing_is_known_about_an_unseen_address_or_another_account(user, other_user):
    a_listing(other_user)

    assert not known(user, "https://example.org/jobs/42", "Research Engineer", "Black Mesa")
    assert not known(user, "https://example.org/jobs/43")


# ------------------------------------------------------------- the web form


def test_the_form_says_so_before_fetching_and_captures_on_the_second_press(
    client, user, monkeypatch
):
    listing = a_listing(user, "https://www.example.org/jobs/1/")
    monkeypatch.setattr(
        "postulo.plugins.fetching.fetch_page",
        lambda url: (_ for _ in ()).throw(AssertionError("nothing should be fetched")),
    )
    client.force_login(user)

    told = client.post(
        reverse("jobs:capture_create"), {"url": "http://example.org/jobs/1", "html": PAGE}
    )

    assert told.status_code == 200
    html = told.content.decode()
    assert "already in your listings" in html
    assert listing.get_absolute_url() in html
    assert 'name="anyway" value="1"' in html
    assert not Capture.objects.for_user(user).exists(), "told, and nothing made"

    made = client.post(
        reverse("jobs:capture_create"),
        {"url": "http://example.org/jobs/1", "html": PAGE, "anyway": "1"},
    )

    assert made.status_code == 302
    assert Capture.objects.for_user(user).count() == 1, "the second press is the answer"


def test_the_form_does_not_ask_about_an_unseen_address(client, user):
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_create"), {"url": "https://example.org/jobs/9", "html": PAGE}
    )

    assert response.status_code == 302
    assert "anyway" not in response.content.decode()


# ---------------------------------------------------------- the review screen


def test_the_review_screen_says_what_is_held_already(client, user):
    listing = a_listing(user, "https://example.org/jobs/42")
    elsewhere = a_listing(user, "https://board.example/view?id=7")
    capture = a_capture(user, "https://www.example.org/jobs/42/")
    client.force_login(user)

    html = client.get(reverse("jobs:capture_review", args=[capture.pk])).content.decode()

    assert "already in your listings" in html
    assert listing.get_absolute_url() in html
    assert "A listing with this title at this company exists" in html
    assert elsewhere.get_absolute_url() in html


def test_the_review_screen_stays_quiet_for_a_new_advert(client, user):
    capture = a_capture(user, "https://example.org/jobs/77")
    client.force_login(user)

    html = client.get(reverse("jobs:capture_review", args=[capture.pk])).content.decode()

    assert "data-known" not in html


# ------------------------------------------------------------------ the API


def bearer_for(user, *scopes):
    _record, raw = ApiToken.issue(user, "Extension", scopes=scopes or ("captures",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def ask(client, bearer, postings):
    return client.post(
        "/api/v1/captures/known",
        data=json.dumps({"postings": postings}),
        content_type="application/json",
        **bearer,
    )


def test_the_api_answers_per_posting_and_stores_nothing(client, user):
    listing = a_listing(user, "https://www.example.org/jobs/42/")
    waiting = a_capture(user, "https://example.org/jobs/42")
    elsewhere = a_listing(user, "https://board.example/view?id=7")

    response = ask(
        client,
        bearer_for(user),
        [
            {
                "url": "http://example.org/jobs/42",
                "title": "Research Engineer",
                "company": "Black Mesa",
            },
            {
                "url": "https://board.example/view?id=8",
                "title": "research engineer",
                "company": "black mesa",
            },
            {"url": "https://example.org/jobs/999"},
        ],
    )

    assert response.status_code == 200, response.content
    first, second, third = response.json()
    assert [row["id"] for row in first["listings"]] == [listing.pk]
    assert first["listings"][0]["listing_url"].endswith(listing.get_absolute_url())
    assert [row["id"] for row in first["captures"]] == [waiting.pk]
    assert first["captures"][0]["review_url"].endswith(f"/jobs/captures/{waiting.pk}/review/")
    assert [row["id"] for row in first["similar"]] == [elsewhere.pk]
    assert [row["id"] for row in second["similar"]] == [listing.pk, elsewhere.pk] or [
        row["id"] for row in second["similar"]
    ] == [elsewhere.pk, listing.pk]
    assert second["listings"] == [] and third == {
        "url": "https://example.org/jobs/999",
        "listings": [],
        "captures": [],
        "similar": [],
    }
    assert Capture.objects.for_user(user).count() == 1, "asking makes nothing"


def test_the_api_tells_nothing_about_where_the_person_stands(client, user):
    """A `captures` token learns a posting is held, not its company, address or state (#536)."""
    a_listing(user, "https://example.org/jobs/42")

    first = ask(client, bearer_for(user), [{"url": "https://example.org/jobs/42"}]).json()[0]

    held = first["listings"][0]
    assert set(held) == {"id", "title", "created_at", "listing_url"}
    assert not {"state", "company_name", "url"} & set(held)


def test_the_api_needs_the_captures_scope_and_takes_a_hundred_at_most(client, user):
    assert (
        ask(client, bearer_for(user, "read"), [{"url": "https://example.org/jobs/1"}]).status_code
        == 403
    )
    too_many = [{"url": f"https://example.org/jobs/{n}"} for n in range(101)]
    assert ask(client, bearer_for(user), too_many).status_code == 422


# ------------------------------------------------- an account with a long history (#556)


def test_the_oldest_of_two_hundred_and_fifty_at_one_path_is_still_found(user):
    """Indeed puts the job in the query, so every listing from it shares one path."""
    for n in range(250):
        a_listing(user, url=f"https://www.indeed.com/viewjob?jk={n}", title=f"Job {n}")

    seen = known(user, "https://indeed.com/viewjob?jk=0")

    assert [posting.title for posting in seen.listings] == ["Job 0"]
    assert known(user, "https://www.indeed.com/viewjob?jk=249").listings[0].title == "Job 249"
    assert not known(user, "https://www.indeed.com/viewjob?jk=250")


def test_the_cost_of_asking_does_not_grow_with_the_account(client, user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    bearer = bearer_for(user)
    asked = [
        {"url": f"https://www.indeed.com/viewjob?jk=new{n}", "title": f"New {n}", "company": "X"}
        for n in range(40)
    ]

    def queries() -> int:
        with CaptureQueriesContext(connection) as seen:
            assert ask(client, bearer, asked).status_code == 200
        # What the question asks of the listings and captures; the rate limit's bookkeeping
        # in the cache table is not part of it.
        return len([q for q in seen.captured_queries if '"jobs_' in q["sql"]])

    few = queries()
    for n in range(60):
        a_listing(user, url=f"https://www.indeed.com/viewjob?jk=old{n}")
        a_capture(user, url=f"https://www.indeed.com/viewjob?jk=waiting{n}")
    assert queries() == few
