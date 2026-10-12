"""The service worker's offline page, and what the worker may keep (#895).

Installing Postulo as an app gave the worker a fetch handler, which it had been kept without
on purpose: a worker that answers from a cache is a second copy of the application, and one
left on a shared device outlives signing out. So the rule is narrow and checked here. The
worker keeps one page, the offline page; it answers a page load from the cache only when the
network gave nothing at all; and that page carries nothing about anybody.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

WORKER = Path(__file__).resolve().parents[2] / "src" / "postulo" / "static" / "js" / "sw.js"


def worker_source() -> str:
    return WORKER.read_text(encoding="utf-8")


def test_the_offline_page_names_nobody(client, user):
    client.force_login(user)
    signed_in = client.get(reverse("core:offline"))
    signed_out = Client().get(reverse("core:offline"))

    assert signed_in.status_code == signed_out.status_code == 200
    assert user.email not in signed_in.content.decode(), "it is kept on the device"
    assert "Account menu" not in signed_in.content.decode(), "no masthead, no menu"


def test_the_worker_keeps_the_offline_page_and_nothing_else():
    source = worker_source()

    # One place writes to a cache, and it writes the offline page.
    assert source.count(".put(") == 1
    assert "cache.put(OFFLINE_PAGE, response)" in source
    assert 'OFFLINE_PAGE = "/offline/"' in source
    assert reverse("core:offline") == "/offline/"
    # And one place reads from one, which reads the same page.
    assert len(re.findall(r"caches\.match\(", source)) == 2
    assert all(
        call.startswith("caches.match(OFFLINE_PAGE")
        for call in re.findall(r"caches\.match\([^)]*\)", source)
    )


def test_the_worker_answers_only_a_page_load_and_only_on_a_failed_one():
    source = worker_source()
    handler = source[source.index('addEventListener("fetch"') :]
    handler = handler[: handler.index("\n});")]

    assert 'request.mode !== "navigate"' in handler, "no script, picture or request a page makes"
    assert 'request.method !== "GET"' in handler, "a form that failed is not answered with a page"
    assert "origin !== self.location.origin" in handler, "another site is not ours to answer"
    # The network first, and the kept page only in the `catch`: a page that loaded, error or
    # not, is the page shown.
    assert (
        handler.index("fetch(request)") < handler.index(".catch(") < handler.index("caches.match")
    )


def test_signing_out_still_takes_the_worker_away():
    """#753's rule holds with the worker registered for everybody signed in."""
    script = (WORKER.parents[0] / "app.js").read_text(encoding="utf-8")

    assert "registration.unregister()" in script
