"""Where the server is allowed to dial, for the protocols that are not HTTP.

`plugins/http.py` and `plugins/fetching.py` already do this carefully for a URL somebody
typed, and none of it is on the path a mail backend takes: Django opens a socket to a host
and a port and asks nothing. That was fine while the host was always the operator's own. It
stops being fine the moment a person can type one (#149), and the guard has to exist before
the field does.

Three things the HTTP guard does that a naive connection would not, and all three are here.

**Refuse private and loopback addresses** unless the operator has said otherwise. The switch
is the one that already exists — `POSTULO_CONNECTIONS_ALLOW_PRIVATE` — because somebody whose
mail server is on their own network is the same case as their Paperless on the same network,
and one decision made once by the person who owns the machine beats two.

**Check every address the name answers with**, not the first. A name that resolves to one
public and one private address is otherwise a way straight through.

**Connect to the address that was checked.** This is the one people leave out and it is the
one that matters: a name with a one-second lifetime is free to answer publicly for the check
and privately a moment later, and the check would have passed on an address nobody contacted.
So the approval returns an address, and the caller dials *that* while still proving the
certificate against the name a person typed.

**The refusal is decided before anything is dialled**, which is what keeps this from being a
port scanner with a form around it. Every private address gets the same answer whether
something is listening on it or not, because nothing ever finds out.

**What "private" means is decided once, here**, by `is_public`, and the HTTP guard in
`plugins/public_addresses.py` asks the same function: two copies of a list of ranges are two
lists that drift apart (#321).
"""

from __future__ import annotations

import ipaddress
import smtplib
import socket

from django.conf import settings
from django.utils.translation import gettext as _

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class Refused(Exception):
    """This host may not be dialled under the instance's policy."""


class Unresolvable(Exception):
    """The name does not resolve. Kept apart from `Refused`: different fact, different fix."""


# ------------------------------------------------------------- what counts as public

#: NAT64's well-known prefix (RFC 6052): what DNS64 hands an IPv6-only host for a site that
#: has only IPv4. It sits in ``::/8`` -- its first sixteen bits are ``0064`` -- and is the one
#: part of that range judged by the address it carries rather than refused with the rest.
_WELL_KNOWN_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")

#: IPv6 ranges an IPv4 address travels inside, in its last 32 bits. An address in one of them
#: reaches that IPv4 address by whatever path the host has -- on a host with NAT64,
#: ``64:ff9b::a9fe:a9fe`` *is* ``169.254.169.254``, the cloud metadata service -- so what it
#: carries is judged as well as the IPv6 address around it (#321). The IPv4-mapped form, 6to4
#: and Teredo are read with `ipaddress`'s own properties in `carried_ipv4`.
_CARRIES_IN_LAST_32_BITS = (
    _WELL_KNOWN_NAT64,
    # NAT64 for a network's own translator (RFC 8215). An operator may pick a prefix other
    # than a /96 inside it, which puts the IPv4 address elsewhere; the range is in
    # `_NEVER_PUBLIC` for that reason, so reading the common case is all this is for.
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    ipaddress.IPv6Network("::/96"),  # IPv4-compatible (RFC 4291, deprecated)
    ipaddress.IPv6Network("::ffff:0:0:0/96"),  # IPv4-translated (RFC 2765, SIIT)
)

#: Refused whatever they hold. Listed here rather than left to `ipaddress`, whose answer has
#: changed with the interpreter: before 3.12.4 it called the local NAT64 prefix and 6to4
#: global. `requires-python` no longer admits those, and the decision does not rest on that
#: either (#321).
_NEVER_PUBLIC = (
    # Site-local, retired by RFC 3879 and still routed inside networks that never renumbered.
    ipaddress.IPv6Network("fec0::/10"),
    # Reserved by the IETF. It holds the IPv4-compatible form (deprecated since 2006) and the
    # IPv4-translated one (RFC 2765), which some stacks and stateless translators carry to
    # the address inside, and `is_global` passes nearly all of it. No public name answers
    # with an address in it, bar the two forms `is_public` reads before it looks here: the
    # IPv4-mapped one and `_WELL_KNOWN_NAT64`.
    ipaddress.IPv6Network("::/8"),
    # A network's own NAT64 translator (RFC 8215). A prefix shorter than a /96 puts the IPv4
    # address in the middle and leaves the last 32 bits to whoever wrote the address, so what
    # `carried_ipv4` reads there says nothing about where it leads.
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    ipaddress.IPv6Network("2002::/16"),  # 6to4 (RFC 3056), deprecated by RFC 7526
    ipaddress.IPv6Network("2001::/32"),  # Teredo (RFC 4380)
)


def carried_ipv4(address: Address) -> list[ipaddress.IPv4Address]:
    """Every IPv4 address ``address`` carries inside it, in each form that carries one.

    IPv4-mapped (``::ffff:0:0/96``), NAT64 (``64:ff9b::/96`` and ``64:ff9b:1::/48``),
    IPv4-compatible (``::/96``), IPv4-translated (``::ffff:0:0:0/96``), 6to4 (``2002::/16``,
    the IPv4 address in bits 16-47) and Teredo (``2001::/32``: the server in bits 32-63, the
    client in the last 32, inverted). An IPv4 address carries nothing.
    """
    if isinstance(address, ipaddress.IPv4Address):
        return []
    if address.ipv4_mapped is not None:
        return [address.ipv4_mapped]
    if address.sixtofour is not None:
        return [address.sixtofour]
    if address.teredo is not None:
        return list(address.teredo)
    if any(address in network for network in _CARRIES_IN_LAST_32_BITS):
        return [ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)]
    return []


def is_public(address: Address) -> bool:
    """Whether ``address`` is on the public internet: the one question every guard asks.

    ``is_global`` -- the IANA special-purpose registries, as `ipaddress` reads them -- answers
    it for almost every address. What it misses is here:

    * **an IPv6 address carrying an IPv4 one** is only as public as what it carries, so a
      NAT64 or 6to4 address holding ``127.0.0.1`` is refused however public its prefix is;
    * **the IPv4-mapped form** is not an IPv6 destination at all, but the IPv4 address written
      the way a dual-stack socket takes it, so it is judged by that address alone;
    * **site-local addresses and the rest of ``::/8``**, which ``is_global`` passes.

    So a mapped or a well-known NAT64 address carrying a public one is allowed -- the second
    is what DNS64 hands an IPv6-only host for every site that has only IPv4, and refusing it
    would refuse most of the web there. The local NAT64 prefix, 6to4 and Teredo are refused
    whatever they carry, in `_NEVER_PUBLIC` rather than by the registry, so the answer does
    not change with the interpreter.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public(address.ipv4_mapped)
        if address not in _WELL_KNOWN_NAT64 and any(
            address in network for network in _NEVER_PUBLIC
        ):
            return False
        if not all(is_public(carried) for carried in carried_ipv4(address)):
            return False
    return address.is_global


def addresses_for(host: str) -> list[Address]:
    """Every address this name answers with, in whatever order the resolver gave them."""
    host = (host or "").strip()
    if not host:
        raise Unresolvable(str(_("No server to connect to.")))
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as error:
        # `UnicodeError` for a name the resolver cannot encode at all: an empty label
        # (`a..b`) or one over 63 characters. It is not a `gaierror`, and uncaught it left
        # the mail guard with a traceback where a refusal belongs (#321).
        raise Unresolvable(str(_("%(host)s could not be looked up.") % {"host": host})) from error
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def approve(host: str, *, allow_private: bool) -> Address:
    """The address to dial, or a refusal that never touched the network.

    Returns one of the resolved addresses. Which one does not matter — every one of them had
    to pass — and returning it rather than the name is the whole point: the caller connects
    to this, not to whatever the resolver says a second from now.
    """
    found = addresses_for(host)
    if not found:
        raise Unresolvable(str(_("%(host)s could not be looked up.") % {"host": host}))
    if allow_private:
        return found[0]
    private = [address for address in found if not is_public(address)]
    if private:
        raise Refused(
            str(
                _(
                    "%(host)s is on a private or local network. Postulo only connects to "
                    "one when the operator sets POSTULO_CONNECTIONS_ALLOW_PRIVATE=true."
                )
                % {"host": host}
            )
        )
    return found[0]


def private_allowed() -> bool:
    """The operator's one decision, ``POSTULO_CONNECTIONS_ALLOW_PRIVATE``, read in one place.

    Here rather than in `plugins.http`, which asks this module in turn: the plugin client and
    the mail check are two guards and one switch, and the switch belongs below both (#248).
    """
    return bool(getattr(settings, "POSTULO_CONNECTIONS_ALLOW_PRIVATE", False))


# ------------------------------------------------- dialling an address, proving a name


class _ProvesTheName:
    """Dial the address that was approved; prove the certificate against the name typed.

    `smtplib` takes the server name for TLS from whatever it was told to connect to, which
    is the address once the connection is pinned — and a certificate checked against a
    number never matches. So the name is carried separately and put back before any wrapping
    happens. `plugins/http.py` solves the same problem with `sni_hostname`, for the same
    reason and in the same shape.
    """

    def __init__(self, *args, certificate_name: str = "", **kwargs):
        self._certificate_name = certificate_name
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        # Before `super()`, deliberately: for an implicit-TLS connection this is the call
        # that wraps the socket, and it reads the name from the attribute set here.
        if self._certificate_name:
            self._host = self._certificate_name
        return super()._get_socket(host, port, timeout)


class PinnedSMTP(_ProvesTheName, smtplib.SMTP):
    pass


# Named after the class it wraps, which is why it is not CamelCase.
class PinnedSMTP_SSL(_ProvesTheName, smtplib.SMTP_SSL):
    pass
