"""Every capture on the API names its posting by an opaque key (#711).

The browser extension counts the postings waiting for review once each, and it carried a copy
of `same_url` to tell them apart. The list hands it the grouping instead: `posting_key`, the
same for two captures of one posting, different for two postings, and a keyed hash rather than
the address -- a token that only holds ``captures`` is not told the address (#761), and a bare
digest of it would give it back a guess at a time.
"""

from __future__ import annotations

import hashlib

import pytest

from postulo.api.api import api
from postulo.api.models import ApiToken
from postulo.core.addresses import same_url
from postulo.jobs.models import Capture, CaptureStatus
from postulo.plugins import secrets

pytestmark = pytest.mark.django_db


def waiting(owner, url: str, title: str = "Test Engineer") -> Capture:
    return Capture.objects.create(
        owner=owner, url=url, status=CaptureStatus.PENDING, data={"title": title}
    )


def token_for(user, *scopes):
    _record, raw = ApiToken.issue(user, "script", scopes=scopes)
    return {"Authorization": f"Bearer {raw}"}


def keys(client, headers) -> dict[int, str]:
    rows = client.get("/api/v1/captures", headers=headers).json()["items"]
    return {row["id"]: row["posting_key"] for row in rows}


def test_two_captures_of_one_posting_share_a_key_and_two_postings_do_not(client, user):
    first = waiting(user, "https://www.careers.example/jobs/1/")
    again = waiting(user, "http://careers.example/JOBS/1")
    other = waiting(user, "https://careers.example/jobs/2")

    found = keys(client, token_for(user, "captures", "read"))

    assert found[first.pk] and found[first.pk] == found[again.pk]
    assert found[other.pk] and found[other.pk] != found[first.pk]


def test_the_key_is_the_one_the_stored_address_has(client, user):
    """Made from `Capture.url_key`, which is what `same_url` returns for the stored address."""
    capture = waiting(user, "https://www.careers.example/jobs/1/?ref=9")

    found = keys(client, token_for(user, "captures"))

    assert capture.url_key == same_url(capture.url)
    assert found[capture.pk] == secrets.posting_key(user.pk, capture.url_key)


def test_a_captures_only_token_gets_the_key_and_not_the_address(client, user):
    capture = waiting(user, "https://careers.example/jobs/1")

    [row] = client.get("/api/v1/captures", headers=token_for(user, "captures")).json()["items"]

    assert row["url"] == ""
    assert row["posting_key"] == keys(client, token_for(user, "captures", "read"))[capture.pk]
    assert "careers.example" not in str(row)


def test_the_key_is_stable_from_one_request_to_the_next(client, user):
    waiting(user, "https://careers.example/jobs/1")
    headers = token_for(user, "captures")

    assert keys(client, headers) == keys(client, headers)
    assert keys(client, headers) == keys(client, token_for(user, "captures", "read"))


def test_the_key_is_no_plain_digest_of_the_address(client, user):
    capture = waiting(user, "https://www.careers.example/jobs/1/")
    [key] = keys(client, token_for(user, "captures")).values()

    plain = set()
    for spelling in {capture.url, capture.url_key, same_url(capture.url)}:
        for algorithm in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256"):
            digest = hashlib.new(algorithm, spelling.encode())
            plain |= {digest.hexdigest(), digest.hexdigest()[: len(key)]}
    assert key not in plain


def test_the_key_depends_on_the_instance_key(user, settings):
    before = secrets.posting_key(user.pk, "careers.example/jobs/1")
    settings.SECRET_KEY = "another-key-" + "x" * 50
    settings.POSTULO_FIELD_KEY = ""

    assert secrets.posting_key(user.pk, "careers.example/jobs/1") != before


def test_two_accounts_have_unrelated_keys_for_one_posting(client, user, other_user):
    mine = waiting(user, "https://careers.example/jobs/1")
    theirs = waiting(other_user, "https://careers.example/jobs/1")

    assert (
        keys(client, token_for(user, "captures"))[mine.pk]
        != (keys(client, token_for(other_user, "captures"))[theirs.pk])
    )


def test_an_address_with_no_host_has_no_key(user):
    """Such captures match nothing, so they must not all become one posting."""
    assert secrets.posting_key(user.pk, "") == ""
    assert secrets.posting_key(user.pk, same_url("not an address")) == ""


def test_asking_whether_an_address_is_known_answers_its_key(client, user):
    """So the extension can merge what it has not sent yet with the list, without `same_url`."""
    capture = waiting(user, "https://careers.example/jobs/1")
    headers = token_for(user, "captures")

    answer = client.post(
        "/api/v1/captures/known",
        {"postings": [{"url": "http://www.careers.example/jobs/1/"}]},
        content_type="application/json",
        headers=headers,
    ).json()

    assert answer[0]["posting_key"] == keys(client, headers)[capture.pk]


def test_the_schema_describes_the_key(client, user):
    schemas = api.get_openapi_schema()["components"]["schemas"]

    for shape in ("CaptureOut", "KnownOut"):
        field = schemas[shape]["properties"]["posting_key"]
        assert field["type"] == "string" and "keyed hash" in field["description"], shape
        assert "posting_key" in schemas[shape]["required"], shape
