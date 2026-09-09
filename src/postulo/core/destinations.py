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
"""

from __future__ import annotations

import ipaddress
import smtplib
import socket

from django.utils.translation import gettext as _

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class Refused(Exception):
    """This host may not be dialled under the instance's policy."""


class Unresolvable(Exception):
    """The name does not resolve. Kept apart from `Refused`: different fact, different fix."""


def addresses_for(host: str) -> list[Address]:
    """Every address this name answers with, in whatever order the resolver gave them."""
    host = (host or "").strip()
    if not host:
        raise Unresolvable(str(_("No server to connect to.")))
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
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
    private = [address for address in found if not address.is_global]
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
    """The operator's one decision, reused rather than reinvented."""
    from postulo.plugins.http import private_destinations_allowed

    return private_destinations_allowed()


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
