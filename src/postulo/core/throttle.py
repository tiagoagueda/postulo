"""How often one account may make the server do something expensive.

Everything here needs an account or a token, so none of it is reachable by a stranger — and
nothing bounded what somebody with one could do. The surface that matters is **capture**: it
makes Postulo itself issue an outbound request to an address the caller supplies.
``check_destination`` already refuses private addresses and revalidates on every redirect, so
that is not SSRF; what was missing is a bound on *how often*, without which a self-hosted box
becomes a modest scanner or runs out of its own outbound connections (#112).

**Keyed on the account, not the address.** All of this needs one, and an account is the thing
being limited: sharing an office network should not mean sharing an allowance, and changing
address should not hand somebody a fresh one.

**Written against the cache rather than against a new dependency.** The cache is already
there, already backs allauth's own limits, and this is thirty lines. A rate limiter is not
where a self-hosted application should acquire a supply chain.

**A fixed window, and the honesty about what that means.** The count lives under a key naming
the window it belongs to, so the allowance refills at the boundary rather than sliding. Twice
the limit is therefore possible across a boundary, which for these numbers is a detail rather
than a hole. And ``incr`` is not atomic on Django's database cache, so two simultaneous
requests can both read the same count: under heavy concurrency this undercounts, which errs
towards letting somebody through rather than towards locking them out. Both are the right way
round for a limit whose purpose is to stop a machine being ridden, not to meter billing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

#: `20/h`, `5/m`, `600/d`. Empty, `0` or anything unparseable means no limit, because an
#: operator who mistypes a rate should get their instance working rather than locked.
_SPEC = re.compile(r"^\s*(\d+)\s*/\s*([smhd])\s*$")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True)
class Rate:
    """How many times, in how many seconds."""

    times: int
    seconds: int

    def __bool__(self) -> bool:
        return self.times > 0 and self.seconds > 0


#: What "no limit" is, so callers never have to compare against None.
UNLIMITED = Rate(0, 0)


def parse(spec: str | None) -> Rate:
    match = _SPEC.match(str(spec or ""))
    if not match:
        return UNLIMITED
    times, unit = match.groups()
    return Rate(int(times), _SECONDS[unit])


class TooOften(Exception):
    """The allowance is spent. Carries the wait, so a caller can say how long."""

    def __init__(self, rate: Rate, retry_after: int) -> None:
        self.rate = rate
        self.retry_after = retry_after
        super().__init__(
            str(
                _("Too many requests: %(times)d allowed every %(seconds)d seconds.")
                % {"times": rate.times, "seconds": rate.seconds}
            )
        )


def _now() -> float:
    """The clock, behind a seam.

    Not indirection for its own sake: a test that wants to watch a window turn cannot patch
    `time.time` globally, because Django's database cache computes its absolute expiry from
    the same call — a frozen clock in 1970 makes every entry it writes already expired, and
    the limit then never counts anything. Patching this instead moves only this module.
    """
    return time.time()


def consume(action: str, who: object, rate: Rate) -> None:
    """Count one use of ``action`` by ``who``, or raise :class:`TooOften`.

    ``who`` is anything with a primary key — an account, a token — or a string for the
    endpoints that are guarded by a shared token rather than by an account.
    """
    if not rate:
        return
    identity = getattr(who, "pk", None) or str(who)
    window = int(_now()) // rate.seconds
    key = f"postulo:rate:{action}:{identity}:{window}"

    # `add` only sets when the key is absent, so the first request of a window creates it
    # and the rest increment. `incr` raises when the key expired between the two, which is
    # a window boundary and means this request is the first of the next one.
    if cache.add(key, 1, timeout=rate.seconds):
        used = 1
    else:
        try:
            used = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=rate.seconds)
            used = 1

    if used > rate.times:
        elapsed = int(_now()) % rate.seconds
        raise TooOften(rate, retry_after=max(1, rate.seconds - elapsed))


def rate_for(name: str) -> Rate:
    """The configured rate for one surface, by the name of its setting."""
    return parse(getattr(settings, name, ""))


def capture(user) -> None:
    """The tightest of the three: this one makes the server talk to somebody else's."""
    consume("capture", user, rate_for("POSTULO_CAPTURE_RATE"))


def api(token) -> None:
    """Per token rather than per account, so revoking one revokes its allowance with it."""
    consume("api", token, rate_for("POSTULO_API_RATE"))


def endpoint(name: str, request) -> None:
    """`/logs` and `/metrics`, which a shared token guards rather than an account.

    There is no account to key on, so the caller is the address. That is a weaker key than
    the rest of this module uses and it is the best one available: a collector is a machine
    at a fixed address, and the token is the thing that actually authorises it.

    `REMOTE_ADDR` rather than a header, because `TrustedProxyMiddleware` has already replaced
    it with the rightmost untrusted entry of `X-Forwarded-For` and stripped that header from
    peers that are not proxies. So this is the caller, not the proxy in front of them.
    """
    consume(
        f"endpoint:{name}",
        request.META.get("REMOTE_ADDR") or "unknown",
        rate_for("POSTULO_ENDPOINT_RATE"),
    )
