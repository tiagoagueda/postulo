"""The postal address rows of *Your details*, in a real browser (#306).

What only a browser can say about them. A row reads in the order somebody decides it --
what the address is, the address, then the star and the bin -- and the address's parts sit
on the lines its country writes them on, so the rows are measured: side by side from 640
pixels up with every box of a line level, whatever is written under one of them, and one
under another on a phone. A part its country refuses says so beside its own box. And the
name of a kind of *Other* is the stylesheet's to show, so it has to appear with scripts off.

The unit tests (`tests/test_postal_addresses.py`, `tests/test_postal_rules.py`) hold what is
refused and what the markup says; nothing here counts lines of text, which CI's font and
this machine's break differently.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_reflow import NARROW, SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e


def with_addresses(applicant, language: str = "en-GB") -> None:
    """A Portuguese address that fits, the primary; an American one with a name of its own;
    one kept from before, which Portugal would refuse if it were typed today; and a town
    and a country with no street and no postcode, which is a place and is kept as one."""
    from postulo.core.models import PostalAddress

    profile = applicant.profile
    profile.language = language
    profile.save(update_fields=["language"])
    for parts in (
        {
            "street": "Rua do Exemplo 1",
            "postcode": "1000-001",
            "municipality": "Lisboa",
            "country": "PT",
            "is_primary": True,
        },
        {
            "kind": "other",
            "label": "My sister's",
            "street": "1600 Pennsylvania Avenue NW",
            "postcode": "20500",
            "municipality": "Washington",
            "region": "DC",
            "country": "US",
        },
        {"street": "Rua Antiga 5", "postcode": "1000", "municipality": "", "country": "PT"},
        {"municipality": "Porto", "country": "PT"},
    ):
        PostalAddress.objects.create(owner=applicant, holder=profile, **parts)


def save(page: Page) -> None:
    page.locator("main form").first.locator("button[type=submit]").first.click()


def refuse_the_first_row(page: Page, base: str) -> None:
    """Portugal's postcode in a form it never uses, and the town taken out: two refusals on
    one line of the first row."""
    page.goto(f"{base}/accounts/profile/")
    page.locator("input[name=addresses-0-postcode]").fill("1000")
    page.locator("input[name=addresses-0-municipality]").fill("")
    save(page)
    expect(page.locator("#id_addresses-0-postcode_error")).to_be_visible()
    expect(page.locator("#id_addresses-0-municipality_error")).to_be_visible()


#: One address row as it is drawn: every control that can be seen, in source order, with
#: where it is; and each line of the row with where each of its boxes starts.
ROW = """(index) => {
  const row = document.querySelectorAll('#section-addresses li[data-address-row]')[index];
  const place = (el) => {
    const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right};
  };
  const name = (control) => control.hasAttribute('data-remove-trigger')
    ? 'remove' : control.name.split('-').pop();
  const controls = [];
  const every = 'select, textarea, input:not([type=hidden]), button';
  for (const control of row.querySelectorAll(every)) {
    if (control.checkVisibility()) controls.push({name: name(control), ...place(control)});
  }
  const lines = [];
  for (const line of row.querySelectorAll('.address-line')) {
    const boxes = [];
    for (const field of line.children) {
      if (!field.checkVisibility()) continue;
      const control = field.querySelector('select, textarea, input');
      const said = field.querySelector('[role=alert]');
      boxes.push({
        name: name(control),
        field: place(field),
        box: place(control),
        said: said ? place(said) : null,
      });
    }
    lines.push(boxes);
  }
  return {controls, lines};
}"""

PARTS = ["street", "postcode", "municipality", "region", "country"]


@pytest.mark.parametrize("language", ["en-GB", "pt-PT", "de"])
def test_at_1280_a_refusal_sits_beside_its_box_and_the_line_stays_level(
    live_server, page: Page, applicant, language
):
    """The two parts of one line each say what their country expects, under their own box
    and within their own column, and the two boxes still start level: an error under one
    does not lift it above its neighbour, and neither does a label that wraps."""
    with_addresses(applicant, language)
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 1280, "height": 900})
    refuse_the_first_row(page, live_server.url)

    row = page.evaluate(ROW, 0)
    assert [control["name"] for control in row["controls"]] == [
        "kind",
        *PARTS,
        "primary",
        "remove",
    ], "what it is, then the address, then the star and the bin"

    [line] = [
        line
        for line in row["lines"]
        if {box["name"] for box in line} == {"postcode", "municipality"}
    ]
    postcode, town = line
    assert postcode["name"] == "postcode", "Portugal writes the postcode before the town"
    assert abs(postcode["box"]["top"] - town["box"]["top"]) <= 1, line
    assert postcode["box"]["right"] <= town["box"]["left"], "side by side"
    for part in line:
        said = part["said"]
        assert said is not None, part["name"]
        assert said["top"] >= part["box"]["bottom"], "under its own box"
        assert said["left"] >= part["field"]["left"] - 1
        assert said["right"] <= part["field"]["right"] + 1, "and within its own column"

    # Every line of the row with more than one box on it is level, not only that one.
    together = 0
    for boxes in row["lines"]:
        tops = [box["box"]["top"] for box in boxes]
        if len(tops) > 1:
            together += 1
            assert max(tops) - min(tops) <= 1, boxes
    assert together >= 2, row["lines"]
    assert page.evaluate(SPILLS) == []
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0

    # And a label that takes more room than its neighbour's -- a longer word for the
    # postcode in another language, any label under a text-spacing override -- moves both
    # boxes down together. Made to wrap here, because which label wraps depends on the font.
    page.evaluate(
        """() => {
          const box = document.querySelector('input[name=addresses-0-postcode]');
          const label = box.closest('[data-part]').querySelector('label');
          label.textContent = 'A name for the postcode that cannot fit on one line of its column';
        }"""
    )
    [postcode, town] = next(
        line
        for line in page.evaluate(ROW, 0)["lines"]
        if {box["name"] for box in line} == {"postcode", "municipality"}
    )
    assert postcode["box"]["top"] > line[0]["box"]["top"] + 10, "the label did wrap"
    assert abs(postcode["box"]["top"] - town["box"]["top"]) <= 1, (postcode, town)


def test_at_1280_the_parts_are_on_the_lines_their_country_writes_them_on(
    live_server, page: Page, applicant
):
    """The United States writes the town, the state and the ZIP code on one line, in that
    order; Portugal the postcode and then the town. Left to right is the order of the
    source, so what is read is what is seen."""
    with_addresses(applicant)
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server.url}/accounts/profile/")

    american = page.evaluate(ROW, 1)
    [line] = [line for line in american["lines"] if len(line) == 3]
    assert [box["name"] for box in line] == ["municipality", "region", "postcode"]
    lefts = [box["box"]["left"] for box in line]
    assert lefts == sorted(lefts) and len(set(lefts)) == 3, lefts
    widths = [box["field"]["right"] - box["field"]["left"] for box in line]
    assert widths[2] < widths[0], "the ZIP code is the short part, at whichever end it sits"

    # The name is drawn beside the kind, because this row's kind is Other.
    kind, name = american["lines"][0]
    assert (kind["name"], name["name"]) == ("kind", "label")
    assert abs(kind["box"]["top"] - name["box"]["top"]) <= 1


@pytest.mark.parametrize("language", ["en-GB", "de", "el"])
def test_at_320_every_box_is_a_line_of_its_own_with_a_refusal_showing(
    live_server, page: Page, applicant, language
):
    """Nothing sits beside a long part on a phone: each box under the one before it, in the
    order of the source, with the refusals showing and nothing across the page."""
    with_addresses(applicant, language)
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": NARROW, "height": 800})
    refuse_the_first_row(page, live_server.url)

    for index in (0, 1):
        row = page.evaluate(ROW, index)
        boxes = [
            control for control in row["controls"] if control["name"] in ("kind", "label", *PARTS)
        ]
        tops = [box["top"] for box in boxes]
        assert tops == sorted(tops) and len(set(tops)) == len(tops), (index, boxes)
        last = max(box["bottom"] for box in boxes)
        for control in row["controls"]:
            if control["name"] in ("primary", "remove"):
                assert control["top"] >= last - 1, "the star and the bin end the row"

    assert page.evaluate(SPILLS) == []
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [NARROW, 1280])
def test_the_rows_with_a_refusal_and_a_mark_have_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
    scheme,
    width,
):
    """Everything the rows can show at once: a part refused, in red beside its box; a row
    kept as it was, with its mark; a place, with what is said under it; and a name drawn
    open for a kind of Other."""
    with_addresses(applicant)
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": width, "height": 900})
    refuse_the_first_row(page, live_server.url)
    expect(page.locator("[data-kept-as-it-was]")).to_be_visible()
    expect(page.locator("[data-place-note]")).to_be_visible()
    expect(page.locator("input[name=addresses-1-label]")).to_be_visible()

    found = violations_on(page, axe_source)
    assert not found, describe(f"/accounts/profile/ address rows ({scheme}, {width})", found)


def test_a_refusal_is_read_with_its_box(live_server, page: Page, applicant):
    """The box names the sentence beside it as what describes it, and says it is invalid."""
    with_addresses(applicant)
    sign_in(page, live_server.url)
    refuse_the_first_row(page, live_server.url)

    box = page.locator("input[name=addresses-0-postcode]")
    expect(box).to_have_attribute("aria-invalid", "true")
    described = box.get_attribute("aria-describedby").split()
    assert "id_addresses-0-postcode_error" in described
    for name in described:
        assert page.locator(f"#{name}").count() == 1, f"{name} is named and is not on the page"
    expect(page.locator("#id_addresses-0-postcode_error")).to_contain_text("1000-001")


def test_a_postcode_is_put_right_as_it_is_saved(live_server, page: Page, applicant):
    """Typed without its hyphen, kept with it, and shown that way when the page comes back."""
    from postulo.core.models import PostalAddress

    with_addresses(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    box = page.locator("input[name=addresses-0-postcode]")
    box.fill("1000100")
    save(page)
    expect(page.get_by_text("Your details have been saved.")).to_be_visible()

    expect(page.locator("input[name=addresses-0-postcode]")).to_have_value("1000-100")
    assert PostalAddress.objects.get(is_primary=True).postcode == "1000-100"


def test_a_row_kept_as_it_was_says_so_and_does_not_stop_the_page(
    live_server, page: Page, applicant
):
    """The mark is words and a sentence, the page saves round it, and it is still there."""
    with_addresses(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    mark = page.locator("[data-kept-as-it-was]")
    expect(mark).to_have_count(1)
    expect(mark).to_contain_text("Kept as it was")
    expect(mark).to_contain_text("1000-001")

    page.locator("input[name=headline]").fill("Saved round a marked row")
    save(page)

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    expect(page.locator("[data-kept-as-it-was]")).to_have_count(1)


def test_a_town_and_a_country_are_kept_as_a_place_and_told_so_quietly(
    live_server, page: Page, applicant
):
    """Typed into the empty row with nothing else, saved, and drawn again: no refusal, no
    mark, and under the row what a whole address in its country also carries. The kept
    place of `with_addresses` says the same, and neither is the amber of a row that fails."""
    from postulo.core.models import PostalAddress

    with_addresses(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    rows = page.locator("#section-addresses li[data-address-row]")
    expect(rows).to_have_count(5)
    expect(page.locator("[data-place-note]")).to_have_count(1)
    expect(rows.nth(3).locator("[data-place-note]")).to_contain_text("1000-001")
    expect(rows.nth(3).locator("[data-kept-as-it-was]")).to_have_count(0)

    page.locator("input[name=addresses-4-municipality]").fill("Austin")
    page.locator("select[name=addresses-4-country]").select_option("US")
    save(page)
    expect(page.get_by_text("Your details have been saved.")).to_be_visible()

    assert PostalAddress.objects.filter(municipality="Austin", country="US", street="").exists()
    expect(page.locator("[data-kept-as-it-was]")).to_have_count(1)
    note = (
        page.locator("#section-addresses li[data-address-row]").nth(4).locator("[data-place-note]")
    )
    expect(note).to_be_visible()
    expect(note).to_contain_text("20500")
    expect(note).not_to_have_attribute("role", "alert")


def test_a_new_row_with_only_its_country_changed_saves_nothing_and_says_nothing(
    live_server, page: Page, applicant
):
    """The country a new row starts on is the chooser's; choosing another is not writing an
    address. The page saves, and there is no row and no error for it."""
    from postulo.core.models import PostalAddress

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.locator("select[name=addresses-0-country]").select_option("US")
    save(page)

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    assert not PostalAddress.objects.exists()
    expect(page.locator("#section-addresses [role=alert]")).to_have_count(0)


def choose_other_and_name_it(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/profile/")
    kind = page.locator("select[name=addresses-0-kind]")
    name = page.locator("input[name=addresses-0-label]")

    expect(name).to_be_hidden()
    kind.select_option("other")
    expect(name).to_be_visible()
    # And back: a kind that has a name of its own takes the box away again.
    kind.select_option("home")
    expect(name).to_be_hidden()
    kind.select_option("other")

    # Saved with no name, the page comes back with the box drawn and its error beside it.
    save(page)
    expect(page.locator("input[name=addresses-0-label]")).to_be_visible()
    expect(page.locator("#id_addresses-0-label_error")).to_be_visible()

    page.locator("input[name=addresses-0-label]").fill("The boat")
    save(page)
    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    expect(page.locator("select[name=addresses-0-kind]")).to_have_value("other")
    expect(page.locator("input[name=addresses-0-label]")).to_have_value("The boat")


def test_choosing_other_shows_the_name(page: Page, live_server, applicant):
    with_addresses(applicant)
    sign_in(page, live_server.url)
    choose_other_and_name_it(page, live_server.url)

    assert applicant.profile.postal_addresses.get(is_primary=True).label == "The boat"


def test_choosing_other_shows_the_name_with_scripts_off(browser, live_server, applicant):
    """Nothing about the box is a script's: the stylesheet follows the menu either way, so
    somebody who chooses Other with scripts off can give the name the page then asks for."""
    with_addresses(applicant)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        choose_other_and_name_it(page, live_server.url)
    finally:
        context.close()

    assert applicant.profile.postal_addresses.get(is_primary=True).label == "The boat"


def test_a_row_taken_off_leaves_focus_on_what_the_next_row_is(live_server, page: Page, applicant):
    """A row's first control is its kind now, so that is where focus lands when the row
    before it goes (#303)."""
    with_addresses(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    rows = page.locator("#section-addresses li[data-address-row]")
    rows.nth(0).locator("[data-remove-trigger]").click()
    page.locator("dialog[open]").get_by_role("button", name="Remove", exact=True).click()

    expect(page.locator("select[name=addresses-1-kind]")).to_be_focused()
