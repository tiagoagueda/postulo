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


#: Classes that begin `tag-` and are not tones: the two that dress the picker on the tag
#: form (#285). Named here rather than matched around, so that a third one has to be
#: thought about instead of quietly slipping past the assertion below.
NOT_TONES = {"picker", "preview"}


def test_it_shows_the_whole_tag_palette(client, staff_user):
    """Every colour a tag can be, on one page, in both themes. Six of the seven were unspent
    until #285 gave applicants the palette; the gallery is still where they can be compared."""
    painted = set(re.findall(r"\.tag-([a-z]+)\s*\{", CSS)) - NOT_TONES

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


# --------------------------------------------------------------- the system's tokens


def test_every_rounding_comes_from_the_one_radius(client, staff_user):
    """There was no radius token at all, so seven rounding decisions disagreed because
    there was nothing to agree with (#292). The values are unchanged; the rule is new."""
    theme = CSS[CSS.index("@theme {") : CSS.index("@layer base")]

    assert "--radius: 0.5rem;" in theme
    for step in ("sm", "md", "lg", "xl"):
        assert f"--radius-{step}:" in theme
        derived = re.search(rf"--radius-{step}:\s*([^;]+);", theme).group(1)
        assert "var(--radius)" in derived, f"--radius-{step} is not derived from the base"

    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()
    for utility, _token in design.RADII:
        assert utility in html


def test_there_are_three_planes_and_each_has_a_border_as_well(client, staff_user):
    """A shadow is thrown away under forced colours, so it may say what is above what and
    may never be the only thing saying it."""
    theme = CSS[CSS.index("@theme {") : CSS.index("@layer base")]
    assert "--shadow-raised:" in theme and "--shadow-floating:" in theme

    # The definition, not the first text that looks like it: compact overrides `.card`
    # earlier in the file, and anchoring on `.card {` found that instead (#292).
    definition = re.search(r"\n  \.card \{(.+?)\n  \}", CSS, re.S)
    assert definition, "the card rule has moved"
    assert "shadow-raised" in definition.group(1), "a card is the raised plane"

    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()
    body = html[html.index("<main") : html.index("</main>")]
    for utility, _name, _note in design.PLANES:
        if utility:
            assert utility in body
    # Each sample carries a border beside its shadow, which is what survives forced colours.
    assert body.count("border border-ink-200 bg-white p-3") >= len(design.PLANES)


def test_the_masthead_is_raised_only_once_something_is_behind_it(client, staff_user):
    """With no script it keeps the border it always had, which is the whole fallback."""
    script = (Path(__file__).resolve().parents[1] / "src/postulo/static/js/app.js").read_text(
        "utf-8"
    )

    assert "[data-site-header][data-scrolled]" in CSS
    assert 'setAttribute("data-scrolled"' in script
    assert 'removeAttribute("data-scrolled")' in script
    assert "style.boxShadow" not in script, "the shadow is in the stylesheet, not written in"


def test_nothing_floats_by_writing_a_blur_radius_any_more(client, staff_user):
    """`shadow-lg` meant *a popover* only because somebody wrote `shadow-lg` twice."""
    from pathlib import Path as P

    root = P(__file__).resolve().parents[1]
    templates = list((root / "src/postulo/templates").rglob("*.html"))
    stray = [path.relative_to(root) for path in templates if "shadow-lg" in path.read_text("utf-8")]

    assert not stray, f"these still ask for a blur radius rather than a plane: {stray}"


# ------------------------------------------------------------- a list with nothing in it


def test_the_empty_state_is_a_component_rather_than_sixteen_copies(client, staff_user):
    """Two shapes, written by hand, on sixteen pages: an empty *Applications* and an empty
    *Tags* looked like pages from different applications (#292)."""
    from pathlib import Path as P

    root = P(__file__).resolve().parents[1]
    component = root / "src/postulo/templates/cotton/empty.html"
    assert component.exists()

    using = [
        path.relative_to(root)
        for path in (root / "src/postulo/templates").rglob("*.html")
        if "<c-empty" in path.read_text("utf-8")
    ]
    assert len(using) >= 6, f"only {len(using)} pages use it"


#: The five shapes "there is nothing here" used to be written in, by hand (#291).
HAND_WRITTEN_NOTHING = (
    "card py-12 text-center",
    'class="card text-sm text-ink-500 dark:text-ink-400"',
    "px-4 py-6 text-sm text-ink-500 dark:text-ink-400",
)


def test_no_template_writes_its_own_nothing(client, staff_user):
    """Forty-seven of them in five shapes, and every one is `<c-empty>` now (#291): the page's
    own, `variant="quiet"` for a widget's note, `variant="row"` for a list box with no rows.
    A conditional sentence goes in the slot, a second button in the footer slot, so nothing
    is left that the component cannot say. The board column's dashed "Empty" is not one of
    these: it is a drop target, and the dashes say where a card may be let go."""
    from pathlib import Path as P

    root = P(__file__).resolve().parents[1]
    by_hand = [
        (path.relative_to(root), shape)
        for path in (root / "src/postulo/templates").rglob("*.html")
        for shape in HAND_WRITTEN_NOTHING
        if path.name != "empty.html" and shape in path.read_text("utf-8")
    ]

    assert not by_hand, f"still written by hand: {by_hand}"


def test_the_gallery_shows_both_kinds_of_nothing(client, staff_user):
    client.force_login(staff_user)
    html = client.get(reverse("server:design")).content.decode()
    body = html[html.index("<main") : html.index("</main>")]

    assert "design-empty" in body
    assert body.count("empty gap-2") >= 3, "a page with nothing yet, a filter that missed, a note"
    assert 'data-variant="quiet"' in body, "and the quiet shape a widget leaves (#291)"


# ------------------------------------------------------------------ a page's own name


def test_a_page_title_is_written_one_way_now():
    """Six spellings of the same heading, because there was nothing to agree with (#292).

    The count is held rather than the absence: a handful of pages put a multi-line
    `blocktranslate` in the heading or space their subtitle differently, and those keep
    their own markup rather than being bent into the component. What they may not do is
    choose their own *size*, which is why the assertion below is about the token.
    """
    from pathlib import Path as P

    root = P(__file__).resolve().parents[1]
    templates = list((root / "src/postulo/templates").rglob("*.html"))

    using = [p for p in templates if "<c-page-title" in p.read_text("utf-8")]
    assert len(using) >= 50, f"only {len(using)} pages use the component"

    # Every page title asks for the token. The survivors are the report's statistics, which
    # are a different idea with a component of their own still to come.
    sizes = set()
    for path in templates:
        for match in re.finditer(r'class="([^"]*text-2xl[^"]*)"', path.read_text("utf-8")):
            sizes.add(match.group(1))
    assert sizes <= {"text-2xl font-semibold tabular-nums"}, (
        f"a page title still picks its own size: {sorted(sizes)}"
    )


def test_the_title_size_and_the_spacing_rhythm_are_named_once():
    theme = CSS[CSS.index("@theme {") : CSS.index("@layer base")]

    assert "--text-title:" in theme and "--text-title--line-height:" in theme
    assert "--spacing:" in theme, "the rhythm every margin is a multiple of"
