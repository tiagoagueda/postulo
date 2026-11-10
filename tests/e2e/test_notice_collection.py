"""One open tab asks for the waiting browser notices, on behalf of all of them (#1155)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

#: The once-a-minute timer is kept instead of started, so that a test can see which tab set
#: one and ring it without waiting a minute; and permission reads as granted, which a
#: headless browser does not give.
STUB = """
(() => {
  window.everyMinute = [];
  const real = window.setInterval;
  window.setInterval = function (run, delay) {
    if (delay === 60000) {
      window.everyMinute.push(run);
      return 0;
    }
    return real.apply(window, arguments);
  };
  Object.defineProperty(Notification, 'permission', { get: () => 'granted', configurable: true });
})();
"""

NO_LOCKS = """
Object.defineProperty(Navigator.prototype, 'locks', { value: undefined, configurable: true });
"""

TIMERS = "window.everyMinute.length"
QUEUED = "navigator.locks.query().then((all) => all.pending.length)"


def a_browser_notifier(user) -> None:
    from postulo.plugins.models import Connection

    Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="browser",
        label="Laptop",
        enabled=True,
        config={"delivery": "tab"},
    )


def becomes(page: Page, expression: str, wanted: int) -> None:
    """Wait until ``expression`` is ``wanted``: the lock is answered a moment after the load."""
    seen = None
    for _attempt in range(100):
        seen = page.evaluate(expression)
        if seen == wanted:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"{expression} stayed {seen!r}, not {wanted!r}")


def ring(page: Page) -> None:
    """A minute passes in this tab, and it asks for what is waiting."""
    with page.expect_request(
        lambda request: (
            request.method == "POST" and request.url.endswith(reverse("notifications:waiting"))
        )
    ):
        page.evaluate("window.everyMinute.forEach((run) => run())")


def test_one_tab_asks_each_minute_and_the_next_takes_over_when_it_closes(
    page: Page, live_server, applicant
):
    """Every open tab asked once a minute, hidden ones included, so five tabs were five
    requests, and five write transactions, for the same notices. One tab asks now; the
    others wait their turn, and one of them takes over when that tab is closed (#1155)."""
    a_browser_notifier(applicant)
    page.context.add_init_script(STUB)
    sign_in(page, live_server.url, "/")
    expect(page.locator("body")).to_have_attribute("data-browser-notices-ready", "")
    becomes(page, TIMERS, 1)
    ring(page)

    other = page.context.new_page()
    other.goto(f"{live_server.url}/")
    expect(other.locator("body")).to_have_attribute("data-browser-notices-ready", "")
    assert other.evaluate(TIMERS) == 0, "the second tab set a timer of its own"
    becomes(other, QUEUED, 1)
    assert other.evaluate(TIMERS) == 0, "it waits for the first tab's turn to end"

    # A tab brought to the front still asks at once, whoever holds the turn.
    with other.expect_request(lambda request: request.method == "POST"):
        other.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    page.close()
    becomes(other, TIMERS, 1)
    ring(other)
    other.close()


def test_a_browser_without_the_lock_asks_from_every_tab_as_before(
    page: Page, live_server, applicant
):
    """Where there is no lock to hold, no tab would ever have asked (#1155)."""
    a_browser_notifier(applicant)
    page.context.add_init_script(STUB + NO_LOCKS)
    sign_in(page, live_server.url, "/")
    expect(page.locator("body")).to_have_attribute("data-browser-notices-ready", "")
    assert page.evaluate(TIMERS) == 1

    other = page.context.new_page()
    other.goto(f"{live_server.url}/")
    expect(other.locator("body")).to_have_attribute("data-browser-notices-ready", "")
    assert other.evaluate(TIMERS) == 1
    ring(other)
    other.close()
