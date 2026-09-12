"""Editing a cell in a real browser, and what axe makes of it while it is open (#135).

The machinery either side of this is covered by `tests/test_editable_cells.py`. Three
things are not, and none of them can be tested honestly without a browser: **focus**, which
is the failure the issue names outright — a keyboard user thrown to the top of a re-rendered
table on every edit — **Escape**, which is not a form key and had to be asked for, and
**what axe says about a table with an input in one of its cells**, in both themes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e

AXE = Path(__file__).resolve().parents[2] / "node_modules" / "axe-core" / "axe.min.js"
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def a_company(applicant, name: str = "Aperture Science"):
    from postulo.jobs.models import Company

    return Company.objects.create(owner=applicant, name=name)


def open_the_editor(page: Page, live_server, applicant, name: str = "Aperture Science"):
    company = a_company(applicant, name)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")
    page.locator("[data-cell-open]").first.click()
    return company


def test_the_value_becomes_an_input_where_it_sits(page: Page, live_server, applicant):
    open_the_editor(page, live_server, applicant)

    editor = page.locator("[data-cell-editor] input:not([type=hidden])")

    expect(editor).to_be_visible()
    expect(editor).to_have_value("Aperture Science")


def test_the_caret_lands_in_it(page: Page, live_server, applicant):
    """Otherwise opening an editor is two actions: one to open it, one to find it."""
    open_the_editor(page, live_server, applicant)

    expect(page.locator("[data-cell-editor] input:not([type=hidden])")).to_be_focused()


def test_saving_puts_focus_back_where_it_came_from(page: Page, live_server, applicant):
    """The failure the issue names: a keyboard user thrown to the top of a re-rendered
    table on every edit.
    """
    company = open_the_editor(page, live_server, applicant)

    field = page.locator("[data-cell-editor] input:not([type=hidden])")
    field.fill("Aperture Laboratories")
    field.press("Enter")

    expect(page.locator("[data-cell-open]").first).to_be_focused()
    company.refresh_from_db()
    assert company.name == "Aperture Laboratories"


def test_escape_abandons_and_keeps_the_old_value(page: Page, live_server, applicant):
    company = open_the_editor(page, live_server, applicant)

    field = page.locator("[data-cell-editor] input:not([type=hidden])")
    field.fill("Something else")
    field.press("Escape")

    expect(page.locator("[data-cell-editor]")).to_have_count(0)
    expect(page.locator("[data-cell-open]").first).to_contain_text("Aperture Science")
    company.refresh_from_db()
    assert company.name == "Aperture Science"


def test_escape_works_before_htmx_has_wired_the_editor(page: Page, live_server, applicant):
    """The editor arrives by a swap, and htmx wires what it swapped in -- the Cancel button
    Escape clicks -- only when the swap *settles*, 20 ms later by default, while focus is put
    in the input the moment it lands. For those 20 ms Escape reached a Cancel that nothing
    was listening to, which a browser test is fast enough to hit one run in three (#161).

    The window is widened to two seconds here, so the race is not a race: an Escape pressed
    inside it must still abandon the edit.
    """
    page.add_init_script(
        "document.addEventListener('DOMContentLoaded', function () {"
        " htmx.config.defaultSettleDelay = 2000; });"
    )
    open_the_editor(page, live_server, applicant)

    page.locator("[data-cell-editor] input:not([type=hidden])").press("Escape")

    expect(page.locator("[data-cell-editor]")).to_have_count(0)
    expect(page.locator("[data-cell-open]").first).to_contain_text("Aperture Science")


def test_a_refusal_appears_in_the_cell_and_the_value_stays_to_be_fixed(
    page: Page, live_server, applicant
):
    a_company(applicant, "Black Mesa")
    open_the_editor(page, live_server, applicant, "Aperture Science")

    field = page.locator("[data-cell-editor] input:not([type=hidden])")
    field.fill("Black Mesa")
    field.press("Enter")

    expect(page.locator("[data-cell-editor]")).to_be_visible()
    expect(page.locator("[data-cell-editor] input:not([type=hidden])")).to_have_value("Black Mesa")
    # Once, in the block Postulo already announces every field error from.
    alert = page.locator("[data-cell-editor] [role=alert]")
    expect(alert).to_have_count(1)
    expect(alert).to_contain_text("already have a company")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_nothing_with_an_editor_open(page: Page, live_server, applicant, scheme):
    """A table with an input in one of its cells is a shape axe has not seen here before."""
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    page.emulate_media(color_scheme=scheme)
    open_the_editor(page, live_server, applicant)

    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    violations = page.evaluate(
        """async (tags) => {
            const results = await axe.run(document, {
                runOnly: { type: "tag", values: tags },
                resultTypes: ["violations"],
            });
            return results.violations.map(v => `${v.id}: ${v.help}`);
        }""",
        TAGS,
    )

    assert not violations, f"{scheme}: {violations}"
