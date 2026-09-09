"""Proving a set of SMTP settings without sending anybody a message.

The backend that *uses* them lives in `postulo.notifications.transport`, because which
transport carries the mail is a plugin question now (#104) and this is not. What is left
here is the thing the Email page needs and the SMTP transport's `test()` calls: open a
socket, negotiate, sign in, hang up.
"""

from __future__ import annotations

import smtplib
import ssl

from django.utils.translation import gettext_lazy as _

#: Split out only because it does not fit on a line inside the branch that raises it.
NO_STARTTLS = _("The server does not offer STARTTLS. Turn it off, or use a port that does.")

#: The port each kind of connection is conventionally offered on. Used only to explain a
#: failure -- never to refuse one, because a relay on a port of its own is ordinary.
CONVENTIONAL_PORTS = {"none": (25,), "starttls": (587, 25), "ssl": (465,)}


class ConnectionFailed(Exception):
    """The SMTP server could not be reached, negotiated with, or logged in to."""


def _mismatch(security: str, port: int) -> str:
    """A sentence about why a connection to this port, done this way, hangs.

    Both kinds of failure look identical from here -- a wait, then a disconnection -- and
    the wait is the worst part of it. Pointing one at the other's port is not an exotic
    mistake: the two ports are documented interchangeably by half the providers there are.
    """
    if security != "ssl" and port == 465:
        return str(
            _(
                "Port 465 expects TLS from the first byte, so it is waiting for a "
                "certificate while Postulo waits for a greeting. Choose “TLS from the "
                "first byte”, or use port 587 with STARTTLS."
            )
        )
    if security == "ssl" and port in (587, 25):
        return str(
            _(
                "Port %(port)s expects a connection in the clear that is upgraded "
                "afterwards. Choose “STARTTLS, after connecting”, or use port 465."
            )
            % {"port": port}
        )
    return ""


def check_connection(
    *, host: str, port: int, username: str, password: str, security: str, timeout: int
) -> str:
    """Prove a set of SMTP settings without sending a message to anybody.

    Connect, say hello, start TLS if asked, log in if there is a username, ask the server
    to do nothing, and hang up. That exercises every part of the configuration a real send
    depends on — reachability, the certificate, the credentials — and involves no third
    party who then has to be told to ignore an email.

    The values are the caller's, deliberately, so that a configuration can be tried before
    it is saved. Testing what is stored would mean overwriting whatever works in order to
    find out whether the replacement does.

    The address is not checked against the private-address rule that governs capture. That
    rule exists because a capture URL comes off a stranger's page; this is an administrator
    typing their own infrastructure, and a relay on 10.0.0.0/8 is the ordinary case for a
    self-hosted instance. Do not "fix" this by reusing the capture check.
    """
    from . import destinations

    if not host:
        raise ConnectionFailed(str(_("No server to connect to.")))
    try:
        with _open(host, port, security, timeout) as server:
            server.ehlo()
            if security == "starttls":
                if not server.has_extn("starttls"):
                    raise ConnectionFailed(str(NO_STARTTLS))
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username:
                server.login(username, password)
            server.noop()
    except ConnectionFailed:
        raise
    except (destinations.Refused, destinations.Unresolvable) as error:
        # Decided before anything was dialled, so this says the same thing whether or not
        # something is listening there. That is what stops the test button being a port
        # scanner with a form around it (#148).
        raise ConnectionFailed(str(error)) from error
    except smtplib.SMTPAuthenticationError as error:
        raise ConnectionFailed(
            str(_("The server refused those credentials: %(detail)s"))
            % {"detail": _describe(error)}
        ) from error
    except (OSError, smtplib.SMTPException, ssl.SSLError) as error:
        # The hint goes first: `timed out` is true and useless, and somebody who has just
        # waited ten seconds for it deserves the sentence that names the actual problem.
        hint = _mismatch(security, port)
        detail = f"{type(error).__name__}: {error}"
        raise ConnectionFailed(f"{hint} ({detail})" if hint else detail) from error

    if username:
        return str(_("Connected to %(host)s:%(port)s and signed in as %(user)s.")) % {
            "host": host,
            "port": port,
            "user": username,
        }
    return str(_("Connected to %(host)s:%(port)s. No username, so nothing was signed in.")) % {
        "host": host,
        "port": port,
    }


def host_policy() -> bool:
    """Whether this instance may dial a private address for mail.

    The environment is exempt, and that is the project's ordinary rule rather than a hole in
    this one: `POSTULO_EMAIL_HOST` is a line in a file only the operator can edit, and the
    default it carries is `localhost`. Checking the operator's own file would refuse the
    default configuration of every instance that has never opened the Email page.

    A host stored from the page is checked, because that page is about to be reachable by
    somebody who is not the operator (#149) and the guard has to exist before the field does.
    """
    from . import destinations, site

    return destinations.private_allowed() or site.overridden_by("email_host") is not None


def _open(host: str, port: int, security: str, timeout: int):
    """The socket, opened the way this kind of connection is opened, to an approved address.

    Implicit TLS is a different constructor rather than a flag, because the handshake happens
    before any SMTP is spoken; there is no point in the conversation at which a plain `SMTP`
    object could be persuaded into it.

    Both dial the *address* that was approved and prove the certificate against the *name*
    that was typed. Resolving again here would reopen the window the approval closed (#148).
    """
    from . import destinations

    address = destinations.approve(host, allow_private=host_policy())
    if security == "ssl":
        return destinations.PinnedSMTP_SSL(
            host=str(address),
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
            certificate_name=host,
        )
    return destinations.PinnedSMTP(
        host=str(address), port=port, timeout=timeout, certificate_name=host
    )


def _describe(error) -> str:
    """The server's own words, which are what an administrator actually needs."""
    detail = getattr(error, "smtp_error", b"")
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    return detail or str(error)
