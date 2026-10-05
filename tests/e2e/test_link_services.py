"""The row of a web link in a browser: a service, its icon, and the address (#305, #213).

What the server draws and checks is in `tests/test_link_services.py`. What needs a browser
is what a reader meets: the row on one line at a desk and in a column on a phone, read in
the order it is written; the icon beside the closed select following the choice; the name
box there for *Other* and gone for a named service, with scripts and without them; an
address pasted into a new row choosing its service at once; and the page passing axe in
both themes and reflowing at 320 pixels.

Nothing here counts lines of text. CI draws in DejaVu Sans and a desk in Segoe UI, so what
is asserted is where one control sits beside another, which is what the issue promises.
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest
from playwright.sync_api import Browser, Page, expect

from .selects import DRAWN, button_of, choose
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS, SPILLS
from .test_row_removal import sign_in

pytestmark = pytest.mark.e2e

#: Where each control of a row is, and whether it is drawn at all. The service is measured
#: where a person sees it: at the button built for the select, where scripts run (#301).
BOXES = """(row) => {
  const box = (selector) => {
    const found = __DRAWN__(row.querySelector(selector));
    if (!found || !found.checkVisibility()) return null;
    const r = found.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
  };
  return {
    service: box('select[data-service-select]'),
    name: box('input[name$="-label"]'),
    address: box('[data-address-box]'),
    primary: box('.primary-choice, input[type="radio"]'),
  };
}""".replace("__DRAWN__", DRAWN)


@pytest.fixture
def links(applicant):
    """A profile on a service, one on a server of a federated one, one that is Other with
    a name, a project on somebody's forge, and a website."""
    from postulo.core.models import WebLink

    profile = applicant.profile
    wanted = [
        ("social", "linkedin", "", "https://www.linkedin.com/in/alex-morgan-1a2b3c", True),
        ("social", "mastodon", "", "https://fosstodon.org/@alex", False),
        ("social", "", "A forum", "https://forum.example.org/u/alex", False),
        ("repository", "forgejo", "", "https://git.example.org/alex/thing", True),
        ("website", "", "Blog", "https://alex.example.org", True),
    ]
    return [
        WebLink.objects.create(
            owner=applicant,
            holder=profile,
            kind=kind,
            service=service,
            label=label,
            url=url,
            is_primary=primary,
        )
        for kind, service, label, url, primary in wanted
    ]


@pytest.fixture
def contact(applicant):
    from postulo.core.models import WebLink
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=applicant, name="Aperture")
    person = Contact.objects.create(owner=applicant, company=company, name="Cave Johnson")
    WebLink.objects.create(
        owner=applicant,
        holder=person,
        kind="social",
        service="linkedin",
        url="https://www.linkedin.com/in/cave",
        is_primary=True,
    )
    WebLink.objects.create(
        owner=applicant,
        holder=person,
        kind="social",
        label="A forum",
        url="https://forum.example.org/u/cave",
    )
    return person


def rows_of(page: Page, kind: str):
    return page.locator(f"[data-web-links={kind}] ol > li")


def shown_icon(row) -> list[str]:
    """The icons of a row that are drawn beside its service: one, the chosen service's. In
    the select's own button where scripts run, and the server's, beside the native select,
    where they do not -- never both (#301)."""
    return row.locator(
        ".input-group :is([data-option-mark], [data-select-mark]) svg[data-icon]"
    ).evaluate_all("(icons) => icons.filter((i) => i.checkVisibility()).map((i) => i.dataset.icon)")


def in_one_line(boxes: dict, *names: str) -> None:
    """Each named control starts after the one before it ends, and all of them overlap in
    height: a line, read from its start. Mirrored for a right-to-left page by the caller."""
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


# --------------------------------------------------------------------- where things sit


@pytest.mark.parametrize("where", ["profile", "contact"])
def test_at_a_desk_a_row_is_one_line_service_first(page: Page, live_server, links, contact, where):
    """From `sm` up: the service, the name when the service is Other, the address, then the
    star and the bin -- in that order, because that is the order they are written in.

    On *Your details* the controls are a star and a bin and end the line. A contact's form
    still words them, *Primary* and *Remove*, and beside a name there is no room for the
    words on the line: they wrap under it, after the address, which is still their place
    in the order."""
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url)
    path = "/accounts/profile/" if where == "profile" else f"/jobs/contacts/{contact.pk}/edit/"
    page.goto(f"{live_server.url}{path}")
    social = rows_of(page, "social")

    on_a_service = social.nth(0).evaluate(BOXES)
    assert on_a_service["name"] is None, "a named service needs no name"
    in_one_line(on_a_service, "service", "address", "primary")

    other = social.nth(2 if where == "profile" else 1).evaluate(BOXES)
    if where == "profile":
        in_one_line(other, "service", "name", "address", "primary")
    else:
        in_one_line(other, "service", "name", "address")
        assert other["primary"]["top"] >= other["address"]["top"], other

    new = social.last.evaluate(BOXES)
    assert new["name"] is None, "nothing chosen yet, so no name to ask for"
    in_one_line(new, "service", "address", "primary")


def test_at_a_desk_a_website_has_its_name_and_no_select(page: Page, live_server, links):
    """A website is its own thing: no service to choose, and a name that is always there."""
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    website = rows_of(page, "website").first.evaluate(BOXES)

    assert website["service"] is None
    in_one_line(website, "name", "address", "primary")


def test_on_a_phone_a_row_is_a_column_in_the_same_order(page: Page, live_server, links):
    page.set_viewport_size({"width": 320, "height": 640})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    social = rows_of(page, "social")

    in_a_column(social.nth(0).evaluate(BOXES), "service", "address", "primary")
    in_a_column(social.nth(2).evaluate(BOXES), "service", "name", "address", "primary")
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls sideways at 320"


def test_the_order_read_is_the_order_tabbed(page: Page, live_server, links):
    """The source order is the reading order, so Tab goes service, name, address, and then
    the row's controls. The star is one radio of a group, so Tab stops on the chosen one:
    on the primary row it is next after the address, and on any other row the bin is."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    def tabbed_from(row, presses: int) -> list[str]:
        row.locator("select").focus()
        visited = []
        for _press in range(presses):
            page.keyboard.press("Tab")
            visited.append(
                page.evaluate(
                    "() => document.activeElement.name"
                    " || document.activeElement.getAttribute('aria-label')"
                )
            )
        return visited

    primary = tabbed_from(rows_of(page, "social").nth(0), 3)
    assert primary[0].endswith("-url") and primary[1].endswith("-primary"), primary
    assert primary[2].startswith("Remove "), primary

    other = tabbed_from(rows_of(page, "social").nth(2), 3)
    assert other[0].endswith("-label") and other[1].endswith("-url"), other
    assert other[2] == "Remove A forum", other


# -------------------------------------------------------- the icon and the name follow


def test_the_icon_beside_the_select_follows_the_choice(page: Page, live_server, links):
    """An option holds words and nothing else, so the icon is drawn by the select's own
    control: the chosen service's in the button, each service's in the list (#301). The one
    the server draws beside the native select, for a page without scripts, is put away."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    row = rows_of(page, "social").nth(0)
    select = row.locator("select")
    name = row.locator('input[name$="-label"]')

    assert shown_icon(row) == ["user"], "LinkedIn, as stored"
    expect(row.locator("[data-option-mark]")).to_be_hidden()
    expect(button_of(select).locator("svg[data-icon=user]")).to_have_count(1)
    expect(name).to_be_hidden()

    select.select_option("mastodon")
    assert shown_icon(row) == ["at-sign"]
    expect(name).to_be_hidden()

    select.select_option("other")
    assert shown_icon(row) == ["globe"]
    expect(name).to_be_visible()

    select.select_option("youtube")
    assert shown_icon(row) == ["video"]
    expect(name).to_be_hidden()

    # Chosen from the list, as a person does, it is the same.
    choose(page, select, "Bluesky")
    expect(select).to_have_value("bluesky")
    assert shown_icon(row) == ["at-sign"]

    assert shown_icon(rows_of(page, "repository").nth(0)) == ["git-branch"]


def test_an_address_pasted_into_a_new_row_chooses_its_service_at_once(
    page: Page, live_server, links
):
    """Nobody has to choose LinkedIn and then paste a LinkedIn address. A choice made by
    hand is left alone, and so is every stored row, which has no "nothing chosen"."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "social").last
    select = new.locator("select")
    address = new.locator("input[type=url]")

    expect(select).to_have_value("")
    address.fill("pt.linkedin.com/in/alex")
    expect(select).to_have_value("linkedin")
    assert shown_icon(new) == ["user"]

    # Still the address's to say while nobody has chosen: another host, another service;
    # a host no service knows, and nothing is chosen again.
    address.fill("https://bsky.app/profile/alex.bsky.social")
    expect(select).to_have_value("bluesky")
    address.fill("https://forum.example.org/u/alex")
    expect(select).to_have_value("")
    assert shown_icon(new) == ["globe"]

    # Chosen by hand, it stays whatever is typed afterwards.
    select.select_option("other")
    address.fill("https://www.linkedin.com/in/alex")
    expect(select).to_have_value("other")

    # A stored row is never guessed for.
    stored = rows_of(page, "social").nth(2)
    stored.locator("input[type=url]").fill("https://www.linkedin.com/in/another")
    expect(stored.locator("select")).to_have_value("other")


def test_a_pasted_address_is_saved_under_the_service_it_chose(page: Page, live_server, links):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "repository").last

    new.locator("input[type=url]").fill("https://codeberg.org/alex/thing")
    expect(new.locator("select")).to_have_value("codeberg")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    from postulo.core.models import WebLink

    saved = WebLink.objects.get(url="https://codeberg.org/alex/thing")
    assert (saved.kind, saved.service, saved.label) == ("repository", "codeberg", "")


def test_a_refused_address_says_what_one_looks_like_beside_it(page: Page, live_server, links):
    """The sentence is the address's own error: the box is described by it, and it names
    the service, an address there, and the way out."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    row = rows_of(page, "repository").nth(0)

    row.locator("select").select_option("github")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    row = rows_of(page, "repository").nth(0)
    address = row.locator("input[type=url]")
    expect(address).to_have_attribute("aria-invalid", "true")
    expect(address).to_have_accessible_description(
        "That does not look like an address on GitHub. One looks like "
        "https://github.com/name/project; choose “Other” to list an address of another shape."
    )
    # What was chosen is still chosen, and drawn.
    expect(row.locator("select")).to_have_value("github")
    assert shown_icon(row) == ["git-branch"]


# -------------------------------------------------------------------- with scripts off


def test_with_scripts_off_choosing_other_shows_the_name_and_saves_it(
    browser: Browser, live_server, links
):
    """The stylesheet shows the box, not the script: `:has()` follows the select. And an
    address left with no service chosen is given one when the page is saved."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        new = rows_of(page, "social").last
        name = new.locator('input[name$="-label"]')

        expect(name).to_be_hidden()
        new.locator("select").select_option("other")
        expect(name).to_be_visible()
        new.locator("select").select_option("linkedin")
        expect(name).to_be_hidden()
        new.locator("select").select_option("other")
        name.fill("A wiki")
        new.locator("input[type=url]").fill("https://wiki.example.org/alex")

        pasted = rows_of(page, "repository").last
        pasted.locator("input[type=url]").fill("https://github.com/alex/thing")
        # No script, so nothing chose: saving does.
        expect(pasted.locator("select")).to_have_value("")
        page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

        expect(page.get_by_text("Your details have been saved.")).to_be_visible()
        from postulo.core.models import WebLink

        wiki = WebLink.objects.get(url="https://wiki.example.org/alex")
        assert (wiki.service, wiki.label) == ("", "A wiki")
        assert WebLink.objects.get(url="https://github.com/alex/thing").service == "github"
        # And the saved rows are drawn with what was saved: the icon included.
        assert shown_icon(rows_of(page, "repository").nth(1)) == ["git-branch"]
        expect(rows_of(page, "social").nth(3).locator('input[name$="-label"]')).to_be_visible()
    finally:
        context.close()


# ------------------------------------------- what the script chose is not what is saved

#: Addresses on a host a service answers on that are not a profile or a project there: the
#: front page somebody had open, a single video, a page of settings. Each is *Other*, by
#: the rule that guessing is never how an address comes to be refused. The third thing is
#: what the script shows beside the row while the address is typed.
ON_THE_HOST_AND_NOT_A_PROFILE = (
    ("social", "https://www.linkedin.com/feed/", "linkedin"),
    ("social", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ("social", "https://x.com/home", "x"),
    ("repository", "https://github.com/settings/profile", "github"),
)


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_an_address_the_script_chose_for_is_saved_as_saving_alone_saves_it(
    browser: Browser, live_server, applicant, scripts
):
    """The script reads the host alone, and saving reads the host and the shape. What the
    script chose was posted like a choice made by hand, so each of these was refused with
    scripts on -- "That does not look like an address on LinkedIn" about a service nobody
    had chosen -- and saved as *Other* with them off. The script's choice is shown and
    never posted: the row goes as one with nothing chosen, and ends as the same stored row
    either way."""
    context = browser.new_context(java_script_enabled=scripts)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        for kind, address, shown in ON_THE_HOST_AND_NOT_A_PROFILE:
            page.goto(f"{live_server.url}/accounts/profile/")
            new = rows_of(page, kind).last
            new.locator("input[type=url]").fill(address)
            # While it is typed the row may say what the host suggests; with no script
            # nothing chose.
            expect(new.locator("select")).to_have_value(shown if scripts else "")
            page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
            expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    finally:
        context.close()

    stored = {
        (row.kind, row.url): (row.service, row.label) for row in applicant.profile.web_links.all()
    }
    assert stored == {
        (kind, address): ("", "") for kind, address, _shown in ON_THE_HOST_AND_NOT_A_PROFILE
    }


def test_a_choice_made_by_hand_after_the_script_chose_is_posted_as_made(
    page: Page, live_server, applicant
):
    """Only the script's own choice is taken back. Somebody who then chooses for
    themselves has said what the address is, and is told when it is not that."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "social").last

    new.locator("input[type=url]").fill("https://www.linkedin.com/feed/")
    expect(new.locator("select")).to_have_value("linkedin")
    new.locator("select").select_option("mastodon")
    new.locator("select").select_option("linkedin")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    row = rows_of(page, "social").first
    expect(row.locator("input[type=url]")).to_have_attribute("aria-invalid", "true")
    expect(row.locator("select")).to_have_value("linkedin")


# ---------------------------------------------------------------------- axe, both themes


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_rows_have_no_violations(
    page: Page,
    live_server,
    links,
    contact,
    axe_source,  # noqa: F811
    scheme,
):
    """*Your details* and the contact form, with rows on a service, on Other and unchosen;
    again with Other chosen on a new row, which is when the name box is on the page; and
    once more with an address refused, which is when the row carries an error."""
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    failures = []
    for path in ("/accounts/profile/", f"/jobs/contacts/{contact.pk}/edit/"):
        page.goto(f"{live_server.url}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} ({scheme})", found))
        rows_of(page, "social").last.locator("select").select_option("other")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} with Other chosen ({scheme})", found))

    page.goto(f"{live_server.url}/accounts/profile/")
    rows_of(page, "repository").nth(0).locator("select").select_option("github")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
    expect(rows_of(page, "repository").nth(0).locator("[role=alert]")).to_be_visible()
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"your details with an address refused ({scheme})", found))
    assert not failures, "\n\n".join(failures)


# ------------------------------------------- the name typed beside the service's address (#678)
#
# A row on a service with a fixed front asks for the name, with the front drawn before it:
# text in the group, so it cannot be typed into and can be selected, named by the box's
# description, written left to right, and whole at every width.

PREFIX = "[data-address-group] > span[id$='_prefix']"


def prefix_of(row):
    return row.locator(PREFIX)


def address_of(row):
    return row.locator("[data-address-box]")


def test_choosing_a_service_shows_its_prefix_and_other_gives_the_address_box_back(
    page: Page, live_server, links
):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "social").last
    select = new.locator("select")
    label = new.locator("[data-address-label]")

    # A new row starts on nothing: the whole address, as before.
    expect(prefix_of(new)).to_be_hidden()
    expect(label).to_have_text("Address")
    expect(address_of(new)).to_have_attribute("type", "url")

    select.select_option("linkedin")
    expect(prefix_of(new)).to_be_visible()
    expect(prefix_of(new)).to_have_text("https://www.linkedin.com/in/")
    expect(label).to_have_text("Username")
    expect(address_of(new)).to_have_attribute("type", "text")
    expect(address_of(new)).to_have_accessible_description("https://www.linkedin.com/in/")

    # Nothing can be typed into the prefix, and it is not a control: it is text.
    assert not prefix_of(new).evaluate("(el) => el.isContentEditable")
    assert prefix_of(new).evaluate("(el) => el.querySelector('input, select, textarea') === null")
    assert prefix_of(new).evaluate("(el) => getComputedStyle(el).userSelect !== 'none'")

    # Another service swaps it; a name already typed stays where it was typed.
    address_of(new).fill("alex")
    select.select_option("threads")
    expect(prefix_of(new)).to_have_text("https://www.threads.com/@")
    expect(address_of(new)).to_have_value("alex")

    # A service with no front, and Other, are the address box.
    select.select_option("mastodon")
    expect(prefix_of(new)).to_be_hidden()
    expect(label).to_have_text("Address")
    expect(address_of(new)).to_have_attribute("type", "url")
    select.select_option("linkedin")
    select.select_option("other")
    expect(prefix_of(new)).to_be_hidden()
    # The name typed after the prefix is not lost: it is the address.
    expect(address_of(new)).to_have_value("https://www.threads.com/@alex")
    expect(address_of(new)).not_to_have_attribute("aria-describedby", re.compile("_prefix"))


def test_a_whole_address_pasted_beside_a_prefix_is_shown_whole(page: Page, live_server, links):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "social").last
    new.locator("select").select_option("linkedin")

    address_of(new).fill("https://www.linkedin.com/company/aperture")
    expect(prefix_of(new)).to_be_hidden()
    expect(address_of(new)).not_to_have_attribute("aria-describedby", re.compile("_prefix"))
    address_of(new).fill("alex")
    expect(prefix_of(new)).to_be_visible()


def test_a_name_typed_is_saved_as_the_address_it_makes(page: Page, live_server, links):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "social").last
    new.locator("select").select_option("threads")
    address_of(new).fill("@alex")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    from postulo.core.models import WebLink

    saved = WebLink.objects.get(url="https://www.threads.com/@alex")
    assert (saved.kind, saved.service) == ("social", "threads")
    # And drawn again as the name after its prefix.
    again = rows_of(page, "social").filter(has=page.locator("[data-address-box][value='alex']"))
    expect(again).to_have_count(1)
    expect(prefix_of(again)).to_have_text("https://www.threads.com/@")


def test_a_name_that_breaks_the_rule_is_refused_beside_the_box(page: Page, live_server, links):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    new = rows_of(page, "social").last
    new.locator("select").select_option("x")
    address_of(new).fill("far_too_long_for_x_name")
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()

    refused = rows_of(page, "social").filter(
        has=page.locator("[data-address-box][value='far_too_long_for_x_name']")
    )
    expect(address_of(refused)).to_have_attribute("aria-invalid", "true")
    expect(address_of(refused)).to_have_accessible_description(
        re.compile(r"^https://x\.com/ That is not a username on X\.")
    )
    expect(prefix_of(refused)).to_be_visible()


def test_with_scripts_off_a_saved_row_shows_its_prefix_and_a_new_one_the_address_box(
    browser: Browser, live_server, links
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        saved = rows_of(page, "social").nth(0)
        expect(prefix_of(saved)).to_have_text("https://www.linkedin.com/in/")
        expect(address_of(saved)).to_have_value("alex-morgan-1a2b3c")
        assert not prefix_of(saved).evaluate("(el) => el.isContentEditable")
        new = rows_of(page, "social").last
        expect(prefix_of(new)).to_be_hidden()
        expect(address_of(new)).to_have_attribute("type", "url")

        # The server drew the saved row's service, so a name typed there is composed.
        address_of(saved).fill("alex-morgan")
        page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("Your details have been saved.")).to_be_visible()
        from postulo.core.models import WebLink

        assert WebLink.objects.filter(url="https://www.linkedin.com/in/alex-morgan").exists()
    finally:
        context.close()


def test_a_row_stored_in_another_shape_is_kept_and_says_so(page: Page, live_server, applicant):
    from postulo.core.models import WebLink

    WebLink.objects.create(
        owner=applicant,
        holder=applicant.profile,
        kind="social",
        service="linkedin",
        url="https://www.linkedin.com/company/aperture",
        is_primary=True,
    )
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    row = rows_of(page, "social").first

    expect(prefix_of(row)).to_be_hidden()
    expect(address_of(row)).to_have_value("https://www.linkedin.com/company/aperture")
    expect(row.locator("[data-kept-as-written]")).to_be_visible()
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    assert WebLink.objects.get().url == "https://www.linkedin.com/company/aperture"


#: Where the prefix sits against its group: whole inside it, and nothing sideways.
PREFIX_FITS = """(row) => {
  const group = row.querySelector('[data-address-group]');
  const prefix = group.querySelector("span[id$='_prefix']");
  const box = group.querySelector('[data-address-box]');
  const g = group.getBoundingClientRect(), p = prefix.getBoundingClientRect();
  const b = box.getBoundingClientRect();
  return {
    group: [g.left, g.right, g.top, g.bottom], prefix: [p.left, p.right, p.top, p.bottom],
    box: [b.left, b.right, b.top, b.bottom], wide: prefix.scrollWidth > prefix.clientWidth + 1,
    text: prefix.textContent.trim(),
  };
}"""


def test_at_320_pixels_the_group_wraps_and_the_prefix_is_whole(page: Page, live_server, links):
    page.set_viewport_size({"width": 320, "height": 640})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    row = rows_of(page, "social").nth(0)

    fit = row.evaluate(PREFIX_FITS)

    assert fit["text"] == "https://www.linkedin.com/in/" and not fit["wide"]
    assert fit["prefix"][0] >= fit["group"][0] - 0.5 and fit["prefix"][1] <= fit["group"][1] + 0.5
    assert fit["box"][1] <= fit["group"][1] + 0.5, "the box is inside the group"
    assert fit["box"][2] >= fit["prefix"][3] - 0.5, "the box wraps under the prefix"
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_the_prefix_is_whole_at_200_percent_and_under_the_text_spacing_override(
    page: Page, live_server, links
):
    from .test_text_spacing import ADOPT, TEXT_SPACING, ZOOMED, lost_on

    page.set_viewport_size({"width": ZOOMED, "height": 800})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.evaluate(ADOPT, TEXT_SPACING)
    row = rows_of(page, "social").nth(0)

    fit = row.evaluate(PREFIX_FITS)

    assert not fit["wide"] and fit["text"] == "https://www.linkedin.com/in/"
    assert fit["prefix"][1] <= fit["group"][1] + 0.5
    assert [lost for lost in lost_on(page) if "data-address-group" in lost] == []


def test_on_a_right_to_left_page_the_prefix_keeps_its_order(
    page: Page, live_server, links, applicant
):
    profile = applicant.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    row = rows_of(page, "social").nth(0)

    fit = row.evaluate(PREFIX_FITS)

    assert fit["text"] == "https://www.linkedin.com/in/"
    assert prefix_of(row).evaluate("(el) => el.querySelector('bdi').dir") == "ltr"
    # Read left to right whatever the page is: the prefix, then the name after it.
    assert fit["prefix"][0] - fit["group"][0] < 12, fit
    assert fit["box"][0] >= fit["prefix"][0] - 0.5, fit


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_row_with_a_prefix_has_no_violations(
    page: Page,
    live_server,
    links,
    axe_source,  # noqa: F811
    scheme,
):
    """The saved row with its prefix, a new row with a service chosen by hand, and the
    row beside a refused name."""
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    failures = []
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"a saved row with its prefix ({scheme})", found))
    new = rows_of(page, "social").last
    new.locator("select").select_option("instagram")
    address_of(new).fill("two words")
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"a prefix chosen ({scheme})", found))
    page.locator("main form").first.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("[data-address-box][aria-invalid=true]")).to_have_count(1)
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"a name refused ({scheme})", found))
    assert not failures, "\n\n".join(failures)
