"""Choosing what a CV prints about you, in a real browser (#308).

The card on a CV's settings is a disclosure, a row of native menus and a list that belongs
to one answer of one of them. Nothing in it is a script's: the disclosure is a `<details>`,
and the list of identifiers is shown by the stylesheet, following the menu above it with
`:has()`. So the walk is taken twice, with JavaScript and without, and has to come out the
same: open the card, choose a number and a link, tick an identifier, ask for the pronouns,
save -- and find the preview on the CV's page printing what was chosen.

The page is then looked at in the state the ordinary walks do not reach, with the card open
and the list showing: axe in both themes, the targets, and reflow at 320 pixels in the
three languages the reflow walk is taken in.

And the name's line is read where only a browser can read it: where each character of it
is drawn. A form of address in one script before a name in another must keep its full stop
on its own side, in a document that runs either way.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_reflow import LANGUAGES, NARROW, SCROLLS_SIDEWAYS, SPILLS
from .test_target_size import too_small

pytestmark = pytest.mark.e2e

MOBILE = "+351912345678"
DESK = "+351211111111"
LINKEDIN = "https://www.linkedin.com/in/alex-morgan"
MASTODON = "https://mastodon.example/@alex"
SITE = "https://alex.example"
ORCID = "0000-0002-1825-0097"
#: An address as long as the column allows, to hold the menus to the card at 320 pixels.
LONG = "https://forge.example/" + "a-very-long-path-segment/" * 8 + "alex"


@pytest.fixture
def cv(applicant):
    """Somebody with several of everything, and the one CV the fixture gives them."""
    import datetime as dt

    from allauth.account.models import EmailAddress
    from django.contrib.contenttypes.models import ContentType

    from postulo.accounts.models import PersonIdentifier
    from postulo.core.models import PhoneNumber, PostalAddress, WebLink
    from postulo.documents.models import CV, CVItem
    from postulo.resume.models import Experience

    profile = applicant.profile
    profile.form_of_address = "Dr"
    profile.pronouns = "they/them"
    profile.save()
    PhoneNumber.objects.create(
        owner=applicant, holder=profile, number=MOBILE, kind="mobile", is_primary=True
    )
    PhoneNumber.objects.create(owner=applicant, holder=profile, number=DESK, kind="work")
    for kind, url, label, primary in (
        ("social", LINKEDIN, "LinkedIn", True),
        ("social", MASTODON, "", False),
        ("repository", LONG, "", True),
        ("website", SITE, "", True),
    ):
        WebLink.objects.create(
            owner=applicant, holder=profile, kind=kind, url=url, label=label, is_primary=primary
        )
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value=ORCID)
    PersonIdentifier.objects.create(
        profile=profile, scheme="other", value="R-1234", label="ResearcherID"
    )
    PostalAddress.objects.create(
        owner=applicant, holder=profile, municipality="Lisboa", country="PT", is_primary=True
    )
    EmailAddress.objects.create(user=applicant, email="alex@work.example", verified=True)

    document = CV.objects.get(owner=applicant, name="Main CV")
    job = Experience.objects.create(
        owner=applicant, organisation="Aperture", role="Engineer", start_date=dt.date(2021, 3, 1)
    )
    CVItem.objects.create(
        owner=applicant,
        cv=document,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=job.pk,
    )
    return document


def choose_and_save(page: Page, base: str, cv) -> None:
    """The whole choice, by the controls a person has: no address typed, no value set."""
    page.goto(f"{base}/documents/cvs/{cv.pk}/")
    preview = page.frame_locator("iframe[data-document-preview]").locator("body")
    expect(preview).to_contain_text(MOBILE)
    expect(preview).to_contain_text(LINKEDIN)

    page.get_by_role("link", name="Settings", exact=True).click()
    card = page.locator("[data-cv-prints]")
    expect(card.get_by_role("heading", name="What this CV prints about you")).to_be_visible()

    # Shut until something is chosen, with the master switch outside it and in view.
    phone = page.locator("select[name=prints_phone]")
    expect(page.locator("input[name=show_contact_details]")).to_be_visible()
    expect(page.locator("input[name=show_contact_details]")).to_be_checked()
    expect(phone).to_be_hidden()
    card.locator("summary").click()
    expect(phone).to_be_visible()

    # Each menu names its rows the way *Your details* does, and opens on following it.
    expect(phone).to_have_value("default")
    expect(phone.locator("option")).to_have_text(
        ["Your primary number", f"{MOBILE} (Mobile)", f"{DESK} (Work)", "No telephone number"]
    )
    phone.select_option(label=f"{DESK} (Work)")
    page.locator("select[name=prints_social]").select_option(label=MASTODON)
    page.locator("select[name=prints_website]").select_option(label="No website")

    # The list of identifiers belongs to one answer of its menu, and is there only then.
    ticks = page.locator("[data-shown-if-chosen]")
    expect(ticks).to_be_hidden()
    page.locator("select[name=prints_identifiers]").select_option("chosen")
    expect(ticks).to_be_visible()
    ticks.get_by_label(f"ORCID: {ORCID}").check()
    page.locator("select[name=prints_identifiers]").select_option("default")
    expect(ticks).to_be_hidden()
    page.locator("select[name=prints_identifiers]").select_option("chosen")
    expect(ticks.get_by_label(f"ORCID: {ORCID}")).to_be_checked()

    page.get_by_label("Print your pronouns after your name").check()
    page.locator("main form").get_by_role("button", name="Save", exact=True).click()

    # Saving lands on the CV's own page, and the frame there is the document as chosen.
    expect(page).to_have_url(f"{base}/documents/cvs/{cv.pk}/")
    preview = page.frame_locator("iframe[data-document-preview]").locator("body")
    expect(preview).to_contain_text(DESK)
    expect(preview).to_contain_text(MASTODON)
    expect(preview).to_contain_text("Alex Morgan (they/them)")
    expect(preview).to_contain_text(f"ORCID {ORCID}")
    text = preview.inner_text()
    for gone in (MOBILE, LINKEDIN, SITE, "R-1234"):
        assert gone not in text, gone
    # Left alone, so they follow the profile as they did.
    assert "Lisboa, Portugal" in text and "alex.morgan@example.org" in text

    # Opened again, the choosers are open on what was chosen.
    page.get_by_role("link", name="Settings", exact=True).click()
    expect(page.locator("[data-cv-choices]")).to_have_attribute("open", "")
    expect(page.locator("select[name=prints_phone]").locator("option:checked")).to_have_text(
        f"{DESK} (Work)"
    )
    expect(page.locator("[data-shown-if-chosen]").get_by_label(f"ORCID: {ORCID}")).to_be_checked()
    expect(
        page.locator("[data-shown-if-chosen]").get_by_label("ResearcherID: R-1234")
    ).not_to_be_checked()


def test_choosing_what_a_cv_prints_and_seeing_it_in_the_preview(page: Page, live_server, cv):
    sign_in(page, live_server.url)
    choose_and_save(page, live_server.url, cv)

    cv.refresh_from_db()
    assert cv.pinned_phone.number == DESK and cv.pinned_social.url == MASTODON
    assert cv.website_choice == "none" and cv.show_pronouns
    assert [row.value for row in cv.pinned_identifiers.all()] == [ORCID]


def test_the_same_with_scripts_off(browser, live_server, cv):
    """The disclosure is the browser's and the list is the stylesheet's: neither is a
    script's, and the preview is a frame the page load fetches."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        choose_and_save(page, live_server.url, cv)
    finally:
        context.close()

    cv.refresh_from_db()
    assert cv.pinned_phone.number == DESK and cv.website_choice == "none"
    assert [row.value for row in cv.pinned_identifiers.all()] == [ORCID]


def open_card(page: Page, base: str, cv) -> None:
    """The settings page with every control of the card showing, the list included."""
    page.goto(f"{base}/documents/cvs/{cv.pk}/edit/")
    if page.locator("select[name=prints_phone]").is_hidden():
        page.locator("[data-cv-prints] summary").click()
    page.locator("select[name=prints_identifiers]").select_option("chosen")
    expect(page.locator("[data-shown-if-chosen]")).to_be_visible()
    expect(page.locator("input[name=show_pronouns]")).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_card_open_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    cv,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    open_card(page, live_server.url, cv)

    found = violations_on(page, axe_source)
    assert not found, describe(f"/documents/cvs/{cv.pk}/edit/ with the card open ({scheme})", found)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_card_saying_a_chosen_row_has_gone_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    cv,
    scheme,
):
    """The warning is drawn on both pages, and only once a pinned row has been deleted."""
    from postulo.documents import printing
    from postulo.documents.models import Prints

    desk = cv.owner.profile.phone_numbers.get(number=DESK)
    printing.choose(cv, "phone", Prints.CHOSEN, desk.pk)
    cv.save()
    desk.delete()

    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    for path in (f"/documents/cvs/{cv.pk}/", f"/documents/cvs/{cv.pk}/edit/"):
        page.goto(f"{live_server.url}{path}")
        expect(page.locator("[data-cv-gone]")).to_contain_text(
            "The telephone number chosen for this CV is no longer in your details"
        )
        found = violations_on(page, axe_source)
        assert not found, describe(f"{path} with a chosen row gone ({scheme})", found)
    # The disclosure is open on it, and the menu on what the CV prints now.
    expect(page.locator("select[name=prints_phone]")).to_be_visible()
    expect(page.locator("select[name=prints_phone]")).to_have_value("none")


def test_everything_in_the_open_card_is_big_enough_to_hit(live_server, page: Page, cv):
    sign_in(page, live_server.url)
    open_card(page, live_server.url, cv)
    assert too_small(page) == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_open_card_reflows_at_320_pixels(live_server, page: Page, cv, language):
    """Nothing across, with a web address as long as the column allows in one of the menus:
    a native menu is as wide as its longest option unless the card holds it."""
    from postulo.accounts.models import Profile

    sign_in(page, live_server.url)
    Profile.objects.filter(user=cv.owner).update(language=language)
    page.set_viewport_size({"width": NARROW, "height": 800})
    open_card(page, live_server.url, cv)

    assert page.evaluate(SPILLS) == []
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0
    card = page.locator("[data-cv-prints]").bounding_box()
    for name in ("prints_phone", "prints_repository", "prints_identifiers"):
        menu = page.locator(f"select[name={name}]").bounding_box()
        assert menu["x"] >= card["x"] and menu["x"] + menu["width"] <= card["x"] + card["width"]


# ------------------------------------------- the name's line, in a document either way

#: The characters of an element, left to right as they are drawn on the page and without
#: the spaces. What the markup says is one order and what the bidirectional algorithm
#: draws is another; only the second is what a reader sees.
DRAWN = """
heading => {
  const found = [];
  const walker = heading.ownerDocument.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    for (let index = 0; index < node.data.length; index++) {
      if (node.data[index].trim() === '') continue;
      const range = heading.ownerDocument.createRange();
      range.setStart(node, index);
      range.setEnd(node, index + 1);
      found.push([node.data[index], range.getBoundingClientRect().left]);
    }
  }
  return found.sort((one, other) => one[1] - other[1]).map(pair => pair[0]).join('');
}
"""


def name_line_as_drawn(page: Page, base: str, cv, *, language, first, last, form, pronouns) -> str:
    owner = cv.owner
    owner.first_name, owner.last_name = first, last
    owner.save()
    owner.profile.form_of_address = form
    owner.profile.pronouns = pronouns
    owner.profile.save()
    cv.language = language
    cv.show_form_of_address = cv.show_pronouns = True
    cv.save()

    sign_in(page, base)
    page.goto(f"{base}/documents/cvs/{cv.pk}/")
    frame = page.frame_locator("iframe[data-document-preview]")
    expect(frame.locator("html")).to_have_attribute("dir", "rtl" if language == "ar" else "ltr")
    heading = frame.locator("header.contact h1")
    expect(heading).to_contain_text(last)
    return heading.evaluate(DRAWN)


def test_a_latin_form_of_address_keeps_its_stop_in_a_document_set_right_to_left(
    page: Page, live_server, cv
):
    """*Dr.* before an Arabic name, in an Arabic CV. Left to the paragraph, the stop is a
    neutral character at the edge of a left-to-right run and is drawn on the far side of
    it: *.Dr*. In a `<bdi>` of its own the form of address is read by itself."""
    drawn = name_line_as_drawn(
        page,
        live_server.url,
        cv,
        language="ar",
        first="علي",
        last="حسن",
        form="Dr.",
        pronouns="he/him",
    )
    assert "Dr." in drawn, drawn
    assert ".Dr" not in drawn, drawn
    assert "he/him" in drawn, drawn
    # The document still runs right to left: the form of address is before the name, so
    # it is drawn to the right of it, and the pronouns after it, to the left.
    assert drawn.index("he/him") < drawn.index("ع") < drawn.index("Dr.")


def test_an_arabic_form_of_address_keeps_its_stop_in_a_document_set_left_to_right(
    page: Page, live_server, cv
):
    """The other way round: *د.* before a name in Latin letters, in an English CV. The stop
    belongs after the letter as Arabic is read, which is to its left; left to the
    paragraph it is drawn to the right, as though it ended an English sentence."""
    drawn = name_line_as_drawn(
        page,
        live_server.url,
        cv,
        language="en",
        first="Alex",
        last="Morgan",
        form="د.",
        pronouns="هي",
    )
    assert ".د" in drawn, drawn
    assert "د." not in drawn, drawn
    assert drawn.index("د") < drawn.index("AlexMorgan") < drawn.index("ه")
