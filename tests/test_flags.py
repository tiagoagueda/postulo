"""Country flags: an image, because the emoji was not one on Windows.

A flag emoji is not a character. It is two *regional indicator* code points -- U+1F1F5 and
U+1F1F9 for Portugal -- and a font is invited, never required, to draw the pair as a flag.
Segoe UI Emoji has never accepted the invitation, so Windows draws exactly what the code
points say: `PT`. Both places Postulo used flags carried a comment predicting this and
calling it a legible fallback; it was reported as a bug by the maintainer, on Windows,
which is the answer to whether it reads as a fallback (#88).

What these check is the contract the rest of the interface leans on: that the file exists
for every country the telephone field can offer, that the tag refuses anything that is not a
country, and that a language with no flag renders as nothing rather than as a broken image.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template
from django.urls import reverse

from postulo.core import languages, phones
from postulo.core.templatetags import postulo as tags

REPO = Path(__file__).resolve().parents[1]
LIST = REPO / "assets" / "flags.txt"


def listed() -> list[str]:
    return [
        line.replace("#", " ").split()[0]
        for line in LIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def render(template: str, **context) -> str:
    return Template("{% load postulo %}" + template).render(Context(context))


# ------------------------------------------------------------------- the set


def test_the_list_holds_every_country_the_telephone_field_offers():
    """Any country in the list can be chosen, and the chosen one's flag is shown.

    A country added to `phones.COUNTRIES` without running `npm run sync:flags` would be a
    country whose flag silently does not appear, which is the sort of thing nobody notices
    until it is somebody's own.
    """
    assert set(listed()) == {code.lower() for code, _, _ in phones.COUNTRIES}


def test_every_listed_flag_is_actually_there():
    missing = [code for code in listed() if not (tags.FLAG_DIR / f"{code}.svg").is_file()]
    assert not missing, f"run `npm run sync:flags`: {missing}"


def test_nothing_is_there_that_is_not_listed():
    """The committed set is the list and nothing else, exactly as with the icons."""
    keep = {f"{code}.svg" for code in listed()} | {"LICENSE.txt"}
    extra = sorted(path.name for path in tags.FLAG_DIR.iterdir() if path.name not in keep)
    assert not extra, f"not in {LIST.name}: {extra}"


def test_the_licence_travels_with_the_artwork():
    """flag-icons is MIT, which asks that the notice go wherever the files go."""
    notice = (tags.FLAG_DIR / "LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in notice
    assert "Panayiotis Lipiridis" in notice


def test_every_language_with_a_flag_has_one_to_show():
    """`FLAG_COUNTRIES` names a country; that country has to be one Postulo ships."""
    for code, country in languages.FLAG_COUNTRIES.items():
        assert country.lower() in listed(), f"{code} points at {country}"


def test_the_two_maps_never_contradict_each_other():
    """The flag beside a language and the country its telephone field starts on are two
    different questions, and both are answered by hand.

    They may legitimately differ -- a language with no uncontested home gets no flag while
    the telephone field still has to start somewhere -- but where both have an opinion they
    must agree, or the same page says two things about one language. This is what the emoji
    map used to duplicate, in a codebase whose own telephone field refuses to store a
    country column for exactly that reason.
    """
    for code, country in languages.FLAG_COUNTRIES.items():
        other = phones.FROM_LANGUAGE.get(code)
        assert other in (None, country), f"{code}: flag says {country}, telephone says {other}"


# ------------------------------------------------------------------- the tag


def test_the_tag_draws_a_flag():
    html = render('{% flag "pt" %}')
    assert 'class="flag"' in html
    assert 'data-flag="pt"' in html
    assert "/flags/pt" in html and html.endswith(">")
    # Stated, so the row does not jump when the image lands.
    assert 'width="20"' in html and 'height="15"' in html


def test_the_tag_takes_a_country_however_it_is_written():
    assert render('{% flag "PT" %}') == render('{% flag " pt " %}') == render('{% flag "pt" %}')


def test_a_flag_is_decoration_unless_it_is_given_words():
    """Beside a name somebody can already read, a flag adds nothing a screen reader should
    repeat -- "Portugal flag, portugues (Portugal)" is worse than silence."""
    assert 'alt="" aria-hidden="true"' in render('{% flag "pt" %}')

    alone = render('{% flag "pt" label="Portugal" %}')
    assert 'alt="Portugal"' in alone
    assert "aria-hidden" not in alone


@pytest.mark.parametrize(
    "country", ["", "   ", "x", "xyz", "zz", "../icons/mail", "p t", "1a", None]
)
def test_nothing_is_drawn_for_anything_that_is_not_a_country(country):
    """Empty is a real answer: a language with no uncontested home gets no flag, and the
    row simply closes up. Nothing here may become a path, either -- an unrecognised code
    can arrive from a form field, and under the manifest storage production uses, asking
    for a file that was never collected raises rather than returning a dead link."""
    assert render("{% flag country %}", country=country) == ""
    assert tags.flag_url(country) == ""


def test_the_url_is_the_one_the_static_machinery_gives():
    """Never a string built by hand: files are served under a content hash, and a URL
    assembled from a pattern would be right in development and wrong in production."""
    from django.templatetags.static import static

    assert tags.flag_url("pt") == static("flags/pt.svg")


# ------------------------------------------------------- where they are shown


@pytest.mark.django_db
def test_the_telephone_field_shows_the_chosen_country_without_a_script(client, user):
    """An `<option>` holds text and nothing else, so the flag sits over the closed select.

    The server draws it, and the script only keeps it in step: with JavaScript blocked the
    field still shows the flag of the country it loaded with, which is the true answer
    until the form is saved.
    """
    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    client.force_login(user)

    html = client.get(reverse("jobs:contact_create")).content.decode()

    holder = re.search(r"<span[^>]*data-phone-flag[^>]*>(.*?)</span>", html, re.S)
    assert holder, "no flag beside the country chooser"
    assert 'data-flag="pt"' in holder.group(1)


@pytest.mark.django_db
def test_every_country_option_carries_its_own_flag_for_the_script(client, user):
    """There is no pattern a script could build a URL from -- each file has its own content
    hash -- so each option carries the answer."""
    client.force_login(user)
    html = client.get(reverse("jobs:contact_create")).content.decode()

    options = re.findall(r'<option value="([A-Z]{2})" data-flag="([^"]*)"', html)
    assert len(options) == len(phones.COUNTRIES)
    for code, url in options:
        assert url.endswith(".svg"), f"{code} has no flag"
        assert f"/flags/{code.lower()}" in url


@pytest.mark.django_db
def test_no_option_label_carries_a_flag_any_more(client, user):
    """It could never have been an image, and on Windows it was two letters."""
    client.force_login(user)
    html = client.get(reverse("jobs:contact_create")).content.decode()

    labels = re.findall(r"<option [^>]*>([^<]*)</option>", html)
    assert labels, "no options at all"
    indicators = [label for label in labels if any(ch in label for ch in "\U0001f1e6\U0001f1ff")]
    assert not indicators, indicators


def test_the_stylesheet_keeps_a_white_edged_flag_from_disappearing():
    """Poland, Japan, Finland and half of Austria reach their own edge in white, and a white
    card behind them takes that edge away. An outline rather than an inset shadow, because a
    replaced element paints its image over an inset shadow and under nothing at all."""
    css = (REPO / "src" / "postulo" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    rule = re.search(r"\.flag\{[^}]*\}", css)
    assert rule, "no .flag rule in the compiled stylesheet"
    assert "outline" in rule.group(0)
    assert re.search(r"\.flag:where\(\[data-theme=dark\][^)]*\)\{[^}]*outline-color", css), (
        "the outline does not change in the dark theme"
    )
