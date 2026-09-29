"""The boundary around remembered places (#267).

A remembered place is where one person's corrections showed a field to be on a site they use.
It is theirs: read only for their own captures, handed only to Postulo's own sources, listed
and forgotten only from their own settings. The flows are in `tests/test_remembered_on_review.py`;
this is the part an attacker would try.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from postulo.jobs.models import FieldHint

pytestmark = pytest.mark.django_db

SITE = "careers.example.org"
PAGE = (
    '<html lang="en-GB"><body><main><h1>Engineer</h1>'
    '<span id="employer">Aperture Science</span></main></body></html>'
)


def test_a_forged_request_forgets_nothing(user):
    FieldHint.objects.create(owner=user, host=SITE, field="company_name", place={"id": "e"})
    honest = Client()
    honest.force_login(user)

    forged = Client(enforce_csrf_checks=True)
    forged.cookies = honest.cookies  # the person's own session, as a forged request rides it

    response = forged.post(reverse("settings:capture_forget"), {"host": SITE})

    assert response.status_code == 403
    assert FieldHint.objects.for_user(user).count() == 1


def test_another_account_s_places_never_read_a_page_of_mine(client, user, other_user):
    """Through the API too: a token reads with its owner's places and nobody else's."""
    from postulo.api.models import ApiToken

    FieldHint.objects.create(
        owner=other_user, host=SITE, field="company_name", place={"id": "employer"}
    )
    _record, raw = ApiToken.issue(user, "Extension")

    response = client.post(
        "/api/v1/captures/preview",
        data=json.dumps({"url": f"https://{SITE}/jobs/1", "html": PAGE}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"]["company_name"] == ""
    assert response.json()["hinted"] == []


def test_the_settings_page_lists_only_my_sites(client, user, other_user):
    FieldHint.objects.create(owner=other_user, host=SITE, field="title", place={"id": "t"})
    client.force_login(user)

    html = client.get(reverse("settings:capture")).content.decode()

    assert SITE not in html
