"""Import and Export beside the heading, with a choice when there is more than one (#862)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.core import transfers
from postulo.plugins import api

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    for key in [k for k in transfers.REGISTRY if k.startswith("test-")]:
        transfers.forget(key)


def group(html: str, subject: str) -> str:
    return re.search(
        rf'<div class="button-group"[^>]*data-transfers="{subject}".*?</div>', html, re.S
    )[0]


def test_the_builtins_are_registered_for_the_three_pages():
    assert [t.key for t in transfers.offered("calendar", "export")] == ["calendar-ical-export"]
    assert [t.key for t in transfers.offered("calendar", "import")] == ["calendar-ical-import"]
    assert len(transfers.offered("details", "export")) == 2
    first, *_rest = transfers.offered("career", "import")
    assert first.key == "career-europass-import"


def test_a_key_already_taken_is_refused_and_a_bad_direction_ignored():
    mine = transfers.Transfer("test-a", "career", "export", "A", lambda request: "/a/")

    assert transfers.register(mine) is True
    assert transfers.register(mine) is False
    assert (
        transfers.register(transfers.Transfer("test-b", "career", "sideways", "B", lambda r: "/"))
        is False
    )
    assert (
        transfers.register(transfers.Transfer("", "career", "export", "C", lambda r: "/")) is False
    )


def test_the_plugin_surface_offers_the_registry():
    assert api.Transfer is transfers.Transfer and api.register_transfer is transfers.register


def test_career_draws_the_group_with_a_popover_for_two_ways_in(client, user):
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    box = group(html, "career")
    assert 'data-transfer="import"' in box and 'data-transfer="export"' in box
    assert 'popovertarget="import-career"' in box, "Europass and the record: two, so it asks"
    assert reverse("resume:candidate_file") in box, "one way out goes straight there"
    assert 'id="import-career"' in html and reverse("resume:europass_import") in html


def test_a_plugins_format_joins_the_list_and_turns_a_link_into_a_choice(client, user):
    client.force_login(user)
    before = client.get(reverse("resume:overview")).content.decode()
    assert 'popovertarget="export-career"' not in before

    api.register_transfer(
        api.Transfer(
            "test-json-resume", "career", "export", "JSON Resume", lambda r: "/json-resume/"
        )
    )
    after = client.get(reverse("resume:overview")).content.decode()

    assert 'popovertarget="export-career"' in after and "/json-resume/" in after
    assert "JSON Resume" in after


def test_details_draws_the_group_and_keeps_only_a_pointer_to_everything(client, user):
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    box = group(html, "details")
    assert 'popovertarget="export-details"' in box, "the record as JSON, and the card as a vCard"
    assert reverse("jobs:own_vcard") in html
    assert "data-candidate-file" not in html and "data-own-vcard" not in html
    assert f'href="{reverse("core:export")}"' in html


def test_the_calendar_download_carries_what_the_page_shows():
    from django.test import RequestFactory

    request = RequestFactory().get("/calendar/", {"view": "week", "kinds": "interview"})

    [export] = transfers.offered("calendar", "export")

    assert export.url(request).endswith("?view=week&kinds=interview")
