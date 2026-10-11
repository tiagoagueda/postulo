"""A replayed external_id does not read an entry back to a token that may only bind (#763)."""

from __future__ import annotations

import json

import pytest

from postulo.api.models import ApiToken
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


def issue(user, name, *scopes):
    _record, raw = ApiToken.issue(user, name, scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, listing, headers, **fields):
    payload = {
        "kind": "note",
        "summary": "A private summary",
        "body": "A private body",
        "external_id": "msg-1",
        **fields,
    }
    return client.post(
        f"/api/v1/listings/{listing.pk}/events",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


@pytest.fixture
def listing(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    return JobPosting.objects.create(owner=user, company=company, title="Research Engineer")


def test_the_token_that_wrote_it_gets_it_back_in_full(client, user, listing):
    mine = issue(user, "extension", "listings:bind")
    first = post(client, listing, mine)

    again = post(client, listing, mine)

    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["body"] == "A private body"


def test_another_token_that_may_only_bind_is_told_it_exists_and_not_what_it_says(
    client, user, listing
):
    writer = issue(user, "writer", "write")
    post(client, listing, writer)
    replayer = issue(user, "replayer", "listings:bind")

    replay = post(client, listing, replayer, summary="x", body="y")

    assert replay.status_code == 200
    body = replay.json()
    assert body["external_id"] == "msg-1" and body["listing_id"] == listing.pk and body["id"]
    assert body["summary"] == "" and body["body"] == "" and body["actor"] == ""
    assert "private" not in replay.content.decode().lower()


def test_a_token_that_may_read_sees_the_entry(client, user, listing):
    post(client, listing, issue(user, "writer", "write"))
    reader = issue(user, "reader", "listings:bind", "read")

    replay = post(client, listing, reader)

    assert replay.json()["body"] == "A private body"
