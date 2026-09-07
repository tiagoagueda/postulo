"""Sending mail with the settings that are in force now, not the ones that were at boot.

``MAILERS`` is built once, when the settings module is imported. That is fine for a value
that only ever comes from the environment and fatal for one an administrator can change
from a page: the form would save, the page would say so, and every message would keep going
to the old server until somebody restarted the container. A page that appears to work and
does not is worse than no page.

Django builds a fresh backend for every send — ``MailersHandler.create_connection`` caches
nothing — so a backend that resolves its own settings in ``__init__`` picks the new ones up
on the next message, with no restart and no signal to wire up. That is what this is.

The from-address is here for the same reason. ``DEFAULT_FROM_EMAIL`` is read at send time
by everything that sends anything, Django's and allauth's code included, and there is no
hook in any of it. The backend is the one place every message passes through.
"""

from __future__ import annotations

import smtplib
import ssl

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend
from django.utils.translation import gettext_lazy as _

from . import site


class SiteSMTPBackend(EmailBackend):
    """SMTP, configured from the environment and the Server settings page together.

    Takes no ``OPTIONS``: they would be the frozen values this exists to avoid, and
    accepting them would leave two places to look when the wrong server is being used.
    """

    def __init__(self, **kwargs):
        for name in ("host", "port", "username", "password", "use_tls", "timeout"):
            kwargs.pop(name, None)
        resolved = site.email_settings()
        self.from_address = resolved["from_address"]
        super().__init__(
            host=resolved["host"],
            port=resolved["port"],
            username=resolved["username"],
            password=resolved["password"],
            use_tls=resolved["use_tls"],
            timeout=resolved["timeout"],
            **kwargs,
        )

    def send_messages(self, email_messages):
        """Stamp the resolved from-address on anything that did not choose one.

        "Did not choose one" means the address Django fills in from ``DEFAULT_FROM_EMAIL``
        when a caller passes none. A message that names its own sender keeps it: this is
        for the ninety-nine that do not, not a rule about who Postulo may send as.
        """
        for message in email_messages:
            if message.from_email == settings.DEFAULT_FROM_EMAIL:
                message.from_email = self.from_address
        return super().send_messages(email_messages)


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
