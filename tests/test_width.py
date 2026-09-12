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

pytestmark = pytest.mark.django_db

#: The `<main>` element's class list, whatever else is on the tag.
MAIN = re.compile(r'<main\b[^>]*\bclass="([^"]*)"')

#: Pages whose content is a grid of some kind -- a table, a board, the dashboard, a list of
#: rows -- and so the pages the cap was costing something on.
WIDE = [
    "/",
    "/applications/",
    "/applications/board/",
    "/applications/interviews/",
    "/applications/reminders/",
    "/applications/suggestions/",
    "/jobs/companies/",
    "/listings/",
    "/documents/cvs/",
    "/documents/letters/",
    "/documents/sent/",
    "/documents/files/",
]

#: A form, a detail page, an area with a layout of its own: a wide page is not a wide
#: paragraph, so these keep the 1280-pixel measure.
MEASURED = [
    "/applications/new/",
    "/jobs/postings/new/",
    "/documents/letters/new/",
    "/career/",
    "/accounts/profile/",
    "/settings/appearance/",
    "/export/",
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


def test_the_header_and_footer_span_the_screen(client, user):
    """A masthead capped at 1280 pixels over a table that has taken the width would sit
    narrower than the content under it, which reads as broken."""
    client.force_login(user)
    html = client.get("/applications/").content.decode()
    header = html[html.index("<header") : html.index("</header>")]
    footer = html[html.index("<footer") : html.index("</footer>")]
    assert "max-w-" not in header
    assert "max-w-" not in footer
