"""The live server's database connections do not outlive their threads (#294).

The e2e suite answers requests on threads that host event loops (the Chromium PDF
backend drives Playwright from the request itself). A database read made while such a
loop runs starts a second, thread-private connection, and when the thread goes the
connection is left open for the collector, whose ResourceWarning has been failing an
innocent test. ``tests/e2e/conftest.py`` sweeps those after every test; this is the
repro that keeps it so.
"""

from __future__ import annotations

import asyncio
import threading

import pytest


@pytest.mark.e2e
@pytest.mark.django_db
def test_connection_left_by_a_loop_hosting_thread_is_swept():
    """A worker thread that hosts an event loop cannot leave its connection open."""
    from django.contrib.auth import get_user_model
    from django.db import connections

    from tests.e2e.conftest import _WRAPPERS, _close_connections_left_behind

    shared = connections["default"]
    shared.inc_thread_sharing()  # what LiveServer.start() does before its threads run
    try:

        def worker():
            # A request thread: it sees the server's wrapper, and then an event loop
            # runs on it while the ORM is used, as the CV PDF render does.
            connections["default"] = shared
            get_user_model().objects.count()

            async def during():
                get_user_model().objects.count()

            asyncio.run(during())

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        # Drop the reference: a dead thread that nothing holds is what makes its
        # wrapper unreachable, and what the sweep looks for.
        del thread

        orphans = [
            wrapper
            for thread_ref, wrapper in _WRAPPERS.values()
            if thread_ref() is None and wrapper.connection is not None
        ]
        # The loop window started a second connection, and its thread is gone. Without
        # the sweep it would be collected later, warning. It is still here, so the
        # scenario really is this one:
        assert orphans
        _close_connections_left_behind()
        assert not any(
            thread_ref() is None and wrapper.connection is not None
            for thread_ref, wrapper in _WRAPPERS.values()
        )
    finally:
        shared.dec_thread_sharing()
