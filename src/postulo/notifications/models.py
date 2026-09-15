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
