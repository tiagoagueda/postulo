"""The person's own SMTP: mail that leaves as them, from their address, over their server.

The user-side half of #149. The instance's half is `postulo.plugins.smtp`, and the two are
deliberately not the same thing wearing two hats:

============  ==================================  ===============================
              ``smtp`` (transport)                ``own-mail`` (outbox)
============  ==================================  ===============================
Belongs to    the instance                        the person
Sends as      the instance                        the person
Configured    Server settings → Email             Settings → Connections
Switched off  never, while it is the way back in  by the person, or by an administrator
Recovers      yes                                 **never**
============  ==================================  ===============================

**The last row is the one that matters.** `accounts/recovery.py` reads transports, and this
is not one. A person switching their own mail off cannot thereby remove their own way back
into their account — the failure #104 was written to prevent, which would otherwise arrive
through a door nobody had locked (#143).

**Why it needs settings of its own at all.** Sending as somebody means putting their address
on a message. Doing that through the instance's server is spoofing: SPF says that server is
not authorised for their domain, DKIM signs it as somebody else, and a receiving server
bounces it or bins it. There is no way to send as a person except over a server that is
entitled to send as them, which is theirs and not Postulo's.

**The address is a field rather than a guess.** It is not the account's sign-in address:
plenty of people sign in with one address and correspond from another, and guessing would be
a message going out with the wrong name on it. `postulo.core.correspondence` refuses to send
a message whose sender disagrees with this field rather than quietly rewriting it.
"""

from __future__ import annotations

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from postulo.plugins.api import FieldSpec, TestResult, declares, shipped


@declares(
    shipped(
        name="own-mail",
        label=_lazy("Your own email"),
        kind="outbox",
        description=_lazy(
            "Send from your own address, over your own mail server, rather than from this "
            "instance. What leaves carries your name and your domain, and a reply or a "
            "bounce comes back to you. Switched off, everything this instance sends on its "
            "own account — notifications, sign-in codes, getting back in — is unaffected."
        ),
    )
)
class OwnMail:
    """SMTP belonging to the person, used for correspondence they send."""

    def config_fields(self) -> list[FieldSpec]:
        from postulo.core import mail

        return [
            FieldSpec(
                name="from_address",
                label=_lazy("Send from"),
                type="email",
                required=True,
                help=_lazy(
                    "The address recipients will see, and where their replies will go. Your "
                    "mail server has to be entitled to send from it."
                ),
            ),
            FieldSpec(name="host", label=_lazy("Server"), type="text", required=True),
            FieldSpec(
                name="port",
                label=_lazy("Port"),
                type="integer",
                required=False,
                help=_lazy("587 for STARTTLS, 465 for TLS from the first byte."),
            ),
            FieldSpec(
                name="security",
                label=_lazy("Encryption"),
                type="choice",
                choices=tuple(mail.MailSecurity.choices),
                required=False,
                help=_lazy("Nearly every provider wants STARTTLS on 587. Some want TLS on 465."),
            ),
            FieldSpec(name="username", label=_lazy("Username"), type="text", required=False),
            FieldSpec(
                name="password",
                label=_lazy("Password"),
                type="password",
                secret=True,
                required=False,
                help=_lazy(
                    "Stored encrypted and never shown back. Many providers want an "
                    "app-specific password here rather than the one you sign in with."
                ),
            ),
        ]

    def test(self, config: dict) -> TestResult:
        """Prove the settings without sending anything to anybody else.

        The same connection check the instance's own mail uses, including the destination
        guard: a person's outbox is somewhere the server dials at somebody's typing, which
        is exactly the shape #148 exists for.
        """
        from postulo.core import mail

        try:
            report = mail.check_connection(
                host=str(config.get("host") or ""),
                port=_port(config),
                username=str(config.get("username") or ""),
                password=str(config.get("password") or ""),
                security=str(config.get("security") or ""),
                timeout=10,
            )
        except mail.ConnectionFailed as error:
            return TestResult(False, str(error))
        return TestResult(True, report)

    def send(self, message, config: dict) -> int:
        """One message, over this person's server, with this person's address on it."""
        from postulo.core.mail import GuardedBackend

        backend = GuardedBackend(
            alias="default",
            host=str(config.get("host") or ""),
            port=_port(config),
            username=str(config.get("username") or ""),
            password=str(config.get("password") or ""),
            # Never both: Django refuses the pair, and one field cannot produce it (#158).
            use_tls=config.get("security") == "starttls",
            use_ssl=config.get("security") == "ssl",
            timeout=20,
        )
        return backend.send_messages([message]) or 0

    def summary(self, config: dict) -> str:
        address = config.get("from_address") or ""
        host = config.get("host") or ""
        if not address:
            return str(_("No address set."))
        return f"{address} · {host}" if host else str(address)


def _port(config: dict) -> int:
    """The port, or the one the chosen encryption conventionally uses.

    Somebody who picked *TLS* and left the port blank meant 465, and asking them to know
    that is asking them to know something their provider's help page already told them
    once (#158).
    """
    from postulo.core import mail

    given = config.get("port")
    if given:
        return int(given)
    return mail.DEFAULT_MAIL_PORTS.get(str(config.get("security") or ""), 587)
