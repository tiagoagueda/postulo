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


class ConnectionFailed(Exception):
    """The SMTP server could not be reached, negotiated with, or logged in to."""


def check_connection(
    *, host: str, port: int, username: str, password: str, use_tls: bool, timeout: int
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
    if not host:
        raise ConnectionFailed(str(_("No server to connect to.")))
    try:
        with smtplib.SMTP(host=host, port=port, timeout=timeout) as server:
            server.ehlo()
            if use_tls:
                if not server.has_extn("starttls"):
                    raise ConnectionFailed(str(NO_STARTTLS))
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username:
                server.login(username, password)
            server.noop()
    except ConnectionFailed:
        raise
    except smtplib.SMTPAuthenticationError as error:
        raise ConnectionFailed(
            str(_("The server refused those credentials: %(detail)s"))
            % {"detail": _describe(error)}
        ) from error
    except (OSError, smtplib.SMTPException, ssl.SSLError) as error:
        raise ConnectionFailed(f"{type(error).__name__}: {error}") from error

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


def _describe(error) -> str:
    """The server's own words, which are what an administrator actually needs."""
    detail = getattr(error, "smtp_error", b"")
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    return detail or str(error)
