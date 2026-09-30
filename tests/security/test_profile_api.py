"""The API's profile call: the caller's own details and nobody else's (#309).

A new surface, so its boundary tests come with it. There is no id in the address, which is
the first line of defence -- nothing names another person's profile -- and these hold the
rest: the scopes, the owner, and what a value sent through it can do on a page.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from postulo.api.models import ApiToken

pytestmark = pytest.mark.django_db


def issue(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, payload, **headers):
    return client.patch(
        "/api/v1/profile", data=json.dumps(payload), content_type="application/json", **headers
    )


def named(user, first: str, last: str, **profile):
    user.first_name, user.last_name = first, last
    user.save()
    for name, value in profile.items():
        setattr(user.profile, name, value)
    user.profile.save()


def test_a_capture_token_reaches_none_of_it(client, user):
    token = issue(user, "captures")
    assert client.get("/api/v1/profile", **token).status_code == 403
    assert patch(client, {"pronouns": "she/her"}, **token).status_code == 403
    user.profile.refresh_from_db()
    assert user.profile.pronouns == ""


def test_a_read_token_cannot_patch_and_a_write_token_cannot_get(client, user):
    """Each call asks for its own scope. That is all this holds: it is not a claim that a
    token holding `write` learns nothing, which the next test says plainly."""
    reader = issue(user, "read")
    response = patch(client, {"form_of_address": "Dr"}, **reader)
    assert response.status_code == 403 and "'write'" in response.json()["detail"]
    assert client.get("/api/v1/profile", **issue(user, "write")).status_code == 403
    user.profile.refresh_from_db()
    assert user.profile.form_of_address == ""


def test_a_patch_answers_with_the_record_whatever_it_changed(client, user):
    """Decided, not overlooked (#309): every `PATCH` in this API answers with the record as
    it stands, `PATCH /companies/{id}` as much as this one, so a token holding `write` sees
    what it may change -- by sending nothing at all, if it likes. The scope's label and
    the API's description both say so, and this is here so that changing either the
    behaviour or the words is a decision somebody takes rather than an accident."""
    named(user, "Alex", "Morgan", pronouns="they/them")
    response = patch(client, {}, **issue(user, "write"))
    assert response.status_code == 200
    body = response.json()
    assert (body["first_name"], body["pronouns"]) == ("Alex", "they/them")

    from postulo.api.api import api
    from postulo.api.models import SCOPES

    assert "your details" in str(SCOPES["write"])
    described = " ".join(api.description.split())
    assert "the owner's own details (`/profile`)" in described
    assert "A `PATCH` answers with the record" in described


def test_a_token_reads_and_writes_its_owners_profile_and_nobody_elses(client, user, other_user):
    named(user, "Alex", "Morgan", pronouns="they/them")
    named(other_user, "Sam", "Rivera", pronouns="she/her", location="Porto")

    body = client.get("/api/v1/profile", **issue(user, "read")).json()
    assert body["first_name"] == "Alex" and body["pronouns"] == "they/them"
    assert "Sam" not in json.dumps(body) and "Porto" not in json.dumps(body)

    response = patch(
        client, {"pronouns": "he/him", "first_name": "Changed"}, **issue(user, "write")
    )
    assert response.status_code == 200
    other_user.refresh_from_db()
    other_user.profile.refresh_from_db()
    assert other_user.first_name == "Sam" and other_user.profile.pronouns == "she/her"


def test_an_unknown_field_changes_nothing_it_names(client, user):
    """Only what the schema lists is written: not the theme, not the plugins, not whose it is."""
    named(user, "Alex", "Morgan")
    response = patch(
        client,
        {"theme": "dark", "plugins_off": ["x"], "user_id": 999, "printed_location": "Mars"},
        **issue(user, "write"),
    )
    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.theme != "dark" and user.profile.plugins_off == []
    assert user.profile.location == ""


def test_markup_sent_through_the_api_is_text_on_the_page(client, user):
    """A value an API client wrote is drawn into *Your details* as a box's value, escaped."""
    named(user, "Alex", "Morgan")
    sent = '"><script>alert(1)</script>'
    assert len(sent) <= 40
    response = patch(client, {"form_of_address": sent}, **issue(user, "write"))
    assert response.status_code == 200

    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html
