"""Next draws its chevron after the word, and both chevrons turn round right to left (#902)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

VIEWS = ("month", "week", "day", "agenda")


def step(client, which, **query):
    html = client.get(reverse("applications:calendar"), query).content.decode()
    return re.search(rf'<a[^>]*data-step="{which}"[^>]*>.*?</a>', html, re.S)[0]


@pytest.mark.parametrize("view", VIEWS)
def test_next_reads_the_word_then_the_chevron(client, user, view):
    client.force_login(user)

    button = step(client, "next", view=view)

    assert button.index(">Next</span>") < button.index('data-icon="chevron-right"')


@pytest.mark.parametrize("view", VIEWS)
def test_previous_still_reads_the_chevron_then_the_word(client, user, view):
    client.force_login(user)

    button = step(client, "previous", view=view)

    assert button.index('data-icon="chevron-left"') < button.index(">Previous</span>")


def test_both_chevrons_turn_round_on_a_right_to_left_page(client, user):
    client.force_login(user)

    for which in ("previous", "next"):
        assert "data-rtl-flip" in step(client, which), which


def test_the_stylesheet_turns_a_marked_chevron_round():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "src/postulo/static/css/app.css").read_text(
        encoding="utf-8"
    )
    assert re.search(r'\[dir="?rtl"?\]\s*\[data-rtl-flip\]\s*\{\s*transform:\s*scaleX\(-1\)', css)
