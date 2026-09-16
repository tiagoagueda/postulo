"""The scheduler as something an operator runs, rather than as a function that works (#221).

The pass itself was tested; running it twice, running it while it was already running, and
noticing that it had stopped running were not. These cover the four faults the audit found:
work sent twice because nothing claimed it, a pass ending because one item raised, syncs
with no time limit holding up everything behind them, and nothing anywhere recording that a
pass had happened.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from postulo.applications.models import Application, Reminder, Status
from postulo.applications.quiet import announce_quiet_applications
from postulo.applications.services import change_status
from postulo.core import metrics, scheduler
from postulo.jobs.models import Company, JobPosting
from postulo.notifications.management.commands.send_due_reminders import announce_due_reminders

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def heartbeat_in_a_temporary_place(settings, tmp_path):
    settings.POSTULO_SCHEDULER_HEARTBEAT = tmp_path / "scheduler-heartbeat"
    cache.delete(scheduler.LEASE_KEY)
    yield
    cache.delete(scheduler.LEASE_KEY)


def an_application(user, *, quiet_days: int = 0) -> Application:
    """An application applied for ``quiet_days`` ago, with nothing since.

    `last_activity_at` is annotated from the timeline rather than stored, so silence is
    made the way the rest of the suite makes it: by dating the status change.
    """
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(
        application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=quiet_days)
    )
    application.refresh_from_db()
    return application


# --------------------------------------------------------------- claiming before sending


def test_a_reminder_is_stamped_before_it_is_announced(user, monkeypatch):
    """Two schedulers announce it once between them, not once each."""
    reminder = Reminder.objects.create(
        owner=user, summary="Chase them", due_at=timezone.now() - dt.timedelta(hours=1)
    )
    seen: list[int | None] = []

    def watching(owner, notification):
        # What the second scheduler would find at the moment the first one is sending.
        seen.append(Reminder.objects.get(pk=reminder.pk).notified_at)
        return 1

    # `notify` may be handed a function that builds the notification (#223); these doubles
    # stand in for it and so have to accept the same thing.

    monkeypatch.setattr(
        "postulo.notifications.management.commands.send_due_reminders.notify", watching
    )

    assert announce_due_reminders() == (1, 1)
    assert seen[0] is not None, "stamped before the message went, not after"


def test_a_reminder_already_claimed_elsewhere_is_not_announced_again(user, monkeypatch):
    reminder = Reminder.objects.create(
        owner=user, summary="Chase them", due_at=timezone.now() - dt.timedelta(hours=1)
    )
    # Exactly what a second scheduler does between our read and our claim.
    Reminder.objects.filter(pk=reminder.pk).update(notified_at=timezone.now())

    sent = []
    monkeypatch.setattr(
        "postulo.notifications.management.commands.send_due_reminders.notify",
        lambda owner, notification: (
            sent.append(notification() if callable(notification) else notification) or 1
        ),
    )

    assert announce_due_reminders() == (0, 0)
    assert sent == [], "the other scheduler had it"


def test_one_reminder_that_raises_does_not_end_the_pass(user, monkeypatch):
    first = Reminder.objects.create(
        owner=user, summary="Breaks", due_at=timezone.now() - dt.timedelta(hours=2)
    )
    Reminder.objects.create(
        owner=user, summary="Fine", due_at=timezone.now() - dt.timedelta(hours=1)
    )

    def sometimes(owner, notification):
        message = notification() if callable(notification) else notification
        if message.title == "Breaks":
            raise RuntimeError("the notifier exploded")
        return 1

    monkeypatch.setattr(
        "postulo.notifications.management.commands.send_due_reminders.notify", sometimes
    )

    stamped, delivered = announce_due_reminders()
    assert (stamped, delivered) == (2, 1), "both dealt with, one of them delivered"
    first.refresh_from_db()
    assert first.notified_at is not None, "not retried for ever: it had its turn"


def test_quiet_applications_are_claimed_before_the_message_goes(user, monkeypatch):
    an_application(user, quiet_days=60)
    seen = []

    def watching(owner, notification):
        # Resolved here, as `notify` resolves it, so the stamps are read at the moment the
        # message is actually built.
        notification() if callable(notification) else notification
        seen.append(list(Application.objects.values_list("quiet_announced_at", flat=True)))
        return 1

    monkeypatch.setattr("postulo.applications.quiet.notify", watching)

    stamped, delivered = announce_quiet_applications()
    assert (stamped, delivered) == (1, 1)
    assert seen[0][0] is not None, "stamped before the message went"
    assert announce_quiet_applications() == (0, 0), "and not again"


# ------------------------------------------------------------------------- the pass lease


def test_only_one_pass_runs_at_a_time():
    with scheduler.only_one_pass(60) as mine:
        assert mine is True
        with scheduler.only_one_pass(60) as theirs:
            assert theirs is False, "a second scheduler finds the lease held"
    with scheduler.only_one_pass(60) as afterwards:
        assert afterwards is True, "and it is given back at the end of a pass"


def test_the_command_leaves_a_pass_already_running_alone(user, capsys):
    Reminder.objects.create(
        owner=user, summary="x", due_at=timezone.now() - dt.timedelta(minutes=5)
    )
    cache.add(scheduler.LEASE_KEY, "somebody else", 60)

    call_command("send_due_reminders")

    assert "Another scheduler is mid-pass" in capsys.readouterr().out
    assert Reminder.objects.get().notified_at is None, "left for the one that has the lease"


# --------------------------------------------------------------------------- the heartbeat


def test_a_finished_pass_leaves_a_heartbeat(user):
    assert scheduler.last_pass() is None, "nothing has run here yet"

    call_command("send_due_reminders")

    beat = scheduler.last_pass()
    assert beat is not None
    assert abs((timezone.now() - beat).total_seconds()) < 60


def test_the_heartbeat_is_reported_as_a_metric_and_zero_when_there_is_none(user):
    def value_of(name):
        return next(metric for metric in metrics.collect() if metric.name == name).samples[0][1]

    assert value_of("postulo_scheduler_last_pass_timestamp_seconds") == 0

    scheduler.beat()

    assert value_of("postulo_scheduler_last_pass_timestamp_seconds") > 0


def test_overdue_counts_only_what_is_late_and_pending_only_what_is_due(user):
    now = timezone.now()
    Reminder.objects.create(owner=user, summary="tomorrow", due_at=now + dt.timedelta(days=1))
    Reminder.objects.create(owner=user, summary="just now", due_at=now - dt.timedelta(minutes=1))
    Reminder.objects.create(owner=user, summary="late", due_at=now - dt.timedelta(hours=3))

    def samples(name):
        metric = next(m for m in metrics.collect() if m.name == name)
        return {labels["kind"]: value for labels, value in metric.samples}

    assert samples("postulo_pending")["reminders"] == 2, "due, not everything ever set"
    assert samples("postulo_overdue")["reminders"] == 1, "late enough to mean something"
