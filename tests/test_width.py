"""Which pages take the whole screen, and which keep their measure (#188).

Every page used to sit in a 1280-pixel column -- `max-w-7xl` on `<main>` -- so on a wide
monitor a table with ten chosen columns scrolled inside a box with grey on both sides.
`<main>` takes its width from the page now: a grid empties the `main_width` block, and
everything else keeps the measure it had. This checks the split from the rendered markup;
`tests/e2e/test_width.py` checks what a browser makes of it at 2560 pixels.
"""

from __future__ import annotations

import re

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
    "/applications/reminders/",
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
]

#: A form, a detail page, an area with a layout of its own: a wide page is not a wide
#: paragraph, so these keep the 1280-pixel measure.
MEASURED = [
    "/applications/new/",
    "/jobs/postings/new/",
    "/documents/letters/new/",
    "/career/",
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
