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

Nothing here leaves the machine: the resolver is stubbed, and every response comes from a
`MockTransport`.
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from postulo.jobs import logos
from postulo.plugins import fetching, http

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
            raise fetching.UnsafeURL("That address is on a private or local network.")
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
