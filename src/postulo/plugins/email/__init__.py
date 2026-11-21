"""The built-in notifier: plain email, through the instance's mail settings.

It ships in the box so that an instance with working mail can notify without installing
anything, and it is a plugin like any other so that nothing about it is special: the same
connection form, the same Test button, the same event switches. Apprise and the rest sit
beside it, not instead of it.

A connection sends each notification as it happens, or keeps them and sends one mail a day
at an hour in the person's own time zone (#1259). Keeping is the core's: the notifier says
until when (`hold_until`) and is handed what was kept together (`send_many`).
"""

from __future__ import annotations

import datetime as dt

from django.core.mail import send_mail
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.translation import ngettext

from postulo.plugins.api import (
    ConnectionUnusable,
    FieldSpec,
    Notification,
    TestResult,
    declares,
    shipped,
)

#: Each notification in a mail of its own, as it happens.
AT_ONCE = "now"
#: Everything kept, in one mail at the chosen hour.
DAILY = "daily"
#: The hour a daily mail goes at when the connection has not said, in the person's zone.
DEFAULT_HOUR = 8


def _hour(config: dict) -> int:
    try:
        hour = int(config.get("hour", DEFAULT_HOUR))
    except (TypeError, ValueError):
        return DEFAULT_HOUR
    return hour if 0 <= hour <= 23 else DEFAULT_HOUR


def next_mail_at(config: dict, now: dt.datetime | None = None) -> dt.datetime:
    """The next time the connection's hour comes round, in the current time zone.

    `notify` has made that zone the recipient's, so eight o'clock is eight o'clock where they
    live, on either side of a change of clocks: the day is added on the wall clock.
    """
    now = timezone.localtime(now)
    at = now.replace(hour=_hour(config), minute=0, second=0, microsecond=0)
    if at <= now:
        at += dt.timedelta(days=1)
    return at


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


def _digest(notifications: list[Notification]) -> str:
    """Each notification as it would have been mailed, oldest first, a rule between them."""
    return "\n----\n\n".join(_body(notification) for notification in notifications)


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
        label=_lazy("Email notifications"),
        kind="notifier",
        parent="builtin-notifications",
        settings="server:email",
        categories=("notifications",),
        icon="mail",
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
            ),
            FieldSpec(
                "when",
                str(_("When")),
                type="choice",
                choices=(
                    (AT_ONCE, str(_("As it happens"))),
                    (DAILY, str(_("Once a day"))),
                ),
                default=AT_ONCE,
                required=False,
                help=str(
                    _(
                        "Once a day keeps everything for one mail at the hour below. A "
                        "reminder is due when it is due: to have reminders arrive at once, "
                        "add a second email connection that sends as it happens, and switch "
                        "them on there and off here."
                    )
                ),
            ),
            FieldSpec(
                "hour",
                str(_("Hour of the daily mail")),
                type="choice",
                choices=tuple((f"{hour:02d}", f"{hour:02d}:00") for hour in range(24)),
                default=f"{DEFAULT_HOUR:02d}",
                required=False,
                help=str(_("In your own time zone, the one your settings name.")),
            ),
        ]

    def hold_until(self, notification: Notification, config: dict, user) -> dt.datetime | None:
        """Kept until the connection's hour when it sends once a day; else sent now (#1259)."""
        if config.get("when") != DAILY:
            return None
        return next_mail_at(config)

    def send(self, notification: Notification, config: dict, user) -> None:
        send_mail(
            subject=_subject(notification.title),
            message=_body(notification),
            from_email=None,
            recipient_list=[recipient(config, user)],
            fail_silently=False,
        )

    def send_many(self, notifications: list[Notification], config: dict, user) -> None:
        """What was kept, in one mail; or each in its own, for a connection that sends at once.

        Handed every message of this connection that fell due in one pass, oldest first: what
        a daily connection kept, and a send that failed and is due again. One message alone is
        mailed as it would have been, under its own title.
        """
        if config.get("when") != DAILY or len(notifications) == 1:
            for notification in notifications:
                self.send(notification, config, user)
            return
        count = len(notifications)
        send_mail(
            # Worded in the language the kept messages were worded in: the pass that hands them
            # over has switched to it.
            subject=_subject(
                ngettext("%(count)s notification", "%(count)s notifications", count)
                % {"count": count}
            ),
            message=_digest(notifications),
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
