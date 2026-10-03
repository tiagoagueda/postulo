"""Two papercuts (#260): a table names itself, and a discard offers the way back."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_the_configurable_tables_carry_a_caption(client, user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    companies = client.get(reverse("jobs:company_list")).content.decode()
    assert '<caption class="sr-only">Companies</caption>' in companies
    table = companies[companies.index("<table") : companies.index("<caption")]
    assert "<thead" not in table and "<tr" not in table, "the caption is the table's first child"


def test_every_registered_table_has_a_label():
    from postulo.core import tables

    unnamed = [cls.name for cls in tables.TABLES.values() if not str(cls.label)]
    assert not unnamed, f"tables with no caption: {unnamed}"


def test_discarding_says_where_the_discarded_ones_are(client, user):
    from postulo.jobs.models import Capture, CaptureStatus

    capture = Capture.objects.create(
        owner=user,
        url="https://example.org/job",
        source_name="schema.org",
        data={"title": "Tester"},
    )
    client.force_login(user)

    response = client.post(reverse("jobs:capture_discard", args=[capture.pk]), follow=True)
    html = response.content.decode()
    where = reverse("jobs:capture_list") + "?show=discarded"

    capture.refresh_from_db()
    assert capture.status == CaptureStatus.DISCARDED
    assert f'Capture discarded. It is in <a href="{where}" class="underline">' in html
    assert "&lt;a" not in html, "the link survived the session storage"

    # The link goes somewhere (#380): the discarded capture, with the way to put it back.
    page = client.get(where)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Tester" in body
    assert reverse("jobs:capture_restore", args=[capture.pk]) in body


def test_a_discarded_capture_can_be_put_back(client, user, other_user):
    from postulo.jobs.capture_views import next_pending
    from postulo.jobs.models import Capture, CaptureStatus

    capture = Capture.objects.create(
        owner=user,
        url="https://example.org/job",
        data={"title": "Tester"},
        status=CaptureStatus.DISCARDED,
    )
    client.force_login(user)
    restore = reverse("jobs:capture_restore", args=[capture.pk])

    client.post(restore)
    capture.refresh_from_db()
    assert capture.status == CaptureStatus.PENDING
    assert next_pending(user) == capture

    # Not discarded any more: nothing to put back, and nothing changes.
    capture.status = CaptureStatus.ACCEPTED
    capture.save()
    client.post(restore)
    capture.refresh_from_db()
    assert capture.status == CaptureStatus.ACCEPTED

    stranger = Capture.objects.create(
        owner=other_user, url="https://example.org/x", status=CaptureStatus.DISCARDED
    )
    assert client.post(reverse("jobs:capture_restore", args=[stranger.pk])).status_code == 404
    stranger.refresh_from_db()
    assert stranger.status == CaptureStatus.DISCARDED


def test_discarding_several_says_so_too(client, user):
    from postulo.jobs.models import Capture

    kept = [
        Capture.objects.create(
            owner=user, url=f"https://example.org/{n}", source_name="x", data={"title": f"Job {n}"}
        )
        for n in range(2)
    ]
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_discard_selected"),
        {"selected": [c.pk for c in kept]},
        follow=True,
    )
    html = response.content.decode()
    assert "Captures discarded: 2. They are in <a href=" in html


def test_an_ordinary_message_is_still_escaped(client, user):
    """Only a message sent with the `safe` tag is rendered as markup."""
    from django.contrib.messages import constants
    from django.contrib.messages.storage.base import Message
    from django.template import engines

    shown = [
        Message(constants.INFO, "<b>plain</b>"),
        Message(constants.INFO, "<b>marked</b>", extra_tags="safe"),
    ]
    html = engines["django"].get_template("partials/messages.html").render({"messages": shown})
    assert "&lt;b&gt;plain&lt;/b&gt;" in html
    assert "<b>marked</b>" in html
    assert 'class="alert' in html and 'data-variant="info"' in html
