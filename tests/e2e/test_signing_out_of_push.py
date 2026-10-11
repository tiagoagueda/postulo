"""Signing out takes the browser off push, and does not wait for a browser that cannot (#753)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

#: A service worker registration that records what was done to it, in the tab's session
#: storage because the page is gone by the time the sign-out has been answered.
STUB = """
(() => {
  const note = (what) => {
    const seen = JSON.parse(sessionStorage.getItem('calls') || '[]');
    seen.push(what);
    sessionStorage.setItem('calls', JSON.stringify(seen));
  };
  const subscription = {
    unsubscribe: () => { note('unsubscribe'); return Promise.resolve(true); },
  };
  const registration = {
    pushManager: { getSubscription: () => Promise.resolve(subscription) },
    unregister: () => { note('unregister'); return Promise.resolve(true); },
  };
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { getRegistration: () => Promise.resolve(registration) },
    configurable: true,
  });
})();
"""


def sign_out(page: Page) -> None:
    page.locator("[data-account-menu] button[popovertarget]").click()
    page.locator('form[action$="/logout/"] button[type="submit"]').click()


def test_the_subscription_and_the_worker_are_dropped_and_then_the_sign_out_goes_on(
    page: Page, live_server, applicant
):
    page.add_init_script(STUB)
    sign_in(page, live_server.url, "/")

    sign_out(page)

    page.wait_for_function("JSON.parse(sessionStorage.getItem('calls') || '[]').length === 2")
    page.wait_for_load_state("load")
    calls = page.evaluate("JSON.parse(sessionStorage.getItem('calls') || '[]')")
    assert calls == ["unsubscribe", "unregister"]
    page.goto(f"{live_server.url}/settings/")
    expect(page).to_have_url(re.compile(r"/accounts/login"))


def test_a_browser_with_no_worker_signs_out_at_once(page: Page, live_server, applicant):
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', {value: undefined, configurable: true});"
    )
    sign_in(page, live_server.url, "/")

    sign_out(page)

    page.goto(f"{live_server.url}/settings/")
    expect(page).to_have_url(re.compile(r"/accounts/login"))
