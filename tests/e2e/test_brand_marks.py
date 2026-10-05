"""The brand marks in a browser: beside a contact's links (#654).

What the tag and the sync script write, and the colours held to 3:1, are in
`tests/test_brands.py`. What needs a browser is what a reader meets: a mark drawn, visible,
beside the thing it stands for; decorative, so a screen reader hears the link and not a
picture; in the colour its mode records, which is the page's black or white for a
one-colour mark and the owner's own for a brand one, in each theme; passing axe in both
themes; and whole at 320 pixels and under the text-spacing override.

The company page is where the marks are drawn, beside a contact's links.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e

#: Each mark on the page: its slug, its mode, and the colour each theme draws it in.
LIGHT, DARK = "rgb(0, 0, 0)", "rgb(255, 255, 255)"
MARKS = {
    # One colour, where the owner's guidelines allow it: black on the light page and white
    # on the dark one, whichever ink the words beside it are in.
    "github": ("single-colour", {"light": LIGHT, "dark": DARK}),
    "forgejo": ("single-colour", {"light": LIGHT, "dark": DARK}),
    # The owner's published colour, in both themes.
    "mastodon": ("brand", {"light": "rgb(99, 100, 255)", "dark": "rgb(99, 100, 255)"}),
}


@pytest.fixture
def company(applicant):
    """A company with two identifiers (neither has a mark) and contacts whose
    links are on services that have a mark, in either mode, and on ones that have none."""
    from postulo.core.models import WebLink
    from postulo.jobs.models import Company, CompanyIdentifier, Contact

    acme = Company.objects.create(owner=applicant, name="Aperture")
    CompanyIdentifier.objects.create(owner=applicant, company=acme, scheme="wikidata", value="Q95")
    CompanyIdentifier.objects.create(
        owner=applicant, company=acme, scheme="lei", value="HWUPKR0MPOU8FGXBT394"
    )
    for name, links in (
        (
            "Cave Johnson",
            [
                ("repository", "github", "https://github.com/cave/thing"),
                ("social", "mastodon", "https://fosstodon.org/@cave"),
            ],
        ),
        ("Chell", [("repository", "forgejo", "https://git.example.org/chell/thing")]),
        ("Glados", [("social", "x", "https://x.com/glados")]),
    ):
        contact = Contact.objects.create(owner=applicant, company=acme, name=name)
        for kind, service, url in links:
            WebLink.objects.create(
                owner=applicant,
                holder=contact,
                kind=kind,
                service=service,
                url=url,
                is_primary=True,
            )
    return acme


def open_company(page: Page, live_server, company, scheme: str = "light") -> None:
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")


def mark(page: Page, slug: str):
    return page.locator(f'svg[data-brand="{slug}"]')


#: What the page's box for a mark says: where it is, how big, what colour, and what a
#: screen reader is told of it.
FACTS = """(svg) => {
  const box = svg.getBoundingClientRect();
  const style = getComputedStyle(svg);
  // The link's first line box: a long address may wrap, and its start is what sits by the mark.
  const beside = svg.nextElementSibling || svg.parentElement.querySelector('a');
  const words = svg.closest('dt, p');
  return {
    mode: svg.dataset.brandMode,
    fill: style.fill,
    colour: style.color,
    width: box.width,
    height: box.height,
    visible: svg.checkVisibility() && style.visibility === 'visible' && box.width > 0,
    hidden: svg.getAttribute('aria-hidden'),
    named: ['role', 'aria-label', 'aria-labelledby'].some((name) => svg.hasAttribute(name))
      || !!svg.querySelector('title'),
    focusable: svg.getAttribute('focusable') || svg.hasAttribute('tabindex'),
    left: box.left, right: box.right, top: box.top, bottom: box.bottom,
    words: words ? words.getBoundingClientRect().toJSON() : null,
    text: words ? words.textContent.replace(/\\s+/g, ' ').trim() : '',
    beside: beside && beside.matches('a') ? beside.getClientRects()[0].toJSON() : null,
  };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("slug", sorted(MARKS))
def test_a_mark_is_drawn_in_the_colour_its_mode_records(
    page: Page, live_server, company, slug, scheme
):
    open_company(page, live_server, company, scheme)
    svg = mark(page, slug)
    expect(svg).to_have_count(1)
    expect(svg).to_be_visible()

    facts = svg.evaluate(FACTS)

    mode, colours = MARKS[slug]
    assert facts["mode"] == mode
    assert facts["fill"] == colours[scheme], facts
    assert facts["width"] >= 12 and facts["height"] >= 12, "a mark that can be seen"


@pytest.mark.parametrize("slug", sorted(MARKS))
def test_a_mark_is_decorative_and_the_words_beside_it_name_the_thing(
    page: Page, live_server, company, slug
):
    open_company(page, live_server, company)
    facts = mark(page, slug).evaluate(FACTS)

    assert facts["hidden"] == "true", "a screen reader hears the link, not a picture"
    assert not facts["named"], "no role, label or title: no stray accessible name"
    assert not facts["focusable"]
    assert facts["text"], "and there are words beside it for it to be decorative of"


@pytest.mark.parametrize("slug", ["github", "forgejo", "mastodon"])
def test_a_link_mark_sits_just_before_the_link_it_stands_for(
    page: Page, live_server, company, slug
):
    open_company(page, live_server, company)
    facts = mark(page, slug).evaluate(FACTS)
    link = facts["beside"]
    assert link is not None, "the mark is followed by its link"
    assert facts["right"] <= link["left"] + 1, "before it, in reading order"
    assert link["left"] - facts["right"] < 12, "and next to it"
    assert facts["top"] < link["bottom"] and facts["bottom"] > link["top"], "on the same line"


def test_the_identifier_lines_draw_no_mark_for_schemes_that_have_none(
    page: Page, live_server, company
):
    """Wikidata has none (its official logo is three coloured bars, and Simple Icons' flat
    version would be a recolouring) and an LEI none; ORCID's mark has no read-only place
    that shows it yet, which tests/test_identifiers.py holds on the page with a double."""
    open_company(page, live_server, company)
    entries = page.locator("[data-identifiers] > div")
    expect(entries).to_have_count(2)
    expect(page.locator("[data-identifiers] svg")).to_have_count(0)
    expect(entries.filter(has_text="Wikidata").locator("dt")).to_have_text("Wikidata")


def test_a_service_without_a_mark_draws_none_and_the_page_has_only_these(
    page: Page, live_server, company
):
    open_company(page, live_server, company)
    assert page.locator("svg[data-brand]").evaluate_all(
        "(all) => all.map((svg) => svg.dataset.brand).sort()"
    ) == ["forgejo", "github", "mastodon"]
    expect(page.locator('main a[href="https://x.com/glados"]')).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_with_marks_has_no_violations(
    page: Page,
    live_server,
    company,
    axe_source,  # noqa: F811
    scheme,
):
    open_company(page, live_server, company, scheme)
    expect(mark(page, "github")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"the company page with its marks ({scheme})", found)


def test_at_320_pixels_the_marks_are_whole_and_nothing_scrolls_sideways(
    page: Page, live_server, company
):
    page.set_viewport_size({"width": 320, "height": 640})
    open_company(page, live_server, company)
    for slug in MARKS:
        expect(mark(page, slug)).to_be_visible()
        facts = mark(page, slug).evaluate(FACTS)
        assert 0 <= facts["left"] and facts["right"] <= 320, facts
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_under_the_text_spacing_override_the_marks_stay_beside_their_words(
    page: Page, live_server, company
):
    from .test_text_spacing import ADOPT, TEXT_SPACING, ZOOMED, lost_on

    page.set_viewport_size({"width": ZOOMED, "height": 800})
    open_company(page, live_server, company)
    page.evaluate(ADOPT, TEXT_SPACING)
    for slug in MARKS:
        expect(mark(page, slug)).to_be_visible()
    for slug in ("github", "mastodon"):
        facts = mark(page, slug).evaluate(FACTS)
        assert facts["beside"] is not None
        assert facts["right"] <= facts["beside"]["left"] + 1
    assert lost_on(page) == []
