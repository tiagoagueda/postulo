"""A plugin's widget on the dashboard, in a real browser (#1102).

The dashboard's walks -- axe in both themes and right to left, 320 pixels, the size of what
can be pressed, arranging from the keyboard and by dragging -- taken with a widget a plugin
brought on the page beside Postulo's own. The plugin is `tests.widget_plugin`, a sync whose
widget is drawn once for each of two connections, so the card holds its sub-headings too.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.widget_plugin import registered

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)
from .test_reflow import NARROW, SCROLLS_SIDEWAYS, SPILLS
from .test_target_size import too_small
from .test_widget_drag import drag, order, rows

pytestmark = pytest.mark.e2e

KEY = "glance:at-a-glance"


@pytest.fixture
def with_the_widget(furnished):  # noqa: F811
    """Glance installed, two connections to it, and its widget second on the dashboard."""
    from postulo.plugins.models import Connection

    applicant = furnished["applicant"]
    for label in ("Home", "Work"):
        Connection.objects.create(
            owner=applicant,
            kind="sync",
            plugin="glance",
            label=label,
            config={"url": "https://dav.example.org"},
        )
    with registered():
        profile = applicant.profile
        keys = [key for key in profile.dashboard_widgets if key != KEY]
        profile.dashboard_widgets = [keys[0], KEY, *keys[1:]]
        profile.dashboard_known = sorted({*profile.dashboard_known, KEY})
        profile.save(update_fields=["dashboard_widgets", "dashboard_known"])
        yield furnished


def the_widget(page: Page):
    return page.locator(f'[data-widget="{KEY}"]')


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("arranging", [False, True])
def test_a_dashboard_with_a_plugins_widget_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    with_the_widget,
    scheme,
    arranging,
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/{'?arrange=1' if arranging else ''}")

    expect(the_widget(page)).to_have_count(1)
    expect(the_widget(page).get_by_role("heading", name="At a glance")).to_be_visible()
    expect(the_widget(page).get_by_text("Syncing with Work.")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/ with a plugin's widget ({scheme})", found)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_it_has_no_violations_right_to_left(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    with_the_widget,
    scheme,
):
    profile = with_the_widget["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/")

    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    expect(the_widget(page)).to_have_count(1)
    found = violations_on(page, axe_source)
    assert not found, describe(f"/ rtl with a plugin's widget ({scheme})", found)


def test_it_reflows_at_320_pixels(live_server, page: Page, with_the_widget):
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": NARROW, "height": 800})

    for path in ("/", "/?arrange=1"):
        page.goto(f"{base}{path}")
        expect(the_widget(page)).to_have_count(1)
        assert page.evaluate(SPILLS) == [], path
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], path


def test_what_it_offers_to_press_is_big_enough(live_server, page: Page, with_the_widget):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/")

    expect(the_widget(page).get_by_role("link", name="See them all").first).to_be_visible()
    assert too_small(page) == []


def test_it_is_arranged_from_the_keyboard_like_any_other(live_server, page: Page, with_the_widget):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/?arrange=1")

    take_off = page.get_by_role("button", name="Take At a glance off")
    take_off.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/?arrange=1")

    expect(page.get_by_role("button", name="Add At a glance")).to_be_visible()
    page.goto(f"{base}/")
    expect(the_widget(page)).to_have_count(0)


def test_it_can_be_dragged_like_any_other(live_server, page: Page, with_the_widget):
    applicant = with_the_widget["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/?arrange=1")
    before = order(applicant)
    assert before[1] == KEY

    with page.expect_navigation():
        drag(page, rows(page).nth(1).element_handle(), rows(page).nth(3).element_handle())

    after = order(applicant)
    assert after.index(KEY) == 3, "the plugin's widget moved where it was dropped"
    assert sorted(after) == sorted(before)
