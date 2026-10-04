"""A select's own control, in a real browser (#301).

Every `<select>` is drawn by the server as a native select, and `app.js` builds Basecoat's
select beside it: a button showing the choice and a list in a popover, whose options can
hold a flag or an icon. `tests/test_select_options.py` reads what the server writes. What
takes a browser is everything a person does with it, and everything the page around it
went on assuming was a native select:

* **the native select stays the form's control**: a choice sets it and raises its `input`
  and `change`, once, and setting it from elsewhere moves the button;
* **the keyboard**: the arrows, Home, End, Page Down, a letter, Enter, Space, Escape, Tab,
  with nothing chosen by passing over it;
* **a long list** has a box that narrows it, whatever the case or the accents, and a
  dialling code finds its country;
* **what a screen reader is told**: a combobox with the label's name and the choice, a
  list box, each option with where it stands, the groups by their names;
* **with scripts off** it is the browser's own select, and nothing else is drawn;
* **the three rules that follow a select from the stylesheet** still follow it;
* **where it sits**: in a dialog, in a table's header, on a board's card, in a row that
  arrived after the page did, and a row that left takes its list with it;
* **a flag is drawn once**, in the button, and the one beside the native select is put away;
* **an answer that is required** is asked for under the button, in the page's words;
* **a finger**, **a narrow window in four languages**, **forced colours**, **reduced
  motion**, and **axe** in both themes with a list open.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from .selects import active_option, button_of, choose, list_of, open_list, options_of
from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)
from .test_application_filters import search  # noqa: F401
from .test_people_menu import NO_ANCHORS
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 740}
NARROWEST = {"width": 320, "height": 640}

#: How many options a list has before it gets a box to narrow it: `SELECT_FILTER_FROM`.
FILTER_FROM = 16

#: Counts every `input` and `change` a select raises from now on.
LISTEN = """(select) => {
  window.heard = [];
  for (const name of ['input', 'change']) {
    select.addEventListener(name, () => window.heard.push(name + ':' + select.value));
  }
}"""

#: Where an open list is against its button and the window, and whether a click on each
#: option that is drawn would land on it.
PLACED = """(panel) => {
  const button = document.querySelector('[popovertarget="' + CSS.escape(panel.id) + '"]');
  const width = document.documentElement.clientWidth;
  const height = document.documentElement.clientHeight;
  const box = panel.getBoundingClientRect();
  const from = button.getBoundingClientRect();
  const list = panel.querySelector('[role="listbox"]').getBoundingClientRect();
  const hits = [...panel.querySelectorAll('[role="option"]:not([aria-hidden="true"])')]
    .filter((row) => {
      const r = row.getBoundingClientRect();
      return r.top >= list.top - 1 && r.bottom <= list.bottom + 1;
    })
    .map((row) => {
      const r = row.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!hit && row.contains(hit);
    });
  const rtl = getComputedStyle(panel).direction === 'rtl';
  return {
    open: panel.matches(':popover-open'),
    inside: box.left >= 0 && box.top >= 0 && box.right <= width + 0.5 && box.bottom <= height + 0.5,
    beside: Math.abs(box.top - from.bottom) <= 8 || Math.abs(from.top - box.bottom) <= 8,
    startsWithButton: Math.abs(rtl ? box.right - from.right : box.left - from.left) <= 1,
    asWideAsButton: box.width >= Math.min(from.width, width - 32) - 1,
    seen: hits.length,
    reachable: hits.every(Boolean),
  };
}"""


@pytest.fixture
def administrator(applicant):
    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save(update_fields=["is_staff", "is_superuser"])
    return applicant


def to_the_gallery(page: Page, base: str) -> None:
    from tests.e2e.conftest import PASSWORD

    sign_in(page, base)
    page.goto(f"{base}/server/design/")
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button").first.click()
        page.goto(f"{base}/server/design/")
    expect(page.locator("#design-choices")).to_be_visible()


def placed(page: Page, panel) -> dict:
    """Where the list is once it has been put beside its button. The browser places an
    anchored one as it draws it; the script places one a task after the click."""
    page.wait_for_function(f"(panel) => ({PLACED})(panel).beside", arg=panel.element_handle())
    return panel.evaluate(PLACED)


# ------------------------------------------------------- the select is still the control


def test_the_native_select_is_still_the_control_and_the_button_drives_it(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-icons")
    button = button_of(select)

    # Still in the document, out of the tab order and out of the accessibility tree.
    expect(select).to_have_attribute("aria-hidden", "true")
    expect(select).to_have_attribute("tabindex", "-1")
    assert select.evaluate("(el) => el.getBoundingClientRect().width") <= 1
    expect(button).to_have_text("Mobile")

    select.evaluate(LISTEN)
    choose(page, select, "Home")
    expect(select).to_have_value("home")
    expect(button).to_have_text("Home")
    expect(button).to_be_focused()
    assert page.evaluate("() => window.heard") == ["input:home", "change:home"]

    # The same choice again is not a change.
    choose(page, select, "Home")
    assert page.evaluate("() => window.heard") == ["input:home", "change:home"]

    # And the other way round: whatever sets the select and says so moves the button.
    select.select_option("work")
    expect(button).to_have_text("Work")
    expect(button.locator("svg[data-icon=briefcase]")).to_have_count(1)


def test_a_click_on_the_label_gives_the_focus_to_the_button(page: Page, live_server, administrator):
    to_the_gallery(page, live_server.url)
    page.get_by_text("Each with an icon", exact=True).click()
    select = page.locator("#design-choice-icons")
    expect(button_of(select)).to_be_focused()
    expect(list_of(page, select)).to_be_hidden()
    # A script that hands the focus to the select hands it to the button as well.
    page.locator("#design-choice-groups").focus()
    expect(button_of(page.locator("#design-choice-groups"))).to_be_focused()


# ------------------------------------------------------------------------ the keyboard


def test_the_keyboard_works_a_short_list_and_chooses_only_when_asked(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-icons")
    button = button_of(select)
    panel = list_of(page, select)
    select.evaluate(LISTEN)

    button.focus()
    page.keyboard.press("ArrowDown")
    expect(panel).to_be_visible()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(button).to_be_focused()
    assert active_option(page, select) == "Mobile", "it opens on the choice it holds"

    page.keyboard.press("ArrowDown")
    assert active_option(page, select) == "Work"
    page.keyboard.press("End")
    assert active_option(page, select) == "Other"
    page.keyboard.press("ArrowDown")
    assert active_option(page, select) == "Other", "a list does not go round"
    page.keyboard.press("Home")
    assert active_option(page, select) == "Mobile"
    page.keyboard.press("h")
    assert active_option(page, select) == "Home", "a letter goes to the option it starts"
    assert page.evaluate("() => window.heard") == [], "passing over an option chooses nothing"

    # Escape closes it, chooses nothing and leaves the focus on the button.
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(button).to_be_focused()
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(select).to_have_value("mobile")

    # Enter opens it and Enter chooses.
    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("work")
    expect(button).to_be_focused()
    assert page.evaluate("() => window.heard") == ["input:work", "change:work"]

    # Space opens it and Space chooses; the key coming up does not open it again.
    page.keyboard.press("Space")
    expect(panel).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Space")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("home")

    # A letter on the closed button opens the list on the option it starts.
    page.keyboard.press("o")
    expect(panel).to_be_visible()
    assert active_option(page, select) == "Other"
    # Tab chooses, closes and moves on, as it does from a native list.
    page.keyboard.press("Tab")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("other")
    expect(button).not_to_be_focused()
    assert page.evaluate("() => document.activeElement.id") == "design-choice-long-trigger"


def test_an_option_that_is_switched_off_is_said_and_passed_over(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-groups")
    button = button_of(select)
    button.focus()
    page.keyboard.press("ArrowDown")
    panel = list_of(page, select)
    expect(panel.get_by_role("option", name="Europe/Paris")).to_have_attribute(
        "aria-disabled", "true"
    )
    page.keyboard.press("ArrowDown")
    assert active_option(page, select) == "Asia/Seoul", "the keys pass over it"
    # A click on it chooses nothing and leaves the list open.
    panel.get_by_role("option", name="Europe/Paris").click(force=True)
    expect(panel).to_be_visible()
    expect(select).to_have_value("Europe/Lisbon")
    # Nor does a press on a group's heading, and neither takes the focus off the button:
    # the keys go on working the list.
    panel.locator("[data-select-heading]").first.click()
    expect(panel).to_be_visible()
    expect(button).to_be_focused()
    page.keyboard.press("End")
    assert active_option(page, select) == "Asia/Tokyo"
    page.keyboard.press("Escape")

    # A select nobody may change is a button nobody may press.
    expect(button_of(page.locator("#design-choice-disabled"))).to_be_disabled()


def test_a_single_key_typed_on_a_select_is_not_one_of_the_pages_shortcuts(
    page: Page, live_server, administrator
):
    """`/` goes to the search box unless somebody is typing, and a letter typed on a list
    is typing: it looks down the list."""
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-long")
    button_of(select).focus()
    page.keyboard.press("/")
    box = list_of(page, select).locator("[data-select-filter]")
    expect(box).to_be_focused()
    expect(box).to_have_value("/")


# ------------------------------------------------------------------------- a long list


def test_a_long_list_has_a_box_that_narrows_it_whatever_the_case_or_the_accents(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    short = page.locator("#design-choice-icons")
    open_list(page, short)
    expect(list_of(page, short).locator("[data-select-filter]")).to_have_count(0)
    assert short.locator("option").count() < FILTER_FROM
    page.keyboard.press("Escape")

    select = page.locator("#design-choice-long")
    assert select.locator("option").count() >= FILTER_FROM
    button = button_of(select)
    panel = open_list(page, select)
    box = panel.locator("[data-select-filter]")
    expect(box).to_be_focused()
    everything = options_of(page, select).count()
    assert everything == select.locator("option").count()

    box.fill("CÔTE")
    expect(options_of(page, select)).to_have_text(["Côte d’Ivoire"])
    box.fill("cote d")
    expect(options_of(page, select)).to_have_text(["Côte d’Ivoire"])
    assert active_option(page, select) == "Côte d’Ivoire", "the keys are on the first match"

    box.fill("zzzz")
    expect(options_of(page, select)).to_have_count(0)
    expect(panel.get_by_role("status")).to_have_text("Nothing matches.")
    assert (
        panel.locator('[role="listbox"]').evaluate(
            "(list) => getComputedStyle(list, '::before').content"
        )
        == '"Nothing matches."'
    )
    page.keyboard.press("Enter")
    expect(panel).to_be_hidden()
    expect(select).to_have_value(""), "nothing to choose, so nothing chosen"

    # Typing on the closed button opens the list with the letter already in the box.
    button.focus()
    page.keyboard.press("p")
    expect(box).to_be_focused()
    expect(box).to_have_value("p")
    page.keyboard.type("ortug")
    expect(options_of(page, select)).to_have_text(["Portugal"])
    page.keyboard.press("Enter")
    expect(select).to_have_value("PT")
    expect(button).to_be_focused()
    expect(button).to_have_text("Portugal")

    # The box is the list's own business: it is no control of a form, and it has no name.
    assert box.evaluate("(el) => el.form") is None
    assert box.get_attribute("name") is None

    # Tab from the box chooses an option the keys were moved to and goes on from the
    # button, as it does from a short list: to the next control, not back to the top of the
    # page. (What the box alone found, Tab does not choose: see below.)
    page.keyboard.press("j")
    page.keyboard.type("apa")
    expect(options_of(page, select)).to_have_text(["Japan"])
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Tab")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("JP")
    assert page.evaluate("() => document.activeElement.id") == "design-choice-groups-trigger"


def test_a_dialling_code_finds_its_country(page: Page, live_server, applicant):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")
    select = page.locator("[data-phone-country]")
    panel = open_list(page, select)
    box = panel.locator("[data-select-filter]")
    for typed in ("351", "+351", "00351"):
        box.fill(typed)
        expect(options_of(page, select)).to_have_text(["+351 Portugal"])
    page.keyboard.press("Enter")
    expect(select).to_have_value("PT")


#: What is typed, and the option the keys go to for it in each list: the first whose words
#: start with it, then the first with a word that does, then the first that holds it --
#: not simply the first that holds it, which in the lists in the order of their country
#: codes is "+236 Central African Republic" (CF) for "fr" and "+971 United Arab Emirates"
#: (AE) for "it". All three Uniteds start with "uni", so the first in the list has it.
RANKED = {
    "gallery": {
        "fr": "France",
        "it": "Italy",
        "uni": "United Arab Emirates",
        "kingd": "United Kingdom",
    },
    "telephone": {
        "fr": "+33 France",
        "it": "+39 Italy",
        "uni": "+971 United Arab Emirates",
        "kingd": "+44 United Kingdom",
    },
    "address": {
        "fr": "France",
        "it": "Italy",
        "uni": "United Arab Emirates",
        "kingd": "United Kingdom",
    },
}


@pytest.mark.parametrize("where", sorted(RANKED))
def test_typing_puts_the_keys_on_the_option_it_most_likely_means(
    page: Page, live_server, administrator, where
):
    if where == "gallery":
        to_the_gallery(page, live_server.url)
        select = page.locator("#design-choice-long")
    else:
        sign_in(page, live_server.url)
        if where == "telephone":
            page.goto(f"{live_server.url}/jobs/contacts/new/")
            select = page.locator("[data-phone-country]")
        else:
            page.goto(f"{live_server.url}/accounts/profile/")
            select = page.locator("select[name=addresses-0-country]")
    button = button_of(select)
    button.scroll_into_view_if_needed()
    for typed, meant in RANKED[where].items():
        button.focus()
        page.keyboard.type(typed)
        box = list_of(page, select).locator("[data-select-filter]")
        expect(box).to_have_value(typed)
        assert active_option(page, select) == meant, typed
        # The box still leaves every option that holds what was typed.
        assert options_of(page, select).count() >= 1
        page.keyboard.press("Enter")
        assert meant.split(" ")[-1] in select.evaluate("(s) => s.selectedOptions[0].textContent")


def test_a_letter_on_a_list_with_no_box_finds_a_word_inside_an_option(
    page: Page, live_server, administrator
):
    """No option of the time zones starts with "s", and one has a word that does."""
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-groups")
    button_of(select).focus()
    page.keyboard.press("s")
    expect(list_of(page, select)).to_be_visible()
    assert active_option(page, select) == "Asia/Seoul"
    page.keyboard.press("Escape")
    page.keyboard.press("t")
    assert active_option(page, select) == "Asia/Tokyo"
    page.keyboard.press("Enter")
    expect(select).to_have_value("Asia/Tokyo")


def test_tab_chooses_only_an_option_somebody_moved_to(page: Page, live_server, administrator):
    """What the box found first is the box's guess; Tab leaves with nothing chosen. An
    option reached with the arrows, Home or End, the page keys or the pointer is chosen."""
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-long")
    button = button_of(select)
    panel = list_of(page, select)
    box = panel.locator("[data-select-filter]")
    select.evaluate(LISTEN)

    button.focus()
    page.keyboard.press("ArrowDown")
    expect(box).to_be_focused()
    page.keyboard.type("zz")
    page.keyboard.press("Tab")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("")
    assert page.evaluate("() => document.activeElement.id") == "design-choice-groups-trigger"
    assert page.evaluate("() => window.heard") == []

    # The same box, with the arrows used after typing: that is a choice.
    button.focus()
    page.keyboard.type("japa")
    page.keyboard.press("ArrowDown")
    assert active_option(page, select) == "Japan"
    page.keyboard.press("Tab")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("JP")

    # And with the pointer moved onto an option.
    button.focus()
    page.keyboard.type("portu")
    panel.get_by_role("option", name="Portugal").hover()
    page.keyboard.press("Shift+Tab")
    expect(panel).to_be_hidden()
    expect(select).to_have_value("PT")

    # Typing again after a move makes the place the box's guess once more.
    button.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.type("fr")
    page.keyboard.press("Tab")
    expect(select).to_have_value("PT")


def test_home_and_end_in_a_long_lists_box_move_the_caret(page: Page, live_server, administrator):
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-long")
    button_of(select).focus()
    page.keyboard.type("port")
    box = list_of(page, select).locator("[data-select-filter]")
    expect(box).to_be_focused()
    page.keyboard.press("Home")
    assert box.evaluate("(el) => [el.selectionStart, el.selectionEnd]") == [0, 0]
    assert active_option(page, select) == "Portugal"
    page.keyboard.press("End")
    assert box.evaluate("(el) => [el.selectionStart, el.selectionEnd]") == [4, 4]
    # Typed in the middle, it goes in the middle.
    page.keyboard.press("Home")
    page.keyboard.type("x")
    expect(box).to_have_value("xport")


# ----------------------------------------------------------- what a screen reader is told


def test_a_screen_reader_hears_the_name_the_role_the_choice_and_each_options_place(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    section = page.locator("#design-choices")

    # The native select is hidden from the tree, so each name finds one control.
    button = section.get_by_role("combobox", name="Each with an icon", exact=True)
    expect(button).to_have_count(1)
    expect(button).to_have_text("Mobile")
    expect(button).to_have_attribute("aria-haspopup", "listbox")
    expect(button).to_have_attribute("aria-expanded", "false")

    button.focus()
    page.keyboard.press("ArrowDown")
    listbox = page.get_by_role("listbox", name="Each with an icon", exact=True)
    expect(listbox).to_be_visible()
    assert button.get_attribute("aria-controls") == listbox.get_attribute("id")
    options = listbox.get_by_role("option")
    expect(options).to_have_text(["Mobile", "Work", "Home", "Other"])
    expect(listbox.get_by_role("option", selected=True)).to_have_text(["Mobile"])
    assert options.evaluate_all(
        "(rows) => rows.map((row) =>"
        " [row.getAttribute('aria-posinset'), row.getAttribute('aria-setsize')])"
    ) == [["1", "4"], ["2", "4"], ["3", "4"], ["4", "4"]]
    # The option the keys are on is the one the button says it is on.
    page.keyboard.press("ArrowDown")
    assert button.get_attribute("aria-activedescendant") == options.nth(1).get_attribute("id")
    page.keyboard.press("Escape")
    assert button.get_attribute("aria-activedescendant") is None

    # Groups are groups, each called by its heading.
    grouped = section.get_by_role("combobox", name="In groups, with one switched off")
    grouped.click()
    groups = page.get_by_role("listbox", name="In groups, with one switched off").get_by_role(
        "group"
    )
    expect(groups).to_have_count(2)
    expect(groups.nth(0)).to_have_accessible_name("Europe")
    expect(groups.nth(1)).to_have_accessible_name("Asia")
    expect(groups.nth(1).get_by_role("option")).to_have_text(["Asia/Seoul", "Asia/Tokyo"])
    page.keyboard.press("Escape")

    # In a long list the box is the combobox while the list is open, under the same name,
    # and what is left of the list is counted again.
    long = section.get_by_role("combobox", name="A long list, with a box to narrow it")
    long.click()
    box = page.get_by_role("combobox", name="A long list, with a box to narrow it").and_(
        page.locator("input")
    )
    expect(box).to_be_focused()
    expect(box).to_have_attribute("aria-autocomplete", "list")
    box.fill("land")
    left = page.locator("#design-choice-long-listbox").get_by_role("option")
    sizes = set(left.evaluate_all("(rows) => rows.map((row) => row.getAttribute('aria-setsize'))"))
    assert sizes == {str(left.count())}
    first = left.first
    assert box.get_attribute("aria-activedescendant") == first.get_attribute("id")


# --------------------------------------------------------------------------- scripts off


def test_with_scripts_off_it_is_the_browsers_own_select(browser: Browser, live_server, applicant):
    """The scripts-off path is the native select, drawn by the server and painted as it
    always was: no button, no list, and the form posts what was chosen."""
    from postulo.core.models import PhoneNumber

    PhoneNumber.objects.create(
        owner=applicant, holder=applicant.profile, kind="mobile", number="+351912345678"
    )
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        expect(page.locator("[data-select]")).to_have_count(0)
        kind = page.locator("select[name=phone_numbers-0-kind]")
        expect(kind).to_be_visible()
        assert kind.bounding_box()["height"] >= 24
        assert kind.get_attribute("aria-hidden") is None
        # The flag the server drew beside the country stands, as it did (#88).
        expect(page.locator("[data-phone-flag] img").first).to_have_attribute("data-flag", "pt")
        kind.select_option("work")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("select[name=phone_numbers-0-kind]")).to_have_value("work")
    finally:
        context.close()
    assert PhoneNumber.objects.get(owner=applicant).kind == "work"


def test_with_scripts_on_the_form_posts_the_same_name_and_value(page: Page, live_server, applicant):
    from postulo.core.models import PhoneNumber

    PhoneNumber.objects.create(
        owner=applicant, holder=applicant.profile, kind="mobile", number="+351912345678"
    )
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    choose(page, page.locator("select[name=phone_numbers-0-kind]"), "Work")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("select[name=phone_numbers-0-kind]")).to_have_value("work")
    assert PhoneNumber.objects.get(owner=applicant).kind == "work"


# ------------------------------------------- the stylesheet's rules that follow a select


def test_the_three_rules_that_follow_a_select_still_follow_it(page: Page, live_server, applicant):
    """`data-if-other`, `.phone-row` and `data-if-chosen` are each a `:has()` on the select's
    checked option. The select is still the one that holds the choice, so they hold."""
    from postulo.accounts.models import PersonIdentifier
    from postulo.documents.models import CV

    PersonIdentifier.objects.create(
        profile=applicant.profile, scheme="orcid", value="0000-0002-1825-0097"
    )
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/accounts/profile/")

    row = page.locator("#section-phones li").first
    kind = row.locator("select[name$='-kind']")
    name = row.locator("[data-name-if-other]")
    line = row.locator("[data-phone-line]")

    def under_the_kind() -> bool:
        return line.bounding_box()["y"] >= button_of(kind).bounding_box()["y"] + 30

    expect(name).to_be_hidden()
    assert not under_the_kind(), "the number is on the kind's line"

    choose(page, kind, "Other")
    expect(name).to_be_visible(), "data-if-other shows the name"
    assert under_the_kind(), ".phone-row puts the number on a line of its own"
    choose(page, kind, "Mobile")
    expect(name).to_be_hidden()
    assert not under_the_kind()

    cv = CV.objects.get(owner=applicant)
    page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.locator("[data-cv-prints] summary").click()
    which = page.locator("select[name=prints_identifiers]")
    ticks = page.locator("[data-shown-if-chosen]")
    expect(which).to_have_value("default")
    expect(ticks).to_be_hidden()
    panel = open_list(page, which)
    panel.locator('[role="option"][data-value="chosen"]').click()
    expect(which).to_have_value("chosen")
    expect(ticks).to_be_visible(), "data-if-chosen shows the list to tick"
    panel = open_list(page, which)
    panel.locator('[role="option"][data-value="default"]').click()
    expect(ticks).to_be_hidden()


# ----------------------------------------------------------------------- flags and icons


def test_a_flag_is_drawn_once_and_follows_the_country(page: Page, live_server, applicant):
    """The flag beside the closed native select is the scripts-off path. With scripts on
    the button draws the chosen country's flag and every option draws its own, and the
    server's is put away rather than drawn twice."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")
    group = page.locator("[data-phone-field]")
    select = group.locator("[data-phone-country]")
    button = button_of(select)
    expect(group.locator("[data-phone-flag]")).to_be_hidden()
    expect(group.locator("img.flag:visible")).to_have_count(0)

    select.select_option("PT")
    expect(group.locator("img.flag:visible")).to_have_count(1)
    flag = button.locator("img.flag")
    expect(flag).to_have_attribute("alt", "")
    page.wait_for_function(
        "(image) => image.complete && image.naturalWidth > 0", arg=flag.element_handle()
    )
    src = flag.get_attribute("src")
    assert "/flags/pt" in src

    panel = open_list(page, select)
    panel.locator("[data-select-filter]").fill("japan")
    option = options_of(page, select).first
    expect(option.locator("img.flag")).to_have_count(1)
    option.click()
    expect(select).to_have_value("JP")
    assert "/flags/jp" in button.locator("img.flag").get_attribute("src")
    expect(group.locator("img.flag:visible")).to_have_count(1)


def test_each_kind_draws_its_icon_in_the_list_and_in_the_button(page: Page, live_server, applicant):
    from postulo.accounts.models import PersonIdentifier
    from postulo.core.models import PhoneNumber, PostalAddress, WebLink

    profile = applicant.profile
    PhoneNumber.objects.create(owner=applicant, holder=profile, kind="fax", number="+351212345678")
    PostalAddress.objects.create(
        owner=applicant, holder=profile, kind="postal", municipality="Lisboa", country="PT"
    )
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value="0000-0002-1825-0097")
    WebLink.objects.create(
        owner=applicant,
        holder=profile,
        kind="repository",
        service="codeberg",
        url="https://codeberg.org/alex/postulo",
    )
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    for name, chosen, another, icon in (
        ("phone_numbers-0-kind", "printer", "Mobile", "smartphone"),
        ("addresses-0-kind", "mailbox", "Home", "home"),
        ("identifiers-0-scheme", "graduation-cap", "Wikidata", "globe"),
        ("repositories-0-service", "git-branch", "Other", "globe"),
    ):
        select = page.locator(f"select[name={name}]")
        button = button_of(select)
        expect(button.locator(f"svg[data-icon={chosen}]")).to_have_count(1), name
        panel = open_list(page, select)
        option = panel.get_by_role("option", name=another, exact=True)
        expect(option.locator(f"svg[data-icon={icon}]")).to_have_count(1), name
        expect(option.locator("svg[data-icon]").first).to_have_attribute("aria-hidden", "true")
        # Every option that can be chosen has one, or the room for one.
        assert (
            panel.locator('[role="option"]').count() == panel.locator("[data-select-mark]").count()
        )
        option.click()
        expect(button.locator(f"svg[data-icon={icon}]")).to_have_count(1), name

    # The icon the server drew beside the service, for a page without scripts, is put away.
    row = page.locator("[data-web-links=repository] li").first
    expect(row.locator("[data-option-mark]")).to_be_hidden()
    expect(row.locator(".input-group [data-select-mark] svg:visible")).to_have_count(1)

    # Countries, in an address, with their flags.
    country = page.locator("select[name=addresses-0-country]")
    expect(button_of(country).locator("img.flag")).to_have_count(1)
    expect(page.locator("[data-address-row] [data-flag-holder]").first).to_be_hidden()

    # And the language of the career record.
    language = page.locator("select[name=record_language]")
    panel = open_list(page, language)
    assert panel.locator('[role="option"] img.flag').count() > 3
    page.keyboard.press("Escape")


def test_time_zones_are_in_groups_by_continent(page: Page, live_server, applicant):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/language/")
    select = page.locator("select[name=time_zone]")
    assert select.locator("optgroup").evaluate_all("(groups) => groups.map((g) => g.label)") == [
        "Africa",
        "Americas",
        "Antarctica",
        "Asia",
        "Atlantic Ocean",
        "Australia",
        "Europe",
        "Indian Ocean",
        "Pacific Ocean",
        "Other time zones",
    ]
    panel = open_list(page, select)
    headings = panel.locator("[data-select-heading]")
    expect(headings).to_have_count(10)
    panel.locator("[data-select-filter]").fill("lisbon")
    expect(options_of(page, select)).to_have_text(["Europe/Lisbon"])
    # A group with nothing left in it goes with its options.
    expect(panel.locator("[data-select-heading]:visible")).to_have_text(["Europe"])
    page.keyboard.press("Enter")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("select[name=time_zone]")).to_have_value("Europe/Lisbon")


# ------------------------------------------------------------------------ where it sits


def test_in_a_dialog_the_list_is_whole_and_escape_closes_the_list_alone(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    page.get_by_role("button", name="Open a dialog with a choice in it").click()
    dialog = page.locator("#design-choice-dialog")
    assert dialog.evaluate("(dialog) => dialog.matches(':modal')")
    select = page.locator("#design-choice-dialog-kind")
    button = button_of(select)

    button.focus()
    page.keyboard.press("ArrowDown")
    panel = list_of(page, select)
    found = placed(page, panel)
    assert found["open"] and found["inside"] and found["beside"], found
    assert found["seen"] == 4 and found["reachable"], "the dialog's box does not cut the list"

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    assert dialog.evaluate("(dialog) => dialog.open"), "Escape closed the list, not the dialog"
    expect(button).to_be_focused()

    choose(page, select, "Work")
    expect(select).to_have_value("work")
    assert dialog.evaluate("(dialog) => dialog.open")
    page.keyboard.press("Escape")
    assert not dialog.evaluate("(dialog) => dialog.open"), "and the next Escape is the dialog's"


@pytest.mark.parametrize("placed_by", ["anchor", "script"])
def test_in_a_tables_header_the_list_is_whole_and_narrows_the_table(
    page: Page,
    live_server,
    search,  # noqa: F811
    placed_by,
):
    """The header sits in the box the table scrolls in, which clips whatever is laid out in
    it. The list is in the top layer, beside its button, whole. `script` is a browser with
    no anchor positioning, where `app.js` places it."""
    if placed_by == "script":
        page.context.add_init_script(NO_ANCHORS)
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/?view=table")
    page.locator("th[data-col=status] summary").click()
    select = page.locator("#filter-status")
    button = button_of(select)
    expect(button).to_be_visible()

    button.click()
    panel = list_of(page, select)
    found = placed(page, panel)
    assert found["open"] and found["inside"] and found["beside"], (placed_by, found)
    assert found["startsWithButton"] and found["asWideAsButton"], (placed_by, found)
    assert found["seen"] and found["reachable"], (placed_by, found)

    with page.expect_response(lambda response: "status=rejected" in response.url):
        panel.get_by_role("option", name="Rejected", exact=True).click()
    expect(page).to_have_url(re.compile(r"[?&]status=rejected(&|$)"))
    expect(page.locator("#applications-table tbody tr")).to_have_count(1)
    # The header was replaced with the table. The select that came back has a button of
    # its own, showing the filter in force, and the focus is on it.
    fresh = button_of(page.locator("#filter-status"))
    expect(fresh).to_have_text("Rejected")
    expect(fresh).to_be_focused()
    # Nothing of the table that went is left: one button and one list for each select.
    assert page.evaluate(LEFT_BEHIND) == []


#: Buttons and lists that belong to no select on the page, and selects with two.
LEFT_BEHIND = """() => {
  const wrong = [];
  for (const root of document.querySelectorAll('[data-select]')) {
    const select = root.previousElementSibling;
    if (!select || select.tagName !== 'SELECT') wrong.push('a button with no select');
  }
  for (const select of document.querySelectorAll('select:not([multiple])')) {
    const root = select.nextElementSibling;
    if (!root || !root.matches('[data-select]')) {
      wrong.push((select.id || select.name) + ' has no button');
    }
  }
  const panels = document.querySelectorAll('[data-select-panel]').length;
  const roots = document.querySelectorAll('[data-select]').length;
  if (panels !== roots) wrong.push(panels + ' lists for ' + roots + ' buttons');
  return wrong;
}"""


def test_on_a_boards_card_the_status_has_its_icons_and_a_choice_moves_the_card_once(
    page: Page,
    live_server,
    search,  # noqa: F811
):
    from postulo.applications.models import Application, ApplicationEvent

    draft = Application.objects.get(posting__title="Resonance Technician")
    events = ApplicationEvent.objects.filter(application=draft).count()
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/?view=board")

    # A list is built when it is opened, and not before: a board is all buttons.
    assert page.locator("[data-card]").count() >= 4
    assert page.locator('[data-card] [role="option"]').count() == 0
    # The icons are drawn once for the board, not once a card.
    assert page.locator("template[data-option-icons]").count() == 1

    card = page.locator(f"[data-card='{draft.pk}']")
    select = card.locator("select[name=status]")
    button = button_of(select)
    expect(button).to_have_accessible_name("Status of Resonance Technician at Black Mesa")
    expect(button).to_have_accessible_description(
        "Choose a status to move this card to another column. From the keyboard the move is "
        "made when you press Enter or leave the menu, so passing over a status on the way "
        "does not record it. Cards can also be dragged between columns with a mouse."
    )
    expect(button.locator("svg[data-icon=pencil]")).to_have_count(1)

    button.focus()
    page.keyboard.press("ArrowDown")
    panel = list_of(page, select)
    found = placed(page, panel)
    assert found["inside"] and found["reachable"], found
    icons = panel.locator('[role="option"]').evaluate_all(
        "(rows) => rows.map((row) => row.querySelector('[data-select-mark] svg').dataset.icon)"
    )
    assert icons == [
        "pencil",
        "send",
        "mail-check",
        "search",
        "users",
        "clipboard-check",
        "handshake",
        "circle-check",
        "circle-x",
        "circle-minus",
        "ghost",
    ]
    # Three statuses are passed over on the way to the fourth, and only the fourth is kept.
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page.locator(f"[data-board-column=screening] [data-card='{draft.pk}']")).to_be_visible()
    draft.refresh_from_db()
    assert draft.status == "screening"
    assert ApplicationEvent.objects.filter(application=draft).count() == events + 1, (
        "one move, one entry"
    )


def test_a_row_that_arrives_later_gets_its_button_and_one_that_leaves_takes_it_along(
    page: Page, live_server, applicant
):
    """What a swap brings in is made ready as it lands, and what it takes away leaves
    nothing behind: no button without its select, no list in the top layer."""
    from postulo.accounts.models import PersonIdentifier

    PersonIdentifier.objects.create(profile=applicant.profile, scheme="wikidata", value="Q95")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    assert page.evaluate(LEFT_BEHIND) == []
    before = page.locator("[data-select]").count()

    # A row of the identifiers' own markup, as the server draws one, swapped in after the
    # last: the native select and its icons, and nothing a script built.
    page.evaluate(
        """() => {
            const list = document.querySelector('[data-identifiers] ol, [data-identifiers] ul');
            const last = list.querySelector('li:last-of-type');
            const copy = last.cloneNode(true);
            copy.querySelectorAll('[data-select], [data-select-said]')
              .forEach((made) => made.remove());
            for (const select of copy.querySelectorAll('select')) {
              for (const name of ['data-select-ready', 'aria-hidden', 'tabindex']) {
                select.removeAttribute(name);
              }
            }
            copy.id = 'arrived-later';
            const html = copy.outerHTML.replaceAll('identifiers-1-', 'identifiers-9-');
            window.htmx.swap(list, html, {swapStyle: 'beforeend'});
        }"""
    )
    arrived = page.locator("#arrived-later select[name=identifiers-9-scheme]")
    expect(button_of(arrived)).to_be_visible()
    expect(page.locator("[data-select]")).to_have_count(before + 1)
    choose(page, arrived, "ORCID")
    expect(arrived).to_have_value("orcid")
    assert page.evaluate(LEFT_BEHIND) == []

    # Open its list, and take the row away under it.
    open_list(page, arrived)
    assert page.evaluate("() => document.querySelectorAll(':popover-open').length") == 1
    page.evaluate(
        """() => window.htmx.swap(
            document.getElementById('arrived-later'), '', {swapStyle: 'outerHTML'})"""
    )
    expect(page.locator("#arrived-later")).to_have_count(0)
    expect(page.locator("[data-select]")).to_have_count(before)
    assert page.evaluate("() => document.querySelectorAll(':popover-open').length") == 0
    assert page.evaluate(LEFT_BEHIND) == []
    # And the page still answers: the keys of another select work as they did.
    kind = page.locator("select[name=identifiers-0-scheme]")
    button_of(kind).focus()
    page.keyboard.press("ArrowDown")
    expect(list_of(page, kind)).to_be_visible()


def test_a_select_inside_its_label_or_spaced_by_a_margin_is_drawn_as_it_was(
    page: Page, live_server, administrator
):
    """Two ways a page writes a select that the wrapper round its button has to allow for.
    A label written round its select holds the button and the list as well, so the name is
    the label's own words and not everything in it. And a margin a page gives its children
    lands on the wrapper, which has no box, so the button takes it."""
    to_the_gallery(page, live_server.url)
    page.evaluate(
        """() => window.htmx.swap(document.getElementById('design-choices'), `
            <div class="space-y-3" id="written-otherwise">
              <label class="flex items-center gap-2">
                <span class="sr-only">Currency</span>
                <select name="currency" class="w-auto">
                  <option value="EUR">EUR</option><option value="USD">USD</option>
                </select>
              </label>
              <select id="spaced" aria-label="Spaced">
                <option>One</option><option>Two</option>
              </select>
              <p id="after-spaced">What comes after it.</p>
            </div>`, {swapStyle: 'beforeend'})"""
    )
    written = page.locator("#written-otherwise")
    currency = written.get_by_role("combobox", name="Currency", exact=True)
    expect(currency).to_have_count(1)
    expect(currency).to_have_text("EUR")
    currency.click()
    expect(page.get_by_role("listbox", name="Currency", exact=True)).to_be_visible()
    expect(currency).to_have_accessible_name("Currency"), "open, it is still called that"
    page.get_by_role("option", name="USD", exact=True).click()
    expect(written.locator("select[name=currency]")).to_have_value("USD")
    expect(currency).to_be_focused()

    spaced = button_of(page.locator("#spaced"))
    gap = page.locator("#after-spaced").bounding_box()["y"] - (
        spaced.bounding_box()["y"] + spaced.bounding_box()["height"]
    )
    assert abs(gap - 12) <= 0.5, f"the space under it is {gap}, not the 12 pixels asked for"


def test_only_one_list_is_open_and_a_click_elsewhere_closes_it(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    first = page.locator("#design-choice-icons")
    second = page.locator("#design-choice-groups")
    open_list(page, first)
    button_of(second).focus()
    page.keyboard.press("Enter")
    expect(list_of(page, second)).to_be_visible()
    expect(list_of(page, first)).to_be_hidden()
    assert page.evaluate("() => document.querySelectorAll(':popover-open').length") == 1
    page.locator("#design-choices-heading").click()
    expect(list_of(page, second)).to_be_hidden()
    expect(second).to_have_value("Europe/Lisbon"), "a click elsewhere chooses nothing"
    # Pressing the button of an open list closes it, and does not open it again.
    button_of(first).click()
    expect(list_of(page, first)).to_be_visible()
    button_of(first).click()
    expect(list_of(page, first)).to_be_hidden()


def test_in_a_disclosure_escape_closes_the_list_and_leaves_the_disclosure_open(
    page: Page,
    live_server,
    search,  # noqa: F811
):
    """The column chooser is a `<details>` that Escape closes. Its list of rows per page
    opens over it, and the first Escape is the list's."""
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/?view=table")
    chooser = page.locator("details[data-menu]", has=page.locator("#page-size-applications"))
    chooser.locator("summary").click()
    select = page.locator("#page-size-applications")
    button = button_of(select)
    expect(button).to_be_visible()

    button.focus()
    page.keyboard.press("ArrowDown")
    panel = list_of(page, select)
    found = placed(page, panel)
    assert found["inside"] and found["reachable"], found
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(chooser).to_have_attribute("open", "")
    expect(button).to_be_focused()

    # A choice from the list is a click inside the disclosure, which stays open for it.
    panel = open_list(page, select)
    panel.get_by_role("option").last.click()
    expect(chooser).to_have_attribute("open", "")
    expect(select).not_to_have_value("25")
    page.keyboard.press("Escape")
    expect(chooser).not_to_have_attribute("open", "")


def test_what_is_typed_in_the_box_is_not_work_and_a_choice_is(page: Page, live_server, applicant):
    """A form with something typed in it asks before the page is left (#258). The box that
    narrows a list sits inside that form and is none of its fields: typing in it changes
    nothing that would be lost. Choosing does."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")
    select = page.locator("[data-phone-country]")
    form = select.locator("xpath=ancestor::form")
    panel = open_list(page, select)
    panel.locator("[data-select-filter]").fill("portug")
    assert form.get_attribute("data-dirty") is None
    page.keyboard.press("Escape")
    assert form.get_attribute("data-dirty") is None

    panel = open_list(page, select)
    panel.locator("[data-select-filter]").fill("portug")
    page.keyboard.press("Enter")
    expect(select).to_have_value("PT")
    expect(form).to_have_attribute("data-dirty", "1")


# ------------------------------------------------------------- an answer that is required


def test_a_required_select_with_no_answer_takes_the_focus_and_says_why(
    page: Page,
    live_server,
    furnished,  # noqa: F811
):
    """The browser's own bubble points at the control that is wrong, which is out of
    sight. The button takes the focus instead and says why, in the page's language."""
    from postulo.accounts.models import Profile

    Profile.objects.filter(user=furnished["applicant"]).update(language="fr-FR")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/suggestions/")
    select = page.locator("select[name=application]").first
    button = button_of(select)
    form = select.locator("xpath=ancestor::form")
    here = page.url

    form.locator("button[type=submit]").click()
    expect(button).to_be_focused()
    expect(button).to_have_attribute("aria-invalid", "true")
    said = page.locator("[data-select-said]")
    expect(said).to_have_count(1)
    expect(said).to_have_attribute("role", "alert")
    words = said.inner_text().strip()
    assert words and words != "Choose one of the options.", "said in the page's language"
    assert said.get_attribute("id") in button.get_attribute("aria-describedby").split()
    assert page.url == here, "nothing was sent"

    # Asked twice, it is said once; an answer takes it back.
    form.locator("button[type=submit]").click()
    expect(page.locator("[data-select-said]")).to_have_count(1)
    panel = open_list(page, select)
    panel.get_by_role("option").nth(1).click()
    expect(page.locator("[data-select-said]")).to_have_count(0)
    assert button.get_attribute("aria-invalid") is None
    assert button.get_attribute("aria-describedby") is None


#: A form of two required controls, empty, added to the gallery: a select and a box, in
#: the order asked for. Each control notes whether its `invalid` was declined -- which is
#: what keeps the browser's bubble off it.
TWO_REQUIRED = """(order) => {
  const form = document.createElement('form');
  form.id = 'two-required';
  const select = '<label for=two-select>A</label><select id=two-select name=a required>'
    + '<option value="">-</option><option value=x>X</option></select>';
  const box = '<label for=two-box>B</label><input id=two-box name=b required>';
  form.innerHTML = (order === 'select first' ? select + box : box + select)
    + '<button id=two-send>Send</button>';
  form.addEventListener('submit', (event) => event.preventDefault());
  document.querySelector('#design-choices').appendChild(form);
  window.declined = {};
  for (const control of form.querySelectorAll('select, input')) {
    control.addEventListener('invalid', (event) => {
      window.declined[control.id] = event.defaultPrevented;
    });
  }
  document.dispatchEvent(new CustomEvent('htmx:afterSwap'));
}"""


@pytest.mark.parametrize("order", ["select first", "box first"])
def test_the_first_wrong_control_keeps_the_focus_whichever_it_is(
    page: Page, live_server, administrator, order
):
    to_the_gallery(page, live_server.url)
    page.evaluate(TWO_REQUIRED, order)
    select = page.locator("#two-select")
    button = button_of(select)
    expect(button).to_be_attached()
    page.locator("#two-send").click()
    expect(page.locator("#two-required [data-select-said]")).to_have_count(1)
    declined = page.evaluate("() => window.declined")
    if order == "select first":
        # The button has the focus and says why; the box is told nothing yet, so no
        # bubble of the browser's points elsewhere.
        expect(button).to_be_focused()
        # A fixed wait on purpose: the focus must stay on the button for 100 ms, not be
        # taken by a late browser bubble or the box.
        page.wait_for_timeout(100)
        expect(button).to_be_focused()
        assert declined == {"two-select": True, "two-box": True}
    else:
        # The box comes first, and the browser reports it as it would have.
        expect(page.locator("#two-box")).to_be_focused()
        assert declined == {"two-select": True, "two-box": False}
    # The next check is a check of its own.
    if order == "select first":
        page.locator("#two-box").fill("b")
        select.select_option("x")
        page.locator("#two-box").fill("")
        page.locator("#two-send").click()
        expect(page.locator("#two-box")).to_be_focused()
        assert page.evaluate("() => window.declined['two-box']") is False


# --------------------------------------------------- a press outside an open list


def test_a_click_outside_an_open_list_closes_it_and_presses_nothing_else(
    page: Page, live_server, administrator
):
    to_the_gallery(page, live_server.url)
    icons = page.locator("#design-choice-icons")
    groups = page.locator("#design-choice-groups")
    dialog = page.locator("#design-choice-dialog")
    opens_dialog = page.get_by_role("button", name="Open a dialog with a choice in it")

    open_list(page, icons)
    opens_dialog.click()
    expect(list_of(page, icons)).to_be_hidden()
    assert dialog.evaluate("(d) => d.matches(':popover-open, [open]')") is False
    expect(button_of(icons)).to_have_attribute("aria-expanded", "false")

    # Another select's button: the first list closes and the second does not open; a
    # second press opens it.
    open_list(page, icons)
    button_of(groups).click()
    expect(list_of(page, icons)).to_be_hidden()
    expect(list_of(page, groups)).to_be_hidden()
    button_of(groups).click()
    expect(list_of(page, groups)).to_be_visible()
    page.keyboard.press("Escape")

    # A press that comes after, with no list open, presses what it lands on.
    opens_dialog.click()
    expect(dialog).to_have_attribute("open", "")
    assert dialog.evaluate("(dialog) => dialog.matches(':modal')")

    # In a modal dialog, a press on its backdrop closes the list and leaves the dialog.
    kind = page.locator("#design-choice-dialog-kind")
    open_list(page, kind)
    page.mouse.click(5, 5)
    expect(list_of(page, kind)).to_be_hidden()
    expect(dialog).to_have_attribute("open", "")


def test_a_tap_outside_an_open_list_closes_it_and_presses_nothing_else(
    browser: Browser, live_server, applicant
):
    context = browser.new_context(viewport=PHONE, has_touch=True, is_mobile=True)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/contacts/new/")
        # Something to press above the list: a button that counts its presses.
        page.evaluate(
            """() => {
                window.pressed = 0;
                const press = document.createElement('button');
                press.type = 'button';
                press.id = 'press-me';
                press.textContent = 'Press me';
                press.addEventListener('click', () => { window.pressed += 1; });
                const field = document.querySelector('[data-phone-country]')
                    .closest('fieldset, .field');
                field.parentNode.insertBefore(press, field);
            }"""
        )
        select = page.locator("[data-phone-country]")
        button = button_of(select)
        press = page.locator("#press-me")
        press.evaluate("(el) => el.scrollIntoView({block: 'start'})")
        button.tap()
        panel = list_of(page, select)
        expect(panel).to_be_visible()
        box = press.bounding_box()
        middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        assert not panel.evaluate(
            "(panel, at) => panel.contains(document.elementFromPoint(at[0], at[1]))", middle
        )
        page.touchscreen.tap(*middle)
        expect(panel).to_be_hidden()
        # A fixed wait on purpose: 300 ms for a late synthetic click from the tap to land;
        # the button must stay unpressed.
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.pressed") == 0
        # With the list closed, the same tap presses the button.
        page.touchscreen.tap(*middle)
        page.wait_for_function("() => window.pressed === 1")
    finally:
        context.close()


# ------------------------------------------------------------------------------ a finger


def test_on_a_phone_the_list_is_at_least_as_usable_as_the_platforms(
    browser: Browser, live_server, applicant
):
    """A phone loses its own picker, by decision, so the list has to be usable by a finger:
    options tall enough, a box that does not make the browser zoom, and a list that scrolls
    inside itself and leaves the page where it was."""
    context = browser.new_context(viewport=PHONE, has_touch=True, is_mobile=True)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/contacts/new/")
        assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        select = page.locator("[data-phone-country]")
        button = button_of(select)
        button.scroll_into_view_if_needed()
        button.tap()
        panel = list_of(page, select)
        expect(panel).to_be_visible()
        found = placed(page, panel)
        assert found["inside"] and found["reachable"], found

        # The focus stays on the button: a box that took it would call up the keyboard over
        # the list somebody opened to look down. The box is a tap away.
        box = panel.locator("[data-select-filter]")
        expect(box).to_be_visible()
        expect(box).not_to_be_focused()
        expect(button).to_be_focused()
        assert box.evaluate("(el) => parseFloat(getComputedStyle(el).fontSize)") >= 16
        assert box.bounding_box()["height"] >= 24
        heights = options_of(page, select).evaluate_all(
            "(rows) => rows.slice(0, 12).map((row) => row.getBoundingClientRect().height)"
        )
        assert min(heights) >= 24, heights

        listbox = panel.locator('[role="listbox"]')
        scrolls = listbox.evaluate(
            """(list) => ({
                taller: list.scrollHeight > list.clientHeight + 100,
                overflow: getComputedStyle(list).overflowY,
                contained: getComputedStyle(list).overscrollBehaviorY,
            })"""
        )
        assert scrolls == {"taller": True, "overflow": "auto", "contained": "contain"}
        page_at = page.evaluate("() => window.scrollY")
        listbox.evaluate("(list) => { list.scrollTop = 600; }")
        assert listbox.evaluate("(list) => list.scrollTop") > 0
        assert page.evaluate("() => window.scrollY") == page_at

        box.tap()
        expect(box).to_be_focused()
        box.fill("portug")
        options_of(page, select).first.tap()
        expect(panel).to_be_hidden()
        expect(select).to_have_value("PT")
        expect(button).to_contain_text("+351")

        # A keyboard attached to it still works the list from the button, and a letter
        # typed there goes into the box.
        button.tap()
        expect(panel).to_be_visible()
        page.keyboard.press("ArrowDown")
        assert active_option(page, select) != "+351 Portugal"
        page.keyboard.type("japan")
        expect(box).to_be_focused()
        expect(options_of(page, select)).to_have_text(["+81 Japan"])
        page.keyboard.press("Enter")
        expect(select).to_have_value("JP")
    finally:
        context.close()


# ----------------------------------------------------- narrow, in four languages, mirrored


@pytest.mark.parametrize("language", ["en-GB", "de", "el", "ar"])
def test_at_320_pixels_the_lists_stay_in_the_window_in_four_languages(
    page: Page, live_server, applicant, language
):
    from postulo.accounts.models import PersonIdentifier, Profile
    from postulo.core.models import PhoneNumber, PostalAddress

    profile = applicant.profile
    PhoneNumber.objects.create(owner=applicant, holder=profile, kind="work", number="+351912345678")
    PostalAddress.objects.create(
        owner=applicant, holder=profile, kind="home", municipality="Lisboa", country="PT"
    )
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value="0000-0002-1825-0097")
    sign_in(page, live_server.url)
    Profile.objects.filter(user=applicant).update(language=language)
    page.set_viewport_size(NARROWEST)
    page.goto(f"{live_server.url}/accounts/profile/")
    rtl = language == "ar"
    assert page.evaluate("() => document.documentElement.dir") == ("rtl" if rtl else "ltr")
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], language

    for name in (
        "form_of_address",
        "record_language",
        "phone_numbers-0-kind",
        "phone_numbers-0-number_0",
        "addresses-0-kind",
        "addresses-0-country",
        "identifiers-0-scheme",
    ):
        select = page.locator(f"select[name={name}]")
        button = button_of(select)
        button.scroll_into_view_if_needed()
        box = button.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 320.5, (language, name, box)
        assert box["height"] >= 24, (language, name, box)
        button.click()
        panel = list_of(page, select)
        found = placed(page, panel)
        assert found["open"] and found["inside"], (language, name, found)
        assert found["seen"] and found["reachable"], (language, name, found)
        if found["startsWithButton"]:
            pass  # where there is room, it starts where its button starts, either way round
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], (language, name)

    # Mirrored: in a right-to-left page the flag is at the right of the words, and the
    # chevron at the left of the button.
    country = button_of(page.locator("select[name=addresses-0-country]"))
    sides = country.evaluate(
        """(button) => {
            const flag = button.querySelector('img.flag').getBoundingClientRect();
            const words = button.querySelector('[data-select-text]').getBoundingClientRect();
            const chevron = button.querySelector(':scope > svg').getBoundingClientRect();
            return {flagFirst: flag.left < words.left, chevronLast: chevron.left > words.left};
        }"""
    )
    assert sides == {"flagFirst": not rtl, "chevronLast": not rtl}, (language, sides)


def test_a_wide_list_near_the_end_of_a_row_is_moved_into_the_window(
    page: Page, live_server, administrator
):
    """A list starts where its button starts, and a list wider than what is left of the
    window from there is moved back into it rather than cut."""
    to_the_gallery(page, live_server.url)
    page.set_viewport_size(NARROWEST)
    select = page.locator("#design-choice-long")
    button_of(select).scroll_into_view_if_needed()
    panel = open_list(page, select)
    found = placed(page, panel)
    assert found["inside"] and found["reachable"], found


# ------------------------------------------------------- forced colours, reduced motion


def test_under_forced_colours_the_button_has_a_border_and_the_keys_place_is_marked(
    page: Page, live_server, administrator
):
    page.emulate_media(forced_colors="active")
    to_the_gallery(page, live_server.url)
    select = page.locator("#design-choice-icons")
    button = button_of(select)
    border = button.evaluate(
        "(el) => [getComputedStyle(el).borderTopWidth, getComputedStyle(el).borderTopStyle]"
    )
    assert border == ["1px", "solid"]
    # An outline is where the focus is and nowhere else: a transparent one, which is what
    # hiding an outline leaves under forced colours, is painted by the theme all the same.
    widths = page.locator("#design-choices [data-select-trigger]:not(:disabled)").evaluate_all(
        "(buttons) => buttons.map((el) => parseFloat(getComputedStyle(el).outlineWidth))"
    )
    assert len(widths) >= 3 and set(widths) == {0}, widths
    button.focus()
    page.keyboard.press("ArrowDown")
    outline = button.evaluate(
        "(el) => [getComputedStyle(el).outlineStyle, parseFloat(getComputedStyle(el).outlineWidth)]"
    )
    assert outline[0] == "solid" and outline[1] >= 2, outline
    panel = list_of(page, select)
    colours = panel.locator('[role="option"]').evaluate_all(
        "(rows) => rows.map((row) => getComputedStyle(row).backgroundColor)"
    )
    assert colours[0] != colours[1], "the option the keys are on is told from the others"
    assert len(set(colours[1:])) == 1
    framed = panel.locator('[role="option"], [role="listbox"]').evaluate_all(
        "(parts) => parts.map((el) => parseFloat(getComputedStyle(el).outlineWidth))"
    )
    assert set(framed) == {0}, "no option has a frame of its own"
    assert panel.evaluate("(el) => parseFloat(getComputedStyle(el).borderTopWidth)") >= 1


def test_nothing_moves_as_a_list_opens(page: Page, live_server, administrator):
    """A panel that glides into place is motion nobody asked for: there is none, whatever
    the system says, so there is nothing for `prefers-reduced-motion` to switch off."""
    to_the_gallery(page, live_server.url)
    for motion in ("reduce", "no-preference"):
        page.emulate_media(reduced_motion=motion)
        page.reload()
        select = page.locator("#design-choice-icons")
        panel = open_list(page, select)
        moving = panel.evaluate(
            """(panel) => [panel, ...panel.querySelectorAll('*')].filter((el) => {
                const style = getComputedStyle(el);
                const long = (value) => value.split(',').some((part) => parseFloat(part) > 0.001);
                return (long(style.transitionDuration) && style.transitionProperty !== 'none')
                    || (style.animationName !== 'none' && long(style.animationDuration));
            }).map((el) => el.outerHTML.slice(0, 80))"""
        )
        assert moving == [], (motion, moving)
        page.keyboard.press("Escape")


# --------------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_is_clean_with_a_list_open(
    page: Page,
    live_server,
    furnished,  # noqa: F811
    axe_source,  # noqa: F811
    scheme,
):
    from tests.e2e.conftest import PASSWORD

    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    failures = []

    def check(where: str) -> None:
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{where} ({scheme})", found))

    page.goto(f"{base}/server/design/")
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button").first.click()
        page.goto(f"{base}/server/design/")
    for ident in ("design-choice-icons", "design-choice-long", "design-choice-groups"):
        select = page.locator(f"#{ident}")
        button_of(select).scroll_into_view_if_needed()
        open_list(page, select)
        check(f"the gallery with {ident} open")
        page.keyboard.press("Escape")
    page.get_by_role("button", name="Open a dialog with a choice in it").click()
    open_list(page, page.locator("#design-choice-dialog-kind"))
    check("the gallery's dialog with its list open")

    page.goto(f"{base}/applications/?view=board")
    open_list(page, page.locator("[data-card] select[name=status]").first)
    check("the board with a card's status open")

    page.goto(f"{base}/accounts/profile/")
    for name in ("phone_numbers-0-number_0", "record_language"):
        select = page.locator(f"select[name={name}]")
        button_of(select).scroll_into_view_if_needed()
        open_list(page, select)
        check(f"your details with {name} open")
        page.keyboard.press("Escape")

    page.goto(f"{base}/applications/suggestions/")
    select = page.locator("select[name=application]").first
    select.locator("xpath=ancestor::form").locator("button[type=submit]").click()
    expect(page.locator("[data-select-said]")).to_have_count(1)
    check("suggestions with an answer asked for")

    assert not failures, "\n\n".join(failures)
