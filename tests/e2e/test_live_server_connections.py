"""Requests in flight at once each keep a transaction of their own (#1024).

The browser job failed at random with ``no such savepoint`` in the middle of a page, and then
with ``TransactionManagementError`` on every request and every teardown after it: 1,829
errors from one. On an in-memory SQLite database pytest-django's live server hands the test
thread's one connection to every request thread, and every request is a transaction
(``ATOMIC_REQUESTS``), so the transactions of requests that overlap are tangled in the one
wrapper's bookkeeping. A request that comes in while another is inside its transaction
makes a savepoint rather than a transaction; if the other commits between the ``SAVEPOINT``
and Django noting it down, the savepoint is gone, the next request to arrive opens a fresh
transaction on the same wrapper, and the first one, finishing inside it, asks SQLite to
release a savepoint it no longer has.

The browser suite now runs on a database in a file (``django_db_modify_db_settings`` in
``tests/conftest.py``, and the live server waits for it in ``tests/e2e/conftest.py``), where
each request thread opens a connection of its own. This plays that interleaving out, without a
browser and without luck: three requests to a view of this module's own that waits for the
test to let it go, and a pause held open in the one window the race needs.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest
from django.db.backends.base.base import BaseDatabaseWrapper
from django.http import HttpResponse
from django.urls import path

pytestmark = pytest.mark.e2e

#: Per request, an event its view sets once inside its transaction and one it waits for.
_GATES: dict[str, tuple[threading.Event, threading.Event]] = {}


def _held(request, name):
    from django.contrib.auth import get_user_model

    entered, release = _GATES[name]
    get_user_model().objects.exists()
    entered.set()
    release.wait(timeout=30)
    get_user_model().objects.exists()
    return HttpResponse(name)


urlpatterns = [path("held/<str:name>/", _held)]


def test_the_live_server_does_not_share_the_test_threads_connection(live_server):
    """Each request thread connects for itself, rather than borrowing the test's."""
    assert live_server.thread.connections_override == {}


def test_requests_in_flight_at_once_each_finish_their_own_transaction(
    live_server, settings, monkeypatch
):
    settings.ROOT_URLCONF = __name__
    _GATES.clear()
    _GATES.update({name: (threading.Event(), threading.Event()) for name in ("a", "b", "c")})
    answers: dict[str, int | str] = {}

    # The race's window, held open: a savepoint made on a request thread waits, after SQLite
    # has made it and before `Atomic` notes it down, until the test says go on. Only the
    # second request ever makes one, and only when its transaction is nested in the first's.
    pausing, paused, resume = threading.Event(), threading.Event(), threading.Event()
    savepoint = BaseDatabaseWrapper.savepoint

    def savepoint_with_a_pause(self):
        sid = savepoint(self)
        if pausing.is_set():
            paused.set()
            resume.wait(timeout=30)
        return sid

    monkeypatch.setattr(BaseDatabaseWrapper, "savepoint", savepoint_with_a_pause)

    # By address, not by `localhost`, which Windows tries over IPv6 first and takes a second
    # or two to give up on: long enough to put the requests out of the order this is about.
    port = live_server.thread.port

    def ask(name):
        asked = urllib.request.Request(
            f"http://127.0.0.1:{port}/held/{name}/", headers={"Host": f"localhost:{port}"}
        )
        try:
            with urllib.request.urlopen(asked, timeout=90) as reply:  # noqa: S310 - the live server
                answers[name] = reply.status
        except urllib.error.HTTPError as refused:
            answers[name] = refused.code
        except OSError as failed:  # pragma: no cover - says what went wrong if it does
            answers[name] = repr(failed)

    threads = {name: threading.Thread(target=ask, args=(name,)) for name in _GATES}
    try:
        threads["a"].start()
        assert _GATES["a"][0].wait(timeout=20), "the first request never reached its view"

        # B arrives while A is in its transaction. On a shared connection its transaction is
        # a savepoint inside A's, and it stops in the window; on a connection of its own it
        # waits for A's write lock (`transaction_mode: IMMEDIATE`) instead.
        pausing.set()
        threads["b"].start()
        paused.wait(timeout=2)

        # A commits, and with it goes B's savepoint, if B has one.
        _GATES["a"][1].set()
        threads["a"].join(timeout=30)
        pausing.clear()
        resume.set()
        assert _GATES["b"][0].wait(timeout=20), "the second request never reached its view"

        # C arrives while B is in its view. On a shared connection the wrapper is out of its
        # atomic block since A's commit, so C begins a transaction on it; on its own, C waits
        # for B's write lock.
        threads["c"].start()
        _GATES["c"][0].wait(timeout=2)

        # B finishes: inside C's transaction, on a shared connection, it releases the
        # savepoint A's commit took away.
        _GATES["b"][1].set()
        threads["b"].join(timeout=30)
        assert _GATES["c"][0].wait(timeout=20), "the third request never reached its view"
        _GATES["c"][1].set()
        threads["c"].join(timeout=30)
    finally:
        resume.set()
        for _entered, release in _GATES.values():
            release.set()
        for thread in threads.values():
            if thread.ident is not None:
                thread.join(timeout=30)

    assert answers == {"a": 200, "b": 200, "c": 200}
