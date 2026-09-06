"""Every page a person can reach is either checked for accessibility or excused in writing.

The browser suite runs axe-core over a list of addresses. That list was written by hand,
which means a page added tomorrow is not on it and nothing says so — and that is exactly
what happened: the whole suggestions queue, every letter page, the contact and industry
forms, both error pages and the administration area had never been looked at.

So the list stops being the authority. This walks the URL resolver instead and insists that
every pattern is either visited by the browser suite or named in `EXCUSED` with a reason.
Adding a page without deciding about it is now a failing test rather than an omission
nobody notices.

It lives outside the browser suite deliberately: it needs no browser, runs in
milliseconds, and should fail on a laptop long before CI gets to the slow part.
"""

from __future__ import annotations

import re

import pytest
from django.urls import get_resolver

from tests.e2e.test_accessibility import VISITED_URL_NAMES

#: Patterns nothing will ever visit with a browser, and why. Keep this honest: a reason
#: like "hard to set up" belongs in the list of things to fix, not here.
EXCUSED: dict[str, str] = {
    "core:healthz": "a JSON status document for monitoring to poll",
    "core:manifest": "the JSON manifest a browser reads when installing the app",
    "core:logs_endpoint": "one JSON object per line, for a log collector to scrape",
    "core:export_download": "a zip archive arriving as a download",
    "core:import_csv_template": "a spreadsheet arriving as a download",
    "documents:cv_export": "a rendered PDF arriving as a download",
    "documents:upload_download": "an uploaded file arriving as a download",
    "documents:rendered_download": "a rendered document arriving as a download",
    "applications:interview_ics": "a calendar file for one interview",
    "applications:interview_calendar": "a calendar feed of every interview",
    "accounts:avatar": "an image, served through a permission check",
    "jobs:company_logo": "a company's logo image, served from this instance",
    "jobs:company_logo_action": "a POST that sets or clears a company's logo",
    "applications:event_create": "a POST from the application page, which is visited",
    "api:token_create": "a POST that mints a token and shows it once",
    "core:import_csv_forget": "a POST that discards the stashed spreadsheet",
    "core:table_settings": "a POST that records which columns a table shows",
    "accounts:avatar_refresh": "a POST that fetches the picture again",
    "accounts:theme": "a POST from the theme switch in the header",
    "accounts:invite_revoke": "a POST that withdraws an invitation",
    "applications:status": "a POST from the board and from the application page",
    "applications:quiet_action": "a POST that dismisses the gone-quiet prompt",
    "applications:reminder_complete": "a POST that marks a reminder done",
    "applications:suggestion_action": "a POST from the suggestions page, which is visited",
    "documents:cv_item_move": "a POST that reorders one entry on a CV",
    "documents:rendered_archive": "a POST that files a sent document away",
    "documents:upload_archive": "a POST that files an uploaded document away",
    "jobs:capture_discard": "a POST that throws away a captured posting",
    "listings:shortlist": "a POST from the listings table",
    "listings:discard": "a POST from the listings table",
    "listings:restore": "a POST that brings a discarded listing back",
    "resume:item_move": "a POST that reorders one entry in the career record",
    "resume:link_check": "a POST that asks whether one link still answers",
    "resume:link_check_all": "a POST that asks the same of every link",
    "server:plugin_action": "a POST that installs, removes or disables a plugin",
    "server:email_test": "a POST that sends one test message",
    "server:person_admin": "a POST that grants or removes administrator rights",
    "server:person_active": "a POST that suspends or restores an account",
    "connections:test": "a POST that tries a connection and reports back",
    "connections:sync_now": "a POST that runs one synchronisation immediately",
    "connections:backfill": "a POST that queues everything a store has not received",
    "api:token_revoke": "a POST that withdraws an API token",
    "server:index": "redirects to the overview, which is visited",
    "settings:index": "redirects to the appearance page, which is visited",
    "openid_connect_login": "hands the browser to an identity provider",
    "openid_connect_callback": "returns from an identity provider",
    "accounts:invite_accept": "needs a token from an invitation that was actually sent",
}


def url_names() -> set[str]:
    """Every named pattern in the project, as `namespace:name`."""

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                namespace = pattern.namespace or ""
                yield from walk(pattern, f"{prefix}{namespace}:" if namespace else prefix)
            elif pattern.name:
                yield f"{prefix}{pattern.name}"

    return set(walk(get_resolver()))


def theirs(names: set[str], prefix: str) -> set[str]:
    return {name for name in names if name.startswith(prefix)}


def test_no_page_is_reachable_without_somebody_having_decided_about_it():
    """The property the hand-written list could not have."""
    names = url_names()

    # The administration area is Django's, checked as a whole rather than pattern by
    # pattern: there are hundreds of them and they are all the same three templates.
    names -= theirs(names, "admin:")
    # allauth's own pages are visited through the ones Postulo links to; the rest are
    # steps inside flows that need a live provider or a posted credential.
    names -= {name for name in names if name.startswith(("socialaccount_", "mfa_", "account_"))}
    # The JSON API answers machines. It has its own suite, and a schema rather than a page.
    names -= theirs(names, "postulo-api:")

    undecided = sorted(names - set(VISITED_URL_NAMES) - set(EXCUSED))
    assert not undecided, (
        "These are reachable and nobody has said whether they are checked for "
        "accessibility. Add each to the browser suite's list, or to EXCUSED with a "
        f"reason: {undecided}"
    )


def test_nothing_is_excused_that_no_longer_exists():
    """An excuse for a page that has gone is a lie that outlives it."""
    names = url_names()
    stale = sorted(set(EXCUSED) - names)
    assert not stale, f"EXCUSED names patterns that are gone: {stale}"


def test_nothing_is_both_visited_and_excused():
    both = sorted(set(EXCUSED) & set(VISITED_URL_NAMES))
    assert not both, f"listed twice, which means one of them is wrong: {both}"


def test_every_visited_name_exists():
    names = url_names()
    missing = sorted(
        set(VISITED_URL_NAMES) - names - {n for n in VISITED_URL_NAMES if ":" not in n}
    )
    assert not missing, f"the browser suite visits patterns that are gone: {missing}"


@pytest.mark.parametrize("name", sorted(EXCUSED))
def test_every_excuse_says_something(name):
    reason = EXCUSED[name]
    assert len(reason) > 12, f"{name}: {reason!r} is not a reason"
    assert not re.search(r"\b(todo|later|hard|tricky)\b", reason, re.I), (
        f"{name}: that is a thing to fix, not an excuse"
    )
