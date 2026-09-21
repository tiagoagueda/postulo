"""The built-in notifier: plain email, through the instance's mail settings.

It ships in the box so that an instance with working mail can notify without installing
anything, and it is a plugin like any other so that nothing about it is special: the same
connection form, the same Test button, the same event switches. Apprise and the rest sit
beside it, not instead of it.
"""

from __future__ import annotations

from django.core.mail import send_mail
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from postulo.plugins.api import (
    ConnectionUnusable,
    FieldSpec,
    Notification,
    TestResult,
    declares,
    shipped,
)


def _subject(title: str) -> str:
    from postulo.core import site

    # On one line whatever the title holds: a title comes from a captured posting or a
    # reminder the person typed, and a newline in a subject is a header of its own.
    return f"[{site.instance_name()}] {' '.join(title.split())}"


def verified_addresses(user) -> list[str]:
    """The addresses this notifier may write to: the person's own, verified, primary first.

    A notifier sends on the person's behalf, to the person. It used to take any address at
    all, which made a Postulo account a way to mail a stranger a subject line of one's own
    choosing, as often as one liked (#232). allauth already knows which addresses are
    theirs, because it verified them.
    """
    from allauth.account.models import EmailAddress

    if user is None or not getattr(user, "pk", None):
        return []
    rows = EmailAddress.objects.filter(user=user, verified=True).order_by("-primary", "email")
    return [row.email for row in rows]


def recipient(config: dict, user) -> str:
    """Where this connection sends: the configured address if it is still the person's,
    else their primary one, else nowhere."""
    addresses = verified_addresses(user)
    if not addresses:
        raise ConnectionUnusable(
            str(_("None of your addresses is verified, so there is nowhere to send this."))
        )
    wanted = (config.get("to") or "").strip().casefold()
    for address in addresses:
        if address.casefold() == wanted:
            return address
    if wanted:
        raise ConnectionUnusable(
            str(
                _(
                    "This connection sends to %(to)s, which is no longer one of your "
                    "verified addresses. Choose another."
                )
                % {"to": config.get("to")}
            ),
            keep_secrets=True,
        )
    return addresses[0]


def _body(notification: Notification) -> str:
    parts = [notification.title]
    if notification.body:
        parts.append(notification.body)
    if notification.url:
        parts.append(notification.url)
    return "\n\n".join(parts) + "\n"


@declares(
    shipped(
        name="email",
        label="Email",
        kind="notifier",
        description=_lazy(
            "Sends a notification as plain email, through whatever this instance uses to send mail."
        ),
    )
)
class EmailNotifier:
    def config_fields(self, user=None) -> list[FieldSpec]:
        """One choice: which of the person's verified addresses. Postulo hands `user` to a
        `config_fields` that asks for it, and this one does (#232)."""
        addresses = verified_addresses(user)
        if not addresses:
            # Nothing to choose from. `send` and `test` say why, and the form shows no
            # field rather than one that would accept an address that is not theirs.
            return []
        return [
            FieldSpec(
                "to",
                str(_("Send to")),
                type="choice",
                choices=tuple((address, address) for address in addresses),
                default=addresses[0],
                help=str(_("One of your verified addresses. Add another under Your details.")),
            )
        ]

    def send(self, notification: Notification, config: dict, user) -> None:
        send_mail(
            subject=_subject(notification.title),
            message=_body(notification),
            from_email=None,
            recipient_list=[recipient(config, user)],
            fail_silently=False,
        )

    def test(self, config: dict, user=None) -> TestResult:
        try:
            to = recipient(config, user)
        except ConnectionUnusable as why:
            return TestResult(False, str(why))
        sent = send_mail(
            subject=_subject(str(_("Notifications are set up"))),
            message=str(
                _(
                    "This is the test message from your Postulo notifications connection. "
                    "Reminders and captures will arrive the same way."
                )
            )
            + "\n",
            from_email=None,
            recipient_list=[to],
            fail_silently=False,
        )
        if not sent:
            return TestResult(False, str(_("The mail backend accepted nothing.")))
        return TestResult(True, str(_("Sent to %(to)s.") % {"to": to}))
