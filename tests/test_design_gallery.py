"""The gallery: one page showing every piece the interface is made of (#292).

A component seen only inside a feature is a component nobody reviews, and there was nowhere
to look at a change to the paint before it was spread across 184 templates. What is checked
here is that the page exists, that it is the administrator's and not everybody's, and that
it is drawn from the stylesheet's own vocabulary rather than from a copy that can go stale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.core import design

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="admin@example.org",
        password="a-fairly-long-password-42",
        username="admin-one",
        is_staff=True,
        is_superuser=True,
    )


CSS = (Path(__file__).resolve().parents[1] / "assets" / "css" / "app.css").read_text("utf-8")
BASECOAT = (Path(__file__).resolve().parents[1] / "assets" / "css" / "basecoat.css").read_text(
    "utf-8"
)


def test_the_gallery_is_the_administrators_and_not_everybodys(client, user, staff_user):
    """It is a reference for whoever changes the interface, not a page in anybody's way."""
    client.force_login(user)
    assert client.get(reverse("server:design")).status_code in (302, 403, 404)

    client.force_login(staff_user)
    assert client.get(reverse("server:design")).status_code == 200


def test_it_shows_every_button_the_stylesheet_paints(client, staff_user):
    """The variants are data, so the page and the stylesheet cannot drift apart."""
    painted = set(re.findall(r'\.btn\[data-variant="([a-z-]+)"\]', BASECOAT))
    declared = {variant for variant, _label in design.BUTTON_VARIANTS if variant}

    assert painted == declared | {"primary"}, (
        "the gallery names every variant but `primary`, which is what a bare `.btn` is"
    )

    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()
    for variant in declared:
        assert f'data-variant="{variant}"' in html


def test_it_shows_every_size_the_stylesheet_paints(client, staff_user):
    painted = set(re.findall(r'\.btn\[data-size="([a-z-]+)"\]', BASECOAT))
    declared = {size for size, _label in design.BUTTON_SIZES if size}

    # `icon`, `icon-sm` and `icon-xs` are painted as well; the page draws one of them beside
    # the text sizes rather than all three, which the assertion says out loud.
    assert declared <= painted
    assert painted - declared == {"icon", "icon-sm", "icon-xs"}


def test_it_shows_the_whole_tag_palette_including_what_nothing_spends_yet(client, staff_user):
    """Six of the seven are dead code until #285. The gallery is where they can be seen."""
    painted = set(re.findall(r"\.tag-([a-z]+)\s*\{", CSS))

    assert set(design.TAGS) == painted

    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()
    for tag in design.TAGS:
        assert f"tag-{tag}" in html


def test_it_shows_both_colour_scales_whole(client, staff_user):
    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()

    for step in design.INK_STEPS:
        assert f"bg-ink-{step}" in html
    for step in design.BRAND_STEPS:
        assert f"bg-brand-{step}" in html


def test_the_scales_are_the_ones_the_theme_defines(client, staff_user):
    """A swatch for a token that does not exist is a swatch of nothing."""
    theme = CSS[CSS.index("@theme {") : CSS.index("@layer base")]
    for step in design.INK_STEPS:
        assert f"--color-ink-{step}:" in theme
    for step in design.BRAND_STEPS:
        assert f"--color-brand-{step}:" in theme
    for swatch in design.SEMANTIC:
        assert f"--color-{swatch.token}:" in theme


def test_nothing_on_it_does_anything(client, staff_user):
    """A gallery with a live control is a page somebody changes something from by accident.

    Scoped to `<main>`: the page chrome around it carries the theme switch, which is a real
    form on every page and is not what this is about.
    """
    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()
    body = html[html.index("<main") : html.index("</main>")]

    assert "<form" not in body
    assert "csrfmiddlewaretoken" not in body
    assert "<button" in body, "and yet the buttons are real ones, which is the point"


def test_it_is_in_the_sidebar_so_it_can_be_found(client, staff_user):
    from postulo.core.server_sections import SECTIONS

    assert any(section.slug == "design" for section in SECTIONS)

    client.force_login(staff_user)
    html = client.get(reverse("server:overview")).content.decode()
    assert reverse("server:design") in html
