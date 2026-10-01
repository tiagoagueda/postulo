"""*Your name*'s form of address and pronouns, in a real browser (#309).

The box for *Other* is in the page from the start and the stylesheet shows it only while
*Other…* is chosen, following the menu with `:has()`. That rule is the part only a browser
can check, and it has to hold the same with scripts off as on: nothing here is a script's
job. The walk chooses *Other…*, types, saves, and comes back to find its words where it
left them -- once with JavaScript and once without.

The page is also looked at in the state no ordinary walk reaches: both boxes showing and a
location worked out from an address, in both themes for axe and at 320 pixels for reflow.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_reflow import NARROW, SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e


def choose_other_and_type(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/profile/")
    menu = page.locator("select[name=form_of_address]")
    other = page.locator("input[name=form_of_address_other]")

    expect(other).to_be_hidden()
    menu.select_option("other")
    expect(other).to_be_visible()
    other.fill("Rev")

    # And back: a choice from the list takes the box away again.
    pronouns = page.locator("select[name=pronouns]")
    pronouns_other = page.locator("input[name=pronouns_other]")
    pronouns.select_option("other")
    expect(pronouns_other).to_be_visible()
    pronouns.select_option("they/them")
    expect(pronouns_other).to_be_hidden()

    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Your details have been saved.")).to_be_visible()

    # What was typed comes back where it was typed: Other chosen, the box showing it.
    expect(menu).to_have_value("other")
    expect(other).to_be_visible()
    expect(other).to_have_value("Rev")
    expect(pronouns).to_have_value("they/them")
    expect(pronouns_other).to_be_hidden()


def test_choosing_other_shows_the_box_and_keeps_what_was_typed(page: Page, live_server, applicant):
    sign_in(page, live_server.url)
    choose_other_and_type(page, live_server.url)

    applicant.profile.refresh_from_db()
    assert applicant.profile.form_of_address == "Rev"
    assert applicant.profile.pronouns == "they/them"


def test_the_same_with_scripts_off(browser, live_server, applicant):
    """Nothing about the box is a script's: the stylesheet follows the menu either way."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        choose_other_and_type(page, live_server.url)
    finally:
        context.close()

    applicant.profile.refresh_from_db()
    assert applicant.profile.form_of_address == "Rev"


def _with_both_boxes_and_a_derived_location(applicant) -> None:
    """Text no list offers in both, so both boxes are drawn open, and an address to work the
    location out from, so the location box carries its placeholder and its sentence."""
    from postulo.core.models import PostalAddress

    profile = applicant.profile
    profile.form_of_address = "Prof. Dr."
    profile.pronouns = "xe/xem"
    profile.save()
    PostalAddress.objects.create(
        owner=applicant,
        holder=profile,
        street="Rua do Exemplo 1",
        municipality="Lisboa",
        country="PT",
        is_primary=True,
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_your_details_with_both_boxes_open_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
    scheme,
):
    _with_both_boxes_and_a_derived_location(applicant)
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    expect(page.locator("input[name=form_of_address_other]")).to_be_visible()
    expect(page.locator("input[name=pronouns_other]")).to_be_visible()
    expect(page.locator("input[name=location]")).to_have_attribute(
        "placeholder", "Lisboa, Portugal"
    )

    found = violations_on(page, axe_source)
    assert not found, describe(f"/accounts/profile/ with Other open ({scheme})", found)


@pytest.mark.parametrize("language", ["en-GB", "pt-PT"])
def test_your_name_reflows_at_320_pixels(live_server, page: Page, applicant, language):
    """Each part on a line of its own on a phone, both boxes open, nothing across."""
    _with_both_boxes_and_a_derived_location(applicant)
    applicant.profile.language = language
    applicant.profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": NARROW, "height": 800})
    page.goto(f"{live_server.url}/accounts/profile/")
    expect(page.locator("input[name=pronouns_other]")).to_be_visible()

    assert page.evaluate(SPILLS) == []
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0

    # In reading order, one under another: the form of address, the name, the pronouns.
    tops = [
        page.locator(f"[name={name}]").bounding_box()["y"]
        for name in ("form_of_address", "first_name", "last_name", "pronouns")
    ]
    assert tops == sorted(tops) and len(set(tops)) == 4, tops


#: Each menu's width as drawn, and the width its longest option asks for. Measured by
#: letting the menu size itself for a moment, through the CSSOM, which the policy allows.
MENUS = """() => {
  const out = {};
  for (const name of ['form_of_address', 'pronouns']) {
    const menu = document.querySelector(`select[name=${name}]`);
    const drawn = menu.getBoundingClientRect().width;
    menu.style.width = 'max-content';
    out[name] = {drawn, needs: menu.getBoundingClientRect().width};
    menu.style.width = '';
  }
  return out;
}"""


@pytest.mark.parametrize("language", ["en-GB", "de", "el"])
def test_a_menu_is_as_wide_as_what_it_says(live_server, page: Page, applicant, language):
    """A menu had 128 pixels from 640 up, about 81 of them for words, and every profile
    starts on "Not stated": *Nicht angegeben* was drawn as *Nicht angeg*, and so on in 28 of
    the catalogues that translate it. English fitted, which is why nobody saw it. Now a
    menu is as wide as its longest option, in any language and any font."""
    applicant.profile.language = language
    applicant.profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    for width in (640, 1280):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{live_server.url}/accounts/profile/")
        for name, menu in page.evaluate(MENUS).items():
            assert menu["drawn"] + 0.5 >= menu["needs"], (width, name, menu)
        assert page.evaluate(SPILLS) == []
        assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0


#: Where each control of *Your name* is, and where the field around it is: the field is
#: the label, the control and whatever is written under it.
CONTROLS = """() => {
  const out = {};
  const card = document.querySelector('#section-name');
  for (const control of card.querySelectorAll('select, input[type=text]')) {
    if (!control.checkVisibility()) continue;
    const field = control.closest('.field').getBoundingClientRect();
    out[control.name] = {
      top: control.getBoundingClientRect().top,
      from: field.top,
      to: field.bottom,
    };
  }
  return out;
}"""


@pytest.mark.parametrize("language", ["en-GB", "pt-PT"])
def test_an_error_under_one_box_leaves_its_line_in_line(
    live_server, page: Page, applicant, language
):
    """The lines were aligned by the bottom of each whole field, error included, so a box
    that came back with its error was lifted 56 pixels above the menu it belongs to and
    the name boxes beside it. Fields that share a line start their controls level,
    whatever is written under any of them."""
    applicant.profile.language = language
    applicant.profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server.url}/accounts/profile/")
    page.locator("select[name=form_of_address]").select_option("other")
    page.locator("select[name=pronouns]").select_option("other")
    page.locator("main form").first.locator("button[type=submit]").first.click()
    for name in ("form_of_address_other", "pronouns_other"):
        expect(page.locator(f"#id_{name}_error")).to_be_visible()

    controls = page.evaluate(CONTROLS)
    assert set(controls) == {
        "form_of_address",
        "form_of_address_other",
        "first_name",
        "last_name",
        "pronouns",
        "pronouns_other",
    }
    together = 0
    for name, one in controls.items():
        for other_name, other in controls.items():
            if name < other_name and one["from"] < other["to"] and other["from"] < one["to"]:
                together += 1
                assert abs(one["top"] - other["top"]) <= 1, (name, other_name, controls)
    # Not vacuous: each menu shares a line with its box, and the two names share one.
    assert together >= 3, controls
    assert abs(controls["first_name"]["top"] - controls["last_name"]["top"]) <= 1
    for menu in ("form_of_address", "pronouns"):
        assert abs(controls[menu]["top"] - controls[f"{menu}_other"]["top"]) <= 1, controls


def test_a_full_stop_stays_at_the_end_on_a_page_drawn_right_to_left(
    live_server, page: Page, applicant
):
    """*Prof.* in the menu was drawn as *.Prof* and *Prof. Dr.* in the box as *.Prof. Dr*
    under Arabic: the text took the page's direction. The listed option says which way its
    language is written, and the box takes its direction from what is typed in it."""
    profile = applicant.profile
    profile.language = "ar"
    profile.record_language = "pt-PT"
    profile.form_of_address = "Prof."
    profile.pronouns = "Prof. Dr."
    profile.save()
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")

    menu = page.locator("select[name=form_of_address]")
    expect(menu).to_have_value("Prof.")
    assert menu.evaluate("menu => getComputedStyle(menu).direction") == "rtl", "the page's"
    assert menu.evaluate("menu => getComputedStyle(menu.selectedOptions[0]).direction") == "ltr"

    box = page.locator("input[name=pronouns_other]")
    expect(box).to_have_value("Prof. Dr.")
    assert box.evaluate("box => getComputedStyle(box).direction") == "ltr"
    # And it follows what is typed: Arabic in the same box is written right to left.
    box.fill("د.")
    assert box.evaluate("box => getComputedStyle(box).direction") == "rtl"
