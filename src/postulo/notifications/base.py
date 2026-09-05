"""What a notification is, which events produce one, and what a notifier must provide.

Postulo sent nothing of its own until now: a reminder appeared on the dashboard and
nowhere else. A notifier is a connected plugin (see ``postulo.plugins``) that carries a
message somewhere — the built-in one by email, others by whatever they speak — and a
person chooses, per connection, which events reach it.

Events are few on purpose. Telling somebody what they just did themselves is noise; the
events here are the things that happen *to* them: a reminder they set falling due, a
capture arriving from outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from postulo.plugins.base import ConnectedPlugin, FieldSpec

#: Event key → what the connection form calls it. The key is stored on connections as
#: ``event_<key>``; adding an event here adds a switch to every notifier connection.
EVENTS = {
    "reminder_due": _("A reminder falls due"),
    "capture_received": _("A posting arrives through the capture API"),
}


@dataclass(frozen=True)
class Notification:
    """One message, independent of how it travels."""

    event: str
    title: str
    body: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        if self.event not in EVENTS:
            raise ValueError(f"Unknown notification event {self.event!r}; one of {sorted(EVENTS)}.")


@runtime_checkable
class NotifierPlugin(ConnectedPlugin, Protocol):
    """A connected plugin that can carry a notification.

    ``config`` is the connection's configuration and secrets together; ``user`` is the
    person the message is for, so a notifier can fall back to their address or name.
    """

    def send(self, notification: Notification, config: dict, user) -> None: ...


def event_specs() -> list[FieldSpec]:
    """The per-event switches every notifier connection carries. All on by default."""
    return [
        FieldSpec(
            f"event_{key}",
            str(label),
            type="boolean",
            required=False,
            default=True,
        )
        for key, label in EVENTS.items()
    ]


def wants(config: dict, event: str) -> bool:
    """Whether a connection's configuration asks for ``event``. Unset means yes."""
    return bool(config.get(f"event_{event}", True))


def absolute_url(path: str, request: HttpRequest | None = None) -> str:
    """A link a message can carry.

    From the request when there is one; from ``POSTULO_PUBLIC_URL`` when a scheduled job
    has no request; otherwise the bare path, which is still better than nothing.
    """
    if path.startswith(("http://", "https://")):
        return path
    if request is not None:
        return request.build_absolute_uri(path)
    base = (getattr(settings, "POSTULO_PUBLIC_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path
