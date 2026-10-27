"""Postulo as an installed app, as far as a browser test can see it (#895).

Nothing here installs anything: a test browser has no home screen and no install prompt. What
a browser does answer is whether the manifest it reads is one it would install from, whether
the worker registers for somebody signed in, and whether a page load with no network is
answered with Postulo's offline page instead of the browser's own error.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import BrowserType, Page, expect

from .signing_in import sign_in

pytestmark = pytest.mark.e2e

#: Resolves once the worker controls the page and has the offline page kept, so going
#: offline after it tests the worker rather than a race with its installation.
KEPT = """async () => {
  const registration = await navigator.serviceWorker.ready;
  for (let i = 0; i < 100; i++) {
    const kept = await caches.match('/offline/');
    if (kept && navigator.serviceWorker.controller) return registration.scope;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}"""


@pytest.fixture
def browser_context_args(browser_context_args):
    """The worker, which the rest of the suite blocks (`conftest.py`)."""
    return {**browser_context_args, "service_workers": "allow"}


#: Every registration gone and nothing it started still running.
TAKEN_DOWN = """async () => {
  const registrations = await navigator.serviceWorker.getRegistrations();
  await Promise.all(registrations.map((registration) => registration.unregister()));
  return registrations.length;
}"""


@pytest.fixture(autouse=True)
def _no_worker_outlives_the_test(page: Page, live_server):
    """A worker left running can still be fetching when the live server is gone (#895)."""
    yield
    try:
        page.goto(f"{live_server.url}/healthz")
        page.evaluate(TAKEN_DOWN)
    finally:
        page.context.close()


def test_the_manifest_is_one_a_browser_installs_from(page: Page, live_server, applicant):
    sign_in(page, live_server.url)

    manifest = page.evaluate(
        """async () => {
          const link = document.querySelector('link[rel=manifest]');
          const response = await fetch(link.href, {credentials: 'include'});
          return {type: response.headers.get('content-type'), body: await response.json()};
        }"""
    )

    assert manifest["type"].startswith("application/manifest+json")
    body = manifest["body"]
    assert body["display"] == "standalone" and body["scope"] == "/" and body["id"] == "/"
    assert any(icon.get("purpose") == "maskable" for icon in body["icons"])
    # From the page rather than `page.request`, whose connection to the live server is kept
    # open after the test and answered by a thread the session has already shut down.
    icons = page.evaluate(
        """(sources) => Promise.all(sources.map(async (source) => {
          const response = await fetch(source);
          return [source, response.ok, response.headers.get('content-type')];
        }))""",
        [icon["src"] for icon in body["icons"]],
    )
    for source, ok, kind in icons:
        assert ok and kind == "image/png", source


def test_with_no_network_the_installed_app_shows_its_offline_page(
    page: Page, live_server, applicant
):
    sign_in(page, live_server.url)
    assert page.evaluate(KEPT) == f"{live_server.url}/", "the worker controls the whole site"

    page.context.set_offline(True)
    try:
        page.goto(f"{live_server.url}/applications/board/")
        expect(page.get_by_role("heading", level=1)).to_have_text("Postulo cannot reach its server")
        expect(page.get_by_role("link", name="Try again")).to_be_visible()
    finally:
        page.context.set_offline(False)

    page.get_by_role("link", name="Try again").click()
    expect(page.get_by_role("heading", level=1)).not_to_contain_text("cannot reach")


def test_signing_out_unregisters_the_worker(page: Page, live_server, applicant):
    """#753's rule, with the worker now registered for everybody signed in."""
    sign_in(page, live_server.url)
    assert page.evaluate(KEPT)

    page.get_by_label("Account menu", exact=False).click()
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("banner").get_by_role("link", name="Sign in")).to_be_visible()

    assert page.evaluate("navigator.serviceWorker.getRegistration('/').then((r) => !!r)") is False


# ------------------------------------------------------- installing from the address bar
#
# Asked of Chromium itself, through DevTools' `Page.getInstallabilityErrors`: the check that
# decides whether the address bar shows its install icon and whether a page is sent
# `beforeinstallprompt` (#899). Two things make the answer worth having. The suite's own
# browser is the headless shell, which has no installer and answers every page with an empty
# list, so these launch the full Chromium that `playwright install chromium` puts beside it
# (`channel="chromium"`, in its new headless mode). And a context of the ordinary kind is off
# the record, where nothing installs, so the profile is a real one, in a temporary folder.


@pytest.fixture
def full_chromium(browser_type: BrowserType, tmp_path):
    """A page in the full Chromium, with a profile of its own and workers allowed."""
    context = browser_type.launch_persistent_context(
        str(tmp_path / "profile"), channel="chromium", service_workers="allow"
    )
    try:
        yield context.pages[0] if context.pages else context.new_page()
    finally:
        context.close()


def installability_errors(page: Page) -> list[str]:
    """Why Chromium would not install this page, by DevTools' ids; empty when it would."""
    session = page.context.new_cdp_session(page)
    try:
        answer = session.send("Page.getInstallabilityErrors")
    finally:
        session.detach()
    return [
        error["errorId"]
        + "".join(f" {item['name']}={item['value']}" for item in error["errorArguments"])
        for error in answer["installabilityErrors"]
    ]


def test_chromium_would_install_postulo_for_somebody_signed_in(
    full_chromium, live_server, applicant
):
    page = full_chromium
    sign_in(page, live_server.url)
    assert page.evaluate(KEPT), "the worker, with its fetch handler, is in place"

    assert installability_errors(page) == []


def test_chromium_would_install_postulo_from_the_sign_in_page(full_chromium, live_server):
    """Signed out: no session on the manifest's request, and no worker (#753), yet the same
    app -- the install icon is not held back until somebody has signed in (#899)."""
    page = full_chromium
    page.goto(f"{live_server.url}/accounts/login/")

    assert installability_errors(page) == []


def test_the_check_is_not_empty_handed(full_chromium, live_server):
    """A page with no manifest is refused, so an empty list above is Chromium's verdict."""
    page = full_chromium
    page.goto(f"{live_server.url}/offline/")

    assert "no-manifest" in installability_errors(page)


def test_chromium_reads_the_launch_handler(full_chromium, live_server):
    """What Chromium's own parser makes of the manifest, not just what the server sent."""
    page = full_chromium
    page.goto(f"{live_server.url}/accounts/login/")
    session = page.context.new_cdp_session(page)
    try:
        answer = session.send("Page.getAppManifest")
    finally:
        session.detach()

    assert answer["errors"] == []
    parsed = answer["manifest"]
    assert parsed["launchHandler"] == {"clientMode": "kFocusExisting"}
    assert parsed["scope"] == f"{live_server.url}/"
    assert parsed["id"] == f"{live_server.url}/"
