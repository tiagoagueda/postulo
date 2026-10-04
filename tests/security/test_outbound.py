"""Where the server may dial, and that it dials the address it checked (#215).

`docs/THREAT-MODEL.md` rule 5: *resolve, approve every address the name answers with, and
connect to one of those* — one act, not two. Two lookups leave a gap a short-lived DNS record
can answer differently, which is the whole of DNS rebinding.

Two clients, two policies, and the difference is deliberate:

- `client()` is for a **connection** somebody set up. A private address is the point there —
  a Paperless on the LAN, a mail server in the same Compose network — so the operator decides
  with `POSTULO_CONNECTIONS_ALLOW_PRIVATE`.
- `public_only_client()` is for what is public **by definition**: a job posting, a portfolio
  address, a company's logo, a browser's push service. The operator's decision about
  connections is not an answer to those, and this file is where that stays true.

Two more questions since #321: what counts as private, when an IPv6 address carries an IPv4
one inside it; and how much of an answer is read, and for how long, when the server on the
other end is the stranger.

Nothing here leaves the machine: the resolver is stubbed, and every response comes from a
`MockTransport`.
"""

from __future__ import annotations

import gzip
import ipaddress
import itertools
import socket
import threading
import time
import types
import zlib

import httpcore
import httpx
import pytest

from postulo.core import destinations
from postulo.jobs import logos
from postulo.plugins import fetching, http, public_addresses
from postulo.plugins.base import CaptureError

PUBLIC = [ipaddress.ip_address("93.184.216.34")]


def recorder(status: int = 200, body: bytes = b"", content_type: str = "text/plain"):
    """A transport that answers everything the same way and remembers what it was asked."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=body, headers={"Content-Type": content_type})

    return httpx.MockTransport(handler), seen


def resolving(monkeypatch, answers: dict[str, list]):
    """Stub the resolver the guards use: host → addresses, or `UnsafeURL` for a refusal."""

    def public_addresses_for(url: str):
        host = httpx.URL(url).host
        answer = answers.get(host)
        if answer is None:
            raise public_addresses.PrivateAddress("That address is on a private or local network.")
        return answer

    monkeypatch.setattr(http, "public_addresses_for", public_addresses_for)


# ------------------------------------------------- the connection client, and rebinding


def test_the_connection_client_connects_to_the_address_it_approved(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    resolving(monkeypatch, {"paperless.example": PUBLIC})
    transport, seen = recorder()

    with http.client(transport=transport) as client:
        client.get("https://paperless.example/api/documents/")

    assert str(seen[0].url.host) == "93.184.216.34", "the socket went to the approved address"
    assert seen[0].headers["Host"] == "paperless.example", "the site still sees its own name"
    assert seen[0].extensions["sni_hostname"] == "paperless.example", "TLS proves the name"


def test_the_connection_client_refuses_what_the_check_turns_down(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    resolving(monkeypatch, {})
    transport, seen = recorder()

    with (
        http.client(transport=transport) as client,
        pytest.raises(http.DestinationRefused) as refusal,
    ):
        client.get("https://router.local/admin")

    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in str(refusal.value), "it says how to allow it"
    assert seen == [], "nothing was sent"


def test_a_redirect_is_checked_as_the_request_it_is(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    resolving(monkeypatch, {"posting.example": PUBLIC})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "http://192.168.1.50/snapshot.jpg"})

    with (
        http.client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(http.DestinationRefused),
    ):
        client.get("https://posting.example/logo")

    assert len(seen) == 1, "the hop onto the network was never opened"


def test_with_private_destinations_allowed_the_name_is_left_to_the_resolver(monkeypatch, settings):
    """An operator who allowed private addresses has nothing left for pinning to protect.

    Every address passes, so resolving twice cannot become a way through — and leaving the
    name alone is what keeps Compose-internal names working as they always have.
    """
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True

    def refuse(url: str):  # pragma: no cover - called only if the guard gets this wrong
        raise AssertionError("the public check ran although private destinations are allowed")

    monkeypatch.setattr(http, "public_addresses_for", refuse)
    transport, seen = recorder()

    with http.client(transport=transport) as client:
        client.get("http://paperless:8000/api/")

    assert seen[0].url.host == "paperless"


# ----------------------------------------------------------- public by definition: logos


def guarded_logo_client(monkeypatch, transport):
    """Let `logos.download` use the real guard, over a transport that never leaves here."""
    real = http.public_only_client
    monkeypatch.setattr(
        http, "public_only_client", lambda **kwargs: real(transport=transport, **kwargs)
    )


def test_a_logo_is_fetched_publicly_even_where_connections_may_be_private(monkeypatch, settings):
    """The company's own page redirects the fetch onto the LAN, with the switch **on**."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    resolving(monkeypatch, {"blackmesa.test": PUBLIC})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "http://192.168.1.50/snapshot.jpg"})

    guarded_logo_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(logos.UnusableLogo) as refusal:
        logos.download("https://blackmesa.test/logo.png")

    assert "private or local network" in str(refusal.value)
    assert len(seen) == 1, "the address on the LAN was never requested"


def test_a_logo_from_the_open_web_still_arrives(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    resolving(monkeypatch, {"blackmesa.test": PUBLIC})
    transport, seen = recorder(200, b"\x89PNG fake", "image/png")
    guarded_logo_client(monkeypatch, transport)

    assert logos.download("https://blackmesa.test/logo.png") == b"\x89PNG fake"
    assert str(seen[0].url.host) == "93.184.216.34"


# --------------------------------------------------------------------------- robots.txt


@pytest.mark.django_db
def test_robots_txt_is_asked_through_the_guarded_client(monkeypatch):
    """The default client this function opens for itself is the guarded one, not a bare httpx."""
    resolving(monkeypatch, {"posting.example": PUBLIC})
    transport, seen = recorder(404)
    opened: list[dict] = []
    real = http.public_only_client

    def public_only_client(**kwargs):
        opened.append(kwargs)
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(http, "public_only_client", public_only_client)

    assert fetching.robots_allow("https://posting.example/jobs/1") is True
    assert opened, "it opened the guarded client"
    # Pinned, like every other guarded request: the socket goes to the approved address and
    # the site still sees its own name.
    assert seen[0].url.path == "/robots.txt"
    assert str(seen[0].url.host) == "93.184.216.34"
    assert seen[0].headers["Host"] == "posting.example"


# ------------------------------------------------------- the helper plugins without HTTP


def test_approve_host_hands_back_the_address_to_dial(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    from postulo.core import destinations

    monkeypatch.setattr(destinations, "addresses_for", lambda host: PUBLIC)

    assert http.approve_host("imap.example") == "93.184.216.34"


def test_approve_host_refuses_a_private_one_and_says_how_to_allow_it(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    from postulo.core import destinations

    monkeypatch.setattr(
        destinations, "addresses_for", lambda host: [ipaddress.ip_address("127.0.0.1")]
    )

    with pytest.raises(http.DestinationRefused) as refusal:
        http.approve_host("localhost")

    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in str(refusal.value)


def test_approve_host_obeys_the_operator_who_allowed_private_destinations(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    from postulo.core import destinations

    monkeypatch.setattr(
        destinations, "addresses_for", lambda host: [ipaddress.ip_address("192.168.1.50")]
    )

    assert http.approve_host("mailbox.lan") == "192.168.1.50"


def test_the_surface_offers_the_guard_to_plugins():
    from postulo.plugins import api

    assert api.approve_host is http.approve_host
    assert api.check_destination is http.check_destination
    assert api.DestinationRefused is http.DestinationRefused


# ------------------------------------------- an IPv6 address carrying an IPv4 one (#321)

#: The public half of a Teredo address when the other half is the one under test.
ELSEWHERE = ipaddress.IPv4Address("93.184.216.34")


def _v6(prefix: str, low: int) -> ipaddress.IPv6Address:
    return ipaddress.IPv6Address(int(ipaddress.IPv6Address(prefix)) | low)


def _inverted(v4: ipaddress.IPv4Address) -> int:
    return ~int(v4) & 0xFFFF_FFFF


#: Every way an IPv6 address carries an IPv4 one, as the address carrying ``v4``.
CARRIERS = {
    "IPv4-mapped": lambda v4: _v6("::ffff:0:0", int(v4)),
    "NAT64": lambda v4: _v6("64:ff9b::", int(v4)),
    "local NAT64": lambda v4: _v6("64:ff9b:1::", int(v4)),
    "IPv4-compatible": lambda v4: _v6("::", int(v4)),
    "IPv4-translated": lambda v4: _v6("::ffff:0:0:0", int(v4)),
    "6to4": lambda v4: _v6("2002::", int(v4) << 80 | 1),
    "Teredo server": lambda v4: _v6("2001::", int(v4) << 64 | _inverted(ELSEWHERE)),
    "Teredo client": lambda v4: _v6("2001::", int(ELSEWHERE) << 64 | _inverted(v4)),
}


def resolving_everything_to(monkeypatch, address) -> None:
    """Both resolvers, the capture check's and the one for everything that is not HTTP."""
    monkeypatch.setattr(
        public_addresses.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", (str(address), 0))],
    )
    monkeypatch.setattr(destinations, "addresses_for", lambda host: [address])


@pytest.mark.parametrize(
    "inside",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "0.0.0.0",  # noqa: S104 - an address to refuse, not one to bind
        "100.64.0.1",
        "172.16.0.1",
    ],
)
@pytest.mark.parametrize("form", CARRIERS)
def test_an_ipv6_address_carrying_a_private_one_is_refused(monkeypatch, form, inside):
    """``64:ff9b::a9fe:a9fe`` *is* the metadata service, on a host with NAT64.

    ``is_global`` passed it, because the NAT64 prefix is public; what it carries is not.
    Both guards ask the one function, so the capture check and the mail server's check refuse
    it alike.
    """
    address = CARRIERS[form](ipaddress.IPv4Address(inside))
    resolving_everything_to(monkeypatch, address)

    assert ipaddress.IPv4Address(inside) in destinations.carried_ipv4(address)
    assert not destinations.is_public(address)
    with pytest.raises(fetching.UnsafeURL, match="private or local"):
        fetching.public_addresses_for("https://looks-fine.example/jobs/1")
    with pytest.raises(destinations.Refused):
        destinations.approve("mail.looks-fine.example", allow_private=False)


@pytest.mark.parametrize(
    "form,allowed",
    [
        # The IPv4 address itself, written the way a dual-stack socket takes it.
        ("IPv4-mapped", True),
        # What DNS64 hands an IPv6-only host for every site that has only IPv4. Refusing it
        # would refuse most of the web to such a host.
        ("NAT64", True),
        # A network's own translator (RFC 8215), which the registry says is not globally
        # reachable -- it leads wherever that network's operator pointed it.
        ("local NAT64", False),
        # Deprecated since 2006; no public name answers with one.
        ("IPv4-compatible", False),
        # A stateless translator's (RFC 2765), in the IETF's reserved ::/8 like the one above.
        ("IPv4-translated", False),
        # The registry calls neither globally reachable, and nobody publishes a site there.
        ("6to4", False),
        ("Teredo server", False),
        ("Teredo client", False),
    ],
)
def test_carrying_a_public_address_passes_only_where_the_form_is_public(form, allowed):
    address = CARRIERS[form](ipaddress.IPv4Address("8.8.8.8"))

    assert destinations.is_public(address) is allowed


def test_the_retired_site_local_range_is_refused_and_ordinary_ipv6_is_not():
    """``fec0::/10`` was retired in 2004 and ``is_global`` calls it public; networks that never
    renumbered still route it inwards."""
    assert not destinations.is_public(ipaddress.ip_address("fec0::1"))
    assert destinations.is_public(ipaddress.ip_address("2001:4860:4860::8888"))
    assert destinations.is_public(ipaddress.ip_address("93.184.216.34"))


@pytest.mark.parametrize(
    "address",
    [
        "::1:0:a9fe:a9fe",
        "::abcd:0:7f00:1",
        "0:0:0:1::1",
        # One bit and one group past the well-known NAT64 prefix, each carrying 8.8.8.8:
        # the exception is `64:ff9b::/96` and not a bit wider.
        "64:ff9b:0:0:0:1:808:808",
        "64:ff9b:0:1::808:808",
    ],
)
def test_nothing_in_the_ietf_reserved_range_is_public(address):
    """``::/8`` holds the IPv4-compatible and IPv4-translated forms and whatever else a stack
    or a translator makes of it, and no public name answers with any of it. ``is_global``
    passes all of these."""
    assert ipaddress.ip_address(address).is_global, "what the registry alone would say"
    assert not destinations.is_public(ipaddress.ip_address(address))


#: ``ipaddress``'s IPv6 registry as Python 3.12.0 to 3.12.3 read it: before gh-113171, 6to4
#: and the local NAT64 prefix were global. ``requires-python`` no longer admits those, and the
#: decisions are not left to whichever registry the interpreter carries either.
OLD_REGISTRY = [
    ipaddress.IPv6Network(network)
    for network in (
        "::1/128",
        "::/128",
        "::ffff:0:0/96",
        "100::/64",
        "2001::/23",
        "2001:2::/48",
        "2001:db8::/32",
        "2001:10::/28",
        "fc00::/7",
        "fe80::/10",
    )
]


@pytest.mark.parametrize(
    "address",
    [
        # The local NAT64 prefix as a /48 (RFC 6052): 169.254.169.254 in bits 48-87, and the
        # last 32 bits -- the suffix -- 8.8.8.8, whatever the writer liked.
        "64:ff9b:1:a9fe:a9:fe00:808:808",
        "64:ff9b:1:a00:0:100:808:808",
        "2002:808:808::1",
        "2001:0:808:808::f7f7:f7f7",
    ],
)
def test_the_decisions_do_not_depend_on_which_python_reads_the_registry(monkeypatch, address):
    monkeypatch.setattr(ipaddress._IPv6Constants, "_private_networks", OLD_REGISTRY)
    monkeypatch.setattr(ipaddress._IPv6Constants, "_private_networks_exceptions", [])
    address = ipaddress.ip_address(address)
    if not address.teredo:
        assert address.is_global, "the old registry calls it global"

    assert not destinations.is_public(address)


def test_the_operator_who_allowed_private_destinations_still_reaches_them(monkeypatch, settings):
    """The switch is about connections, exactly as before; capture still refuses."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    address = CARRIERS["NAT64"](ipaddress.IPv4Address("192.168.1.50"))
    resolving_everything_to(monkeypatch, address)

    assert destinations.approve("nas.lan", allow_private=True) == address
    assert http.approve_host("nas.lan") == str(address)
    with pytest.raises(fetching.UnsafeURL):
        fetching.public_addresses_for("https://nas.lan/")


# ------------------------------------------- what comes back: bounded in size and time (#321)

CHUNK = 64 * 1024


class Trickle(httpx.SyncByteStream):
    """A body served a chunk at a time, keeping count of how much of it was ever asked for.

    ``tick`` runs before each chunk: a test passes one that moves the clock on, which is how a
    server sending slowly is written without anybody waiting for it.
    """

    def __init__(self, chunks: int, size: int = CHUNK, *, fill: bytes = b"a", tick=None):
        self.chunks, self.size, self.fill, self.tick = chunks, size, fill, tick
        self.sent = 0

    def __iter__(self):
        for _ in range(self.chunks):
            if self.tick:
                self.tick()
            self.sent += self.size
            yield self.fill * self.size


class Pieces(httpx.SyncByteStream):
    """A body served as the pieces given, keeping count of how much of it was ever asked for."""

    def __init__(self, pieces, *, tick=None):
        self.pieces, self.tick = pieces, tick
        self.sent = 0

    def __iter__(self):
        for piece in self.pieces:
            if self.tick:
                self.tick()
            self.sent += len(piece)
            yield piece


#: The start of a gzip stream whose header carries a comment (FLG.FCOMMENT). Everything up to
#: the comment's closing zero is read and inflates to nothing.
GZIP_WITH_A_COMMENT = b"\x1f\x8b\x08\x10\0\0\0\0\0\xff"


class Clock:
    """`time.monotonic` for `plugins.http`, moved on by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def stopped_clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(http, "time", types.SimpleNamespace(monotonic=clock))
    return clock


def resolved_publicly(host, *args, **kwargs):
    """`socket.getaddrinfo` where every name is public. The name is encoded first, as the real
    one encodes it, so a name that cannot be looked up fails here the way it fails there."""
    host.encode("idna")
    return [(None, None, None, "", ("93.184.216.34", 0))]


def serving(monkeypatch, answer) -> list[httpx.Request]:
    """Every name public, and every request answered by ``answer`` through the real guard."""
    monkeypatch.setattr(public_addresses.socket, "getaddrinfo", resolved_publicly)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return answer(request)

    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


#: The two fetches a stranger's server answers, each with its own limit and its own refusal.
FETCHES = {
    "a page": (fetching.fetch_page, fetching.MAX_BYTES, "text/html", fetching.FetchFailed),
    "a logo": (logos.download, logos.MAX_BYTES, "image/png", logos.UnusableLogo),
}


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_body_larger_than_the_limit_stops_at_the_limit(monkeypatch, what):
    """No length declared: the reading stops within one chunk of the limit, not at the end."""
    fetch, limit, kind, refusal = FETCHES[what]
    body = Trickle(chunks=limit // CHUNK * 20)
    serving(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": kind}, stream=body),
    )

    with pytest.raises(refusal, match="larger than"):
        fetch("https://blackmesa.test/big")

    assert limit < body.sent <= limit + CHUNK, "read past the limit by one chunk at most"
    assert body.sent < body.chunks * CHUNK, "and nowhere near the end"


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_body_declared_larger_than_the_limit_is_not_read_at_all(monkeypatch, what):
    fetch, limit, kind, refusal = FETCHES[what]
    body = Trickle(chunks=limit // CHUNK * 2)
    headers = {"Content-Type": kind, "Content-Length": str(limit * 2)}
    serving(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=body))

    with pytest.raises(refusal, match="larger than"):
        fetch("https://blackmesa.test/big")

    assert body.sent == 0, "refused on the header, before a byte"


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_content_length_that_lies_does_not_lift_the_limit(monkeypatch, what):
    fetch, limit, kind, refusal = FETCHES[what]
    body = Trickle(chunks=limit // CHUNK * 20)
    headers = {"Content-Type": kind, "Content-Length": "100"}
    serving(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=body))

    with pytest.raises(refusal, match="larger than"):
        fetch("https://blackmesa.test/small-it-says")

    assert body.sent <= limit + CHUNK


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_body_that_trickles_in_runs_out_of_time(monkeypatch, what):
    """Five seconds a kilobyte: never long enough between reads to trip a read timeout, and
    far too long in all. The deadline is the whole download's, checked between chunks."""
    fetch, _limit, kind, refusal = FETCHES[what]
    clock = stopped_clock(monkeypatch)

    def five_seconds_later():
        clock.now += 5

    body = Trickle(chunks=1000, size=1024, tick=five_seconds_later)
    serving(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": kind}, stream=body),
    )

    with pytest.raises(refusal, match="took longer than"):
        fetch("https://blackmesa.test/slow")

    assert body.sent <= 10 * 1024, "it stopped when the time ran out, not at the end"


@pytest.mark.django_db
def test_an_ordinary_page_arrives_whole_and_asks_only_for_what_it_unpacks(monkeypatch):
    page = (
        "<html><head><title>A role</title></head><body>" + "Work here. " * 5000 + "</body></html>"
    )
    seen = serving(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(page.encode())),
        ),
    )

    fetched = fetching.fetch_page("https://blackmesa.test/jobs/1")

    assert fetched.html == page
    assert fetched.url == "https://blackmesa.test/jobs/1"
    assert seen[-1].headers["Accept-Encoding"] == http.ACCEPT_ENCODING


def test_an_ordinary_logo_arrives_whole(monkeypatch):
    image = b"\x89PNG" + bytes(range(256)) * 400
    serving(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=image),
    )

    assert logos.download("https://blackmesa.test/logo.png") == image


@pytest.mark.django_db
def test_a_compressed_body_is_held_to_the_limit_as_it_inflates(monkeypatch):
    """Fifty megabytes of zeros is fifty kilobytes of gzip. httpx would inflate each chunk
    whole before anything could count it; the reader inflates a piece at a time instead."""
    bomb = gzip.compress(b"\0" * 50_000_000, compresslevel=9)
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Encoding": "gzip"},
            stream=httpx.ByteStream(bomb),
        ),
    )

    with pytest.raises(fetching.FetchFailed, match="larger than"):
        fetching.fetch_page("https://blackmesa.test/bomb")


def test_inflating_never_outruns_the_limit_by_more_than_a_piece():
    bomb = gzip.compress(b"\0" * 50_000_000, compresslevel=9)
    deadline = http.deadline_in(60)
    pieces = list(http._inflated(iter([bomb[:1000]]), len(bomb), deadline, bare=False))

    assert pieces, "it did inflate"
    assert max(len(piece) for piece in pieces) <= 64 * 1024


@pytest.mark.django_db
@pytest.mark.parametrize(
    "coding", ["br", "zstd", "compress", "gzip, gzip", "gzip, br", "identity, br", "UTF-8", "none"]
)
@pytest.mark.parametrize("what", FETCHES)
def test_an_encoding_nobody_asked_for_is_refused_unread(monkeypatch, what, coding):
    """httpx offers brotli when brotli is installed, and 317 bytes of it are 200 MB of zeros
    in one piece. It is no longer asked for, and an answer in it is not unpacked -- nor one in
    two codings at once, nor one in a coding nobody has heard of, which says nothing about
    what the bytes are. The person is told which, in a sentence of their own."""
    fetch, _limit, kind, refusal = FETCHES[what]
    body = Trickle(chunks=10)
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"Content-Type": kind, "Content-Encoding": coding}, stream=body
        ),
    )

    with pytest.raises(refusal) as refused:
        fetch("https://blackmesa.test/encoded")

    assert f"the content encoding “{coding.lower()}”, which Postulo does not unpack" in str(
        refused.value
    )
    assert body.sent == 0


@pytest.mark.django_db
def test_an_answer_that_says_it_is_not_encoded_is_read(monkeypatch):
    page = "<html><body>Plain, and it says so.</body></html>"
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Encoding": "identity"},
            content=page.encode(),
        ),
    )

    assert fetching.fetch_page("https://blackmesa.test/plain").html == page


def _bare_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


@pytest.mark.django_db
@pytest.mark.parametrize("wrapper", ["zlib", "bare"])
@pytest.mark.parametrize("arriving", ["whole", "a byte at a time"])
def test_deflate_is_read_with_its_wrapper_or_without(monkeypatch, wrapper, arriving):
    """``Content-Encoding: deflate`` means zlib-wrapped, and some servers send the bare stream.
    httpx read both, so pages from those servers captured before #321, and they still do --
    however the first bytes, which say which it is, are split across the network's chunks."""
    page = "<html><body>" + "Deflated. " * 2000 + "</body></html>"
    body = zlib.compress(page.encode()) if wrapper == "zlib" else _bare_deflate(page.encode())
    pieces = [body] if arriving == "whole" else [bytes([byte]) for byte in body]
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Encoding": "deflate"},
            stream=Pieces(pieces),
        ),
    )

    assert fetching.fetch_page("https://blackmesa.test/deflated").html == page


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_compressed_body_that_inflates_to_nothing_still_runs_out_of_time(monkeypatch, what):
    """A gzip header's comment is read and inflates to nothing, so a server sending one a byte
    at a time never produced a piece for the deadline to be checked against: 2 MB at ten
    seconds a byte. The deadline is checked for what arrives, not only for what it inflates
    to."""
    fetch, _limit, kind, refusal = FETCHES[what]
    clock = stopped_clock(monkeypatch)

    def five_seconds_later():
        clock.now += 5

    body = Pieces(
        itertools.chain([GZIP_WITH_A_COMMENT], itertools.repeat(b"c", 2000)),
        tick=five_seconds_later,
    )
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"Content-Type": kind, "Content-Encoding": "gzip"}, stream=body
        ),
    )

    with pytest.raises(refusal, match="took longer than"):
        fetch("https://blackmesa.test/slow-and-compressed")

    assert body.sent < 100, "it stopped when the time ran out, not at the end"


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_nothing_after_the_end_of_a_compressed_body_is_read(monkeypatch, what):
    """Whatever follows the end of a gzip stream is not part of it, and used to be read up to
    the limit -- or for as long as a server cared to trickle it. Reading stops at the end."""
    fetch, _limit, kind, _refusal = FETCHES[what]
    content = b"<html>the whole of it</html>"
    member = gzip.compress(content)
    body = Pieces(itertools.chain([member], itertools.repeat(b"z" * CHUNK, 100)))
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"Content-Type": kind, "Content-Encoding": "gzip"}, stream=body
        ),
    )

    fetched = fetch("https://blackmesa.test/and-then-some")

    assert getattr(fetched, "html", fetched) in (content, content.decode())
    assert body.sent == len(member), "not a byte past the end of the stream"


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_compressed_body_is_held_to_the_limit_before_it_inflates(monkeypatch, what):
    """A body that inflates to nothing -- here a gzip header's comment that never ends -- is
    still held to the limit, by what arrived rather than by what it made."""
    fetch, limit, kind, refusal = FETCHES[what]
    body = Pieces(
        itertools.chain([GZIP_WITH_A_COMMENT], itertools.repeat(b"c" * CHUNK, limit // CHUNK * 3))
    )
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"Content-Type": kind, "Content-Encoding": "gzip"}, stream=body
        ),
    )

    with pytest.raises(refusal, match="larger than"):
        fetch("https://blackmesa.test/all-header")

    assert limit < body.sent <= limit + CHUNK


@pytest.mark.django_db
@pytest.mark.parametrize("what", FETCHES)
def test_a_redirect_is_followed_by_name_and_its_body_is_never_read(monkeypatch, what):
    """Streamed, a redirect's body is closed unread -- httpx following on its own reads each
    one whole first. And the next hop is asked for by the site's name: the request that was
    redirected had been pinned to a number, and a relative address joined to that one asked
    the next hop for the number, with no name for TLS to prove, and the capture kept the
    number as the listing's address (the first half of #363)."""
    fetch, _limit, kind, _refusal = FETCHES[what]
    ballast = Trickle(chunks=1000)

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/moved":
            return httpx.Response(301, headers={"Location": "/here"}, stream=ballast)
        return httpx.Response(200, headers={"Content-Type": kind}, content=b"<html>here</html>")

    seen = serving(monkeypatch, answer)

    fetched = fetch("https://blackmesa.test/moved")

    if what == "a page":
        assert fetched.url == "https://blackmesa.test/here", "the address kept is the site's"
    assert ballast.sent == 0, "the redirect's body was never read"
    hops = [request for request in seen if request.url.path != "/robots.txt"]
    assert [request.url.path for request in hops] == ["/moved", "/here"]
    for request in hops:
        assert str(request.url.host) == "93.184.216.34", "each hop went through the guard"
        assert request.headers["Host"] == "blackmesa.test", "and asked for the site by name"
        assert request.extensions["sni_hostname"] == "blackmesa.test"


def answering_robots(monkeypatch, answer) -> list[httpx.Request]:
    """Every name public, and the client `robots_allow` opens for itself answered by
    ``answer``, through the real guard."""
    monkeypatch.setattr(public_addresses.socket, "getaddrinfo", resolved_publicly)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


@pytest.mark.django_db
def test_a_large_robots_txt_is_obeyed_as_far_as_its_limit(monkeypatch):
    """Asked first, of the same stranger, so read no further than RFC 9309's 500 KiB -- and
    what was read is obeyed, as the large crawlers obey it, rather than thrown away as if the
    site had said nothing. A length declared over the limit is no reason not to begin."""
    rules = b"User-agent: *\nDisallow: /private/\n"
    line = b"# " + b"x" * (CHUNK - 3) + b"\n"
    body = Pieces(
        itertools.chain([rules], itertools.repeat(line, fetching.ROBOTS_MAX_BYTES // CHUNK * 20))
    )
    headers = {"Content-Length": str(fetching.ROBOTS_MAX_BYTES * 20)}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers=headers, stream=body)
    )

    with httpx.Client(transport=transport) as client:
        assert fetching.robots_allow("https://blackmesa.test/private/job", client=client) is False

    assert body.sent <= fetching.ROBOTS_MAX_BYTES + CHUNK


@pytest.mark.django_db
def test_a_rule_the_limit_cuts_in_half_is_left_out(monkeypatch):
    """Half of ``Disallow: /private/`` is ``Disallow: /``, which says something else entirely."""
    head = b"User-agent: *\n"
    padding = b"#" * (fetching.ROBOTS_MAX_BYTES - len(head) - len(b"Disallow: /") - 1) + b"\n"
    body = head + padding + b"Disallow: /private/\n"
    assert body[: fetching.ROBOTS_MAX_BYTES].endswith(b"\nDisallow: /")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))

    with httpx.Client(transport=transport) as client:
        assert fetching.robots_allow("https://blackmesa.test/jobs/1", client=client) is True


@pytest.mark.django_db
def test_robots_txt_asks_only_for_what_it_can_read(monkeypatch):
    """Without a client of its own, httpx offered brotli, `read_body` refused the answer, and a
    refusal allows everything: a site answering in brotli had its ``Disallow`` ignored."""
    rules = b"User-agent: *\nDisallow: /\n"

    def answer(request: httpx.Request) -> httpx.Response:
        if "br" in request.headers.get("Accept-Encoding", ""):
            return httpx.Response(200, headers={"Content-Encoding": "br"}, content=b"\x0b\x80")
        return httpx.Response(200, content=rules)

    seen = answering_robots(monkeypatch, answer)

    assert fetching.robots_allow("https://blackmesa.test/jobs/1") is False
    assert seen[0].headers["Accept-Encoding"] == http.ACCEPT_ENCODING


@pytest.mark.django_db
def test_a_redirected_robots_txt_is_followed_without_reading_the_redirect(monkeypatch):
    """httpx following a redirect itself reads the redirect's body whole first, past every
    limit here, so each hop is followed by hand and closed unread (#364)."""
    ballast = Trickle(chunks=100)

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"Location": "/elsewhere.txt"}, stream=ballast)
        return httpx.Response(200, content=b"User-agent: *\nDisallow: /\n")

    seen = answering_robots(monkeypatch, answer)

    assert fetching.robots_allow("https://blackmesa.test/jobs/1") is False
    assert ballast.sent == 0, "the redirect's body was never read"
    assert [request.url.path for request in seen] == ["/robots.txt", "/elsewhere.txt"]


@pytest.mark.django_db
def test_a_robots_txt_that_redirects_for_ever_allows_after_the_limit(monkeypatch):
    seen = answering_robots(
        monkeypatch, lambda request: httpx.Response(302, headers={"Location": "/robots.txt"})
    )

    assert fetching.robots_allow("https://blackmesa.test/jobs/1") is True
    assert len(seen) == fetching.ROBOTS_MAX_REDIRECTS + 1


@pytest.mark.django_db
def test_a_compressed_robots_txt_that_inflates_to_nothing_runs_out_of_time(monkeypatch):
    clock = stopped_clock(monkeypatch)

    def five_seconds_later():
        clock.now += 5

    body = Pieces(
        itertools.chain([GZIP_WITH_A_COMMENT], itertools.repeat(b"c", 2000)),
        tick=five_seconds_later,
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=body)
    )

    with httpx.Client(transport=transport) as client:
        assert fetching.robots_allow("https://blackmesa.test/jobs/1", client=client) is True

    assert body.sent < 100, "it stopped when the time ran out, not at the end"


# ------------------------------------------- below the body: where httpcore reads alone (#321)

#: How long the stalling server keeps going: what a fetch that ignored its deadline would take.
STALL_SECONDS = 4.0
#: The whole download's time in the tests against it. They wait this long for real, each of
#: them, which is why it is short and why there are eight of them and no more.
BRIEFLY = 0.15


@pytest.fixture
def stalling_server(monkeypatch):
    """A server on loopback that begins an answer and then sends the rest a byte every 20 ms:
    never long enough between two to trip a read timeout, and for `STALL_SECONDS` in all.

    Where it stalls is ``server.stall``:

    * ``the headers`` -- or ``the headers of the second hop``, after a redirect, because the
      deadline is the whole download's and not each request's;
    * ``a compressed body``, in a gzip header's comment, which inflates to nothing;
    * ``the trailers`` after a chunked body whose data has all arrived.

    httpcore reads the first and the last without handing anything up, so nothing `read_body`
    checks between chunks ever runs, and a `MockTransport` reaches none of it: hence a socket.
    ``/robots.txt`` is a 404, unless ``server.robots`` says to stall there too.

    The guard is pointed at it -- ``blackmesa.test`` resolves to loopback, and passes.
    """
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    resolving(monkeypatch, {"blackmesa.test": [ipaddress.ip_address("127.0.0.1")]})
    monkeypatch.setattr(fetching, "validate_public_url", lambda url: url)

    listener = socket.create_server(("127.0.0.1", 0))
    server = types.SimpleNamespace(
        port=listener.getsockname()[1], stall="the headers", robots=False, asked=[]
    )

    def answer(connection: socket.socket) -> None:
        with connection:
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    received = connection.recv(65536)
                    if not received:
                        return
                    request += received
                path = request.split(b" ")[1]
                server.asked.append(path.decode())
                if path == b"/robots.txt" and not server.robots:
                    connection.sendall(
                        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    return
                if server.stall == "the headers of the second hop" and b"/there" not in path:
                    connection.sendall(
                        b"HTTP/1.1 302 Found\r\nLocation: /there" + path + b"\r\n"
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    return
                kind = b"image/png" if path.endswith(b".png") else b"text/html"
                head = b"HTTP/1.1 200 OK\r\nContent-Type: " + kind + b"\r\n"
                if server.stall == "a compressed body":
                    head += b"Content-Encoding: gzip\r\n\r\n" + GZIP_WITH_A_COMMENT
                elif server.stall == "the trailers":
                    head += b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\nX-Stalling: "
                else:
                    head += b"X-Stalling: "
                connection.sendall(head)
                ends = time.monotonic() + STALL_SECONDS
                while time.monotonic() < ends:
                    connection.sendall(b"c")
                    time.sleep(0.02)
            except OSError:
                # The client went away, which is what is hoped for.
                return

    def serve() -> None:
        while True:
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            threading.Thread(target=answer, args=(connection,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield server
    listener.close()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stall", ["the headers of the second hop", "a compressed body", "the trailers"]
)
@pytest.mark.parametrize("what", FETCHES)
def test_a_server_trickling_what_nothing_counts_still_runs_out_of_time(
    monkeypatch, stalling_server, what, stall
):
    """Every wait on the connection is cut to what is left of the download's time, so a
    server sending its headers, its trailers or a body that inflates to nothing a byte at a
    time -- each byte well inside the read timeout, for as long as it likes -- is given up on
    when the time runs out, and not `STALL_SECONDS` later. The trailers were the worst of
    them: the data had all arrived, and the fetch waited and then succeeded."""
    monkeypatch.setattr(fetching, "DOWNLOAD_SECONDS", BRIEFLY)
    monkeypatch.setattr(logos, "DOWNLOAD_SECONDS", BRIEFLY)
    stalling_server.stall = stall
    fetch, _limit, _kind, refusal = FETCHES[what]
    path = "/logo.png" if what == "a logo" else "/jobs/1"
    started = time.monotonic()

    with pytest.raises(refusal, match="took longer than"):
        fetch(f"http://blackmesa.test:{stalling_server.port}{path}")

    assert time.monotonic() - started < STALL_SECONDS / 2


@pytest.mark.django_db
@pytest.mark.parametrize("stall", ["the headers", "a compressed body"])
def test_a_robots_txt_trickled_the_same_way_is_given_up_on(monkeypatch, stalling_server, stall):
    """robots.txt is asked first, of the same stranger, and has its own, shorter time. One
    that does not arrive within it has disallowed nothing, as one that never answers has not."""
    monkeypatch.setattr(fetching, "ROBOTS_DOWNLOAD_SECONDS", BRIEFLY)
    stalling_server.stall, stalling_server.robots = stall, True
    started = time.monotonic()

    assert fetching.robots_allow(f"http://blackmesa.test:{stalling_server.port}/jobs/1") is True

    assert time.monotonic() - started < STALL_SECONDS / 2
    assert stalling_server.asked == ["/robots.txt"]


def test_every_wait_on_a_connection_is_cut_to_what_is_left(monkeypatch):
    """Connecting, the TLS handshake, every read and every write: each waits for the client's
    own timeout or for what is left of the download's time, whichever is shorter, and once
    nothing is left none of them waits at all."""
    clock = stopped_clock(monkeypatch)
    waits: list[tuple[str, float | None]] = []

    class Connection(httpcore.NetworkStream):
        def read(self, max_bytes, timeout=None):
            waits.append(("read", timeout))
            return b"x"

        def write(self, buffer, timeout=None):
            waits.append(("write", timeout))

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            waits.append(("handshake", timeout))
            return self

    class Backend(httpcore.NetworkBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            waits.append(("connect", timeout))
            return Connection()

    backend = http._BoundedBackend(Backend())

    backend.connect_tcp("93.184.216.34", 443, timeout=10.0).read(10, timeout=10.0)
    assert waits == [("connect", 10.0), ("read", 10.0)], "no download under way: its own"

    del waits[:]
    with http.until(http.deadline_in(4)):
        connection = backend.connect_tcp("93.184.216.34", 443, timeout=10.0)
        connection = connection.start_tls(None, "blackmesa.test", timeout=10.0)
        connection.write(b"x", timeout=10.0)
        connection.read(10, timeout=3.0)
        clock.now += 3.5
        connection.read(10, timeout=10.0)
        connection.read(10, timeout=None)
        clock.now += 1
        with pytest.raises(httpcore.ReadTimeout):
            connection.read(10, timeout=10.0)
        with pytest.raises(httpcore.WriteTimeout):
            connection.write(b"x", timeout=10.0)
        with pytest.raises(httpcore.ConnectTimeout):
            connection.start_tls(None, "blackmesa.test", timeout=10.0)
        with pytest.raises(httpcore.ConnectTimeout):
            backend.connect_tcp("93.184.216.34", 443, timeout=10.0)
    connection.read(10, timeout=10.0)

    assert waits == [
        ("connect", 4.0),
        ("handshake", 4.0),
        ("write", 4.0),
        ("read", 3.0),
        ("read", 0.5),
        ("read", 0.5),
        ("read", 10.0),
    ]


def test_a_proxy_from_the_environment_is_held_to_the_deadline_as_well(monkeypatch):
    """httpx opens a pool of its own for each proxy the environment names, beside the
    client's. Each of them hands out connections, so each of them is bounded."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")

    with http.public_only_client() as client:
        pools = [
            transport._pool
            for transport in (client._transport, *client._mounts.values())
            if transport is not None
        ]

    assert len(pools) > 1, "the proxy has a pool of its own"
    assert all(isinstance(pool._network_backend, http._BoundedBackend) for pool in pools)


# ----------------------------- whatever goes wrong is a refusal, never a crash (#321, #607)

#: The two fetches again, each with everything it may raise: `fetch_page`'s callers catch
#: `CaptureError` and nothing else, so anything else is a 500 from the API and a traceback
#: from the errand.
REFUSALS = {
    "a page": (fetching.fetch_page, CaptureError),
    "a logo": (logos.download, logos.UnusableLogo),
}


def a_name_that_changes_its_answer(monkeypatch, *, after: int, then: str) -> None:
    """The resolver answers publicly ``after`` times and from then on fails, or answers with
    loopback. A record with a one-second lifetime is free to do either between two lookups,
    and the client's hook looks every name up again for every request."""
    asked: list[str] = []

    def getaddrinfo(host, *args, **kwargs):
        asked.append(host)
        if len(asked) <= after:
            return [(None, None, None, "", ("93.184.216.34", 0))]
        if then == "fails":
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        return [(None, None, None, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(public_addresses.socket, "getaddrinfo", getaddrinfo)


def moved_once(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/moved":
        return httpx.Response(302, headers={"Location": "/here"})
    return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>here</html>")


SAYS = {"fails": "could not be resolved", "is private": "private or local network"}


@pytest.mark.django_db
@pytest.mark.parametrize("then", SAYS)
@pytest.mark.parametrize(
    "after",
    [
        1,  # looked up to validate the address; refused by the hook, for robots.txt and the page
        2,  # ... and for robots.txt; refused by the hook for the page
        3,  # ... and for the first hop; refused when the redirect's target is validated
        4,  # ... and to validate that; refused by the hook for the second hop
    ],
)
def test_a_name_that_changes_its_answer_between_lookups_is_a_capture_refusal(
    monkeypatch, after, then
):
    """The hook's refusal is `DestinationRefused`, which is neither a `CaptureError` nor an
    httpx error: it left `fetch_page` as itself, and the person read "Something went wrong"
    in place of the sentence the hook was carrying (#607)."""
    serving(monkeypatch, moved_once)
    a_name_that_changes_its_answer(monkeypatch, after=after, then=then)

    with pytest.raises(fetching.UnsafeURL, match=SAYS[then]):
        fetching.fetch_page("https://blackmesa.test/moved")


@pytest.mark.parametrize("then", SAYS)
@pytest.mark.parametrize("after", [0, 1])
def test_a_logo_from_a_name_that_changes_its_answer_is_refused_with_the_reason(
    monkeypatch, after, then
):
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/moved.png":
            return httpx.Response(302, headers={"Location": "/here.png"})
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG")

    serving(monkeypatch, answer)
    a_name_that_changes_its_answer(monkeypatch, after=after, then=then)

    with pytest.raises(logos.UnusableLogo, match=SAYS[then]):
        logos.download("https://blackmesa.test/moved.png")


@pytest.mark.django_db
@pytest.mark.parametrize("then", SAYS)
def test_a_robots_txt_on_a_name_that_changes_its_answer_disallows_nothing(monkeypatch, then):
    """Refused by the hook like any other request, and counted as a robots.txt that could not
    be read. The page's own request is refused by the same hook a moment later."""
    seen = answering_robots(monkeypatch, lambda request: httpx.Response(200, content=b"x"))
    a_name_that_changes_its_answer(monkeypatch, after=0, then=then)

    assert fetching.robots_allow("https://blackmesa.test/jobs/1") is True
    assert seen == [], "nothing was sent"


#: What a hostile page may put in ``Location``. The first four httpx cannot make an address
#: of and says so with `httpx.InvalidURL`, which is not an `httpx.HTTPError`; the names in
#: the next three cannot be encoded to be looked up, or are not valid punycode, and are a
#: `UnicodeError` from the resolver or from the ``idna`` package inside httpx; the last two
#: are urllib's, a port out of range and a bracket with no partner.
NOT_AN_ADDRESS = [
    "http:127.0.0.1/x",
    "https:127.0.0.1",
    "http:\\\\127.0.0.1\\",
    "javascript:alert(1)",
    "http://" + "a" * 64 + ".test/",
    "http://a..test/",
    "http://xn--zz.test/",
    "http://blackmesa.test:99999/",
    "http://[::1",
]


def redirecting_to(location: str):
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/start"):
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html></html>")

    return answer


@pytest.mark.django_db
@pytest.mark.parametrize("location", NOT_AN_ADDRESS)
@pytest.mark.parametrize("what", REFUSALS)
def test_a_redirect_to_something_that_is_not_an_address_is_a_refusal(monkeypatch, what, location):
    fetch, refusal = REFUSALS[what]
    seen = serving(monkeypatch, redirecting_to(location))

    with pytest.raises(refusal):
        fetch("https://blackmesa.test/start.png")

    assert [request.url.path for request in seen if request.url.path != "/robots.txt"] == [
        "/start.png"
    ], "and nothing was asked for after it"


@pytest.mark.parametrize(
    "address,says",
    [
        ("http://[::1", "complete web address"),
        ("http://[not-an-address]/", "complete web address"),
        ("https://blackmesa.test:99999/", "complete web address"),
        ("https://blackmesa.test:https/", "complete web address"),
        ("https://" + "a" * 64 + ".test/", "could not be resolved"),
        ("https://a..test/jobs/1", "could not be resolved"),
    ],
)
def test_an_address_nothing_can_parse_is_refused_where_it_is_checked(address, says):
    """urllib raises `ValueError` for a bracket with no partner, and for a port that is not a
    number in range once somebody asks for it; the resolver raises `UnicodeError` for a label
    it cannot encode. The check has a sentence for each, and its callers catch the check's
    own refusal: the capture form, the API, the hook on every request."""
    with pytest.raises(fetching.UnsafeURL, match=says):
        fetching.validate_public_url(address)


@pytest.mark.django_db
@pytest.mark.parametrize("charset", ["hex", "idna", "rot13", "undefined", "no-such-charset"])
def test_a_page_naming_a_character_set_that_is_not_one_is_read_as_utf_8(monkeypatch, charset):
    """The name in ``Content-Type`` is the site's to choose, and Python has codecs that are not
    character sets: decoding with ``hex`` is a `LookupError`, with ``idna`` a `UnicodeError`."""
    page = "<html><body>Développeuse, à Lyon.</body></html>"
    serving(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": f"text/html; charset={charset}"},
            content=page.encode(),
        ),
    )

    assert fetching.fetch_page("https://blackmesa.test/jobs/1").html == page


def test_a_content_length_that_is_no_number_does_not_stop_the_reading():
    """``²`` is a digit to `str.isdigit` and not a number to `int`. h11 refuses such a header
    before it gets this far; the reader does not rely on that."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers=[(b"Content-Length", "²".encode("latin-1"))], stream=Trickle(chunks=1)
        )
    )

    with (
        httpx.Client(transport=transport) as client,
        client.stream("GET", "https://blackmesa.test/") as response,
    ):
        body = http.read_body(response, limit=fetching.MAX_BYTES, deadline=http.deadline_in(60))

    assert len(body) == CHUNK


def _a_redirect_that_is_not_an_address(monkeypatch) -> None:
    serving(monkeypatch, redirecting_to("http:127.0.0.1/x"))


def _a_name_that_stops_resolving(monkeypatch) -> None:
    serving(monkeypatch, moved_once)
    a_name_that_changes_its_answer(monkeypatch, after=2, then="fails")


#: Two things a stranger's server can do to a capture that used to end in a crash.
CRASHED = {
    "a redirect that is not an address": _a_redirect_that_is_not_an_address,
    "a name that stops resolving": _a_name_that_stops_resolving,
}


@pytest.mark.django_db
@pytest.mark.parametrize("how", CRASHED)
def test_an_api_capture_of_a_hostile_page_answers_a_problem_not_a_crash(
    client, user, monkeypatch, how
):
    from postulo.api.models import ApiToken
    from postulo.jobs.models import Capture

    CRASHED[how](monkeypatch)
    _record, raw = ApiToken.issue(user, "Extension", scopes=("captures",))

    response = client.post(
        "/api/v1/captures",
        data='{"url": "https://blackmesa.test/start"}',
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )

    assert response.status_code == 422
    assert response["Content-Type"] == "application/problem+json"
    assert "could not be" in response.json()["detail"], "the sentence the person is owed"
    assert not Capture.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("how", CRASHED)
def test_a_capture_errand_on_a_hostile_page_ends_refused_rather_than_broken(
    user, monkeypatch, caplog, how
):
    from postulo.core import errands
    from postulo.core.models import ErrandState

    CRASHED[how](monkeypatch)

    errand = errands.send("capture", user, url="https://blackmesa.test/start")

    assert errand.state == ErrandState.FAILED
    assert "Something went wrong" not in errand.error
    assert "could not be" in errand.error, "the refusal's own sentence"
    assert not [record for record in caplog.records if record.exc_info], "and no traceback"


# ------------------------------------------------------- what the second review found


def test_the_back_off_between_retries_is_cut_to_what_is_left(monkeypatch):
    """httpcore sleeps between the attempts of a transport built with `retries`. Nobody asks
    for any today; one that did would otherwise sleep its way past the end of the download."""
    clock = stopped_clock(monkeypatch)
    slept: list[float] = []

    class Backend(httpcore.NetworkBackend):
        def sleep(self, seconds):
            slept.append(seconds)

    backend = http._BoundedBackend(Backend())

    backend.sleep(8.0)
    with http.until(http.deadline_in(2)):
        backend.sleep(8.0)
        backend.sleep(0.5)
        clock.now += 2
        with pytest.raises(httpcore.ConnectTimeout):
            backend.sleep(0.5)

    assert slept == [8.0, 2.0, 0.5]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "address", ["http://[::1", "https://[not-an-address]/x", "http://[127.0.0.1]/"]
)
def test_an_address_nothing_can_parse_has_no_robots_txt_to_forbid_it(address):
    """`urlparse` raises on these. `fetch_page` refuses such an address before it asks; a
    plugin that called this directly got the `ValueError`."""
    assert fetching.robots_allow(address) is True


def test_httpx_is_held_to_the_minor_whose_private_names_the_deadline_wraps():
    """`_hold_to_deadlines` reads a client's transports by httpx's private names, with a
    fallback for the tests' stand-in clients. A release that renamed them would leave every
    download with no deadline and no complaint, so the ceiling is raised by hand, with the
    socket tests above run against the new release."""
    import tomllib
    from pathlib import Path

    project = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    [httpx_line] = [line for line in project["project"]["dependencies"] if line.startswith("httpx")]
    assert httpx_line == "httpx>=0.28,<0.29", httpx_line
    client = httpx.Client()
    try:
        assert isinstance(client._transport, httpx.HTTPTransport), "the name the wrap reads"
        assert isinstance(client._mounts, dict)
    finally:
        client.close()


# ----------------------------------------- an approved name with one address that will not answer

UNREACHABLE = ipaddress.ip_address("2606:4700:4700::1111")
REACHABLE = ipaddress.ip_address("93.184.216.34")


def test_a_request_falls_back_to_the_next_approved_address(monkeypatch, settings):
    """Pinning to the first address lost the fallback a name's other addresses gave (#547).

    A dual-stack name whose IPv6 is broken answered with that address first, and every request
    to it timed out although the IPv4 one worked.
    """
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    resolving(monkeypatch, {"paperless.example": [UNREACHABLE, REACHABLE]})
    dialled: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        dialled.append(request.url.host)
        if request.url.host == str(UNREACHABLE):
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, content=b"ok")

    with http.client(transport=httpx.MockTransport(handler)) as client:
        response = client.get("https://paperless.example/api/")

    assert response.status_code == 200
    assert dialled == [str(UNREACHABLE), str(REACHABLE)], "each approved address, in order"
    assert response.request.headers["Host"] == "paperless.example"
    assert response.request.extensions["sni_hostname"] == "paperless.example", "TLS proves the name"


def test_a_request_with_no_address_that_answers_fails_as_it_did(monkeypatch, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    resolving(monkeypatch, {"paperless.example": [UNREACHABLE, REACHABLE]})
    dialled: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        dialled.append(request.url.host)
        raise httpx.ConnectError("refused", request=request)

    with (
        http.client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.ConnectError),
    ):
        client.get("https://paperless.example/api/")

    assert len(dialled) == 2, "both were tried, neither more than once"


def test_an_answer_that_is_an_error_is_not_tried_again_elsewhere(monkeypatch, settings):
    """Only a failure to connect moves on: a server that answered 500 was reached."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    resolving(monkeypatch, {"paperless.example": [REACHABLE, UNREACHABLE]})
    transport, seen = recorder(status=500)

    with http.client(transport=transport) as client:
        assert client.get("https://paperless.example/").status_code == 500

    assert len(seen) == 1


def test_a_mail_connection_falls_back_to_the_next_approved_address(monkeypatch):
    """The same for `mail.check_connection`, which dialled the first address only (#547)."""
    from postulo.core import mail

    monkeypatch.setattr(destinations, "addresses_for", lambda host: [UNREACHABLE, REACHABLE])
    dialled: list[str] = []

    def create_connection(address, timeout=None, source_address=None):
        dialled.append(address[0])
        if address[0] == str(UNREACHABLE):
            raise TimeoutError("timed out")
        return types.SimpleNamespace(
            close=lambda: None, sendall=lambda data: None, settimeout=lambda value: None
        )

    monkeypatch.setattr(socket, "create_connection", create_connection)
    replies = iter([(220, b"hello"), (250, b"ok"), (250, b"ok"), (221, b"bye")])
    monkeypatch.setattr("smtplib.SMTP.getreply", lambda self: next(replies))

    summary = mail.check_connection(
        host="mail.example.org", port=25, username="", password="", security="none", timeout=5
    )

    assert dialled == [str(UNREACHABLE), str(REACHABLE)]
    assert "Connected to mail.example.org:25" in summary


def test_a_mail_connection_that_reaches_no_address_says_why(monkeypatch):
    from postulo.core import mail

    monkeypatch.setattr(destinations, "addresses_for", lambda host: [UNREACHABLE, REACHABLE])

    def create_connection(address, timeout=None, source_address=None):
        raise ConnectionRefusedError(f"nobody at {address[0]}")

    monkeypatch.setattr(socket, "create_connection", create_connection)

    with pytest.raises(mail.ConnectionFailed) as failure:
        mail.check_connection(
            host="mail.example.org", port=25, username="", password="", security="none", timeout=5
        )

    assert "nobody at" in str(failure.value)


# ---------------------------------------------- what a refusal says, and only that (#384)


def test_a_host_that_does_not_resolve_is_not_told_about_the_private_address_policy(
    monkeypatch, settings
):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False

    def nothing(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(public_addresses.socket, "getaddrinfo", nothing)

    with pytest.raises(http.DestinationRefused) as refusal:
        http.check_destination("https://nowhere.example/hook")

    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" not in str(refusal.value)
    assert refusal.value.transient is True


def test_a_malformed_or_foreign_address_is_not_told_about_the_policy_either(settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    for url in ("ftp://files.example/x", "https://", "https://[::1/x"):
        with pytest.raises(http.DestinationRefused) as refusal:
            http.check_destination(url)
        assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" not in str(refusal.value), url


def test_a_private_address_for_a_connection_names_the_switch_and_does_not_advise_pasting(
    monkeypatch, settings
):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    monkeypatch.setattr(
        "postulo.plugins.public_addresses._addresses_for",
        lambda host: [ipaddress.ip_address("192.168.1.10")],
    )

    with pytest.raises(http.DestinationRefused) as refusal:
        http.check_destination("https://nas.home/hook")

    message = str(refusal.value)
    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in message
    assert "posting" not in message and "Paste" not in message
    assert refusal.value.transient is False
