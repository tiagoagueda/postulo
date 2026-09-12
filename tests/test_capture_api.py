"""The capture API and its tokens.

The API is the surface a browser extension will eventually use, so what it refuses
matters more than what it accepts.
"""

import json
from decimal import Decimal

import pytest
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.applications.models import Application
from postulo.jobs.models import Capture, CaptureStatus

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


@pytest.fixture
def token(db, user):
    _record, raw = ApiToken.issue(user, "Test device")
    return raw


@pytest.fixture
def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def post_capture(client, bearer, **payload):
    return client.post(
        "/api/v1/captures",
        data=json.dumps(payload),
        content_type="application/json",
        **bearer,
    )


# --------------------------------------------------------------------- tokens


def test_the_secret_is_never_stored(db, user):
    record, raw = ApiToken.issue(user, "Test device")

    assert record.token_hash != raw
    assert raw not in record.token_hash
    assert record.prefix == raw[: len(record.prefix)]
    assert not ApiToken.objects.filter(token_hash=raw).exists()


def test_a_token_identifies_its_owner(client, bearer, user):
    response = client.get("/api/v1/me", **bearer)

    assert response.status_code == 200
    assert response.json()["owner"] == user.email


@pytest.mark.parametrize("header", [{}, {"HTTP_AUTHORIZATION": "Bearer nonsense"}])
def test_no_token_and_a_wrong_token_are_both_refused(client, db, header):
    assert client.get("/api/v1/me", **header).status_code == 401


def test_a_revoked_token_stops_working(client, db, user):
    record, raw = ApiToken.issue(user, "Old laptop")
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    assert client.get("/api/v1/me", **bearer).status_code == 200

    record.revoke()

    assert client.get("/api/v1/me", **bearer).status_code == 401


def test_a_token_belonging_to_a_disabled_account_stops_working(client, db, user):
    _record, raw = ApiToken.issue(user, "Device")
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert client.get("/api/v1/me", **{"HTTP_AUTHORIZATION": f"Bearer {raw}"}).status_code == 401


def test_using_a_token_records_that_it_was_used(client, db, user):
    record, raw = ApiToken.issue(user, "Device")
    assert record.last_used_at is None

    client.get("/api/v1/me", **{"HTTP_AUTHORIZATION": f"Bearer {raw}"})
    record.refresh_from_db()

    assert record.last_used_at is not None


def test_a_capture_token_is_not_a_login(client, db, user, bearer):
    """It reaches the API and nothing else."""
    assert client.get(reverse("applications:list"), **bearer).status_code == 302
    assert client.get(reverse("documents:cv_list"), **bearer).status_code == 302


# -------------------------------------------------------------------- capturing


def test_supplied_html_is_parsed_without_fetching_anything(client, bearer, user):
    """How an extension captures a posting only a signed-in reader can see."""
    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Research Engineer"
    assert body["company_name"] == "Black Mesa"
    assert body["status"] == CaptureStatus.PENDING
    assert "/review/" in body["review_url"]


def test_capturing_creates_nothing_but_a_capture(client, bearer, user):
    post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)

    assert Capture.objects.for_user(user).count() == 1
    assert not Application.objects.for_user(user).exists(), "a parser does not get to write records"


def test_a_page_with_no_posting_is_refused_with_an_explanation(client, bearer):
    response = post_capture(client, bearer, url="https://example.org/", html="<html></html>")

    assert response.status_code == 422
    assert "job posting" in response.json()["detail"]


def test_the_api_will_not_fetch_a_local_address(client, bearer, user):
    """The same guard as the web interface, on the surface a script can reach."""
    response = post_capture(client, bearer, url="http://127.0.0.1/admin")

    assert response.status_code == 422
    assert "private or local" in response.json()["detail"]
    assert not Capture.objects.for_user(user).exists()


# ------------------------------------------------ previewing, and correcting (#171)


def post_preview(client, bearer, **payload):
    return client.post(
        "/api/v1/captures/preview",
        data=json.dumps(payload),
        content_type="application/json",
        **bearer,
    )


def test_a_preview_says_what_was_read_and_keeps_nothing(client, bearer, user, monkeypatch):
    """What a browser extension shows in its popup before anything is sent."""

    def must_not_notify(*args, **kwargs):
        raise AssertionError("a preview tells nobody anything")

    monkeypatch.setattr("postulo.api.api.notify", must_not_notify)

    response = post_preview(client, bearer, url="https://example.org/j/7", html=PAGE)

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://example.org/j/7"
    assert body["source"]
    assert body["data"]["title"] == "Research Engineer"
    assert body["data"]["company_name"] == "Black Mesa"
    assert not Capture.objects.for_user(user).exists()


def test_a_preview_of_a_page_with_no_posting_is_refused_with_an_explanation(client, bearer):
    response = post_preview(client, bearer, url="https://example.org/", html="<html></html>")

    assert response.status_code == 422
    assert "job posting" in response.json()["detail"]


def test_a_preview_needs_the_captures_scope(client, db, user):
    _record, raw = ApiToken.issue(user, "Reader", scopes=("read",))

    response = post_preview(
        client, {"HTTP_AUTHORIZATION": f"Bearer {raw}"}, url="https://example.org/j/7", html=PAGE
    )

    assert response.status_code == 403


def test_without_corrections_the_capture_is_what_the_preview_said(client, bearer, user):
    preview = post_preview(client, bearer, url="https://example.org/j/7", html=PAGE).json()

    post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)
    capture = Capture.objects.for_user(user).get()

    assert capture.data == preview["data"]
    assert capture.source_name == preview["source"]


def test_corrections_replace_what_was_read_and_nothing_else(client, bearer, user):
    preview = post_preview(client, bearer, url="https://example.org/j/7", html=PAGE).json()

    response = post_capture(
        client,
        bearer,
        url="https://example.org/j/7",
        html=PAGE,
        data={"title": "Senior Research Engineer", "location": "Lyon", "salary_min": "55000"},
    )

    assert response.status_code == 201
    assert response.json()["title"] == "Senior Research Engineer"
    capture = Capture.objects.for_user(user).get()
    assert capture.data["title"] == "Senior Research Engineer"
    assert capture.data["location"] == "Lyon"
    assert capture.data["company_name"] == "Black Mesa", "what nobody corrected is what was read"
    assert capture.source_name == preview["source"], "the source is still what read the page"
    assert capture.status == CaptureStatus.PENDING, "corrected is not the same as reviewed"
    assert not Application.objects.for_user(user).exists()

    client.force_login(user)
    initial = client.get(reverse("jobs:capture_review", args=[capture.pk])).context["form"].initial
    assert initial["title"] == "Senior Research Engineer"
    assert initial["salary_min"] == Decimal("55000")


@pytest.mark.parametrize(
    "data",
    [{"titel": "A typo"}, {"title": ""}, {"title": "   "}, {"salary_min": "a lot"}],
    ids=["unknown field", "empty title", "blank title", "not a number"],
)
def test_corrections_that_do_not_make_a_posting_are_refused(client, bearer, user, data):
    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE, data=data)

    assert response.status_code == 422
    assert not Capture.objects.for_user(user).exists()


RESULTS = (
    '<html><head><script type="application/ld+json">'
    + json.dumps(
        {
            "@context": "https://schema.org/",
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "JobPosting",
                    "title": title,
                    "url": f"https://careers.blackmesa.example/jobs/{number}",
                    "hiringOrganization": {"name": "Black Mesa"},
                    "description": "<p>Science.</p>",
                }
                for number, title in ((1, "Research Engineer"), (2, "Test Engineer"), (3, "Clerk"))
            ],
        }
    )
    + "</script></head></html>"
)


@pytest.fixture
def announced(monkeypatch):
    """Every notification the API sends, in order."""
    sent = []
    monkeypatch.setattr(
        "postulo.api.api.notify", lambda owner, notification: sent.append(notification)
    )
    return sent


def test_several_from_one_page_are_announced_once_with_the_count(client, bearer, user, announced):
    """Forty notifications for one button would be worse than none (#177). The first says
    how many are coming; the rest arrive quietly, each still its own capture."""
    for position, number in enumerate((1, 2, 3), start=1):
        response = post_capture(
            client,
            bearer,
            url=f"https://careers.blackmesa.example/jobs/{number}",
            html=RESULTS,
            batch={"size": 3, "position": position},
        )
        assert response.status_code == 201, response.content

    titles = sorted(c.data["title"] for c in Capture.objects.for_user(user))
    assert titles == ["Clerk", "Research Engineer", "Test Engineer"], "each its own capture"
    assert len(announced) == 1
    assert announced[0].title == "Captured 3 postings from careers.blackmesa.example"
    assert "Research Engineer" in announced[0].body
    assert announced[0].url.endswith("/jobs/captures/")


def test_a_capture_on_its_own_is_announced_as_before(client, bearer, user, announced):
    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)
    assert response.status_code == 201
    assert [n.title for n in announced] == ["Captured: Research Engineer"]


@pytest.mark.parametrize(
    "batch",
    [
        {"size": 1, "position": 1},
        {"size": 3, "position": 4},
        {"size": 3, "position": 0},
        {"size": 3},
    ],
)
def test_a_batch_that_makes_no_sense_is_refused(client, bearer, user, batch):
    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE, batch=batch)
    assert response.status_code == 422, response.content
    assert not Capture.objects.for_user(user).exists()


def test_listing_shows_only_your_own_pending_captures(client, bearer, user, other_user):
    post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)
    Capture.objects.create(
        owner=other_user, url="https://example.org/j/9", data={"title": "Theirs"}
    )

    body = client.get("/api/v1/captures", **bearer).json()

    assert [item["title"] for item in body] == ["Research Engineer"]


# ----------------------------------------------------------------- the review


@pytest.fixture
def pending(db, user):
    return Capture.objects.create(
        owner=user,
        url="https://example.org/j/7",
        source_name="schema.org",
        data={
            "title": "Research Engineer",
            "company_name": "Black Mesa",
            "description": "Science.",
        },
    )


def test_the_review_page_arrives_pre_filled(client, user, pending):
    client.force_login(user)
    response = client.get(reverse("jobs:capture_review", args=[pending.pk]))

    assert response.status_code == 200
    initial = response.context["form"].initial
    assert initial["title"] == "Research Engineer"
    assert initial["company_name"] == "Black Mesa"


def test_accepting_a_capture_makes_a_listing_and_links_it(client, user, pending):
    client.force_login(user)
    response = client.post(
        reverse("jobs:capture_review", args=[pending.pk]),
        {
            "company_name": "Black Mesa",
            "title": "Research Engineer",
            "salary_currency": "EUR",
            "salary_period": "year",
        },
    )
    pending.refresh_from_db()

    assert response.status_code == 302
    assert pending.status == CaptureStatus.ACCEPTED
    assert pending.posting is not None
    assert pending.posting.title == "Research Engineer"
    assert pending.posting.derived_state == "new", "a listing to decide about, not an application"
    assert pending.application is None
    assert response.url == pending.posting.get_absolute_url()
    assert not Application.objects.for_user(user).exists()


def test_accepting_a_capture_already_applied_to_makes_the_application_too(client, user, pending):
    client.force_login(user)
    response = client.post(
        reverse("jobs:capture_review", args=[pending.pk]),
        {
            "company_name": "Black Mesa",
            "title": "Research Engineer",
            "salary_currency": "EUR",
            "salary_period": "year",
            "already_applied": "on",
        },
    )
    pending.refresh_from_db()

    assert response.status_code == 302
    assert pending.application is not None
    assert pending.application.posting == pending.posting
    assert pending.application.status == "applied"
    assert pending.posting.derived_state == "applied"
    assert response.url == pending.application.get_absolute_url()


def test_a_capture_can_be_discarded(client, user, pending):
    client.force_login(user)
    client.post(reverse("jobs:capture_discard", args=[pending.pk]))
    pending.refresh_from_db()

    assert pending.status == CaptureStatus.DISCARDED
    assert not Application.objects.for_user(user).exists()


def test_another_accounts_capture_is_not_found(client, other_user, pending):
    client.force_login(other_user)

    assert client.get(reverse("jobs:capture_review", args=[pending.pk])).status_code == 404
    assert client.post(reverse("jobs:capture_discard", args=[pending.pk])).status_code == 404


def test_another_accounts_token_is_not_listed(client, user, other_user):
    ApiToken.issue(other_user, "Their device")
    client.force_login(user)

    response = client.get(reverse("api:token_list"))

    assert list(response.context["tokens"]) == []


def test_a_token_is_shown_once_and_then_never_again(client, user):
    client.force_login(user)
    client.post(reverse("api:token_create"), {"name": "Laptop", "scopes": ["captures"]})

    first = client.get(reverse("api:token_list"))
    second = client.get(reverse("api:token_list"))

    assert first.context["new_token"], "the secret is shown immediately after creation"
    assert second.context["new_token"] is None, "and is not recoverable afterwards"


def test_pasting_the_page_source_skips_fetching_entirely(client, user, monkeypatch):
    """The way round a site that refuses Postulo, and round a login wall.

    Nothing is fetched, so nothing can be refused. This is also exactly what the browser
    extension will do, since the browser has already been allowed to see the page.
    """
    from postulo.plugins import fetching

    def must_not_be_called(url):
        raise AssertionError("nothing should be fetched when the page is supplied")

    monkeypatch.setattr("postulo.jobs.capture_views.fetch_page", must_not_be_called)
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_create"),
        {"url": "https://www.example.org/jobs/1", "html": PAGE},
    )

    assert response.status_code == 302
    capture = Capture.objects.for_user(user).get()
    assert capture.data["title"] == "Research Engineer"
    assert fetching  # imported for clarity about what was bypassed


def test_a_refused_fetch_keeps_the_form_and_explains(client, user, monkeypatch):
    from postulo.plugins.fetching import FetchFailed

    def refuse(url):
        raise FetchFailed("The site refused the request (403). …bot protection…")

    monkeypatch.setattr("postulo.jobs.capture_views.fetch_page", refuse)
    client.force_login(user)

    response = client.post(reverse("jobs:capture_create"), {"url": "https://example.org/jobs/1"})

    assert response.status_code == 200
    assert any("403" in str(m) for m in response.context["messages"])
    assert not Capture.objects.for_user(user).exists()
