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

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.applications.models import Reminder
from postulo.core import scheduler
from postulo.notifications.base import Notification, absolute_url
from postulo.notifications.service import notify

logger = logging.getLogger(__name__)


def announce_due_reminders() -> tuple[int, int]:
    """Announce every outstanding reminder that is due and not yet announced.

    Returns (reminders stamped, deliveries made).

    The stamp is written *before* the message goes, and only if writing it changed a row
    (#221). Two schedulers -- cron and ``--loop`` together, which the Compose file makes easy
    to end up with -- then announce a reminder once between them rather than once each,
    because only one of them can be the process whose ``UPDATE`` matched. It settles the
    other way round from what it looks like: a message that is lost because the process died
    in the half-second after the stamp is a message nobody gets, and a message sent twice is
    a person's telephone going off twice at three in the morning for something they have
    already read. The stamp was always the record of "this one has been dealt with", and
    this only stops it being written after the fact.
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
        claimed = Reminder.objects.filter(pk=reminder.pk, notified_at__isnull=True).update(
            notified_at=now, updated_at=now
        )
        if not claimed:
            continue
        stamped += 1
        application = reminder.application

        def announcement(reminder=reminder, application=application) -> Notification:
            """Built when it is sent, so its words are in the recipient's language (#223).

            Called by `notify` inside the override. The title is the person's own summary
            and needs no translating; the line under it names the role and the employer,
            and that sentence is ours.
            """
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
            return Notification(
                event="reminder_due",
                title=reminder.summary,
                body=body,
                url=url,
                # One reminder falls due once, however many notifiers carry it and however
                # many times one of them retries (#229).
                key=f"reminder:{reminder.pk}",
                occurred_at=reminder.due_at,
                data={
                    "reminder_id": reminder.pk,
                    "due_at": reminder.due_at.isoformat(),
                    "application_id": application.pk if application is not None else None,
                },
            )

        try:
            delivered += notify(reminder.owner, announcement)
        except Exception:
            # One reminder whose application lost its company, or one notifier raising
            # something nobody anticipated, used to end the pass -- and everything after it
            # in the queue waited for the restart (#221).
            logger.exception("Could not announce reminder %s", reminder.pk)
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
        parser.add_argument(
            "--sync-budget",
            type=int,
            default=120,
            help=(
                "Seconds a pass may spend on sync connections before leaving the rest to the "
                "next one (default 120). 0 means no limit."
            ),
        )

    def handle(self, *args, **options) -> None:
        every = max(options["every"], 10)
        # A single run says so when it found nothing, because somebody is watching it. A loop
        # does not, because nobody is, and a line every five minutes for ever is a log file
        # in which the interesting lines cannot be found.
        self.quiet_pass = not options["loop"]
        while True:
            try:
                self.one_pass(every, budget=options["sync_budget"])
                # The one outbound request this loop makes on the instance's own behalf,
                # and only if the operator switched it on: once a day, is there a newer
                # release (#272). A page never asks; this is where the asking happens.
                from postulo.core import updates

                if updates.due():
                    updates.check()
            except Exception:
                # A pass that ends badly is one pass. Before this, a dropped PostgreSQL
                # connection or a SQLite lock timeout ended the *process*, and the container
                # restarted through the whole entrypoint to try the same thing again (#221).
                logger.exception("A scheduler pass ended badly")
                if not options["loop"]:
                    raise
            finally:
                # The connection this pass used may have been closed under it while it
                # slept -- a database restart, or PostgreSQL's own idle timeout. Django
                # does this between requests and there are no requests here.
                close_old_connections()
            if not options["loop"]:
                return
            time.sleep(every)

    def one_pass(self, every: int, *, budget: int) -> None:
        """Everything the clock has made due, once."""
        from postulo.applications.quiet import announce_quiet_applications
        from postulo.core import errands
        from postulo.core.slow import reap_archives
        from postulo.documents.archiving import send_pending
        from postulo.plugins.syncing import run_syncs

        # Held for a little longer than the gap between passes: a pass that takes longer than
        # that is one whose work the next pass may as well pick up.
        with scheduler.only_one_pass(every * 2) as mine:
            if not mine:
                self.stdout.write("Another scheduler is mid-pass; leaving this one to it.")
                return
            stamped, delivered = announce_due_reminders()
            quiet, told = announce_quiet_applications()
            copies_sent, copies_failed = send_pending()
            syncs_ran, syncs_failed = run_syncs(budget=budget)
            # The two things #247 leaves lying about: an export archive holding a whole
            # account, and a week of errand rows nobody is watching any more. Reaped on the
            # pass that already exists rather than by a second timer.
            reaped = reap_archives() + errands.forget_old()

        when = f"{timezone.now():%Y-%m-%d %H:%M}"
        if stamped:
            self.stdout.write(f"{when} {stamped} reminders due, {delivered} deliveries")
        if quiet:
            self.stdout.write(f"{when} {quiet} applications gone quiet, {told} deliveries")
        if copies_sent or copies_failed:
            self.stdout.write(f"{when} {copies_sent} document copies sent, {copies_failed} failed")
        if syncs_ran:
            self.stdout.write(f"{when} {syncs_ran} syncs ran, {syncs_failed} failed")
        if reaped:
            self.stdout.write(f"{when} {reaped} finished errands and expired archives removed")
        if self.quiet_pass and not any(
            (stamped, quiet, copies_sent, copies_failed, syncs_ran, reaped)
        ):
            self.stdout.write("Nothing due.")

        # Last, and only on a pass that finished: the heartbeat is the answer to "is it
        # still going round", and a pass that died halfway did not go round.
        scheduler.beat()
