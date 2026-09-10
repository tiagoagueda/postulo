"""Proving a set of SMTP settings without sending anybody a message.

The backend that *uses* them lives in `postulo.notifications.transport`, because which
transport carries the mail is a plugin question now (#104) and this is not. What is left
here is the thing the Email page needs and the SMTP transport's `test()` calls: open a
socket, negotiate, sign in, hang up.
"""

from __future__ import annotations

import smtplib
import ssl

from django.core.mail.backends.smtp import EmailBackend
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _

#: Split out only because it does not fit on a line inside the branch that raises it.
NO_STARTTLS = _("The server does not offer STARTTLS. Turn it off, or use a port that does.")

#: The port each kind of connection is conventionally offered on. Used only to explain a
#: failure -- never to refuse one, because a relay on a port of its own is ordinary.
CONVENTIONAL_PORTS = {"none": (25,), "starttls": (587, 25), "ssl": (465,)}


class MailSecurity(TextChoices):
    """How TLS gets onto an SMTP session. Two ways, and they are not interchangeable.

    STARTTLS connects in the clear and asks the server to upgrade the socket; implicit TLS
    hands over a certificate before a byte of SMTP is spoken. Point one at the other's port
    and nothing happens until the timeout, because each is waiting for the other to speak.
    """

    NONE = "none", _("None")
    STARTTLS = "starttls", _("STARTTLS, after connecting")
    SSL = "ssl", _("TLS from the first byte")


#: The port each kind of connection is normally offered on. A suggestion, filled in when
#: nobody typed one -- never a correction of a port somebody did type, because a relay on a
#: port of its own is an ordinary thing for a self-hosted instance to have.
DEFAULT_MAIL_PORTS = {
    MailSecurity.NONE: 25,
    MailSecurity.STARTTLS: 587,
    MailSecurity.SSL: 465,
}


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
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    security: str,
    timeout: int,
    token: str = "",
) -> str:
    """Prove a set of SMTP settings without sending a message to anybody.

    Connect, say hello, start TLS if asked, log in if there is a username, ask the server
    to do nothing, and hang up. That exercises every part of the configuration a real send
    depends on — reachability, the certificate, the credentials — and involves no third
    party who then has to be told to ignore an email.

    ``token`` signs in with XOAUTH2 instead of the password, which is what Microsoft 365
    requires from the end of December 2026 (#151). The same call, so the button that proves a
    password configuration proves an OAuth one too, and they cannot disagree about how.

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
            from . import mail_auth

            mail_auth.authenticate(server, username=username, password=password, token=token)
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

    if token:
        return str(_("Connected to %(host)s:%(port)s and signed in as %(user)s with a token.")) % {
            "host": host,
            "port": port,
            "user": username,
        }
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


# --------------------------------------------------- dialling only where allowed


class GuardedBackend(EmailBackend):
    """Django's SMTP backend, dialling only where it is allowed to dial.

    Here rather than in either plugin that uses it, because it is the same guard for both:
    the instance sending as itself and a person sending as themselves are two callers of one
    rule, and a rule with two implementations is a rule with one of them out of date (#149).

    Django resolves the host inside `open()` and hands it straight to `smtplib`. That is the
    right thing for a backend whose host is a settings constant and the wrong thing for one
    whose host somebody typed, which is what the Email page makes it and what #149 makes it
    for everybody. So the address is approved first and the connection is pinned to it, with
    the name kept for the certificate (#148).
    """

    def __init__(self, *args, oauth_token: str = "", **kwargs):
        """``oauth_token`` signs in with XOAUTH2 rather than the password (#151).

        Django authenticates inside `open()` and only with a password, before it publishes
        the connection. So a token session is opened with no password -- Django then signs
        in with nothing -- and authenticated here straight afterwards, on the same socket.
        A token wins where both were given, for the reason `mail_auth.authenticate` says.
        """
        super().__init__(*args, **kwargs)
        self.oauth_token = oauth_token
        if oauth_token:
            self.password = ""

    def open(self):
        import functools

        from postulo.core import destinations, mail, mail_auth

        if self.connection:
            return False
        typed = self.host
        approved = destinations.approve(typed, allow_private=mail.host_policy())
        pinned = destinations.PinnedSMTP_SSL if self.use_ssl else destinations.PinnedSMTP
        self.host = str(approved)
        self._pinned_class = functools.partial(pinned, certificate_name=typed)
        try:
            opened = super().open()
        finally:
            # Put the name back, so anything reading the backend afterwards -- a log line,
            # a summary on a page -- says the server somebody configured rather than a
            # number nobody typed.
            self.host = typed
            self._pinned_class = None
        if opened and self.oauth_token:
            try:
                mail_auth.authenticate(
                    self.connection, username=self.username, token=self.oauth_token
                )
            except OSError:
                # `smtplib`'s errors are `OSError`s, so this is the same net Django casts.
                # An unauthenticated session is closed rather than left to try a send and
                # fail again with a less useful message.
                self.close()
                if not self.fail_silently:
                    raise
                return None
        return opened

    @property
    def connection_class(self):
        return getattr(self, "_pinned_class", None) or super().connection_class
