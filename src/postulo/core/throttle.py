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

import logging
import re
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import caches
from django.utils.translation import ngettext

logger = logging.getLogger(__name__)

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


def is_well_formed(spec: str | None) -> bool:
    """Whether `parse` will read this as what was meant: a rate, or a deliberate no-limit.

    `parse` reads anything unreadable as no limit, so an instance with a mistyped rate keeps
    working. The configuration checks (#233) ask this instead, so the mistype is reported at
    start-up rather than silently opening the door.
    """
    text = str(spec or "").strip()
    return text in ("", "0") or bool(_SPEC.match(text))


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
                ngettext(
                    "Too many requests: %(times)d allowed every %(seconds)d second.",
                    "Too many requests: %(times)d allowed every %(seconds)d seconds.",
                    rate.seconds,
                )
                % {"times": rate.times, "seconds": rate.seconds}
            )
        )


def _store():
    """The cache allowances are counted in: outside any request's transaction (#756)."""
    return caches["ratelimit"]


def _now() -> float:
    """The clock, behind a seam.

    Not indirection for its own sake: a test that wants to watch a window turn cannot patch
    `time.time` globally, because Django's database cache computes its absolute expiry from
    the same call — a frozen clock in 1970 makes every entry it writes already expired, and
    the limit then never counts anything. Patching this instead moves only this module.
    """
    return time.time()


def _key(action: str, who: object, rate: Rate) -> str:
    identity = getattr(who, "pk", None) or str(who)
    window = int(_now()) // rate.seconds
    return f"postulo:rate:{action}:{identity}:{window}"


def consume(action: str, who: object, rate: Rate) -> None:
    """Count one use of ``action`` by ``who``, or raise :class:`TooOften`.

    ``who`` is anything with a primary key — an account, a token — or a string for the
    endpoints that are guarded by a shared token rather than by an account.
    """
    if not rate:
        return
    key = _key(action, who, rate)

    # `add` only sets when the key is absent, so the first request of a window creates it
    # and the rest increment. `incr` raises when the key expired between the two, which is
    # a window boundary and means this request is the first of the next one.
    if _store().add(key, 1, timeout=rate.seconds):
        used = 1
    else:
        try:
            used = _store().incr(key)
        except ValueError:
            _store().set(key, 1, timeout=rate.seconds)
            used = 1
        else:
            # Django's generic `incr` (the database cache, the file cache) is a get and a
            # `set` with the backend's default timeout, which is five minutes: a counter
            # for `30/h` then expired five minutes after its last use, not at the end of
            # its hour, and every limit longer than that was really "per five minutes of
            # quiet" (#734). Put the expiry back to the end of the window. Redis and
            # memcached increment in place and keep it, and touching them is harmless.
            _store().touch(key, max(1, rate.seconds - int(_now()) % rate.seconds))

    if used > rate.times:
        elapsed = int(_now()) % rate.seconds
        raise TooOften(rate, retry_after=max(1, rate.seconds - elapsed))


def spent(action: str, who: object, rate: Rate) -> int:
    """Seconds until ``who`` may use ``action`` again, or 0 while the allowance is not spent.

    Asked without using anything: for an answer that is only given while there is
    allowance left, so a caller can refuse before it looks at what the answer would be.
    ``consume`` is what counts.
    """
    if not rate:
        return 0
    if _store().get(_key(action, who, rate), 0) < rate.times:
        return 0
    elapsed = int(_now()) % rate.seconds
    return max(1, rate.seconds - elapsed)


def rate_for(name: str) -> Rate:
    """The configured rate for one surface, by the name of its setting."""
    return parse(getattr(settings, name, ""))


def capture(user) -> None:
    """The tightest of the three: this one makes the server talk to somebody else's.

    Spent on both surfaces that can cause that fetch -- the web form and the capture API --
    against the one account's allowance, because the thing being bounded is the act and not
    the door it came through (#194).
    """
    consume("capture", user, rate_for("POSTULO_CAPTURE_RATE"))


def fetch(user) -> None:
    """One outbound request to an address a person chose, outside capture.

    Spent once per URL by the link check, the logo address and *Find logo*: the other places
    where the server fetches what somebody typed. Its own allowance and not capture's, since
    checking a hundred links is a normal afternoon and capturing a hundred pages is not (#407).
    """
    consume("fetch", user, rate_for("POSTULO_FETCH_RATE"))


def wikidata(user) -> None:
    """One *Fetch from Wikidata*, against the account's own allowance (#673)."""
    consume("wikidata", user, rate_for("POSTULO_WIKIDATA_RATE"))


def lookup(user) -> None:
    """One *Look up* of a DOI or one *Suggest from my ORCID record* (#688).

    Spent before the request is made, so a service that refuses, times out or answers
    nonsense has still cost one: the allowance bounds what this server asks of a scholarly
    service on a person's behalf, and a failure is still a request.
    """
    consume("lookup", user, rate_for("POSTULO_LOOKUP_RATE"))


def register_lookup(user) -> None:
    """One *Look up* in a company register, against the account's own allowance (#1021).

    Spent once per press, whichever register answers and however many addresses it is asked
    at: the act is what is bounded, and Poland's second address is part of one act.
    """
    consume("register-lookup", user, rate_for("POSTULO_REGISTER_LOOKUP_RATE"))


def api(token) -> None:
    """Per token rather than per account, so revoking one revokes its allowance with it."""
    consume("api", token, rate_for("POSTULO_API_RATE"))
    # And one allowance for the account behind its tokens (#799): token issuance is not
    # bounded, so a per-token allowance alone is one an account can multiply at will.
    consume("api-account", token.owner, rate_for("POSTULO_API_ACCOUNT_RATE"))


#: How many wrong tokens one address may present to `/logs` or `/metrics` in an hour. A
#: collector holding the right token never spends any of it (#472).
REFUSED = Rate(10, 3600)


def _caller(request) -> str:
    return request.META.get("REMOTE_ADDR") or "unknown"


#: How many wrong API tokens one address may present in an hour (#799). Looser than `REFUSED`:
#: a client left holding a revoked token retries without anybody noticing, and must not bar
#: everybody who shares its address for long.
API_REFUSED = Rate(60, 3600)


def barred(name: str, request, rate: Rate | None = None) -> None:
    """Raise :class:`TooOften` for an address that has spent its wrong-token allowance.

    Asked before the token is read, so that an address which has been guessing is refused
    whatever it presents for the rest of the window, a lucky guess included.
    """
    rate = rate or REFUSED
    used = _store().get(_key(f"endpoint-refused:{name}", _caller(request), rate), 0)
    if used > rate.times:
        _say_the_proxy_is_not_trusted(request)
        elapsed = int(_now()) % rate.seconds
        raise TooOften(rate, retry_after=max(1, rate.seconds - elapsed))


def _say_the_proxy_is_not_trusted(request) -> None:
    """When an address is barred and it is a forwarder Postulo does not trust, say so once an hour.

    Everyone behind a proxy that is not in POSTULO_TRUSTED_PROXIES arrives from the proxy's
    address, so one person's wrong tokens bar the collector that holds the right one (#757).
    The fix is one line of configuration; this is the line in the log that points at it.
    """
    from . import proxy

    forwarder = request.META.get(proxy.UNTRUSTED_FORWARDER)
    if not forwarder:
        return
    try:
        first = _store().add(f"postulo:proxy-warned:{forwarder}", True, 3600)
    except Exception:  # a store that is down is no reason to fail the refusal itself
        return
    if first:
        logger.warning(
            "Wrong tokens from %s have used up its allowance. It forwards for other clients but "
            "is not in POSTULO_TRUSTED_PROXIES, so they all share one allowance. Add it there.",
            forwarder,
        )


def refused(name: str, request, rate: Rate | None = None) -> None:
    """Count a wrong token against the address that presented it, or raise."""
    consume(f"endpoint-refused:{name}", _caller(request), rate or REFUSED)


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
