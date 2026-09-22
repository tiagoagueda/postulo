"""What Postulo keeps for notifications: only what is waiting for a browser tab to show it."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel


class BrowserNotice(OwnedModel):
    """A notification waiting for an open Postulo tab to show it (#209).

    The browser notifier's fallback. Web Push reaches a browser with no tab open, but only
    where the browser can subscribe and the push goes through; everywhere else the message
    waits here, and the next tab that asks shows it and marks it shown.

    Short-lived on purpose. A notice is shown once, and one nobody collects is thrown away
    after :data:`postulo.notifications.inbox.KEPT_FOR` -- it is a nudge, not a record, and the
    reminder or the capture it was about is still where it always was.
    """

    event = models.CharField(_("event"), max_length=40)
    title = models.CharField(_("title"), max_length=300)
    body = models.TextField(_("body"), blank=True)
    url = models.CharField(_("address"), max_length=500, blank=True)
    shown_at = models.DateTimeField(_("shown at"), null=True, blank=True)

    class Meta:
        verbose_name = _("browser notice")
        verbose_name_plural = _("browser notices")
        ordering = ("created_at",)
        indexes = [models.Index(fields=("owner", "shown_at"))]

    def __str__(self) -> str:
        return self.title


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", _("Waiting")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed, will retry")
    GIVEN_UP = "given_up", _("Given up")


class WebhookDelivery(OwnedModel):
    """One notification on its way to one webhook receiver (#240).

    A row rather than a request: nothing is posted from inside the request that caused the
    event, the scheduler delivers what is due with backoff, and a receiver down for an
    afternoon gets everything when it comes back. The same shape as a document's copy on its
    way to an external store, for the same reasons.

    The body is kept as the exact text that is signed and sent, so a retry sends the bytes
    the first attempt signed and a receiver that logs signatures can match them. The key is
    the notification's stable one (#229): a retried announcement finds its row and makes no
    second one.
    """

    connection = models.ForeignKey(
        "plugins.Connection",
        on_delete=models.SET_NULL,
        null=True,
        related_name="webhook_deliveries",
        verbose_name=_("connection"),
    )
    event = models.CharField(_("event"), max_length=40)
    key = models.CharField(_("key"), max_length=200, blank=True, db_index=True)
    body = models.TextField(_("body"))
    status = models.CharField(
        _("status"), max_length=12, choices=DeliveryStatus, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    next_attempt_at = models.DateTimeField(_("next attempt"), null=True, blank=True, db_index=True)
    last_attempt_at = models.DateTimeField(_("last attempt"), null=True, blank=True)
    last_status = models.PositiveSmallIntegerField(_("last answer"), null=True, blank=True)
    last_error = models.CharField(_("last error"), max_length=500, blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("webhook delivery")
        verbose_name_plural = _("webhook deliveries")
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return f"{self.event} -> {self.connection_id} ({self.status})"
