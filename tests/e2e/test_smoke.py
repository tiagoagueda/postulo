"""The critical path, in a browser.

One test, one story: sign in, capture a posting by pasting its page, review it into an
application, move it along on the board, record what was sent, and take the export home.
Unit tests cover each step in isolation; this one proves the steps still join up after the
interface changes around them.

Three of those steps are slow enough to be done by a worker rather than while somebody
waits (#247), and with none configured -- which is how this runs -- the work is done where
the request stands and the page that reports it is already finished when it is drawn. So
the story gains one click in three places and loses nothing: *Take me to it*.

Assertions are on what a person sees — headings, labels, button names — rather than on
markup, so a restyle does not fail the test and a broken page does.
"""

import json
import re
import zipfile

import pytest
from playwright.sync_api import Page, expect

from postulo.documents.pdf import PDFBackendUnavailable, get_pdf_backend

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e

POSTING_URL = "https://careers.example.org/jobs/42"

# A page as an employer's site would serve it: schema.org JobPosting in JSON-LD, which the
# built-in source reads. Pasting it is how the browser extension will capture, too.
POSTING_HTML = """<!doctype html>
<html lang="en">
<head>
<title>Senior Django Developer - Aperture Science</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Senior Django Developer",
  "description": "<p>Build the portal that keeps the test subjects informed.</p>",
  "datePosted": "2026-09-01",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "Aperture Science"},
  "jobLocation": {
    "@type": "Place",
    "address": {"@type": "PostalAddress", "addressLocality": "Cambridge", "addressCountry": "GB"}
  }
}
</script>
</head>
<body><h1>Senior Django Developer</h1></body>
</html>
"""


def pdf_backend_available() -> bool:
    try:
        get_pdf_backend()
    except PDFBackendUnavailable:
        return False
    return True


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def test_the_critical_path(live_server, page: Page, applicant) -> None:
    base = live_server.url
    sign_in(page, base)

    # Capture by pasting the page: nothing is fetched, the parser does the work.
    page.goto(f"{base}/jobs/captures/new/")
    page.locator("input[name=url]").fill(POSTING_URL)
    page.locator("form details > summary").click()
    page.locator("textarea[name=html]").fill(POSTING_HTML)
    page.get_by_role("button", name="Read the page").click()

    # Reading the page is a worker's job now, so the press lands on the page that watches
    # it. Nothing was fetched here -- the HTML came with the form -- but the parse is still
    # work somebody asked for, and one path through this is easier to trust than two (#247).
    expect(page.get_by_role("heading", level=1)).to_have_text("Reading the page")
    page.get_by_role("link", name="Take me to it").click()

    # The review screen is the posting form, pre-filled from the page. Saving makes a
    # listing: something noticed, not yet decided about.
    expect(page.locator("#id_title")).to_have_value("Senior Django Developer")
    expect(page.locator("#id_company_name")).to_have_value("Aperture Science")
    page.get_by_role("button", name="Save to listings").click()

    expect(page.get_by_role("heading", level=1)).to_contain_text("Senior Django Developer")
    expect(page.locator("[data-listing-state='new']")).to_be_visible()

    # The decision: apply. That creates the application and starts the timeline.
    page.get_by_role("link", name="Apply", exact=True).click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("Apply: Senior Django Developer")
    page.get_by_role("button", name="Record the application").click()

    expect(page.get_by_role("heading", level=1)).to_contain_text("Senior Django Developer")
    expect(page.get_by_role("link", name="Aperture Science")).to_be_visible()
    expect(page.get_by_text("Application created")).to_be_visible()
    application_url = page.url

    # On the board, changing the select moves the card: the change is submitted at once.
    page.goto(f"{base}/applications/?view=board")
    card = page.locator("article", has_text="Senior Django Developer")
    expect(card).to_have_count(1)
    # Named for the application it belongs to since #227, because thirty identical "Change
    # status" menus told a screen-reader user nothing about which card they were on.
    card.get_by_label(re.compile("^Status of ")).select_option("screening")
    screening_heading = page.get_by_role("heading", name="Screening", exact=True)
    screening = page.locator("section", has=screening_heading)
    expect(screening.locator("article", has_text="Senior Django Developer")).to_have_count(1)

    # Record what was sent. With a PDF backend the CV is frozen and attached; without one
    # the page says so instead of failing silently. Both are correct behaviour.
    page.goto(application_url)
    page.get_by_role("link", name="Record what you sent").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Record what you sent")
    page.locator("#id_cv").select_option(label="Main CV")
    page.get_by_role("button", name="Freeze and attach").click()

    # The slowest button in Postulo: two documents can mean two renders, and on the
    # Chromium backend a render is a browser launch. Watched rather than waited for (#247).
    expect(page.get_by_role("heading", level=1)).to_have_text("Freezing what you sent")
    if pdf_backend_available():
        expect(page.get_by_text("Recorded what you sent.")).to_be_visible()
        page.get_by_role("link", name="Take me to it").click()
        expect(page.get_by_text("Documents sent")).to_be_visible()
    else:
        # No renderer is still one explanation and nothing half-done; it arrives on the
        # page that was watching rather than on the form, in the same words.
        expect(page.get_by_text("That did not work.")).to_be_visible()

    # The export archive holds everything, readable without Postulo. Built by a worker,
    # because reading every record and every file in an account is not a request's job, and
    # then downloaded from an address of its own that checks whose account it holds (#247).
    page.goto(f"{base}/export/")
    page.get_by_role("button", name="Build the archive").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Packing up your data")
    with page.expect_download() as download_info:
        page.get_by_role("link", name="Take me to it").click()
    download = download_info.value
    assert re.search(r"\.zip$", download.suggested_filename)
    with zipfile.ZipFile(download.path()) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("postulo.json"))
    assert manifest["postulo"]["format"] >= 1
    assert manifest["account"]["email"] == EMAIL
    dumped = json.dumps(manifest)
    assert "Senior Django Developer" in dumped
    assert "Aperture Science" in dumped
    if pdf_backend_available():
        assert any(name.startswith("media/") and name.endswith(".pdf") for name in names)

    # Out through the account menu: it opens on the name, and Sign out is inside it. Named
    # rather than selected by shape -- the header has a second disclosure now, the
    # navigation's, which is the one a narrow screen gets (#113).
    page.get_by_label("Account menu", exact=False).click()
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("banner").get_by_role("link", name="Sign in")).to_be_visible()
    expect(page.locator("details[data-menu]")).to_have_count(0)
