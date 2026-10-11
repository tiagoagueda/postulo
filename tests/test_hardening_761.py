"""A captures-only token does not read the queue back (#761)."""

from __future__ import annotations

import pytest

from postulo.api.models import ApiToken
from postulo.jobs.models import Capture, CaptureStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def waiting(user):
    return Capture.objects.create(
        owner=user,
        url="https://careers.example/jobs/1",
        status=CaptureStatus.PENDING,
        data={"title": "Test Engineer", "company_name": "Aperture", "location": "Chell City"},
    )


def token_for(user, *scopes):
    _record, raw = ApiToken.issue(user, "script", scopes=scopes)
    return {"Authorization": f"Bearer {raw}"}


def listed(client, headers):
    return client.get("/api/v1/captures", headers=headers).json()["items"]


def test_a_captures_only_token_sees_the_title_and_the_review_link_and_no_more(
    client, user, waiting
):
    [row] = listed(client, token_for(user, "captures"))

    assert row["title"] == "Test Engineer" and f"/captures/{waiting.pk}/" in row["review_url"]
    for hidden in ("url", "company_name", "location", "source"):
        assert row[hidden] == "", hidden
    assert "Aperture" not in str(row) and "careers.example" not in str(row)


def test_a_token_that_also_reads_sees_all_of_it(client, user, waiting):
    [row] = listed(client, token_for(user, "captures", "read"))

    assert row["url"] == "https://careers.example/jobs/1"
    assert row["company_name"] == "Aperture" and row["location"] == "Chell City"
