"""Browser tests: a real Chromium driven against a live server.

These are excluded from the default run (``-m "not e2e"`` in ``pyproject.toml``) because
they need a browser installed. Run them with::

    uv run playwright install chromium
    uv run pytest -m e2e

CI runs them on every push in their own job, with traces kept on failure.
"""

import os
import sqlite3
import threading
import weakref

import pytest

# Playwright's synchronous API drives an event loop inside the test thread, and Django
# refuses ORM calls from a thread that has a running loop unless told otherwise. Here the
# test thread *is* that thread, on purpose, so the guard is switched off for this process.
# It protects production code from accidental blocking in async views; it has nothing to
# protect in a test that is blocking by design.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytest.importorskip("playwright", reason="the browser tests need the e2e dependency group")

EMAIL = "alex.morgan@example.org"
PASSWORD = "correct-horse-battery-staple"  # a test account's password, not a secret

# -------------------------------------------------------------------- live server cleanup
# Every database wrapper this process creates, beside a weak reference to the thread that
# created it. Held rather than looked up, because a wrapper a dead thread created is
# exactly the object nobody can reach any more (#294).
_WRAPPERS: dict[int, tuple[weakref.ref, object]] = {}


def _register_every_wrapper() -> None:
    from django.db.utils import ConnectionHandler

    original = ConnectionHandler.create_connection

    def create_connection(self, alias):
        wrapper = original(self, alias)
        _WRAPPERS[id(wrapper)] = (weakref.ref(threading.current_thread()), wrapper)
        return wrapper

    ConnectionHandler.create_connection = create_connection


_register_every_wrapper()


def _close_connections_left_behind() -> None:
    """Close the database connections of threads that are no longer running (#294).

    A connection still open in a wrapper whose thread has gone cannot be closed by that
    thread's own clean-up, and the collector finalising it is what raises the
    ResourceWarning the suite has been tripping over. Closing it here, from a thread
    that is running, is the same close, done on time.
    """
    for ident, (thread, wrapper) in list(_WRAPPERS.items()):
        if thread() is not None:
            continue
        connection = getattr(wrapper, "connection", None)
        if connection is None:
            _WRAPPERS.pop(ident, None)
            continue
        try:
            connection.close()
        except sqlite3.InterfaceError:
            pass
        wrapper.connection = None
        _WRAPPERS.pop(ident, None)


@pytest.fixture(autouse=True)
def _orphaned_live_server_connections():
    """No test leaves a database connection for the collector to find (#294).

    The live server answers each request on its own thread, and a database wrapper lives
    in storage a thread can only see from itself, so when such a thread goes away the
    connection it opened cannot be closed and waits for the collector, which finalises
    it with a ResourceWarning that pytest attributes to whichever test is running at the
    moment. That is how the suite has been failing on an innocent test about one run in
    two.

    The connection is born while a request thread hosts an event loop. The Chromium PDF
    backend drives Playwright's synchronous API from the request itself, and asgiref's
    thread-critical storage switches a looping thread from thread-local to context
    storage, so a `connections` read in that window starts a second, thread-private
    connection the thread's own clean-up never sees.

    After each test, the wrappers whose thread has gone are closed. The collector is
    left nothing to find, and the ResourceWarning stays free to announce a genuinely
    new leak.
    """
    yield
    _close_connections_left_behind()


def pytest_sessionfinish(session, exitstatus):
    """Sweep once more after the server stops, so a worker that dies while the last
    response is still in flight leaves no connection behind either (#294). Every test
    is over by then, so whatever is left in the registry can go."""
    _close_connections_left_behind()
    for ident, (_thread, wrapper) in list(_WRAPPERS.items()):
        connection = getattr(wrapper, "connection", None)
        if connection is None:
            _WRAPPERS.pop(ident, None)
            continue
        try:
            connection.close()
        except sqlite3.InterfaceError:
            pass
        wrapper.connection = None
        _WRAPPERS.pop(ident, None)


@pytest.fixture(autouse=True)
def _no_content_security_policy_violations(page):
    """Every browser test runs under the content security policy, and fails on a violation.

    The policy used to be production's alone, so the whole suite -- axe included -- ran
    without it, and an inline script or a `style=` attribute passed CI and broke in
    production (#232). Chromium reports each refusal on the console, which is where this
    reads them, with where it came from: a `style=` in the markup carries the page's
    address, a script the page loaded carries the script's, and a script the suite
    evaluated -- axe, the reflow walk, anything `page.evaluate` ran -- carries none. Only
    the first two are the application's, and only they fail the test: the instruments
    set inline styles of their own, and what they do is not what a visitor's browser
    would refuse.

    A `<script>` or `<style>` element with content is refused whoever adds it, so the
    suite injects axe through the DevTools protocol and its text-spacing override
    through a constructed stylesheet.
    """
    refused: list[str] = []

    def note(message):
        if "Content Security Policy" not in message.text:
            return
        where = message.location or {}
        if not where.get("url"):
            return  # evaluated by the suite, not served by the application
        refused.append(
            f"{message.text} ({where['url']}:{where.get('lineNumber', '?')} "
            f"on {message.page.url if message.page else '?'})"
        )

    page.on("console", note)
    yield
    assert not refused, "the content security policy refused something:\n" + "\n".join(refused)


@pytest.fixture(autouse=True)
def _a_fresh_limiter():
    """Every browser test starts with the sign-in limiter empty.

    Each of these signs in, all of them from one address, and the limit that stops a
    stranger guessing passwords cannot tell a suite from an attacker -- correctly. So the
    suite got a `429` somewhere in the middle once it grew past the allowance, and *which*
    test got it depended on how many ran before it, which is the worst shape a failure can
    have.

    Cleared per test rather than switched off, so the limiter is the real one and
    `tests/security/test_rate_limits.py` goes on holding it to its numbers.
    """
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture
def applicant(db):
    """A person with a verified address and one CV, ready to sign in and send things."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from postulo.documents.models import CV

    user = get_user_model().objects.create_user(
        email=EMAIL, password=PASSWORD, first_name="Alex", last_name="Morgan"
    )
    EmailAddress.objects.create(user=user, email=EMAIL, verified=True, primary=True)
    CV.objects.create(owner=user, name="Main CV", headline="Django developer")
    return user
