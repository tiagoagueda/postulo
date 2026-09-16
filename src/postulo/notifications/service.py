"""Delivering a notification to every notifier a person has switched on for its event."""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.utils import translation

from postulo.plugins.base import ConnectionUnusable
from postulo.plugins.models import Connection

from .base import Notification, wants

logger = logging.getLogger(__name__)


def language_for(user) -> str:
    """The language this person reads Postulo in, or the instance default.

    Not the language of whatever request happens to be in flight. A reminder announced by
    the scheduler has no request at all, and one announced by a capture arriving through
    the API has the `Accept-Language` of whichever tool sent it — neither of which has
    anything to do with the person the message is for (#223).
    """
    from postulo.core import site

    profile = getattr(user, "profile", None)
    chosen = (getattr(profile, "language", "") or "").strip()
    return chosen or site.default_language() or "en-GB"


def notify(user, notification: Notification | Callable[[], Notification]) -> int:
    """Send ``notification`` through each of ``user``'s notifier connections that wants it.

    Delivery is synchronous: a notifier sends one message and either it goes or it does
    not, and the connection remembers which. A failing notifier never fails the caller —
    a capture is still captured, a reminder still due — it is logged and shown on the
    connection instead. Returns how many connections took the message.

    All of it happens in the recipient's language. A `Notification` carries words that are
    already resolved, so switching language at the moment of sending would be too late to
    change them: a caller with something to translate passes a function that builds one
    instead, and it is called here, inside the override. A caller whose text is somebody's
    own typing — a reminder they wrote for themselves — passes the notification as it
    always did (#223).
    """
    with translation.override(language_for(user)):
        message = notification() if callable(notification) else notification
        return _deliver(user, message)


def _deliver(user, notification: Notification) -> int:
    delivered = 0
    connections = Connection.objects.for_user(user).enabled().of_kind("notifier")
    for connection in connections:
        if not wants(connection.config, notification.event):
            continue
        plugin = connection.plugin_instance
        if plugin is None:
            logger.warning(
                "Connection %s uses %r, which is not installed", connection.pk, connection.plugin
            )
            continue
        try:
            plugin.send(notification, connection.full_config, user)
        except ConnectionUnusable as dead:
            # Not a failure to retry: the other side has ended this connection, and trying
            # again would fail the same way every time anything happened (#216). Switch it
            # off with the reason, and forget a credential that is known not to work.
            logger.warning(
                "Notifier %r says connection %s is finished: %s",
                connection.plugin,
                connection.pk,
                dead,
            )
            connection.retire(str(dead), keep_secrets=dead.keep_secrets)
            continue
        except Exception as error:
            logger.exception(
                "Notifier %r failed for connection %s", connection.plugin, connection.pk
            )
            connection.record_test(False, f"{type(error).__name__}: {error}")
            continue
        connection.record_test(True)
        delivered += 1
    return delivered
