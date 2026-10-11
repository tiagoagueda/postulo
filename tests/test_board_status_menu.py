"""A board card's status is changed from a pencil menu at its corner (#845)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.applications.models import Application, Status, status_options
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def card(client, user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    client.force_login(user)
    return application


def card_html(client) -> str:
    html = client.get(reverse("applications:list"), {"view": "board"}).content.decode()
    start = html.index("<article")
    return html[start : html.index("</article>", start)]


def test_the_card_has_a_pencil_menu_and_no_select(client, card):
    html = card_html(client)

    assert "<select" not in html and "<noscript>" not in html
    trigger = re.search(r"<button[^>]*popovertarget[^>]*>", html)[0]
    assert 'aria-label="Status of Research Engineer at Black Mesa"' in trigger
    assert 'aria-describedby="board-move-help"' in trigger
    assert 'data-icon="pencil"' in html
    assert "pe-8" in re.search(r'<a href="[^"]*"[^>]*class="([^"]*)"', html)[1], (
        "the title is padded clear of the corner"
    )


def test_it_offers_one_row_per_status_with_its_icon_and_the_current_one_ticked(client, card):
    html = card_html(client)

    rows = re.findall(r"<button[^>]*role=\"menuitemradio\"[^>]*>.*?</button>", html, re.S)
    assert [re.search(r'value="(\w+)"', row)[1] for row in rows] == [
        value for value, _label, _icon in status_options()
    ]
    for row, (value, label, icon) in zip(rows, status_options(), strict=True):
        assert f'data-icon="{icon}"' in row and str(label) in row
        assert 'name="status"' in row and 'type="submit"' in row
        current = value == Status.APPLIED
        assert (f'aria-checked="{"true" if current else "false"}"') in row
        assert ("disabled" in row) is current, "the status it is in is shown, not offered"


def test_the_form_posts_to_the_status_view_and_returns_to_the_board(client, card):
    html = card_html(client)

    form = re.search(r"<form[^>]*>.*?</form>", html, re.S)[0]
    assert f'action="{reverse("applications:status", args=[card.pk])}"' in form
    assert 'role="none"' in form and "csrfmiddlewaretoken" in form
    assert re.search(r'name="next" value="[^"]*view=board', form)


def test_pressing_a_row_moves_the_card_once(client, card):
    response = client.post(
        reverse("applications:status", args=[card.pk]),
        {"status": "screening", "next": reverse("applications:list") + "?view=board"},
    )

    assert response.status_code == 302
    card.refresh_from_db()
    assert card.status == "screening"


def test_the_help_describes_the_menu_not_a_select(client, card):
    html = client.get(reverse("applications:list"), {"view": "board"}).content.decode()

    assert "Open the menu and choose a status" in html
    assert "leave the menu" not in html, "the keyboard paragraph for the old select is gone"
