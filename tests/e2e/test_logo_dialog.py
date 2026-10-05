"""*Find logo* and *Refresh logo* in a dialog on the company's page (#674).

The button is a `popovertarget` one, so with a script the dialog is modal and without one it
is a popover whose form posts as it always did. The press is a second one, inside the dialog;
its answer -- the errand's state -- replaces the button where it was, polled until it
finishes, and when the work is done the company's header is drawn again with the new logo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.files.base import ContentFile
from playwright.sync_api import Browser, Page, expect

from postulo.core import errands
from postulo.core.models import Errand
from postulo.jobs import logos
from postulo.jobs.models import Company
from tests.e2e import test_accessibility as accessibility
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


@pytest.fixture
def axe() -> str:
    if not accessibility.AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    return accessibility.AXE.read_text(encoding="utf-8")


@pytest.fixture
def company(applicant):
    return Company.objects.create(
        owner=applicant, name="Black Mesa", website="https://blackmesa.test/"
    )


def a_logo() -> ContentFile:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (120, 40), (20, 90, 200, 255)).save(out, format="PNG")
    return ContentFile(out.getvalue())


@pytest.fixture
def finds_a_logo(monkeypatch):
    def find(company):
        logos.store(company, a_logo(), source="website", url="https://blackmesa.test/logo.png")
        return "https://blackmesa.test/logo.png"

    monkeypatch.setattr(logos, "find_on_website", find)


@pytest.fixture
def finds_nothing(monkeypatch):
    def find(company):
        raise logos.UnusableLogo(
            "Nothing on blackmesa.test could be used as a logo: The address answered 403."
        )

    monkeypatch.setattr(logos, "find_on_website", find)


def test_with_a_script_it_is_modal_and_the_header_shows_the_logo_when_done(
    page: Page, live_server, applicant, company, finds_a_logo
):
    sign_in(page, live_server.url, company.get_absolute_url())
    opener = page.locator("#logo-action")
    dialog = page.get_by_role("dialog", name="Find logo")
    drawn = page.locator("#logo-dialog > *")
    expect(page.locator("#company-head img")).to_have_count(0)

    opener.focus()
    page.keyboard.press("Enter")
    expect(drawn).to_be_visible()
    assert dialog.evaluate("(dialog) => dialog.matches(':modal')")
    expect(dialog).to_have_accessible_description(
        "Postulo will read https://blackmesa.test/ and keep the first image that works as the logo."
    )

    dialog.get_by_role("button", name="Find logo").click()
    result = page.locator("#errand-state")
    expect(result).to_contain_text("Found a logo at https://blackmesa.test/logo.png.")
    expect(result).to_be_focused()

    dialog.get_by_role("button", name="Done").click()
    expect(drawn).to_be_hidden()
    expect(page.locator("#company-head img")).to_have_count(1)
    expect(page.locator("#logo-action")).to_have_text("Refresh logo")
    expect(page.locator("#logo-action")).to_be_focused()


def test_progress_comes_first_and_then_the_result(
    page: Page, live_server, applicant, company, finds_a_logo, monkeypatch
):
    from postulo.core import tasks

    monkeypatch.setattr(errands, "worker_expected", lambda: True)
    monkeypatch.setattr(
        tasks, "perform_errand", SimpleNamespace(enqueue=lambda *args, **kwargs: None)
    )

    sign_in(page, live_server.url, company.get_absolute_url())
    page.locator("#logo-action").click()
    dialog = page.get_by_role("dialog", name="Find logo")
    dialog.get_by_role("button", name="Find logo").click()
    result = page.locator("#errand-state")
    expect(result).to_contain_text("You can close this. The work carries on without you.")
    expect(result).to_be_focused()
    assert "Looking for a logo" in result.inner_text()

    errands.perform(Errand.objects.get(kind="logo").pk)
    expect(result).to_contain_text("Found a logo at", timeout=10_000)
    expect(page.locator("#errand-state")).to_be_focused()


def test_a_failure_shows_its_reason_and_the_other_ways(
    page: Page, live_server, applicant, company, finds_nothing
):
    sign_in(page, live_server.url, company.get_absolute_url())
    page.locator("#logo-action").click()
    dialog = page.get_by_role("dialog", name="Find logo")
    dialog.get_by_role("button", name="Find logo").click()
    expect(dialog).to_contain_text("That did not work.")
    expect(dialog).to_contain_text("The address answered 403.")
    expect(
        dialog.get_by_role("link", name="Paste an address or upload a file instead")
    ).to_be_visible()

    dialog.get_by_role("button", name="Close").click()
    expect(page.locator("#logo-dialog > *")).to_be_hidden()
    expect(page.locator("#logo-action")).to_be_focused()
    # Drawn again, so the question is back for a second try.
    expect(page.locator("#logo-dialog")).not_to_contain_text("That did not work")
    page.locator("#logo-action").click()
    expect(
        page.get_by_role("dialog", name="Find logo").get_by_role("button", name="Find logo")
    ).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_dialog_has_no_violations_asking_or_answering(
    page: Page,
    live_server,
    applicant,
    company,
    finds_nothing,
    axe,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, company.get_absolute_url())
    page.locator("#logo-action").click()
    dialog = page.get_by_role("dialog", name="Find logo")
    expect(page.locator("#logo-dialog > *")).to_be_visible()
    asking = accessibility.violations_on(page, axe)
    assert not asking, accessibility.describe(f"asking ({scheme})", asking)

    dialog.get_by_role("button", name="Find logo").click()
    expect(dialog).to_contain_text("That did not work.")
    answering = accessibility.violations_on(page, axe)
    assert not answering, accessibility.describe(f"answering ({scheme})", answering)


def test_without_a_script_it_is_a_popover_and_the_form_posts_as_before(
    browser: Browser, live_server, applicant, company, finds_a_logo
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, company.get_absolute_url())
        dialog = page.locator("#logo-dialog")
        page.locator("#logo-action").click()
        assert dialog.evaluate("(dialog) => dialog.matches(':popover-open')")
        assert not dialog.evaluate("(dialog) => dialog.matches(':modal')")
        dialog.get_by_role("button", name="Find logo").click()
        expect(page.locator("#errand-state")).to_contain_text("Found a logo at")
        assert page.url.endswith(f"/working/{Errand.objects.get(kind='logo').pk}/")
    finally:
        context.close()
