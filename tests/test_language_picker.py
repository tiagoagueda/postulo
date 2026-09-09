"""Choosing a language: one control closed, and everything a dropdown could not say (#119).

> the drop menu should contain the flag the language name and a symbol indicating if the
> translations was done using LLM / draft

An `<option>` holds text and nothing else. It takes `lang` on itself, so a `<select>` can
say *this option is Greek* and that is all — a flag inside the text is read out beside the
name, a symbol inside it is announced as part of a string claiming to be Greek, and there is
nowhere for a percentage at all. So the request's *shape* is answered by a disclosure, and
everything about the state of a translation lives outside the span marked as being in that
language.

What is asserted here is the part a browser test cannot see: which element carries `lang`,
what is hidden from a screen reader and what is read out, and that a symbol is never the
only thing that says something. `tests/e2e/` walks it with a keyboard and axe.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def page(client, user):
    client.force_login(user)
    return client.get(reverse("settings:locale")).content.decode()


def between(html: str, start: str, end: str) -> str:
    return html.split(start)[1].split(end)[0]


def picker(html: str) -> str:
    """The disclosure itself. The page has other `<details>` — the header menu is one."""
    return "data-language-picker" + between(html, "data-language-picker", "</details>")


# ------------------------------------------------------------------ the shape


def test_the_languages_are_behind_one_control_rather_than_a_wall_of_rows(page):
    """The part of the request that was really about shape: thirty-nine rows beside a time
    zone field that is one line, and it will be more than thirty-nine.
    """
    opening = page.split("data-language-picker")[0].rsplit("<details", 1)[1]

    assert "data-language-picker" in page, "there is one control"
    assert "<summary" in picker(page), "and it opens"
    assert "data-menu" in opening, "sharing the disclosure the account menu uses"


def test_it_opens_and_submits_with_no_script_at_all(page):
    """`<details>` opens itself and radios submit themselves. Everything in `app.js` for
    this control gives back keyboard behaviour a native dropdown would have given free —
    none of it is required for somebody to choose a language.
    """
    panel = picker(page)

    assert 'type="radio"' in panel
    assert 'name="language"' in panel
    assert "onclick" not in panel and "onchange" not in panel


def test_the_closed_control_says_what_is_in_use(client, user):
    """Or the setting page has stopped answering "what am I using?" without a click."""
    user.profile.language = "de"
    user.profile.save()
    client.force_login(user)

    html = client.get(reverse("settings:locale")).content.decode()
    summary = between(picker(html), "<summary", "</summary>")

    assert 'lang="de"' in summary
    assert "Deutsch" in summary


def test_the_control_is_not_open_by_default(page):
    """Closed is the whole point: a wall of rows was what it replaced."""
    opening = page.split("data-language-picker")[0].rsplit("<details", 1)[1]

    assert " open" not in opening


# -------------------------------------------------- what a dropdown could not do


def test_each_name_carries_its_own_language_and_nothing_else_does(page):
    """WCAG 3.1.2. A screen reader pronounces Ελληνικά with Greek rules or it says nothing
    a Greek speaker would recognise — and everything *about* the translation is in the
    interface language, so it must sit outside that span.
    """
    marked = re.findall(r'<span lang="([a-z-]+)">([^<]*)</span>', page)

    assert ("el", "Ελληνικά") in marked
    for _code, content in marked:
        assert "%" not in content, "a percentage is not in that language"
        assert "speaker" not in content.lower(), "nor is a phrase about the translation"


def test_the_flag_is_decoration_and_is_not_read_out(page):
    """ "Greek flag, Ελληνικά" is worse than silence: the name already says which it is."""
    flags = re.findall(r"<img[^>]*flags[^>]*>", page)

    assert flags
    for flag in flags:
        assert 'alt=""' in flag
        assert 'aria-hidden="true"' in flag


def test_the_percentage_is_out_of_the_name_now(client, user, monkeypatch):
    """A dropdown had nowhere else to put it, so it was appended to the language's name —
    inside the span marked as being in that language. Here there is somewhere else.
    """
    from postulo.core import languages

    monkeypatch.setattr(
        languages,
        "translation_status",
        lambda: {"de": {"total": 100, "translated": 60, "drafts": 0, "percent": 60}},
    )
    client.force_login(user)

    html = client.get(reverse("settings:locale")).content.decode()

    assert '<span lang="de">Deutsch</span>' in html, "the name alone"
    assert "60%" in html, "and the figure beside it"


# ---------------------------------------------- the symbol is never alone


@pytest.mark.parametrize(
    "state",
    ["reviewed", "draft", "partial"],
)
def test_every_symbol_has_words_that_are_read_out(client, user, state, monkeypatch):
    """A glyph alone is a 1.3.3 failure for anybody who cannot see it and a guess for
    anybody who has not learnt it.
    """
    from postulo.core import languages

    rows = {
        "reviewed": {"total": 100, "translated": 100, "drafts": 0, "percent": 100},
        "draft": {"total": 100, "translated": 100, "drafts": 100, "percent": 100},
        "partial": {"total": 100, "translated": 60, "drafts": 0, "percent": 60},
    }
    monkeypatch.setattr(languages, "translation_status", lambda: {"de": rows[state]})
    client.force_login(user)

    html = client.get(reverse("settings:locale")).content.decode()
    row = between(html, '<span lang="de">Deutsch</span>', "</label>")

    assert 'aria-hidden="true"' in row, "the glyph itself is not announced"
    assert 'class="sr-only"' in row, "and something in words is"


def test_the_legend_is_on_the_page_and_not_only_in_a_tooltip(page):
    """Somebody who has not learnt the symbols has to be able to find out what they mean
    without hovering, which is not a thing a keyboard or a touchscreen does.
    """
    assert "read by a speaker" in page
    assert "written, not yet read by a speaker" in page
    assert "still being translated" in page


def test_the_words_do_not_say_machine_translation(page):
    """`pt-br` was seeded from `pt-pt` and adapted by hand, which is not machine
    translation. What is true of every language in that group is that no speaker has read
    it, and that is the thing to say.
    """
    lowered = page.lower()

    assert "llm" not in lowered
    assert "machine" not in lowered


# ------------------------------------------------------------------ and it still saves


def test_choosing_one_still_saves(client, user):
    client.force_login(user)

    client.post(
        reverse("settings:locale"), {"language": "de", "time_zone": "Europe/Lisbon"}, follow=True
    )
    user.profile.refresh_from_db()

    assert user.profile.language == "de"
