"""Django's async-safety guard stays on for the unit suite (#1160).

Django refuses a database call made from a thread with a running event loop, which is what
catches a synchronous ORM call left in async code. The browser tests need the guard off, since
Playwright runs a loop in the test thread, and their conftest used to switch it off as it was
imported: for the unit suite as well, which imports that file while collecting.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import SynchronousOnlyOperation

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.django_db
def test_a_database_call_from_a_running_loop_is_refused():
    async def count_from_inside_a_loop():
        return get_user_model().objects.count()

    with pytest.raises(SynchronousOnlyOperation):
        asyncio.run(count_from_inside_a_loop())


def test_the_browser_suite_switches_the_guard_off_only_once_a_browser_test_is_selected():
    """Nothing at the top level of the browser conftest touches the variable: a line there
    runs for every unit worker too. It is set in the hook that knows what was selected."""
    source = (ROOT / "tests" / "e2e" / "conftest.py").read_text(encoding="utf-8")

    top_level = [
        line for line in source.splitlines() if re.match(r"\S", line) and "ASYNC_UNSAFE" in line
    ]
    hook = source.split("def pytest_collection_finish(session):")[1].split("\n\n\n")[0]

    assert top_level == []
    assert "session.items" in hook and "DJANGO_ALLOW_ASYNC_UNSAFE" in hook
