"""The block of messaging handles in a browser (#682).

What the server draws and checks is in `tests/test_messaging_handles.py`. What needs a
browser is what a reader meets: the row on one line at a desk and in a column on a phone, in
the order it is written; the name box there for *Other* and gone for a named service, with
scripts and without; a handle saved from the page and refused beside its box; and the block
passing axe in both themes (the walk in `test_accessibility.py` visits it with rows in it,
on *Your details* and on a contact's form).
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from playwright.sync_api import Browser, Page, expect

from .selects import DRAWN
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS, SPILLS
from .test_row_removal import sign_in

pytestmark = pytest.mark.e2e

BOXES = """(row) => {
  const box = (selector) => {
    const found = __DRAWN__(row.querySelector(selector));
    if (!found || !found.checkVisibility()) return null;
    const r = found.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
  };
  return {
    service: box('select'),
    name: box('input[name$="-label"]'),
    handle: box('input[name$="-handle"]'),
    primary: box('.primary-choice, input[type="radio"]'),
  };
}""".replace("__DRAWN__", DRAWN)


@pytest.fixture
def handles(applicant):
    from postulo.core.models import MessagingHandle

    return [
        MessagingHandle.objects.create(
            owner=applicant,
            holder=applicant.profile,
            service=service,
            label=label,
            handle=handle,
            is_primary=primary,
        )
        for service, label, handle, primary in (
            ("matrix", "", "@alex:example.org", True),
            ("signal", "", "alex.42", False),
            ("", "Our IRC", "alex on #aperture", False),
        )
    ]


def rows(page: Page):
    return page.locator("[data-messaging] ol > li")


def in_one_line(boxes: dict, *names: str) -> None:
    drawn = [boxes[name] for name in names]
    assert all(drawn), {name: boxes[name] for name in names}
    for before, after in pairwise(drawn):
        assert after["left"] >= before["right"] - 0.5, (names, boxes)
        assert after["top"] < before["bottom"] and before["top"] < after["bottom"], (names, boxes)


def in_a_column(boxes: dict, *names: str) -> None:
    drawn = [boxes[name] for name in names]
    assert all(drawn), {name: boxes[name] for name in names}
    for before, after in pairwise(drawn):
        assert after["top"] >= before["bottom"] - 0.5, (names, boxes)


def test_at_a_desk_a_row_is_one_line_service_first(page: Page, live_server, handles):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    on_a_service = rows(page).nth(0).evaluate(BOXES)
    assert on_a_service["name"] is None, "a named service needs no name"
    in_one_line(on_a_service, "service", "handle", "primary")
    in_one_line(rows(page).nth(2).evaluate(BOXES), "service", "name", "handle", "primary")


def test_on_a_phone_a_row_is_a_column_in_the_same_order_and_nothing_scrolls_sideways(
    page: Page, live_server, handles
):
    page.set_viewport_size({"width": 320, "height": 640})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    in_a_column(rows(page).nth(0).evaluate(BOXES), "service", "handle", "primary")
    in_a_column(rows(page).nth(2).evaluate(BOXES), "service", "name", "handle", "primary")
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls sideways at 320"


def test_the_order_read_is_the_order_tabbed(page: Page, live_server, handles):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    rows(page).nth(2).locator("select").focus()
    visited = []
    for _press in range(3):
        page.keyboard.press("Tab")
        visited.append(
            page.evaluate(
                "() => document.activeElement.name"
                " || document.activeElement.getAttribute('aria-label')"
            )
        )

    assert visited[0].endswith("-label") and visited[1].endswith("-handle"), visited
    assert visited[2].startswith("Remove "), visited


def test_a_handle_is_saved_from_the_page_and_a_wrong_one_is_refused_beside_its_box(
    page: Page, live_server, handles
):
    from postulo.core.models import MessagingHandle

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows(page).last
    new.locator("select").select_option("threema")
    new.locator("input[name$='-handle']").fill("ABCD123")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    refusal = page.get_by_text("does not look like a handle on Threema")
    expect(refusal).to_be_visible()
    assert MessagingHandle.objects.count() == 3

    page.locator("[data-messaging] ol > li").last.locator("input[name$='-handle']").fill("abcd1234")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    assert MessagingHandle.objects.filter(service="threema", handle="ABCD1234").exists()


def test_with_scripts_off_choosing_other_shows_the_name_and_saves_it(
    browser: Browser, live_server, handles
):
    """The stylesheet shows the box, not the script: `:has()` follows the select."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        new = rows(page).last
        name = new.locator('input[name$="-label"]')

        expect(name).to_be_hidden()
        new.locator("select").select_option("other")
        expect(name).to_be_visible()
        new.locator("select").select_option("telegram")
        expect(name).to_be_hidden()
        new.locator("select").select_option("other")
        name.fill("A wiki")
        new.locator("input[name$='-handle']").fill("alex@wiki")
        page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

        expect(page.get_by_text("Your details have been saved.")).to_be_visible()
        from postulo.core.models import MessagingHandle

        row = MessagingHandle.objects.get(handle="alex@wiki")
        assert (row.service, row.label) == ("", "A wiki")
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_block_has_no_violations_in_either_theme(
    page: Page,
    live_server,
    handles,
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    assert page.locator("#section-messaging").count() == 1

    found = violations_on(page, axe_source)

    assert not found, describe(f"your details with handles ({scheme})", found)
