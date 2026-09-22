"""What a notification is, which events produce one, and what a notifier must provide.

Postulo sent nothing of its own until now: a reminder appeared on the dashboard and
nowhere else. A notifier is a connected plugin (see ``postulo.plugins``) that carries a
message somewhere — the built-in one by email, others by whatever they speak — and a
person chooses, per connection, which events reach it.

Events are few on purpose. Telling somebody what they just did themselves is noise; the
events here are the things that happen *to* them: a reminder they set falling due, a
capture arriving from outside, an employer falling silent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from postulo.plugins.base import ConnectedPlugin, FieldSpec

#: Event key → what the connection form calls it. The key is stored on connections as
#: ``event_<key>``; adding an event here adds a switch to every notifier connection.
EVENTS = {
    "reminder_due": _("A reminder falls due"),
    "capture_received": _("A posting arrives through the capture API"),
    "went_quiet": _("Applications go quiet"),
    "posting_closing": _("A listing you are considering closes soon"),
}


@dataclass(frozen=True)
class Notification:
    """One message, independent of how it travels.

    The first four fields are the message as a person reads it. The last four are what a
    notifier needs to do its job properly and had no way to ask for (#229): a notifier that
    retries could not tell a retry from a second event, one that renders had to guess the
    language, one that files had only "now" for when the thing happened, and one that wanted
    the reminder itself had to parse it back out of a sentence.
    """

    event: str
    title: str
    body: str = ""
    url: str = ""

    #: What this message *is*, stable across the sends that carry it. A notifier that
    #: retries, or two passes that reach the same conclusion, use it to deliver once:
    #: ``reminder:41`` is the same message however many times it is announced, whereas
    #: ``title`` is translated at the moment of sending and differs between two recipients
    #: of the same event. Empty means the sender is claiming no identity, so nothing may be
    #: deduplicated on it -- never fall back to the words.
    key: str = ""

    #: The language the words above are already in, as a Postulo code (``pt-PT``). Set by
    #: `notifications.service.notify` from the recipient's own choice, so a notifier that
    #: renders around the message -- a subject line, a footer, a push payload -- matches it
    #: rather than the language of whatever request happens to be in flight (#223).
    language: str = ""

    #: When the thing being announced happened, which is not when the message was built. A
    #: reminder fell due at its due time; a capture arrived when it arrived. A notifier
    #: filing into something with a timeline wants that one.
    occurred_at: dt.datetime | None = None

    #: The thing itself, in machine terms, for a notifier that does more than print: the
    #: reminder's id, the application's, the due time. Keys are a sender's own vocabulary,
    #: so read it with ``.get`` and work without it -- this is a hint, not a contract.
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event not in EVENTS:
            raise ValueError(f"Unknown notification event {self.event!r}; one of {sorted(EVENTS)}.")
        # `frozen` stops assignment, not mutation of what a field holds, and this one is
        # handed to every notifier a person has: one of them keeping the dict and writing to
        # it later would change what the next send reads.
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def but(self, **changes) -> Notification:
        """This message with ``changes`` applied, since it is frozen.

        `notify` stamps the language on the way past, which is the only reason this exists;
        a sender builds the whole thing at once.
        """
        return replace(self, **changes)


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
