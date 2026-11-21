"""An iPhone or an iPad in a Safari tab is told to add Postulo to the Home Screen (#1259).

There, a page in a tab has no `Notification` at all: Apple gives notifications only to a web
app added to the Home Screen (iOS and iPadOS 16.4 and later). The card used to say that
browsers only allow notifications on HTTPS, which sent the person looking for the wrong
thing. Only Safari on those devices defines `navigator.standalone`, false in a tab, so that is
what the script reads.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

#: Safari in a tab on an iPhone, as far as the page can tell.
IPHONE_TAB = """
(() => {
  delete window.Notification;
  Object.defineProperty(Navigator.prototype, 'standalone', {
    get: () => false,
    configurable: true,
  });
})();
"""

#: Another browser with no notifications here, which is told what it was told before.
NO_NOTIFICATIONS = "delete window.Notification;"


def status_of(page: Page, live_server):
    sign_in(page, live_server.url, reverse("connections:create", args=["notifier", "browser"]))
    card = page.locator("[data-web-push-key]")
    expect(card).to_have_attribute("data-web-push-ready", "")
    return card, card.locator("[data-web-push-status]")


def test_an_iphone_in_a_tab_is_told_to_add_postulo_to_the_home_screen(
    page: Page, live_server, applicant
):
    page.context.add_init_script(IPHONE_TAB)
    card, status = status_of(page, live_server)

    expect(status).to_have_text(card.get_attribute("data-web-push-install-first"))
    expect(status).to_have_attribute("role", "status")
    expect(
        card.get_by_role("button", name=card.get_attribute("data-web-push-allow"))
    ).to_have_count(0)


def test_another_browser_without_notifications_is_told_what_it_was_before(
    page: Page, live_server, applicant
):
    page.context.add_init_script(NO_NOTIFICATIONS)
    card, status = status_of(page, live_server)

    expect(status).to_have_text(card.get_attribute("data-web-push-unsupported"))
