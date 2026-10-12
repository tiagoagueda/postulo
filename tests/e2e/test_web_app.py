"""Postulo as an installed app, as far as a browser test can see it (#895).

Nothing here installs anything: a test browser has no home screen and no install prompt. What
a browser does answer is whether the manifest it reads is one it would install from, whether
the worker registers for somebody signed in, and whether a page load with no network is
answered with Postulo's offline page instead of the browser's own error.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

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
    for icon in body["icons"]:
        response = page.request.get(f"{live_server.url}{icon['src']}")
        assert response.ok and response.headers["content-type"] == "image/png", icon["src"]


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
