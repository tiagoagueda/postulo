"""At 2560 pixels, a table uses the screen and a form keeps its measure (#188).

The other end of reflow. Every page used to sit in a 1280-pixel column, so on a wide
monitor a table with ten chosen columns scrolled inside a box with grey on both sides --
two nested scrolls to read one row for anybody using a magnifier. The unit test next to the
templates checks which pages empty the cap; this checks what the browser does with it.

The company form takes the screen too, as two columns from `2xl`, with the measure kept on
its cards rather than on the page (#210): the tests after it say where the line falls
and that it mirrors right to left. A contact's form and the capture form follow it, and
Your details and Settings keep the same measure beside their sidebar (#320). So do the forms
under Server settings, on the column the frame gives them, while its tables and lists keep
the width.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .test_accessibility import furnished, sign_in  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

#: A monitor rather than a laptop: 1440p, the first width at which the old cap showed.
WIDE = 2560


def width_of(page: Page, selector: str) -> float:
    box = page.locator(selector).first.bounding_box()
    assert box, f"{selector} is not drawn"
    return box["width"]


def test_a_table_takes_the_screen_and_a_form_does_not(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": WIDE, "height": 1200})
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/applications/")
    assert width_of(page, "main") > 2400, "the applications table is still in a 1280px column"
    assert width_of(page, "main .scroll-x") > 2000, "the table's scroll box did not follow"
    assert width_of(page, "header") == WIDE, "the masthead sits narrower than the table"

    page.goto(f"{live_server.url}/applications/new/")
    assert width_of(page, "main") == 1280, "a form is a wide paragraph now"


def test_a_laptop_sees_no_difference(live_server, page: Page, furnished):  # noqa: F811
    """Below the old cap nothing changes: the cap was never reached there."""
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url)

    for path in ("/applications/", "/applications/new/"):
        page.goto(f"{live_server.url}{path}")
        assert width_of(page, "main") == 1280, path


#: Today's single column, `max-w-2xl`: neither column may be narrower than this (#210).
MEASURE = 672

#: The first breakpoint at which two columns of that width fit beside each other: `2xl`.
#: 672 + 24 + 672 inside two 16-pixel gutters needs a window of 1400; at `xl`, 1280, each
#: column would be 612.
TWO_COLUMNS = 1536


def company_form_parts(page: Page) -> tuple[dict, dict, dict]:
    details = page.locator("main form .card").first.bounding_box()
    identifiers = page.locator("main form [data-identifiers]").bounding_box()
    # By its type rather than its name: the right-to-left test reads the page in Arabic.
    save = page.locator('main form button[type="submit"]').bounding_box()
    assert details and identifiers and save
    return details, identifiers, save


def test_the_company_form_is_two_columns_on_a_wide_screen(live_server, page: Page, furnished):  # noqa: F811
    """The details beside the identifiers, each at today's measure, *Save* after both."""
    sign_in(page, live_server.url)
    company = furnished["company"]
    for width in (TWO_COLUMNS, WIDE):
        page.set_viewport_size({"width": width, "height": 1200})
        for path in ("/jobs/companies/new/", f"/jobs/companies/{company.pk}/edit/"):
            page.goto(f"{live_server.url}{path}")
            where = f"{path} at {width}"
            assert width_of(page, "main") == width, f"{where}: the page is capped"
            details, identifiers, save = company_form_parts(page)
            assert details["y"] == identifiers["y"], f"{where}: not side by side"
            assert identifiers["x"] > details["x"] + details["width"], f"{where}: order"
            assert details["width"] >= MEASURE, f"{where}: narrower than one column was"
            assert identifiers["width"] >= MEASURE, f"{where}: narrower than one column was"
            # The measure stays on the parts: a text box or the notes never stretch to the
            # width of the monitor, however wide it is.
            for box in ("#id_name", "#id_notes"):
                assert width_of(page, box) < MEASURE, f"{where}: {box} lost its measure"
            bottom = max(details["y"] + details["height"], identifiers["y"] + identifiers["height"])
            assert save["y"] >= bottom, f"{where}: Save is not after both columns"


def test_below_the_breakpoint_the_company_form_is_one_column(live_server, page: Page, furnished):  # noqa: F811
    """One pixel short of `2xl`, and a laptop: the identifiers under the details, as always."""
    sign_in(page, live_server.url)
    for width in (TWO_COLUMNS - 1, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}/jobs/companies/new/")
        details, identifiers, save = company_form_parts(page)
        assert identifiers["y"] >= details["y"] + details["height"], f"one column at {width}"
        assert details["x"] == identifiers["x"], f"one column at {width}"
        assert details["width"] == identifiers["width"] == MEASURE, f"the measure at {width}"
        assert save["y"] >= identifiers["y"] + identifiers["height"]


def test_the_two_columns_mirror_right_to_left(live_server, page: Page, furnished):  # noqa: F811
    """In Arabic the details sit at the right-hand edge, where reading starts, and the
    identifiers to their left: a flex row follows the direction of the text."""
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": WIDE, "height": 1200})
    page.goto(f"{live_server.url}/jobs/companies/new/")

    assert page.locator("html").get_attribute("dir") == "rtl"
    details, identifiers, _save = company_form_parts(page)
    assert details["y"] == identifiers["y"]
    assert details["x"] > identifiers["x"] + identifiers["width"], "the details lead"
    assert round(details["x"] + details["width"]) == WIDE - 16, "from the reading edge"


# ------------------------------------------------ Your details and Settings (#320)

#: Your details and every page of Settings the core draws. Their text boxes were 2,214
#: pixels wide at 2560, because the frame takes the screen (#198) and nothing on the page
#: kept a measure; now every part stops at `max-w-2xl` beside the sidebar.
FRAMED = (
    "/accounts/profile/",
    "/settings/appearance/",
    "/settings/accessibility/",
    "/settings/language/",
    "/settings/account/",
    "/settings/capture/",
    "/settings/connections/",
    "/settings/connections/add/",
    "/settings/connections/add/notifier/webhook/",
    "/settings/plugins/?internal=1",
    "/capture-tokens/",
    "/export/",
    "/import/",
    "/accounts/delete/",
    "/accounts/email/",
    "/accounts/password/change/",
)

#: The parts of a page, as a browser draws them: every card, and every box somebody types
#: into or chooses from, the language picker's closed line among them.
PARTS = r"""() => {
  const main = document.querySelector('main');
  const drawn = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {x: r.x, right: r.right, width: r.width, name: el.name || el.tagName};
  };
  // A list is drawn as the button built for its select (#301); the select beside it is
  // one pixel, out of sight, and measuring that would say nothing.
  const typed = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio])'
    + ':not([type=submit]), select:not([data-select-ready]), [data-select-trigger], textarea,'
    + ' [data-language-picker] > summary';
  const sidebar = main.querySelector('aside');
  return {
    cards: [...main.querySelectorAll('.card')].filter(drawn).map(box),
    boxes: [...main.querySelectorAll(typed)].filter(drawn).map(box),
    sidebar: sidebar ? box(sidebar) : null,
  };
}"""


def test_your_details_and_settings_keep_the_measure_on_a_wide_screen(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """At 2560: no card wider than the company form's, no box as wide as one, and every
    card at the start edge of the column beside the sidebar rather than in its middle."""
    page.set_viewport_size({"width": WIDE, "height": 1200})
    sign_in(page, live_server.url)
    for path in (*FRAMED, f"/settings/connections/{furnished['connection'].pk}/"):
        page.goto(f"{live_server.url}{path}")
        assert width_of(page, "main") == WIDE, f"{path}: the frame is capped"
        parts = page.evaluate(PARTS)
        assert parts["cards"], f"{path} draws no card"
        for card in parts["cards"]:
            assert card["width"] <= MEASURE, f"{path}: a card {card['width']} wide"
            assert card["x"] > parts["sidebar"]["right"], f"{path}: a card under the sidebar"
        starts = {round(card["x"]) for card in parts["cards"]}
        assert len(starts) == 1, f"{path}: the cards do not share the start edge: {starts}"
        for box in parts["boxes"]:
            assert box["width"] < MEASURE, f"{path}: {box['name']} is {box['width']} wide"


def test_the_measure_beside_the_sidebar_mirrors_right_to_left(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """In Arabic the sidebar is on the right and the cards stop at the right-hand edge of
    the column beside it, where reading starts."""
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    page.set_viewport_size({"width": WIDE, "height": 1200})
    sign_in(page, live_server.url)
    for path in (
        "/accounts/profile/",
        "/settings/language/",
        "/settings/account/",
        # And two under Server settings, where the measure is on the frame's own column.
        "/server/sign-in/",
        "/server/defaults/",
    ):
        page.goto(f"{live_server.url}{path}")
        assert page.locator("html").get_attribute("dir") == "rtl"
        parts = page.evaluate(PARTS)
        ends = {round(card["right"]) for card in parts["cards"]}
        assert len(ends) == 1, f"{path}: the cards do not share the start edge: {ends}"
        (end,) = ends
        assert end < parts["sidebar"]["x"], f"{path}: a card under the sidebar"
        assert end > WIDE / 2, f"{path}: the cards sit at the left-hand end"
        assert all(card["width"] <= MEASURE for card in parts["cards"]), path


# ----------------------------------------------------------- Server settings (#320)

#: The pages of Server settings that keep the measure: every one that is a form, and the
#: record of processing, whose rows are a name over a paragraph. Sign-in, Email, Capture,
#: Defaults and Data protection drew their boxes 2,214 pixels wide at 2560; the three
#: about one person centred a column of their own in that width. `{pk}` is somebody else's
#: account, so the delete page draws its form and not a refusal.
SERVER_FORMS = (
    "/server/sign-in/",
    "/server/email/",
    "/server/capture/",
    "/server/defaults/",
    "/server/data-protection/",
    "/server/record-of-processing/",
    "/server/people/{pk}/username/",
    "/server/people/{pk}/delete/",
    "/server/people/{pk}/recovery/",
)

#: The column beside the sidebar, which is what a Server settings page puts its measure on.
COLUMN = r"""() => {
  const r = document.querySelector('main aside + div').getBoundingClientRect();
  return {x: r.x, right: r.right, width: r.width};
}"""


@pytest.fixture
def somebody_else(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email="somebody.else@example.org", username="somebody-else", password="x"
    )


def test_the_server_settings_forms_keep_the_measure_on_a_wide_screen(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    somebody_else,
):
    """At 2560 the column is the company form's 672 pixels and starts beside the sidebar,
    not in the middle of what is left; every card is in it and no box is as wide as it."""
    page.set_viewport_size({"width": WIDE, "height": 1200})
    sign_in(page, live_server.url)
    for path in SERVER_FORMS:
        path = path.format(pk=somebody_else.pk)
        page.goto(f"{live_server.url}{path}")
        assert width_of(page, "main") == WIDE, f"{path}: the frame is capped"
        parts = page.evaluate(PARTS)
        column = page.evaluate(COLUMN)
        assert column["width"] == MEASURE, f"{path}: the column is {column['width']} wide"
        gap = column["x"] - parts["sidebar"]["right"]
        assert 0 < gap < 100, f"{path}: the column starts {gap} pixels from the sidebar"
        for card in parts["cards"]:
            assert card["width"] <= MEASURE, f"{path}: a card {card['width']} wide"
            assert round(card["x"]) == round(column["x"]), f"{path}: a card off the start edge"
        for box in parts["boxes"]:
            assert box["width"] < MEASURE, f"{path}: {box['name']} is {box['width']} wide"


def test_the_server_settings_tables_and_lists_keep_the_width(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """A table, a grid of cards and rows that set a description beside its controls take
    the screen, as they did. On Plugins the two forms among the lists keep the measure on
    their own card, so no box somebody types into follows the lists across the monitor."""
    page.set_viewport_size({"width": WIDE, "height": 1200})
    sign_in(page, live_server.url)
    me = furnished["applicant"]
    for path in (
        "/server/overview/",
        "/server/people/",
        f"/server/people/{me.pk}/plugins/",
        "/server/logs/",
        "/server/plugins/",
    ):
        page.goto(f"{live_server.url}{path}")
        column = page.evaluate(COLUMN)
        assert column["width"] > 2000, f"{path}: the column is {column['width']} wide"

    # Still on Plugins. Open *Add a repository*, so its boxes are drawn and not only laid out.
    page.locator("main details.card > summary").click()
    parts = page.evaluate(PARTS)
    assert max(card["width"] for card in parts["cards"]) > 2000, "the lists lost the width"
    assert len(parts["boxes"]) >= 4, "the repository's three boxes and the package"
    for box in parts["boxes"]:
        assert box["width"] < MEASURE, f"{box['name']} is {box['width']} wide"
    for form in ("main details.card", 'main form.card[enctype="multipart/form-data"]'):
        assert width_of(page, form) == MEASURE, f"{form} lost its measure"
        assert round(page.locator(form).bounding_box()["x"]) == round(column["x"]), form


# ------------------------------------------------------- a contact's form (#320)


def contact_form_parts(page: Page) -> tuple[dict, dict | None, dict]:
    details = page.locator("main form .card").first.bounding_box()
    rows = page.locator("main form [data-contact-rows]")
    rows_box = rows.bounding_box() if rows.count() else None
    save = page.locator('main form button[type="submit"]').bounding_box()
    assert details and save
    return details, rows_box, save


def merge_button(page: Page) -> dict:
    """*Merge* in the form's own row -- not the one in the notice of a possible duplicate."""
    merge = page.locator('main form a[href*="/merge/"]').bounding_box()
    assert merge
    return merge


def test_a_contact_is_two_columns_on_a_wide_screen(live_server, page: Page, furnished):  # noqa: F811
    """The details beside the numbers and links, each at the company form's measure, and
    *Save* and *Merge* after both, *Merge* at the end of one column's width."""
    sign_in(page, live_server.url)
    contact = furnished["contact"]
    for width in (TWO_COLUMNS, WIDE):
        page.set_viewport_size({"width": width, "height": 1200})
        for path in ("/jobs/contacts/new/", f"/jobs/contacts/{contact.pk}/edit/"):
            page.goto(f"{live_server.url}{path}")
            where = f"{path} at {width}"
            assert width_of(page, "main") == width, f"{where}: the page is capped"
            details, rows, save = contact_form_parts(page)
            assert rows, f"{where}: no rows"
            assert details["y"] == rows["y"], f"{where}: not side by side"
            assert rows["x"] > details["x"] + details["width"], f"{where}: order"
            assert details["width"] == rows["width"] == MEASURE, f"{where}: the measure"
            for box in ("#id_name", "#id_notes", "main form [data-web-links] input[type=url]"):
                assert width_of(page, box) < MEASURE, f"{where}: {box} lost its measure"
            assert save["y"] >= rows["y"] + rows["height"], f"{where}: Save before the rows"
        merge = merge_button(page)
        assert merge["y"] == save["y"], "Merge is on Save's line"
        assert round(merge["x"] + merge["width"]) == round(details["x"] + details["width"])


def test_below_the_breakpoint_a_contact_is_one_column(live_server, page: Page, furnished):  # noqa: F811
    sign_in(page, live_server.url)
    for width in (TWO_COLUMNS - 1, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}/jobs/contacts/{furnished['contact'].pk}/edit/")
        details, rows, save = contact_form_parts(page)
        assert rows["y"] >= details["y"] + details["height"], f"one column at {width}"
        assert details["x"] == rows["x"], f"one column at {width}"
        assert details["width"] == rows["width"] == MEASURE, f"the measure at {width}"
        assert save["y"] >= rows["y"] + rows["height"]
        merge = merge_button(page)
        assert round(merge["x"] + merge["width"]) == round(details["x"] + details["width"])


def test_a_contact_with_no_rows_is_one_column_at_any_width(live_server, page: Page, furnished):  # noqa: F811
    """Every feature that draws a row switched off: nothing for a second column, and the
    details keep their measure at the start edge, *Merge* still at the end of it."""
    from postulo.plugins.messaging_contacts import MESSAGING_CONTACTS
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS
    from postulo.plugins.repositories import REPOSITORIES
    from postulo.plugins.social_profiles import SOCIAL_PROFILES
    from postulo.plugins.websites import WEBSITES

    for plugin in (PHONE_NUMBERS, SOCIAL_PROFILES, REPOSITORIES, WEBSITES, MESSAGING_CONTACTS):
        PluginPolicy.objects.create(
            plugin=plugin, person=furnished["applicant"], state=PluginPolicy.State.FORCED_OFF
        )
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": WIDE, "height": 1200})
    page.goto(f"{live_server.url}/jobs/contacts/{furnished['contact'].pk}/edit/")
    details, rows, save = contact_form_parts(page)
    assert rows is None
    assert details["width"] == MEASURE and round(details["x"]) == 16
    assert save["y"] >= details["y"] + details["height"]
    merge = merge_button(page)
    assert round(merge["x"] + merge["width"]) == round(details["x"] + details["width"])


def test_a_contact_s_two_columns_mirror_right_to_left(live_server, page: Page, furnished):  # noqa: F811
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": WIDE, "height": 1200})
    page.goto(f"{live_server.url}/jobs/contacts/{furnished['contact'].pk}/edit/")

    assert page.locator("html").get_attribute("dir") == "rtl"
    details, rows, _save = contact_form_parts(page)
    assert details["y"] == rows["y"]
    assert details["x"] > rows["x"] + rows["width"], "the details lead"
    assert round(details["x"] + details["width"]) == WIDE - 16, "from the reading edge"


# --------------------------------------------------------- the capture form (#320)


def capture_form_parts(page: Page) -> tuple[dict, dict, dict]:
    form = page.locator("main form[method=post]").bounding_box()
    help_ = page.locator("main [data-capture-help]").bounding_box()
    button = page.locator('main form button[type="submit"]').bounding_box()
    assert form and help_ and button
    return form, help_, button


def test_the_capture_form_sits_beside_its_help_on_a_wide_screen(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """The address and the pasted page beside *How this works* and *Sources installed*,
    each at the 672 pixels the page was, with the button in the form's own column."""
    sign_in(page, live_server.url)
    for width in (TWO_COLUMNS, WIDE):
        page.set_viewport_size({"width": width, "height": 1200})
        page.goto(f"{live_server.url}/jobs/captures/new/")
        assert width_of(page, "main") == width, f"the page is capped at {width}"
        form, help_, button = capture_form_parts(page)
        assert form["y"] == help_["y"], f"not side by side at {width}"
        assert help_["x"] > form["x"] + form["width"], f"order at {width}"
        assert form["width"] == help_["width"] == MEASURE, f"the measure at {width}"
        assert width_of(page, "#id_url") < MEASURE
        assert button["y"] < form["y"] + form["height"], "the button is the form's own"


def test_below_the_breakpoint_the_capture_form_is_one_column(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    sign_in(page, live_server.url)
    for width in (TWO_COLUMNS - 1, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}/jobs/captures/new/")
        form, help_, _button = capture_form_parts(page)
        assert help_["y"] >= form["y"] + form["height"], f"one column at {width}"
        assert form["x"] == help_["x"], f"one column at {width}"
        assert form["width"] == help_["width"] == MEASURE, f"the measure at {width}"


def test_the_capture_form_mirrors_right_to_left(live_server, page: Page, furnished):  # noqa: F811
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": WIDE, "height": 1200})
    page.goto(f"{live_server.url}/jobs/captures/new/")

    assert page.locator("html").get_attribute("dir") == "rtl"
    form, help_, _button = capture_form_parts(page)
    assert form["y"] == help_["y"]
    assert form["x"] > help_["x"] + help_["width"], "the form leads"
    assert round(form["x"] + form["width"]) == WIDE - 16, "from the reading edge"


def test_the_listing_form_keeps_its_one_column(live_server, page: Page, furnished):  # noqa: F811
    """Left as it was on purpose (#320): two of its 768-pixel columns need a window of
    1592, and at `2xl` each would be 740, narrower than the one it has."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": WIDE, "height": 1200})
    page.goto(f"{live_server.url}/listings/new/")
    assert width_of(page, "main") == 1280
    cards = [card.bounding_box() for card in page.locator("main form .card").all()]
    assert len({round(card["x"]) for card in cards}) == 1, "one column"
    assert {round(card["width"]) for card in cards} == {768}


def test_nothing_changed_here_scrolls_sideways_on_a_phone(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    somebody_else,
):
    """At 320 every page #320 touched is one column that fits."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 800})
    contact = furnished["contact"]
    for path in (
        *FRAMED,
        "/jobs/contacts/new/",
        f"/jobs/contacts/{contact.pk}/edit/",
        "/jobs/captures/new/",
        *(path.format(pk=somebody_else.pk) for path in SERVER_FORMS),
        "/server/plugins/",
    ):
        page.goto(f"{live_server.url}{path}")
        result = page.evaluate(SCROLLS_SIDEWAYS)
        assert not result["reached"], f"{path} scrolls {result['reached']}px sideways at 320"
