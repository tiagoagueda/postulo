"""Notice what the clock has changed, and tell the people concerned.

Something has to look at the clock. For a single-instance application one command a
few minutes apart is the right size: run it from the host's cron, or as the ``scheduler``
service in the Compose file, which runs it in a loop. Each pass announces the reminders
that have fallen due and the applications that have gone quiet. Each is announced once;
the stamp survives whether or not the person had a notifier at the time, so adding one
later does not replay a month of old reminders. The same pass sends the copies of
documents that are waiting for an external store, retries the ones that failed, and runs
the sync connections whose interval has come round.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.applications.models import Reminder
from postulo.notifications.base import Notification, absolute_url
from postulo.notifications.service import notify


def announce_due_reminders() -> tuple[int, int]:
    """Announce every outstanding reminder that is due and not yet announced.

    Returns (reminders stamped, deliveries made).
    """
    now = timezone.now()
    due = (
        Reminder.objects.outstanding()
        .filter(due_at__lte=now, notified_at__isnull=True)
        .select_related("owner", "application", "application__posting__company")
    )
    stamped = 0
    delivered = 0
    for reminder in due:
        application = reminder.application
        if application is not None:
            posting = application.posting
            body = _("%(role)s at %(company)s") % {
                "role": posting.title,
                "company": posting.company.name,
            }
            url = absolute_url(application.get_absolute_url())
        else:
            body = ""
            url = absolute_url(reverse("applications:reminder_list"))
        delivered += notify(
            reminder.owner,
            Notification(event="reminder_due", title=reminder.summary, body=body, url=url),
        )
        reminder.notified_at = now
        reminder.save(update_fields=["notified_at", "updated_at"])
        stamped += 1
    return stamped, delivered


class Command(BaseCommand):
    help = (
        "Notify people of reminders that have fallen due and applications that have gone "
        "quiet. Run it from cron, or with --loop."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--loop", action="store_true", help="Keep running, forever.")
        parser.add_argument(
            "--every",
            type=int,
            default=300,
            help="Seconds between passes when looping (default 300).",
        )

    def handle(self, *args, **options) -> None:
        from postulo.applications.quiet import announce_quiet_applications
        from postulo.documents.archiving import send_pending
        from postulo.plugins.syncing import run_syncs

        while True:
            stamped, delivered = announce_due_reminders()
            quiet, told = announce_quiet_applications()
            copies_sent, copies_failed = send_pending()
            syncs_ran, syncs_failed = run_syncs()
            when = f"{timezone.now():%Y-%m-%d %H:%M}"
            if stamped:
                self.stdout.write(f"{when} {stamped} reminders due, {delivered} deliveries")
            if quiet:
                self.stdout.write(f"{when} {quiet} applications gone quiet, {told} deliveries")
            if copies_sent or copies_failed:
                self.stdout.write(
                    f"{when} {copies_sent} document copies sent, {copies_failed} failed"
                )
            if syncs_ran:
                self.stdout.write(f"{when} {syncs_ran} syncs ran, {syncs_failed} failed")
            if not options["loop"]:
                if not any((stamped, quiet, copies_sent, copies_failed, syncs_ran)):
                    self.stdout.write("Nothing due.")
                return
            time.sleep(max(options["every"], 10))
