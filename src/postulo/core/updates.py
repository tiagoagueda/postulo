"""Whether a newer Postulo exists -- asked only when the operator said to, and never
by a page.

The footer prints the version and the plugins page prints it beside every built-in, and
both are right; what neither could say was whether that number is the newest one. There
was no way, from inside a running Postulo, to learn that a release exists, which is how
"the built-ins still say 0.2.1" was reported as a bug against an instance that genuinely
was 0.2.1 (#272). For a self-hosted application that is more than cosmetic: an operator
who does not know a release exists does not know a *security* release exists.

An update check is an outbound request, and this application does not make requests on
a reader's behalf -- `jobs/logos.py` and `plugins/logos.py` exist to avoid exactly that.
So it is **off by default**, asks **one address the operator configured** and nothing
else, runs **on the server's own clock** -- the scheduler's loop, once a day, or the
management command -- and **never on a page load**. What a page shows is whatever the
last check stored, and a page never fetches.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Where the last answer is kept, for every process to read. No expiry: a stale answer with
#: its date is more use than none, and the next check overwrites it.
CACHE_KEY = "postulo:update-check"

#: How often the scheduler asks. A release is not an hourly event.
EVERY = timedelta(days=1)


def enabled() -> bool:
    return bool(getattr(settings, "POSTULO_UPDATE_CHECK", False))


def source() -> str:
    return str(getattr(settings, "POSTULO_UPDATE_SOURCE", "") or "")


def _fetch(url: str) -> dict:
    """The one request. Through the public-only client: the address is public by
    definition, so the operator's own destination policy does not widen what it may dial."""
    from postulo.plugins.http import public_only_client

    with public_only_client(timeout=10.0) as client:
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()


def _version_of(release: dict) -> str:
    """`v0.4.0` -> `0.4.0`: a Forgejo or GitHub release names its tag."""
    tag = str(release.get("tag_name") or release.get("name") or "").strip()
    return tag[1:] if tag[:1] in ("v", "V") and tag[1:2].isdigit() else tag


def is_behind(current: str, latest: str) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def check() -> dict:
    """Ask the configured source, store the answer, and return it.

    Never raises: a source that is down is an answer -- "could not check" with the time --
    and not a reason to end a scheduler pass.
    """
    if not enabled():
        return status()
    now = timezone.now()
    try:
        release = _fetch(source())
        answer = {
            "latest": _version_of(release),
            "url": str(release.get("html_url") or ""),
            "published": str(release.get("published_at") or ""),
            "checked_at": now,
            "error": "",
        }
    except Exception as error:
        logger.warning("The update check could not reach %s: %s", source(), error)
        answer = {"latest": "", "url": "", "published": "", "checked_at": now, "error": str(error)}
    cache.set(CACHE_KEY, answer, timeout=None)
    return status()


def due() -> bool:
    """Whether the scheduler should ask again: enabled, and a day since it last did."""
    if not enabled():
        return False
    stored = cache.get(CACHE_KEY) or {}
    checked_at = stored.get("checked_at")
    return checked_at is None or timezone.now() - checked_at >= EVERY


def status() -> dict:
    """What a page may show: the last stored answer against the running version. Reads
    the cache and nothing else."""
    from postulo.core.context_processors import installed_version

    current = installed_version()
    stored = cache.get(CACHE_KEY) or {}
    latest = stored.get("latest", "")
    return {
        "enabled": enabled(),
        "current": current,
        "latest": latest,
        "behind": bool(latest) and is_behind(current, latest),
        "url": stored.get("url", ""),
        "checked_at": stored.get("checked_at"),
        "error": stored.get("error", ""),
    }
