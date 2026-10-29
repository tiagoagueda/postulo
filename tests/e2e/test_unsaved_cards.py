"""On *Your details*, the card holding a change that has not been saved is marked (#927).

The page is one form of a dozen cards, and the guard of #258 knows only that the form holds
something unsaved. `app.js` marks the card: a soft amber ring (`data-card-unsaved`), "Not
saved" in its title, and a dot on its entry in the section menu. The ring and the words are
what a browser draws from a script's state, so they are looked at here; the template's half
is `tests/test_profile_sections.py`.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401

pytestmark = pytest.mark.e2e

PROFILE = "/accounts/profile/"


def card(page: Page, anchor: str):
    return page.locator(f"#{anchor}")


def badge(page: Page, anchor: str):
    return page.locator(f"#{anchor} [data-card-unsaved-badge]")


def dot(page: Page, anchor: str):
    return page.locator(f'[data-section-link="{anchor}"] [data-card-unsaved-dot]')


def save(page: Page) -> None:
    with page.expect_navigation():
        page.locator("form:has([data-cards]) button[type=submit]").click()


def expect_marked(page: Page, anchor: str) -> None:
    expect(card(page, anchor)).to_have_attribute("data-card-unsaved", "changed")
    expect(badge(page, anchor)).to_have_text("Not saved")
    expect(badge(page, anchor)).to_be_visible()
    expect(dot(page, anchor)).to_have_count(1)
    expect(dot(page, anchor).locator(".sr-only")).to_have_text("Not saved")


def expect_unmarked(page: Page, anchor: str) -> None:
    expect(card(page, anchor)).not_to_have_attribute("data-card-unsaved", "changed")
    expect(card(page, anchor)).not_to_have_attribute("data-card-unsaved", "refused")
    expect(badge(page, anchor)).to_have_count(0)
    expect(dot(page, anchor)).to_have_count(0)


def test_a_change_marks_its_card_and_undoing_it_takes_the_mark_off(
    live_server, page: Page, applicant
):
    sign_in(page, live_server.url, PROFILE)
    first_name = page.locator("input[name=first_name]")

    first_name.fill("Alexandra")

    expect_marked(page, "section-name")
    expect_unmarked(page, "section-contact")
    ring = card(page, "section-name").evaluate(
        "card => { const s = getComputedStyle(card); return [s.outlineStyle, s.outlineWidth]; }"
    )
    assert ring == ["solid", "2px"]
    # In the menu, the entry says so in words a screen reader reads with the section's name.
    entry = page.locator('[data-section-link="section-name"]')
    expect(entry).to_have_accessible_name("Your name Not saved")

    first_name.fill("Alex")

    expect_unmarked(page, "section-name")


def test_a_tick_box_and_a_menu_count_and_two_cards_are_marked_apart(
    live_server, page: Page, applicant
):
    sign_in(page, live_server.url, PROFILE)

    page.locator("input[name=use_gravatar]").click()
    page.locator("select[name=pronouns]").select_option("they")

    expect_marked(page, "section-picture")
    expect_marked(page, "section-name")
    expect_unmarked(page, "section-contact")

    page.locator("select[name=pronouns]").select_option("")
    expect_unmarked(page, "section-name")
    expect_marked(page, "section-picture")


def test_saving_takes_every_mark_off(live_server, page: Page, applicant):
    sign_in(page, live_server.url, PROFILE)
    page.locator("input[name=headline]").fill("Django developer")
    expect_marked(page, "section-contact")

    save(page)

    expect(page.locator("input[name=headline]")).to_have_value("Django developer")
    expect(page.locator("[data-card-unsaved]")).to_have_count(0)
    expect(page.locator("[data-card-unsaved-dot]")).to_have_count(0)


def test_a_refused_save_marks_the_cards_it_brought_back_changed(live_server, page: Page, applicant):
    """Drawn with what was typed, so the server names them, and they stay marked until saved."""
    sign_in(page, live_server.url, PROFILE)
    page.locator("input[name=headline]").fill("Django developer")
    page.locator("input[name=birth_day]").fill("30")
    page.locator("input[name=birth_month]").fill("2")
    page.locator("input[name=birth_year]").fill("1990")

    save(page)

    expect(page.locator("[data-invalid=true]").first).to_be_visible()
    for anchor in ("section-contact", "section-personal"):
        expect(card(page, anchor)).to_have_attribute("data-card-unsaved", "refused")
        expect(badge(page, anchor)).to_be_visible()
        expect(dot(page, anchor)).to_have_count(1)
    expect_unmarked(page, "section-name")
    # Typing it back to what the page holds does not make it saved: nothing was.
    page.locator("input[name=headline]").fill("")
    page.locator("input[name=headline]").fill("Django developer")
    expect(card(page, "section-contact")).to_have_attribute("data-card-unsaved", "refused")


def test_the_ring_is_still_drawn_in_forced_colours(live_server, page: Page, applicant):
    page.emulate_media(forced_colors="active")
    sign_in(page, live_server.url, PROFILE)
    page.locator("input[name=first_name]").fill("Alexandra")

    expect_marked(page, "section-name")
    style = card(page, "section-name").evaluate("card => getComputedStyle(card).outlineStyle")
    assert style == "solid"
    colour = dot(page, "section-name").evaluate("d => getComputedStyle(d).backgroundColor")
    assert colour not in ("rgba(0, 0, 0, 0)", "transparent")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_marked_card_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, PROFILE)
    page.locator("input[name=first_name]").fill("Alexandra")
    page.locator("input[name=headline]").fill("Django developer")
    expect_marked(page, "section-name")
    expect_marked(page, "section-contact")

    found = violations_on(page, axe_source)
    assert not found, describe(f"{PROFILE} with two cards not saved ({scheme})", found)
