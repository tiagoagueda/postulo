"""The country and the number as one group, in a real browser (#304).

The markup, the refusals and the mark a stored number wears are unit-tested. What needs a
browser is what the stylesheet does with them: the group wrapping so that the number has a
line of its own on a phone and shares one with the chooser where there is room; the row
saying what it is before the number (#213), with the kind on one line with the number on
a wide screen and above it on a narrow one; the name box following the kind with scripts
off; a number written left to right on a right-to-left page; which of the group's two
controls has the focus being visible; and axe reading the whole of it, marks and all, in
both themes.

Nothing here counts lines of text. CI draws in DejaVu Sans and a desktop in its own font,
so what is asserted is what the issue promises: which control is beside or below which,
that the number box is wide enough to type a number in, and that nothing leaves the page.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .selects import DRAWN, button_of, drawn
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_phone_flag import sign_in
from .test_reflow import SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e

PHONE = {"width": 320, "height": 800}
DESKTOP = {"width": 1280, "height": 900}

#: The widths a row is wide enough at for the kind to sit beside the number: where the
#: stylesheet starts doing it, a laptop, and a desktop.
WIDE = (768, 1024, 1280)

#: Every row of the block: where its controls are, and whether its name box is drawn.
#: A select is measured where a person sees it: at the button built for it, where scripts
#: run (#301), and at the select itself where they do not.
ROWS = """() => [...document.querySelectorAll('#section-phones li')].map((row) => {
  const drawn = __DRAWN__;
  const box = (selector) => {
    const el = drawn(row.querySelector(selector));
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            width: r.width, height: r.height, drawn: r.width > 0 && r.height > 0};
  };
  return {
    kind: box('select[name$="-kind"]'),
    name: box('input[name$="-label"]'),
    group: box('[data-phone-field]'),
    chooser: box('[data-phone-country]'),
    number: box('input[name$="-number_1"]'),
    primary: box('input[name$="-primary"]'),
  };
})""".replace("__DRAWN__", DRAWN)

#: Where the first digit and the last digit of an element's text are drawn. A number
#: written left to right has its first digit to the left of its last, whatever the page.
ENDS = """(el) => {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const digits = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (let at = 0; at < node.textContent.length; at++) {
      if (/[0-9]/.test(node.textContent[at])) digits.push([node, at]);
    }
  }
  const x = ([node, at]) => {
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + 1);
    return range.getBoundingClientRect().left;
  };
  return {first: x(digits[0]), last: x(digits[digits.length - 1]), digits: digits.length};
}"""


def on_one_line(one: dict, other: dict) -> bool:
    """Whether two boxes share a line: each begins before the other ends."""
    return one["top"] < other["bottom"] and other["top"] < one["bottom"]


#: Where each control of the first row is, and what the group as a whole occupies. The
#: flag is the one that is drawn: in the chooser's button, beside its words (#301).
BOXES = """() => {
  const row = document.querySelector('#section-phones li');
  const drawn = __DRAWN__;
  const box = (selector) => {
    const r = drawn(row.querySelector(selector)).getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            width: r.width, height: r.height};
  };
  const flags = [...row.querySelectorAll('[data-phone-field] img.flag')]
    .filter((flag) => flag.getBoundingClientRect().width > 0);
  const edges = (el) => {
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
  };
  return {
    kind: box('select[name$="-kind"]'),
    group: box('[data-phone-field]'),
    chooser: box('[data-phone-country]'),
    number: box('input[name$="-number_1"]'),
    flags: flags.length,
    flag: edges(flags[0]),
    words: edges(drawn(row.querySelector('[data-phone-country]'))
      .querySelector('[data-select-text]')),
    primary: box('.primary-choice'),
  };
}""".replace("__DRAWN__", DRAWN)


@pytest.fixture
def numbers(applicant):
    """Four rows that between them draw everything the block can: a number that is fine,
    one the plans call impossible, one kept as it was typed, and an *Other* with its name."""
    from postulo.core.models import PhoneNumber

    profile = applicant.profile
    rows = [
        ("+33612345678", "mobile", ""),
        ("+33123", "work", ""),
        ("0612345678", "home", ""),
        ("+5511912345678", "other", "The office in São Paulo"),
    ]
    return [
        PhoneNumber.objects.create(
            owner=applicant,
            holder=profile,
            number=number,
            kind=kind,
            label=label,
            is_primary=index == 0,
        )
        for index, (number, kind, label) in enumerate(rows)
    ]


def your_details(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/profile/")
    page.locator("#section-phones").scroll_into_view_if_needed()


def test_on_a_phone_the_number_has_a_line_of_its_own(page: Page, live_server, numbers):
    """Beside a chooser of fixed width the box used to be left a few pixels wide: unusable,
    and a WCAG 2.2 2.5.8 failure. Inside the one group it drops under the chooser and takes
    the whole line, and the group itself has the row's line to itself."""
    page.set_viewport_size(PHONE)
    sign_in(page, live_server.url)
    your_details(page, live_server.url)

    at = page.evaluate(BOXES)

    assert at["number"]["top"] >= at["chooser"]["bottom"] - 1, "under the chooser"
    assert at["number"]["width"] >= at["group"]["width"] - 4, "and as wide as the group"
    assert at["number"]["width"] >= 176, "eleven rem, room for a number with its spaces"
    assert at["number"]["height"] >= 24 and at["chooser"]["height"] >= 24
    assert at["number"]["bottom"] <= at["group"]["bottom"] + 1, "and inside the group's frame"
    assert abs(at["group"]["width"] - at["kind"]["width"]) <= 2, "no wider than the row"
    assert at["kind"]["bottom"] <= at["group"]["top"], "the kind comes first, above it"
    assert at["group"]["bottom"] <= at["primary"]["top"], "and what is done with it, after"
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0
    assert not page.evaluate(SPILLS)


def test_with_room_the_chooser_and_the_number_share_a_line(page: Page, live_server, numbers):
    page.set_viewport_size(DESKTOP)
    sign_in(page, live_server.url)
    your_details(page, live_server.url)

    at = page.evaluate(BOXES)

    assert abs(at["number"]["top"] - at["chooser"]["top"]) <= 2, "on one line"
    assert at["chooser"]["right"] <= at["number"]["left"] + 1, "the country first"
    assert at["number"]["width"] > at["chooser"]["width"], "and the room goes to the number"
    assert at["flags"] == 1, "one flag, in the chooser: the one beside it is put away"
    assert at["flag"]["left"] >= at["chooser"]["left"], "the flag is inside the chooser"
    assert at["flag"]["right"] <= at["words"]["left"] + 1, "before the country's words"
    assert on_one_line(at["kind"], at["number"]), "and the kind is on that line too (#213)"
    assert at["kind"]["right"] <= at["group"]["left"], "first, as it is read"
    assert at["group"]["bottom"] <= at["primary"]["top"]


def test_the_group_is_not_a_size_container(page: Page, live_server, numbers):
    """Basecoat makes a `role="group"` inside a field a container for its container
    queries. On a slow renderer Chromium then drew the telephone group as an empty frame,
    two pixels high, with its flag, its chooser and its box in the document and no boxes on
    the page: seven draws in twenty-four with the processor throttled, and now and then on
    a loaded machine, which is how it was found. Without the containment, none in
    thirty-two. The group asks no container query, so it is not a container, and every
    control in every one has a box."""
    page.set_viewport_size(PHONE)
    sign_in(page, live_server.url)
    your_details(page, live_server.url)

    groups = page.evaluate(
        """() => [...document.querySelectorAll('[data-phone-field]')].map((group) => ({
            container: getComputedStyle(group).containerType,
            boxes: [...group.querySelectorAll('select, input')].map(
                (control) => __DRAWN__(control).getBoundingClientRect().height),
        }))""".replace("__DRAWN__", DRAWN)
    )

    assert len(groups) == 5, "four rows and the empty one"
    for group in groups:
        assert group["container"] == "normal", group
        assert all(height >= 24 for height in group["boxes"]), group


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_the_one_box_is_whole_inside_its_group(page: Page, live_server, numbers, viewport):
    """While *Several telephone numbers* is off the field is one of a form's fields, in a
    column half the form wide beside fields of other heights. Basecoat makes a control as
    tall as its group, and with the group on two lines each control took the whole height:
    the number was pushed out under the frame, where the group clips. Whichever way the
    group falls, both controls are inside it, and the page is no wider than the window."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=numbers[0].owner, state=PluginPolicy.State.FORCED_OFF
    )
    page.set_viewport_size(viewport)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    group = page.locator("[data-phone-field]")
    expect(group).to_have_count(1)
    expect(group.locator("input")).to_have_value("6 12 34 56 78")

    frame = group.bounding_box()
    for control in (drawn(group.locator("select")), group.locator("input")):
        box = control.bounding_box()
        assert box["height"] >= 24
        assert box["y"] >= frame["y"] - 1
        assert box["y"] + box["height"] <= frame["y"] + frame["height"] + 1, "inside the frame"
        assert box["x"] + box["width"] <= frame["x"] + frame["width"] + 1
    assert group.locator("input").bounding_box()["width"] >= 176
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0


def two_rows(owner, holder, first: str, second: str) -> None:
    """A row of the kind with the longest name, and an *Other* with the name it asks for."""
    from postulo.core.models import PhoneNumber

    PhoneNumber.objects.create(
        owner=owner, holder=holder, number=first, kind="switchboard", is_primary=True
    )
    PhoneNumber.objects.create(
        owner=owner, holder=holder, number=second, kind="other", label="The office in São Paulo"
    )


@pytest.mark.parametrize("language", ["en-GB", "de", "el"])
def test_on_a_wide_screen_the_kind_sits_on_one_line_with_the_number(
    page: Page, live_server, applicant, language
):
    """What #213 asks of a row: "on a wide screen, the kind sits on one line with the
    value". From 768 pixels the kind is in its column and the country and the number, one
    line themselves, are beside it. A row that shows its name has the kind and the name on
    that line and the group on the next: the three together are wider than the card. On a
    phone the group is under the kind, as it was. On *Your details* and on a contact's
    form, and in German and Greek, whose kinds are longer words than the English ones."""
    from postulo.jobs.models import Company, Contact

    applicant.profile.language = language
    applicant.profile.save(update_fields=["language"])
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    contact = Contact.objects.create(owner=applicant, company=company, name="Cave Johnson")
    two_rows(applicant, applicant.profile, "+33612345678", "+5511912345678")
    two_rows(applicant, contact, "+33698765432", "+5511912345679")
    sign_in(page, live_server.url)

    for path in ("/accounts/profile/", f"/jobs/contacts/{contact.pk}/edit/"):
        for width in WIDE:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}{path}")
            plain, other = page.evaluate(ROWS)[:2]
            where = f"{path} at {width} in {language}"

            assert not plain["name"]["drawn"], where
            assert on_one_line(plain["kind"], plain["number"]), where
            assert plain["kind"]["right"] <= plain["group"]["left"], f"the kind first: {where}"
            assert on_one_line(plain["chooser"], plain["number"]), f"the group whole: {where}"
            assert plain["number"]["width"] >= 176, where
            assert plain["group"]["bottom"] <= plain["primary"]["top"], where

            assert other["name"]["drawn"], where
            assert on_one_line(other["kind"], other["name"]), where
            assert other["kind"]["right"] <= other["name"]["left"], where
            under = max(other["kind"]["bottom"], other["name"]["bottom"])
            assert other["group"]["top"] >= under, f"the group on the next line: {where}"
            assert on_one_line(other["chooser"], other["number"]), where
            assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0, where
            assert not page.evaluate(SPILLS), where

        page.set_viewport_size(PHONE)
        page.goto(f"{live_server.url}{path}")
        plain, other = page.evaluate(ROWS)[:2]
        where = f"{path} on a phone in {language}"
        assert not on_one_line(plain["kind"], plain["number"]), where
        assert plain["kind"]["bottom"] <= plain["group"]["top"], where
        assert other["kind"]["bottom"] <= other["name"]["top"], where
        assert other["name"]["bottom"] <= other["group"]["top"], where
        assert plain["group"]["right"] <= plain["kind"]["right"] + 1, f"no wider: {where}"
        assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0, where
        assert not page.evaluate(SPILLS), where


def test_the_row_follows_the_kind_on_a_wide_screen_with_scripts_off(
    browser, live_server, applicant
):
    """The arrangement is the stylesheet's, following the select: choose *Other* and the
    name appears beside the kind and the group goes to the line under them; choose another
    kind and it comes back up. No script has a part in it."""
    two_rows(applicant, applicant.profile, "+33612345678", "+5511912345678")
    context = browser.new_context(
        java_script_enabled=False, viewport={"width": 1024, "height": 900}
    )
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        your_details(page, live_server.url)
        kind = page.locator("#section-phones li").first.locator("select[name$='-kind']")

        plain = page.evaluate(ROWS)[0]
        assert on_one_line(plain["kind"], plain["number"])

        kind.select_option("other")
        chosen = page.evaluate(ROWS)[0]
        assert chosen["name"]["drawn"] and on_one_line(chosen["kind"], chosen["name"])
        assert chosen["group"]["top"] >= max(chosen["kind"]["bottom"], chosen["name"]["bottom"])

        kind.select_option("mobile")
        again = page.evaluate(ROWS)[0]
        assert not again["name"]["drawn"] and on_one_line(again["kind"], again["number"])
    finally:
        context.close()


def test_a_number_is_written_left_to_right_on_a_right_to_left_page(
    page: Page, live_server, numbers, applicant
):
    """In Arabic a stored `+33612345678` showed in the box as `78 56 34 12 6`, and a
    contact's `+33 6 98 76 54 32` as `32 54 76 98 6 33+`: each group of digits is a number
    of its own to a right-to-left line, which lays numbers out from the right. Wherever a
    grouped number is drawn its first digit is to the left of its last: the box, a link,
    the sentence under a row, and the heading of the dialog that asks before removing it."""
    from postulo.core.models import PhoneNumber
    from postulo.jobs.models import Company, Contact

    applicant.profile.language = "ar"
    applicant.profile.save(update_fields=["language"])
    PhoneNumber.objects.create(
        owner=applicant, holder=applicant.profile, number="+330612345679", kind="work"
    )
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    contact = Contact.objects.create(owner=applicant, company=company, name="Cave Johnson")
    PhoneNumber.objects.create(
        owner=applicant, holder=contact, number="+33698765432", kind="work", is_primary=True
    )
    page.set_viewport_size(DESKTOP)
    sign_in(page, live_server.url)
    your_details(page, live_server.url)
    assert page.evaluate("document.documentElement.dir") == "rtl"

    # The measure can see the fault: the same number with nothing to isolate it.
    page.evaluate(
        """() => {
          const probe = document.createElement('p');
          probe.id = 'left-to-the-line';
          probe.textContent = '+33 6 98 76 54 32';
          document.querySelector('main').append(probe);
        }"""
    )
    unisolated = page.locator("#left-to-the-line").evaluate(ENDS)
    assert unisolated["first"] > unisolated["last"], "a right-to-left line reverses the groups"

    box = page.locator("#section-phones li input[name$='-number_1']").first
    expect(box).to_have_value("6 12 34 56 78")
    style = box.evaluate(
        "(el) => { const s = getComputedStyle(el); return [s.direction, s.textAlign]; }"
    )
    assert style == ["ltr", "end"], "written left to right, kept to the side the page starts at"
    size = box.bounding_box()
    box.click(position={"x": 3, "y": size["height"] / 2})
    assert box.evaluate("(el) => el.selectionStart") == 0, "the first digit is at the left"
    box.click(position={"x": size["width"] - 3, "y": size["height"] / 2})
    assert box.evaluate("(el) => el.selectionStart") == len("6 12 34 56 78"), "the last, right"

    said = page.locator("[data-phone-mark='miswritten'] bdi").evaluate(ENDS)
    assert said["digits"] == 11 and said["first"] < said["last"], "in the sentence under a row"

    page.locator("#section-phones [data-remove-trigger]").first.click()
    heading = page.locator("dialog[open] bdi").first
    expect(heading).to_have_text("+33 6 12 34 56 78")
    asked = heading.evaluate(ENDS)
    assert asked["first"] < asked["last"], "in the dialog's heading"

    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")
    link = page.locator("a[href='tel:+33698765432']")
    expect(link).to_have_text("+33 6 98 76 54 32")
    drawn = link.evaluate(ENDS)
    assert drawn["digits"] == 11 and drawn["first"] < drawn["last"], "in a link"


def test_left_to_right_the_number_starts_where_every_box_starts(page: Page, live_server, numbers):
    """The alignment is only for a right-to-left page: on any other the box is as it was."""
    sign_in(page, live_server.url)
    your_details(page, live_server.url)

    box = page.locator("#section-phones li input[name$='-number_1']").first
    style = box.evaluate(
        "(el) => { const s = getComputedStyle(el); return [s.direction, s.textAlign]; }"
    )
    assert style == ["ltr", "start"]


def test_right_to_left_mirrors_the_group(page: Page, live_server, numbers, applicant):
    applicant.profile.language = "ar"
    applicant.profile.save(update_fields=["language"])
    page.set_viewport_size(DESKTOP)
    sign_in(page, live_server.url)
    your_details(page, live_server.url)

    at = page.evaluate(BOXES)

    assert at["number"]["right"] <= at["chooser"]["left"] + 1, "the country at the start"
    assert at["flag"]["right"] <= at["chooser"]["right"], "the flag at the chooser's start"
    assert at["words"]["right"] <= at["flag"]["left"] + 1, "and its words after it"


def test_choosing_a_country_moves_nothing(page: Page, live_server, applicant):
    """The flag's width is kept whether or not a flag is showing."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")
    chooser = page.locator("[data-phone-country]")
    button = button_of(chooser)
    words = button.locator("[data-select-text]")
    expect(button.locator("img.flag")).to_have_count(0)
    before, said = button.bounding_box(), words.bounding_box()

    chooser.select_option("PT")

    expect(button.locator("img.flag")).to_have_count(1)
    after = button.bounding_box()
    assert abs(after["x"] - before["x"]) < 1 and abs(after["width"] - before["width"]) < 1
    assert abs(words.bounding_box()["x"] - said["x"]) < 1, "nor the words inside it"


def test_the_group_is_one_thing_with_two_named_controls(page: Page, live_server, numbers):
    sign_in(page, live_server.url)
    your_details(page, live_server.url)

    group = page.get_by_role("group", name="Telephone number", exact=True).first
    # One combobox by that name: the chooser's own button. The native select it drives is
    # still what holds the country, hidden from the tree (#301).
    chooser = group.get_by_role("combobox", name="Country the number is in")
    expect(chooser).to_have_count(1)
    expect(chooser).to_have_text("+33 France")
    expect(group.locator("[data-phone-country]")).to_have_value("FR")
    expect(group.get_by_role("textbox", name="Phone")).to_have_value("6 12 34 56 78")
    assert "/flags/fr" in chooser.locator("img.flag").get_attribute("src")
    expect(group.locator("[data-phone-flag]")).to_be_hidden()


def test_the_half_that_has_the_focus_is_the_one_outlined(page: Page, live_server, numbers):
    """Two controls inside one ring: the ring alone would not say which of them has the
    focus. Reached by the keyboard, in the order the row reads."""
    sign_in(page, live_server.url)
    your_details(page, live_server.url)
    row = page.locator("#section-phones li").first
    kind = button_of(row.locator("select[name$='-kind']"))
    chooser = button_of(row.locator("[data-phone-country]"))
    number = row.locator("input[name$='-number_1']")

    def outlined(control) -> bool:
        style = control.evaluate(
            "(el) => { const s = getComputedStyle(el);"
            " return [s.outlineStyle, parseFloat(s.outlineWidth), parseFloat(s.outlineOffset)]; }"
        )
        return style[0] == "solid" and style[1] >= 2 and style[2] < 0

    kind.focus()
    page.keyboard.press("Tab")
    expect(chooser).to_be_focused()
    assert outlined(chooser) and not outlined(number)

    page.keyboard.press("Tab")
    expect(number).to_be_focused()
    assert outlined(number) and not outlined(chooser), "inside the group, which clips the rest"


def test_forced_colours_keep_the_focus_inside_the_group(page: Page, live_server, numbers):
    """The outline every field gets under forced colours sits two pixels outside it, and the
    group clips what is outside its halves (#227)."""
    page.emulate_media(forced_colors="active")
    sign_in(page, live_server.url)
    your_details(page, live_server.url)
    number = page.locator("#section-phones li input[name$='-number_1']").first

    page.locator("#section-phones li [data-phone-country]").first.focus()
    page.keyboard.press("Tab")

    expect(number).to_be_focused()
    offset = number.evaluate("(el) => parseFloat(getComputedStyle(el).outlineOffset)")
    assert offset < 0
    # And the same for the other half, which is the chooser's button.
    page.keyboard.press("Shift+Tab")
    chooser = button_of(page.locator("#section-phones li [data-phone-country]").first)
    expect(chooser).to_be_focused()
    assert chooser.evaluate("(el) => parseFloat(getComputedStyle(el).outlineOffset)") < 0


def test_the_name_follows_the_kind(page: Page, live_server, numbers):
    sign_in(page, live_server.url)
    your_details(page, live_server.url)
    rows = page.locator("#section-phones li")

    expect(rows.nth(0).locator("input[name$='-label']")).to_be_hidden()
    expect(rows.nth(3).locator("input[name$='-label']")).to_be_visible()

    rows.nth(0).locator("select[name$='-kind']").select_option("other")
    name = rows.nth(0).locator("input[name$='-label']")
    expect(name).to_be_visible()
    kind = button_of(rows.nth(0).locator("select[name$='-kind']")).bounding_box()
    group = rows.nth(0).locator("[data-phone-field]").bounding_box()
    assert name.bounding_box()["y"] + name.bounding_box()["height"] <= group["y"] + 1
    assert kind["x"] < name.bounding_box()["x"], "beside the kind, before the number"

    rows.nth(0).locator("select[name$='-kind']").select_option("work")
    expect(name).to_be_hidden()


def test_with_scripts_off_choosing_other_reaches_the_name(browser, live_server, numbers):
    """Nothing about the name box is a script's: the stylesheet follows the select, so the
    name the server then demands can be given."""
    from postulo.core.models import PhoneNumber

    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        your_details(page, live_server.url)
        row = page.locator("#section-phones li").first
        name = row.locator("input[name$='-label']")
        expect(name).to_be_hidden()

        row.locator("select[name$='-kind']").select_option("other")
        expect(name).to_be_visible()
        name.fill("Paris desk")
        page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    finally:
        context.close()

    saved = PhoneNumber.objects.get(pk=numbers[0].pk)
    assert saved.kind == "other" and saved.label == "Paris desk"
    assert saved.number == "+33612345678", "untouched, so exactly as it was"
    assert PhoneNumber.objects.get(pk=numbers[1].pk).number == "+33123", "and so is this one"


def test_a_refused_number_is_announced_on_the_box(page: Page, live_server, numbers):
    """The error is under the group, and the box says it is invalid and points at it (#416)."""
    sign_in(page, live_server.url)
    your_details(page, live_server.url)
    row = page.locator("#section-phones li").first
    number = row.locator("input[name$='-number_1']")

    number.fill("06 12 34")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    row = page.locator("#section-phones li").first
    number = row.locator("input[name$='-number_1']")
    expect(number).to_have_attribute("aria-invalid", "true")
    expect(number).to_have_value("06 12 34")
    described = number.get_attribute("aria-describedby").split()
    said = " ".join(page.locator(f"[id='{name}']").inner_text() for name in described)
    assert "That number is too short for France." in said


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_the_rows_have_no_violations(
    page: Page,
    live_server,
    axe_source,  # noqa: F811
    numbers,
    scheme,
    viewport,
):
    """A fine number, an impossible one with its mark, one kept as typed with its warning,
    an *Other* with its name -- and, on a second visit, a refusal."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size(viewport)
    sign_in(page, live_server.url)
    your_details(page, live_server.url)
    expect(page.locator("[data-phone-mark='impossible']")).to_be_visible()
    expect(page.locator("[data-phone-mark='unplaceable']")).to_be_visible()

    found = violations_on(page, axe_source)
    assert not found, describe(f"Your details ({scheme})", found)

    page.locator("#section-phones li input[name$='-number_1']").first.fill("06 12 34")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#section-phones [role=alert]").first).to_be_visible()

    found = violations_on(page, axe_source)
    assert not found, describe(f"Your details, refused ({scheme})", found)
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_contacts_rows_have_no_violations(
    page: Page,
    live_server,
    axe_source,  # noqa: F811
    applicant,
    scheme,
):
    from postulo.core.models import PhoneNumber
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    contact = Contact.objects.create(owner=applicant, company=company, name="Cave Johnson")
    PhoneNumber.objects.create(
        owner=applicant, holder=contact, number="+442079460958", kind="work", is_primary=True
    )
    PhoneNumber.objects.create(owner=applicant, holder=contact, number="3949", kind="switchboard")
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size(PHONE)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/{contact.pk}/edit/")
    expect(page.locator("[data-phone-mark='unplaceable']")).to_be_visible()

    found = violations_on(page, axe_source)

    assert not found, describe(f"a contact's form ({scheme})", found)
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0
    assert not page.evaluate(SPILLS)
