"""The transport that ships in the box: SMTP, over a socket, the way mail has always gone.

It is a plugin like any other so that nothing about it is special — the same declared
fields, the same Test button, the same identity — and it is *internal* so that an instance
with a relay needs to install nothing. Postulo ships exactly one transport and names no
vendor; anything that speaks an HTTP API instead is somebody else's package, which is the
whole argument for the kind (#104).

Its configuration is the one exception to how a transport is configured. Every other
transport declares fields and Postulo stores the answers; SMTP keeps the named columns on
the policy row, because those came first (#84), because each is overridden individually by
its own environment variable, and because a fresh instance has to be able to send a
verification email before there is a database row to read.
"""

from __future__ import annotations

from django.core.mail.backends.smtp import EmailBackend
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from postulo.plugins.base import FieldSpec, TestResult, declares, shipped


class GuardedBackend(EmailBackend):
    """Django's SMTP backend, dialling only where this instance is allowed to dial.

    Django resolves the host inside `open()` and hands it straight to `smtplib`. That is the
    right thing for a backend whose host is a settings constant and the wrong thing for one
    whose host somebody typed, which is what the Email page makes it and what #149 makes it
    for everybody. So the address is approved first and the connection is pinned to it, with
    the name kept for the certificate (#148).
    """

    def open(self):
        import functools

        from postulo.core import destinations, mail

        if self.connection:
            return False
        typed = self.host
        approved = destinations.approve(typed, allow_private=mail.host_policy())
        pinned = destinations.PinnedSMTP_SSL if self.use_ssl else destinations.PinnedSMTP
        self.host = str(approved)
        self._pinned_class = functools.partial(pinned, certificate_name=typed)
        try:
            return super().open()
        finally:
            # Put the name back, so anything reading the backend afterwards -- a log line,
            # a summary on a page -- says the server somebody configured rather than a
            # number nobody typed.
            self.host = typed
            self._pinned_class = None

    @property
    def connection_class(self):
        return getattr(self, "_pinned_class", None) or super().connection_class


@declares(
    shipped(
        name="smtp",
        label="SMTP",
        kind="transport",
        description=_lazy("Sends mail over SMTP, the way a mail server expects to be spoken to."),
    )
)
class SMTPTransport:
    """Delivery over SMTP, configured from the environment and the Email page together."""

    #: This transport's settings are the named columns on the policy row rather than the
    #: generic blob, so the Email page draws them itself and this stays empty. The method
    #: is here because the protocol asks for it and because a page that iterated the fields
    #: of every transport should get an honest answer for this one too: *nothing to ask
    #: here, it is asked above*.
    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> TestResult:
        from postulo.core import mail

        try:
            report = mail.check_connection(
                host=str(config.get("host") or ""),
                port=int(config.get("port") or 25),
                username=str(config.get("username") or ""),
                password=str(config.get("password") or ""),
                security=str(config.get("security") or ""),
                timeout=int(config.get("timeout") or 10),
            )
        except mail.ConnectionFailed as error:
            return TestResult(False, str(error))
        return TestResult(True, report)

    def deliver(self, messages: list, config: dict) -> int:
        """Hand them to Django's SMTP backend, built fresh from the settings in force.

        Fresh every time, deliberately: these values come from a page an administrator can
        change, and a connection held open across a change would go on using the settings
        it was opened with.
        """
        backend = GuardedBackend(
            alias="default",
            host=str(config.get("host") or ""),
            port=int(config.get("port") or 25),
            username=str(config.get("username") or ""),
            password=str(config.get("password") or ""),
            # Never both: Django refuses the pair, and one field cannot produce it (#158).
            use_tls=config.get("security") == "starttls",
            use_ssl=config.get("security") == "ssl",
            timeout=config.get("timeout"),
        )
        return backend.send_messages(messages) or 0

    def summary(self, config: dict) -> str:
        host = config.get("host") or ""
        if not host:
            return str(_("No server set."))
        return f"{host}:{config.get('port') or 25}"
