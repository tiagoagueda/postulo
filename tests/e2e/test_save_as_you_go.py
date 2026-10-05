"""A Settings field saved as it is changed, in a real browser (#656).

The server's half is `tests/test_save_as_you_go.py`. What needs a browser is what the page
does about it: that a change saves without a reload and says so in the live region, that a
refusal is under its field and nothing is stored, that the unsaved-work guard follows what
was saved, and that with scripts off the form is the form it always was.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


def profile_of(applicant, **fields):
    from postulo.accounts.models import Profile

    profile, _created = Profile.objects.get_or_create(user=applicant)
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.save()
    return profile


def stored(applicant, name):
    from postulo.accounts.models import Profile

    return getattr(Profile.objects.get(user=applicant), name)


def open_appearance(page: Page, live_server, applicant, *, on: bool = True, **fields):
    profile_of(applicant, save_as_you_go=on, **fields)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/appearance/")


def asked_to_leave(page: Page) -> bool:
    """Whether the page would ask before it is left: what `beforeunload` would be told."""
    return page.evaluate(
        """() => {
            const event = new Event("beforeunload", { cancelable: true });
            window.dispatchEvent(event);
            return event.defaultPrevented;
        }"""
    )


def test_a_change_is_saved_without_a_reload_and_says_so(page: Page, live_server, applicant):
    open_appearance(page, live_server, applicant)
    page.evaluate("window.stayed = true")

    page.get_by_label("Compact").check()

    expect(page.locator("[data-save-status]")).to_contain_text("Saved at")
    assert stored(applicant, "density") == "compact"
    assert page.evaluate("window.stayed") is True


def test_the_status_is_a_live_region_that_is_there_from_the_start(
    page: Page, live_server, applicant
):
    open_appearance(page, live_server, applicant)

    status = page.locator("[data-save-status]")

    expect(status).to_have_attribute("role", "status")
    expect(status).to_be_empty()


def test_a_number_is_saved_when_it_is_left_and_not_while_it_is_typed(
    page: Page, live_server, applicant
):
    open_appearance(page, live_server, applicant, quiet_after_days=14)
    box = page.get_by_label("Consider an application quiet after")

    box.fill("21")
    assert stored(applicant, "quiet_after_days") == 14
    box.press("Tab")

    expect(page.locator("[data-save-status]")).to_contain_text("Saved at")
    assert stored(applicant, "quiet_after_days") == 21


def test_a_refused_value_says_why_under_its_field_and_is_not_stored(
    page: Page, live_server, applicant
):
    open_appearance(page, live_server, applicant, closing_notice_days=7)
    box = page.get_by_label("Warn me before a listing closes by")

    box.fill("-4")
    box.press("Tab")

    expect(page.locator("[data-save-status]")).to_have_text("Not saved")
    refusal = page.locator("#id_closing_notice_days_error")
    expect(refusal).to_be_visible()
    expect(refusal).to_have_attribute("role", "alert")
    expect(box).to_have_attribute("aria-invalid", "true")
    assert stored(applicant, "closing_notice_days") == 7


def test_a_corrected_value_takes_the_refusal_away(page: Page, live_server, applicant):
    open_appearance(page, live_server, applicant, closing_notice_days=7)
    box = page.get_by_label("Warn me before a listing closes by")
    box.fill("-4")
    box.press("Tab")
    expect(page.locator("#id_closing_notice_days_error")).to_be_visible()

    box.fill("9")
    box.press("Tab")

    expect(page.locator("[data-save-status]")).to_contain_text("Saved at")
    expect(page.locator("#id_closing_notice_days_error")).to_have_count(0)
    assert stored(applicant, "closing_notice_days") == 9


def test_a_stale_page_is_refused_with_the_cells_sentence(page: Page, live_server, applicant):
    from postulo.accounts.models import Profile

    open_appearance(page, live_server, applicant)
    Profile.objects.filter(user=applicant).update(
        updated_at=timezone.now() + dt.timedelta(minutes=5), theme="dark"
    )

    page.get_by_label("Light", exact=True).click()

    refusal = page.locator("#id_theme_error")
    expect(refusal).to_contain_text("Somebody else changed this while you were editing")
    expect(page.get_by_label("Dark", exact=True)).to_be_checked()
    assert stored(applicant, "theme") == "dark"


def test_with_the_setting_off_nothing_is_saved_until_save(page: Page, live_server, applicant):
    open_appearance(page, live_server, applicant, on=False)

    page.get_by_label("Compact").check()
    page.wait_for_timeout(500)

    assert stored(applicant, "density") == "comfortable"
    expect(page.locator("[data-save-status]")).to_have_count(0)
    page.get_by_role("button", name="Save", exact=True).last.click()
    expect(page).to_have_url(f"{live_server.url}/settings/appearance/")
    assert stored(applicant, "density") == "compact"


def test_the_guard_does_not_ask_after_a_save(page: Page, live_server, applicant):
    open_appearance(page, live_server, applicant, quiet_after_days=14)
    box = page.get_by_label("Consider an application quiet after")

    box.fill("21")
    assert asked_to_leave(page) is True
    box.press("Tab")
    expect(page.locator("[data-save-status]")).to_contain_text("Saved at")

    assert asked_to_leave(page) is False


def test_the_guard_asks_while_a_save_is_refused(page: Page, live_server, applicant):
    open_appearance(page, live_server, applicant, closing_notice_days=7)
    box = page.get_by_label("Warn me before a listing closes by")

    box.fill("-4")
    box.press("Tab")
    expect(page.locator("[data-save-status]")).to_have_text("Not saved")

    assert asked_to_leave(page) is True


def test_the_guard_still_asks_for_what_only_save_can_save(page: Page, live_server, applicant):
    """A switch in the navigation list is saved with the whole list, by *Save*, so saving
    another field does not forgive it."""
    open_appearance(page, live_server, applicant)
    page.get_by_label("Compact").check()
    expect(page.locator("[data-save-status]")).to_contain_text("Saved at")
    assert asked_to_leave(page) is False

    page.locator("input[name=navigation]").first.uncheck()
    page.get_by_label("Comfortable").check()
    expect(page.locator("[data-save-status]")).to_contain_text("Saved at")

    assert asked_to_leave(page) is True


def test_the_navigation_is_not_saved_as_it_goes(page: Page, live_server, applicant):
    open_appearance(page, live_server, applicant)

    page.locator("input[name=navigation]").first.uncheck()
    page.wait_for_timeout(500)

    assert stored(applicant, "hidden_nav_items") == []


def test_the_switch_is_on_accessibility_and_takes_a_save(page: Page, live_server, applicant):
    profile_of(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/accessibility/")
    expect(page.locator("[data-save-status]")).to_have_count(0)

    page.get_by_label("Save a change as soon as I make it").check()
    page.get_by_role("button", name="Save", exact=True).click()

    expect(page.locator("[data-save-status]")).to_have_count(1)
    assert stored(applicant, "save_as_you_go") is True


def test_with_scripts_off_the_form_is_what_it_is_today(browser, live_server, applicant):
    profile_of(applicant, save_as_you_go=True)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/settings/appearance/")

        page.get_by_label("Compact").check()
        assert stored(applicant, "density") == "comfortable"
        page.get_by_role("button", name="Save", exact=True).last.click()
        expect(page.locator("[data-save-status]")).to_be_empty()
    finally:
        context.close()

    assert stored(applicant, "density") == "compact"
