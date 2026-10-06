"""One kind of record as a file, in a browser: out, back in, with axe over the review (#659).

The pages are walked empty by `test_accessibility.py`; what a GET cannot reach is the half
after a file has been chosen, which has a table with every outcome in it. So the review is
drawn here, in both themes, with one row of each outcome on it.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)

pytestmark = pytest.mark.e2e


def a_companies_file(path):
    document = {
        "postulo": {"companies_format": 1, "version": "0.5.0"},
        "companies": [
            {"name": "Aperture Science", "location": "Cambridge"},
            {"name": "Black Mesa", "location": "New Mexico"},
            {"name": "BLACK MESA"},
            {"name": "Bad site", "website": "not an address"},
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_export_upload_read_the_plan_and_confirm(live_server, page: Page, furnished, tmp_path):  # noqa: F811
    from postulo.jobs.models import Company

    base = live_server.url
    me = furnished["applicant"]
    sign_in(page, base)

    page.goto(f"{base}/export/")
    card = page.locator("[data-kind-file='companies']")
    expect(card).to_contain_text("Companies")
    card.get_by_role("link").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Companies")

    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download the file").click()
    kept = tmp_path / "kept.json"
    download_info.value.save_as(kept)
    from postulo.core import kind_files

    assert (
        json.loads(kept.read_text(encoding="utf-8"))["postulo"]["companies_format"]
        == kind_files.FORMATS["companies"]
    )

    page.locator("input[type=file]").set_input_files(str(a_companies_file(tmp_path / "in.json")))
    page.locator("[data-kind-upload] button[type=submit]").click()
    review = page.locator("[data-found='companies']")
    expect(review.locator("tr[data-outcome='present']")).to_contain_text("Aperture Science")
    expect(review.locator("tr[data-outcome='add']")).to_contain_text("Black Mesa")
    expect(review.locator("tr[data-outcome='repeated']")).to_have_count(1)
    expect(review.locator("tr[data-outcome='refused']")).to_contain_text("Bad site")
    assert not Company.objects.for_user(me).filter(name="Black Mesa").exists()

    page.get_by_role("button", name="Add what is new").click()
    expect(page.get_by_text("One record was added")).to_be_visible()
    assert Company.objects.for_user(me).filter(name="Black Mesa").count() == 1


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_review_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
    tmp_path,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/export/companies/")
    page.locator("input[type=file]").set_input_files(str(a_companies_file(tmp_path / "in.json")))
    page.locator("[data-kind-upload] button[type=submit]").click()
    expect(page.get_by_role("heading", name="What is in the file")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/export/companies/ review ({scheme})", found)
