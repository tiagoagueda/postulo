"""Delivering a notification to every notifier a person has switched on for its event."""

from __future__ import annotations

import logging

from postulo.plugins.models import Connection

from .base import Notification, wants

logger = logging.getLogger(__name__)


def notify(user, notification: Notification) -> int:
    """Send ``notification`` through each of ``user``'s notifier connections that wants it.

    Delivery is synchronous: a notifier sends one message and either it goes or it does
    not, and the connection remembers which. A failing notifier never fails the caller —
    a capture is still captured, a reminder still due — it is logged and shown on the
    connection instead. Returns how many connections took the message.
    """
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
        except Exception as error:
            logger.exception(
                "Notifier %r failed for connection %s", connection.plugin, connection.pk
            )
            connection.record_test(False, f"{type(error).__name__}: {error}")
            continue
        connection.record_test(True)
        delivered += 1
    return delivered
