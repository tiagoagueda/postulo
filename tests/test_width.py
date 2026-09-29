"""Which pages take the whole screen, and which keep their measure (#188).

Every page used to sit in a 1280-pixel column -- `max-w-7xl` on `<main>` -- so on a wide
monitor a table with ten chosen columns scrolled inside a box with grey on both sides.
`<main>` takes its width from the page now: a grid empties the `main_width` block, and
everything else keeps the measure it had. So does a page whose parts sit side by side and
keep a measure each, like the company form's two cards (#210), a contact's details beside
its rows and the capture form beside its help (#320). Inside a frame with a sidebar the
measure is the page's own, on its parts (#320). This checks the split from the rendered
markup; `tests/e2e/test_width.py` checks what a browser makes of it at 2560 pixels.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from tests.e2e.conftest import applicant  # noqa: F401 - the fixtures `furnished` builds on
from tests.e2e.test_accessibility import furnished  # noqa: F401

pytestmark = pytest.mark.django_db

#: The `<main>` element's class list, whatever else is on the tag.
MAIN = re.compile(r'<main\b[^>]*\bclass="([^"]*)"')

#: Pages whose content is a grid of some kind -- a table, a board, the dashboard, a list of
#: rows -- and so the pages the cap was costing something on.
WIDE = [
    "/",
    "/applications/",
    "/applications/?view=board",
    "/applications/interviews/",
    # What was the reminders page: the calendar's agenda narrowed to them (#316).
    "/applications/calendar/?view=agenda&kinds=reminder",
    "/applications/suggestions/",
    "/jobs/companies/",
    "/listings/",
    "/documents/cvs/",
    "/documents/letters/",
    "/documents/sent/",
    "/documents/files/",
    # A frame with a sidebar is a grid at the top level, whatever the page inside it (#198).
    "/accounts/profile/",
    "/settings/appearance/",
    "/export/",
    # Five that were lists or grids and kept the measure anyway (#273).
    "/applications/report/",
    "/applications/tags/",
    "/jobs/industries/",
    # A form in two columns on a wide screen, whose cards keep the measure instead (#210).
    "/jobs/companies/new/",
    # And two more of the same shape: a contact's details beside its rows, and the capture
    # form beside what it does (#320).
    "/jobs/contacts/new/",
    "/jobs/captures/new/",
]

#: A form, a detail page, an area with a layout of its own: a wide page is not a wide
#: paragraph, so these keep the 1280-pixel measure.
MEASURED = [
    "/applications/new/",
    "/jobs/postings/new/",
    "/documents/letters/new/",
    "/career/",
    # Three cards that could sit two by two, but two of its 768-pixel columns need a window
    # of 1592, above `2xl`; at 1536 each would be 740, narrower than the one it has (#320).
    "/listings/new/",
]


def main_classes(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    match = MAIN.search(response.content.decode())
    assert match, f"{path} has no <main> with a class"
    return match.group(1)


@pytest.mark.parametrize("path", WIDE)
def test_a_grid_takes_the_whole_screen(client, user, path):
    client.force_login(user)
    assert "max-w-" not in main_classes(client, path), f"{path} is still capped"


@pytest.mark.parametrize("path", MEASURED)
def test_everything_else_keeps_its_measure(client, user, path):
    client.force_login(user)
    assert "max-w-7xl" in main_classes(client, path), f"{path} lost its measure"


#: The company form's two parts, each a card with the measure on it (#210).
COMPANY_CARDS = re.compile(r'<(?:div|fieldset) class="card mb-6 max-w-2xl 2xl:flex-1"')


def test_the_company_form_takes_the_screen_and_its_cards_keep_the_measure(client, user):
    """New and edit are the same form: the page is uncapped, nothing wraps the form in a
    capped column, and each of its two cards stops at `max-w-2xl` -- the details, then the
    identifiers, in that order, with nothing reordering them for the eye alone (#210)."""
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)
    for path in ("/jobs/companies/new/", f"/jobs/companies/{company.pk}/edit/"):
        html = client.get(path).content.decode()
        main = MAIN.search(html)
        assert "max-w-" not in main.group(1), f"{path} is still capped"
        inside = html[main.end() : html.index("</main>", main.end())]
        assert "mx-auto" not in inside, f"{path} centres a capped column again"
        cards = COMPANY_CARDS.findall(inside)
        assert len(cards) == 2, f"{path}: {cards}"
        assert inside.index("data-identifiers") > inside.index('name="name"'), path
        assert inside.index('type="submit"') > inside.index("data-identifiers"), path
        assert not re.search(r"\border-", inside), f"{path} reorders something visually"


#: Elements that never close, so never hold anything.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}

#: Inputs that are not a box somebody types into.
NOT_A_BOX = {"hidden", "checkbox", "radio", "submit", "button", "image", "reset"}


class Unmeasured(HTMLParser):
    """Every card and every text box inside `<main>` with no `max-w-` on it or around it.

    A stack of the open elements' classes, so "around it" is the element's own ancestors
    and not whatever came before it in the source. `mx-auto` is collected too: the measure
    sits at the start edge, as the company form's cards do, not in the middle (#210).
    """

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, list[str]]] = []
        self.inside = False
        self.missing: list[str] = []
        self.centred: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "main":
            self.inside = True
            return
        if not self.inside:
            return
        is_box = tag in {"select", "textarea"} or (
            tag == "input" and attributes.get("type", "text") not in NOT_A_BOX
        )
        if "card" in classes or is_box:
            measured = any(
                is_a_measure(name) for _, held in [*self.stack, (tag, classes)] for name in held
            )
            if not measured:
                self.missing.append(
                    f"<{tag} class={' '.join(classes)!r} name={attributes.get('name')!r}>"
                )
        if "mx-auto" in classes:
            self.centred.append(f"<{tag} class={' '.join(classes)!r}>")
        if tag not in VOID:
            self.stack.append((tag, classes))

    def handle_endtag(self, tag):
        if tag == "main":
            self.inside = False
            return
        for depth in range(len(self.stack) - 1, -1, -1):
            if self.stack[depth][0] == tag:
                del self.stack[depth:]
                break


#: Caps that do not keep a form to a measure: they are the frame's width, or none at all.
NOT_A_MEASURE = {"max-w-full", "max-w-none", "max-w-5xl", "max-w-6xl", "max-w-7xl"}


def is_a_measure(name: str) -> bool:
    return (
        name.startswith("max-w-")
        and name not in NOT_A_MEASURE
        and not name.startswith("max-w-screen")
    )


def unmeasured(html: str) -> Unmeasured:
    parser = Unmeasured()
    parser.feed(html)
    return parser


#: Your details and every page of Settings that the core draws, forms and all. The import's
#: mapping is the one that keeps the width -- two tables of a spreadsheet's own columns
#: (#273) -- and is reached only with a sheet stashed, so it is not here. *Delete my account*
#: asks for the password again first, so the browser test measures that one.
FRAMED = [
    "/accounts/profile/",
    "/settings/appearance/",
    "/settings/accessibility/",
    "/settings/language/",
    "/settings/account/",
    "/settings/capture/",
    "/settings/connections/",
    "/settings/connections/add/",
    "/settings/connections/add/notifier/email/",
    "/settings/connections/add/notifier/webhook/",
    "/settings/plugins/",
    "/settings/plugins/?internal=1",
    "/capture-tokens/",
    "/export/",
    "/import/",
    "/accounts/email/",
    "/accounts/password/change/",
]


@pytest.mark.parametrize("path", FRAMED)
def test_your_details_and_settings_keep_the_measure_on_their_parts(client, furnished, path):  # noqa: F811
    """Their text boxes ran 2,214 pixels wide at 2560, because the frame takes the screen
    (#198) and nothing on the form kept a measure of its own. Every card and every box now
    sits inside a `max-w-2xl`, at the start edge of the column beside the sidebar (#320)."""
    client.force_login(furnished["applicant"])
    response = client.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    html = response.content.decode()
    assert "max-w-" not in MAIN.search(html).group(1), f"{path}: the frame is capped again"
    found = unmeasured(html)
    assert not found.missing, f"{path}: no measure on {found.missing}"
    assert not found.centred, f"{path}: centred, not at the start edge: {found.centred}"


def test_a_connection_s_own_page_keeps_the_measure_too(client, furnished):  # noqa: F811
    """The same form as a new connection's, reached with a row that exists (#320)."""
    client.force_login(furnished["applicant"])
    html = client.get(f"/settings/connections/{furnished['connection'].pk}/").content.decode()
    found = unmeasured(html)
    assert not found.missing and not found.centred, (found.missing, found.centred)


def test_the_detector_knows_a_measured_card_from_one_that_is_not():
    page = (
        '<main class="px-4"><div class="max-w-2xl"><div class="card"><input name="a"></div></div>'
    )
    assert not unmeasured(page + "</main>").missing
    loose = page + '<div class="card"><input type="text" name="b"><input type="hidden" name="c">'
    assert len(unmeasured(loose + "</div></main>").missing) == 2  # the card and one box
    assert unmeasured('<main><div class="mx-auto max-w-2xl"></div></main>').centred
    for frame in [*sorted(NOT_A_MEASURE), "max-w-screen-2xl"]:
        wide = f'<main><div class="{frame}"><div class="card"></div></div></main>'
        assert unmeasured(wide).missing, frame


def a_contact_page(client, contact=None) -> str:
    path = f"/jobs/contacts/{contact.pk}/edit/" if contact else "/jobs/contacts/new/"
    html = client.get(path).content.decode()
    main = MAIN.search(html)
    assert "max-w-" not in main.group(1), f"{path} is still capped"
    return html[main.end() : html.index("</main>", main.end())]


def test_a_contact_s_details_and_rows_are_two_columns_each_with_the_measure(client, furnished):  # noqa: F811
    """The details first, then the rows -- telephone numbers, a card of links per kind --
    in a column of their own, each at `max-w-2xl`; *Save* and *Merge* after both, in a row
    that keeps the measure, and nothing reordered for the eye alone (#320)."""
    client.force_login(furnished["applicant"])
    for contact in (None, furnished["contact"]):
        inside = a_contact_page(client, contact)
        where = "edit" if contact else "new"
        assert "mx-auto" not in inside, f"{where}: a capped column is centred again"
        assert '<div class="2xl:flex 2xl:items-start 2xl:gap-6">' in inside, where
        details = inside.index('<div class="card mb-6 max-w-2xl 2xl:flex-1">')
        rows = inside.index('<div class="max-w-2xl 2xl:flex-1" data-contact-rows>')
        assert details < inside.index('name="name"') < rows, where
        assert rows < inside.index("data-phone-numbers") < inside.index("data-web-links"), where
        buttons = inside.index('<div class="flex max-w-2xl flex-wrap items-center gap-3">')
        assert buttons > inside.index("data-web-links"), f"{where}: Save before the rows"
        assert inside.index('type="submit"', buttons), where
        assert not re.search(r"\border-", inside), f"{where} reorders something visually"
    assert "Merge with another contact" in inside[buttons:], "Merge is in the button row"


def test_with_every_row_switched_off_a_contact_is_one_column(client, furnished):  # noqa: F811
    """No telephone numbers and no kind of link: nothing to put in a second column, so there
    is none, and the details keep their measure on their own (#320)."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS
    from postulo.plugins.repositories import REPOSITORIES
    from postulo.plugins.social_profiles import SOCIAL_PROFILES
    from postulo.plugins.websites import WEBSITES

    person = furnished["applicant"]
    for plugin in (PHONE_NUMBERS, SOCIAL_PROFILES, REPOSITORIES, WEBSITES):
        PluginPolicy.objects.create(
            plugin=plugin, person=person, state=PluginPolicy.State.FORCED_OFF
        )
    client.force_login(person)
    inside = a_contact_page(client, furnished["contact"])
    assert "data-contact-rows" not in inside
    assert "data-phone-numbers" not in inside and "data-web-links" not in inside
    assert '<div class="card mb-6 max-w-2xl 2xl:flex-1">' in inside


def test_the_capture_form_sits_beside_what_it_does(client, user):
    """The form -- the address and the pasted page, with its button -- then the help, each
    at `max-w-2xl` in a row from `2xl`, in that order (#320)."""
    client.force_login(user)
    html = client.get("/jobs/captures/new/").content.decode()
    main = MAIN.search(html)
    inside = html[main.end() : html.index("</main>", main.end())]
    assert "mx-auto" not in inside, "a capped column is centred again"
    row = inside.index('<div class="2xl:flex 2xl:items-start 2xl:gap-6">')
    form = inside.index('class="max-w-2xl 2xl:flex-1">', row)
    assert inside.index('name="url"') > form
    button = inside.index('type="submit"', form)
    help_ = inside.index("data-capture-help")
    assert button < help_, "the help comes after the form and its button"
    help_tag = inside[inside.rindex("<div", 0, help_) : help_]
    assert "max-w-2xl" in help_tag and "2xl:flex-1" in help_tag, help_tag
    assert not re.search(r"\border-", inside), "something is reordered visually"


def test_an_application_s_documents_take_the_screen(client, furnished):  # noqa: F811
    """Three lists of rows, which sat in a 768-pixel column inside the 1280 one (#273)."""
    client.force_login(furnished["applicant"])
    path = f"/documents/applications/{furnished['application'].pk}/documents/"
    assert "max-w-" not in main_classes(client, path)


def test_the_import_mapping_takes_the_screen(client, user):
    """A spreadsheet's own columns previewed in a box that scrolled sideways inside a
    1024-pixel column: the #188 symptom exactly (#273). Reached with a sheet stashed."""
    from postulo.core import csv_import

    client.force_login(user)
    session = client.session
    csv_import.stash(session, b"Company,Role,Applied\nAperture,Tester,2026-01-02\n", "x.csv")
    session.save()

    html = client.get("/import/").content.decode()
    assert "Which column is which?" in html, "the mapping step, not the upload step"
    assert "max-w-4xl" not in html
    assert "max-w-" not in MAIN.search(html).group(1)


#: Pages that hold a table or a grid of figures and keep the 1280-pixel measure on
#: purpose, with the reason. Keep it empty if you can.
KEPT: dict[str, str] = {}

#: A table, or a four-column grid of figures -- the dashboard's shape, and the report's.
#: Not `sm:grid-cols-2`, which is how a form pairs its fields, nor `lg:grid-cols-3`, which
#: is a detail page's sidebar layout: those are the pages #188 keeps at 1280 on purpose.
GRID = re.compile(r"<table\b|\blg:grid-cols-4\b")


def test_every_page_with_a_table_or_a_grid_has_taken_the_screen(client, furnished):  # noqa: F811
    """So the next one is caught rather than surveyed (#273): every page the browser suite
    visits is rendered here, and one holding a `<table>` or a `grid-cols` grid inside
    `<main>` must have emptied `main_width`, or be named in KEPT with a reason."""
    from tests.e2e.test_accessibility import signed_in_paths

    client.force_login(furnished["applicant"])
    paths = signed_in_paths(
        furnished["application"],
        furnished["company"],
        furnished["applicant"],
        furnished["experience"],
        things=furnished,
    )
    capped = []
    for path in paths:
        if path in KEPT:
            continue
        response = client.get(path)
        if response.status_code != 200:
            continue
        html = response.content.decode()
        match = MAIN.search(html)
        if not match:
            continue
        inside = html[match.end() : html.find("</main>", match.end())]
        if "max-w-7xl" in match.group(1) and GRID.search(inside):
            capped.append(path)
    assert not capped, f"a table or a grid in a 1280-pixel column: {capped}"


def test_the_header_and_footer_span_the_screen(client, user):
    """A masthead capped at 1280 pixels over a table that has taken the width would sit
    narrower than the content under it, which reads as broken."""
    client.force_login(user)
    html = client.get("/applications/").content.decode()
    header = html[html.index("<header") : html.index("</header>")]
    footer = html[html.index("<footer") : html.index("</footer>")]
    assert "max-w-" not in header
    assert "max-w-" not in footer
