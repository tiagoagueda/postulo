"""What the page does when a request does not arrive, and what Back gives back (#226).

Four failures that had nothing in common except that they were all silent. A filter whose
request answered 500 left the previous rows on the screen and said nothing. A session that
had expired swapped the sign-in page into a table, on a page that still looked signed in.
The back button restored controls whose listeners had been left behind, so the handles and
the chips were there and did nothing. And a form whose answer is a file never navigates, so
the guard that stops a double click never let go of the button again.

Most of this is markup, configuration and the shape of the script, and all of that is
testable here. What is not is the browser actually pressing the button twice, which is in
`tests/e2e/test_submit_guard.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponseRedirect
from django.test import RequestFactory
from django.urls import reverse

from postulo.core.middleware import HtmxLoginRedirectMiddleware

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "src" / "postulo" / "static" / "js" / "app.js").read_text(encoding="utf-8")
SOURCE_CSS = (ROOT / "assets" / "css" / "app.css").read_text(encoding="utf-8")
COMPILED_CSS = (ROOT / "src" / "postulo" / "static" / "css" / "app.css").read_text(encoding="utf-8")

HTMX = {"HX-Request": "true"}


def htmx_config(html: str) -> dict:
    """What `base.html` configures htmx with, read back as htmx would read it."""
    found = re.search(r'<meta name="htmx-config"\s+content=\'([^\']+)\'', html)
    assert found, "base.html no longer carries an htmx-config meta element"
    return json.loads(found.group(1))


# ------------------------------------------------- 1. a request that fails says something


def test_the_page_carries_one_region_for_a_failed_request(client, user):
    """`role="alert"` so it is announced, and empty so that filling it *is* the change.

    A live region created at the moment of the failure is a race with the screen reader,
    and the screen reader loses it often enough to be worth nothing.
    """
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    # The region is the paragraph inside the box, and the dismiss button beside it is
    # outside the region, so the announcement is only ever the words (#275).
    region = re.search(r'<p role="alert" data-htmx-alert([^>]*)></p>', html)
    assert region, "no empty role=alert region on the page"
    assert html.count("data-htmx-alert") == 1, "one region for the whole application"


def test_the_words_are_on_the_page_and_not_in_the_script(client, user):
    """The script holds no English anybody reads, so both sentences are translated by the
    same machinery as every other string, and a failure in Portuguese is in Portuguese.
    """
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    failed = re.search(r'data-alert-failed="([^"]+)"', html)
    offline = re.search(r'data-alert-offline="([^"]+)"', html)
    assert failed and offline
    assert "{status}" in failed.group(1), "and it says which status, because 503 is not 500"

    assert 'failureWords("alertFailed")' in APP_JS
    assert 'failureWords("alertOffline")' in APP_JS
    for sentence in (failed.group(1), offline.group(1)):
        assert sentence.split("{")[0].strip() not in APP_JS


def test_the_script_listens_for_both_ways_a_request_fails():
    """A 500 and a train tunnel are different events and neither had a listener."""
    assert 'addEventListener("htmx:responseError"' in APP_JS
    assert 'document.addEventListener("htmx:sendError", saySilence)' in APP_JS
    assert 'document.addEventListener("htmx:timeout", saySilence)' in APP_JS


def test_a_request_that_works_takes_the_message_away_again():
    """Not a timer: a message that tidies itself up after five seconds is one somebody
    looking at their keyboard never saw at all."""
    assert 'if ((event.detail || {}).successful) {\n      sayFailure("");' in APP_JS


def test_the_part_being_replaced_says_it_is_busy():
    """htmx marks the element that *asked*; what somebody is waiting for is the table."""
    assert 'target.setAttribute("aria-busy", "true")' in APP_JS
    assert 'target.removeAttribute("aria-busy")' in APP_JS
    # Cleared on the events that a failed request actually raises, not only on afterRequest,
    # which is never raised when the request did not reach the server.
    clearing = APP_JS.split("Four events rather than one")[1].split("});")[0]
    for event in ("htmx:afterRequest", "htmx:responseError", "htmx:sendError", "htmx:timeout"):
        assert event in clearing


def test_the_stylesheet_shows_both_states():
    assert '[aria-busy="true"]' in SOURCE_CSS and '[aria-busy="true"]' in COMPILED_CSS
    assert ".page-alert" in SOURCE_CSS and ".page-alert" in COMPILED_CSS
    assert '.page-alert:has(> [role="alert"]:empty)' in SOURCE_CSS, "empty keeps it out of the way"


# ------------------------------------------------- 2. a session that expired says so


def test_an_expired_session_sends_the_browser_to_the_sign_in_page(client):
    """Signed out, asking for a table the way a filter asks for it.

    An `XMLHttpRequest` follows a redirect without telling the script, so htmx saw the 200
    the sign-in page answered with and swapped the whole sign-in page — masthead, footer and
    a password field — into `#companies-table`.
    """
    answer = client.get(reverse("jobs:company_list"), headers=HTMX)

    assert answer.status_code == 204, "nothing to swap"
    assert answer.headers["HX-Redirect"].startswith(reverse("account_login"))


def test_signing_in_again_comes_back_to_the_page_that_was_open(client):
    answer = client.get(reverse("jobs:company_list"), headers=HTMX)

    assert f"next={reverse('jobs:company_list')}" in answer.headers["HX-Redirect"]


def test_a_browser_that_is_not_htmx_still_gets_an_ordinary_redirect(client):
    """The change is for one kind of request, and every other way in is untouched."""
    answer = client.get(reverse("jobs:company_list"))

    assert answer.status_code == 302
    assert "HX-Redirect" not in answer.headers


def test_every_other_redirect_is_left_exactly_as_it_was():
    """A view that redirects an htmx request somewhere else meant it, and the swap that
    follows is what it was written to expect. Turning all of them into page loads would
    undo the swapping this application is built on.
    """
    request = RequestFactory().get("/jobs/companies/")
    request.htmx = True
    elsewhere = HtmxLoginRedirectMiddleware(lambda _: HttpResponseRedirect("/jobs/companies/1/"))

    answer = elsewhere(request)

    assert answer.status_code == 302
    assert "HX-Redirect" not in answer.headers


def test_a_sign_in_page_on_another_host_is_not_one_of_ours():
    """`HX-Redirect` is spent on `location.href`, so an address naming a host would be this
    middleware handing a browser somewhere else because a path happened to match."""
    request = RequestFactory().get("/jobs/companies/")
    request.htmx = True
    away = HtmxLoginRedirectMiddleware(
        lambda _: HttpResponseRedirect(f"https://example.invalid{settings.LOGIN_URL}")
    )

    answer = away(request)

    assert answer.status_code == 302
    assert "HX-Redirect" not in answer.headers


def test_what_the_redirect_was_carrying_is_carried_on(client, user):
    """A session cycled or a message stored on the way out belongs to this reply as much as
    the `Location` did, so the reply is rebuilt rather than thrown away."""
    request = RequestFactory().get("/jobs/companies/")
    request.htmx = True

    def sign_in_first(_):
        response = HttpResponseRedirect(f"{settings.LOGIN_URL}?next=/jobs/companies/")
        response.set_cookie("sessionid", "cycled")
        response.headers["X-Something"] = "kept"
        return response

    answer = HtmxLoginRedirectMiddleware(sign_in_first)(request)

    assert answer.status_code == 204
    assert answer.cookies["sessionid"].value == "cycled"
    assert answer.headers["X-Something"] == "kept"
    assert "Location" not in answer.headers


def test_the_middleware_runs_where_it_can_see_both_things():
    """After django-htmx, which is what sets `request.htmx`, and outside everything that
    answers a request, so the redirect is seen whichever of them wrote it."""
    order = settings.MIDDLEWARE

    assert order.index("postulo.core.middleware.HtmxLoginRedirectMiddleware") == (
        order.index("django_htmx.middleware.HtmxMiddleware") + 1
    )


# ------------------------------------------------- 3. the back button


def test_htmx_keeps_no_copy_of_a_page(client, user):
    """A table swap pushes a URL on every filter, sort and page turn, and htmx kept the body
    of each one in `sessionStorage`. Restoring that copy put the column handles, the *Select
    all* button and the label chips back without their listeners, and the functions that
    would have attached them then skipped the markers already in the markup.

    It also left applications, companies and people in `sessionStorage`, where signing out
    in the same tab does not touch them.
    """
    client.force_login(user)
    config = htmx_config(client.get(reverse("core:home")).content.decode())

    assert config["historyCacheSize"] == 0
    assert config["refreshOnHistoryMiss"] is True


def test_the_views_still_answer_a_restore_with_a_whole_page(client, user):
    """The configuration above means htmx reloads rather than asking; this is the belt to
    that braces, and the reason it is safe to leave those branches where they are."""
    client.force_login(user)
    restore = client.get(
        reverse("jobs:company_list"),
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )

    assert b"<html" in restore.content, "a page, not a table"


def test_one_place_says_when_a_page_is_ready():
    """Six functions wrote their own three registrations, and the two written last forgot
    the swap: a widget row that came back in a swap could not be dragged."""
    assert APP_JS.count('"DOMContentLoaded"') == 1, "only inside onContentReady"
    assert APP_JS.count("onContentReady(") >= 7, "the helper and everything that uses it"
    assert "onContentReady(readyWidgetDragging)" in APP_JS
    assert "onContentReady(readyEveryPushCard)" in APP_JS


# ------------------------------------------------- 4. a button that hands over a file


@pytest.mark.parametrize(
    "template",
    [
        "core/export.html",
        "documents/cv_detail.html",
        "accounts/delete_account.html",
        "applications/report.html",
    ],
)
def test_no_form_that_now_answers_with_a_page_still_claims_to_be_a_download(template: str):
    """These four were the buttons whose answer was a file, which never leaves the page --
    so `pageshow` never came and the guard's mark stayed for the rest of the visit (#226).

    All four send their work off now and answer with the page that watches it, so the
    navigation the guard is waiting for does happen, and a mark that says otherwise would
    release the button a second later for no reason (#247). The rule this replaces is kept
    below, where it still bites: `data-download` and the release that goes with it are
    still in `app.js`, because a form whose answer *is* a file may exist again.
    """
    source = (ROOT / "src" / "postulo" / "templates" / template).read_text(encoding="utf-8")

    # The attribute, not the word: each of these explains in a comment why it no longer
    # carries one, and a comment saying so is the opposite of the thing being looked for.
    assert not re.search(r"\sdata-download[\s>]", source)


def test_the_guard_still_knows_how_to_let_go_of_a_download(client, user):
    """Nothing renders the mark today; the machinery for it stays, and is checked here so
    that the next form answering with a file finds it working."""
    client.force_login(user)

    assert "data-download" not in client.get(reverse("core:export")).content.decode()
    assert 'form.hasAttribute("data-download")' in APP_JS


def test_the_guard_lets_go_of_a_download_rather_than_exempting_it():
    """The accident it exists for is a double click, which happens inside a second. A second
    export a minute later is not an accident, and by then the button works again.
    """
    assert 'form.hasAttribute("data-download")' in APP_JS
    assert "DOWNLOAD_RELEASE" in APP_JS
    assert re.search(r"var DOWNLOAD_RELEASE = \d+;", APP_JS)
    # Released the same way the back button releases it, rather than a second copy of it.
    assert APP_JS.count("function releaseForm(form)") == 1
    assert APP_JS.count("releaseForm(form)") >= 2


# ------------------------------------------------- 5. what was left over


def test_the_log_count_is_not_a_live_region_any_more():
    """`server/logs.html` filtered with a plain form and a whole page load, so there was
    never a change for a live region to announce — and a region that announces nothing
    teaches whoever reads the template next that this is how it is done."""
    source = (ROOT / "src" / "postulo" / "templates" / "server" / "logs.html").read_text(
        encoding="utf-8"
    )

    assert "aria-live" not in source


def test_the_token_is_found_in_one_place():
    """`tokenFor(head)` took an argument it never looked at, and the dashboard's drop wrote
    the same query out again inline."""
    assert "function tokenFor" not in APP_JS
    assert APP_JS.count("input[name=csrfmiddlewaretoken]") == 1
    assert APP_JS.count("csrfToken()") >= 2
