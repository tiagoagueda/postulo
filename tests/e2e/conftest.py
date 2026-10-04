"""Browser tests: a real Chromium driven against a live server.

These are excluded from the default run (``-m "not e2e"`` in ``pyproject.toml``) because
they need a browser installed. Run them with::

    uv run playwright install chromium
    uv run pytest -m e2e

CI runs them on every push in their own job, and runs a failure again alone with a trace.

Every page is drawn in DejaVu Sans, from files in `fonts/` beside this one, on every machine:
`_drawn_in_the_suites_own_font` below says why and how.
"""

import os
import re
import sqlite3
import threading
import weakref
from pathlib import Path

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


# ---------------------------------------------------------------- the long ones go first
#: The tests that walk every page, each a minute or more where the rest take seconds. Run
#: first, so that under xdist no worker is left holding one while the others sit idle (#721).
WALKS = frozenset(
    {
        "test_every_signed_in_page_has_no_violations",
        "test_no_page_scrolls_sideways_at_320_pixels",
        "test_nothing_is_lost_under_the_text_spacing_override",
        "test_nothing_is_lost_at_two_hundred_percent_zoom",
        "test_everything_clickable_is_big_enough_to_hit",
        "test_everything_is_still_big_enough_when_somebody_asks_for_less_room",
        "test_no_page_references_an_element_that_is_not_there",
    }
)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    # Last, after pytest has grouped the parametrised ones, or it scatters the walks again.
    items.sort(key=lambda item: getattr(item, "originalname", item.name) not in WALKS)


# --------------------------------------------------------------- the font every page is drawn in
#: The application's stylesheet, whatever its name carries after `app`.
_STYLESHEET = re.compile(r"/static/css/app[^/]*\.css")

#: The suite's own copy of DejaVu Sans and DejaVu Sans Mono, regular and bold: the four files
#: Debian's `fonts-dejavu-core` installs on CI, and their licence.
FONTS = Path(__file__).resolve().parent / "fonts"

#: Where the live server answers for them, under the static prefix, so the content security
#: policy's `font-src 'self'` already allows them.
_FONT_PATH = "e2e-fonts/"

#: Family names no machine has installed, so a page can only ever draw in the files above:
#: a desktop with the whole DejaVu family would otherwise lend its real oblique to text that
#: CI, with none installed, slants by itself.
SANS = "Postulo E2E Sans"
MONO = "Postulo E2E Mono"

_FACES = (
    (SANS, "normal", "DejaVuSans.ttf"),
    (SANS, "bold", "DejaVuSans-Bold.ttf"),
    (MONO, "normal", "DejaVuSansMono.ttf"),
    (MONO, "bold", "DejaVuSansMono-Bold.ttf"),
)


def _font_rules(static_url: str) -> bytes:
    faces = "".join(
        f'\n@font-face {{ font-family: "{family}"; font-weight: {weight}; '
        f'src: url("{static_url}{_FONT_PATH}{name}") format("truetype"); }}'
        for family, weight, name in _FACES
    )
    return (
        faces
        + f'\n*, ::before, ::after {{ font-family: "{SANS}" !important; }}'
        + f'\ncode, code *, kbd, samp, pre, pre * {{ font-family: "{MONO}" !important; }}\n'
    ).encode()


@pytest.fixture(scope="session", autouse=True)
def _drawn_in_the_suites_own_font():
    """Every page of every test is drawn in DejaVu Sans, from the suite's own files (#717).

    The interface uses the reader's own system font, so the suite measured whatever the
    machine it ran on draws in: Segoe UI on a Windows desktop, DejaVu Sans on CI, which is
    wider. A title squeezed to 41 pixels on the calendar (#316), a footer on three rows
    (#212) and boxes clipped under the text spacing override each passed on a desktop and
    failed on CI. The cure used to be a switch somebody had to remember, and that drew in
    Times on a machine without DejaVu installed; now the suite brings the font with it.

    The live server serves the application's own stylesheet with `@font-face` rules and two
    rules after it -- everything in the sans, `code` in the mono -- and serves the font files
    themselves under the static prefix. A stylesheet and fonts from the application's own
    address are what the content security policy allows, so the policy stays on; the rules
    reach a page whose scripts are off and a context a test opened for itself; and the
    browser is left alone. It is not done by intercepting requests in the browser, because
    that turns the browser's cache off, and the tests that press *Back* are about what the
    cache brings back.
    """
    from django.conf import settings
    from django.contrib.staticfiles import handlers
    from django.http import HttpResponse

    rules = _font_rules(settings.STATIC_URL)
    files = {name: (FONTS / name).read_bytes() for _family, _weight, name in _FACES}
    served = handlers.serve

    def in_the_font(request, path, **kwargs):
        # A file path by now, with Windows' separators on Windows.
        asked = path.replace("\\", "/")
        if asked.startswith(_FONT_PATH) and asked[len(_FONT_PATH) :] in files:
            answer = HttpResponse(files[asked[len(_FONT_PATH) :]], content_type="font/ttf")
            answer["Cache-Control"] = "max-age=86400"
            return answer
        response = served(request, path, **kwargs)
        if response.status_code != 200 or not _STYLESHEET.search(request.path):
            return response
        body = b"".join(response.streaming_content) if response.streaming else response.content
        response.close()
        answer = HttpResponse(body + rules, content_type=response["Content-Type"])
        if "Last-Modified" in response:
            answer["Last-Modified"] = response["Last-Modified"]
        return answer

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(handlers, "serve", in_the_font)
        yield


#: Whether this process has seen a page draw in the suite's font yet.
_FONT_SEEN = []


@pytest.fixture(autouse=True)
def _the_suites_font_is_the_one_drawn(_drawn_in_the_suites_own_font, browser, live_server):
    """The run stops at once, saying why, if a page cannot load the suite's font.

    Otherwise every layout test would measure the browser's fallback and fail, or pass, for
    a reason nobody would guess from the message. Asked by the first test of each process,
    in a context of its own, once that test's database is there to draw a page from.
    """
    if _FONT_SEEN:
        return
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(f"{live_server.url}/accounts/login/")
        loaded = page.evaluate(
            """async () => {
                const faces = await Promise.all([
                    document.fonts.load('16px "Postulo E2E Sans"'),
                    document.fonts.load('bold 16px "Postulo E2E Sans"'),
                    document.fonts.load('16px "Postulo E2E Mono"'),
                ]);
                return faces.every((found) => found.length > 0);
            }"""
        )
    finally:
        context.close()
    if loaded:
        _FONT_SEEN.append(True)
    else:
        pytest.exit(
            "the browser did not load the suite's own DejaVu Sans from tests/e2e/fonts; "
            "every layout test would measure a fallback font instead",
            returncode=3,
        )


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
