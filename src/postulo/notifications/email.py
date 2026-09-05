"""The built-in notifier: plain email, through the instance's mail settings.

It ships in the box so that an instance with working mail can notify without installing
anything, and it is a plugin like any other so that nothing about it is special: the same
connection form, the same Test button, the same event switches. Apprise and the rest sit
beside it, not instead of it.
"""

from __future__ import annotations

from django.core.mail import send_mail
from django.utils.translation import gettext as _

from postulo import __version__
from postulo.plugins.base import FieldSpec, TestResult

from .base import Notification


def _subject(title: str) -> str:
    from postulo.core import site

    return f"[{site.instance_name()}] {title}"


def _body(notification: Notification) -> str:
    parts = [notification.title]
    if notification.body:
        parts.append(notification.body)
    if notification.url:
        parts.append(notification.url)
    return "\n\n".join(parts) + "\n"


class EmailNotifier:
    name = "email"
    version = __version__
    kind = "notifier"
    label = "Email"

    def config_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec(
                "to",
                str(_("Send to")),
                type="email",
                help=str(_("Usually your own address. Anything the instance's mail can reach.")),
            )
        ]

    def send(self, notification: Notification, config: dict, user) -> None:
        to = config.get("to") or getattr(user, "email", "")
        send_mail(
            subject=_subject(notification.title),
            message=_body(notification),
            from_email=None,
            recipient_list=[to],
            fail_silently=False,
        )

    def test(self, config: dict) -> TestResult:
        to = config.get("to")
        if not to:
            return TestResult(False, str(_("No address to send to.")))
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
