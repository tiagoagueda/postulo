"""Last month's report, made on the day the person chose (#1095).

The period report (#56) is what a person hands an employment office as evidence of their
search, and they used to make it by hand each month. Here the scheduler's pass makes it for
them: on a day of each month, or some days before an entry a plugin places on the calendar --
a monthly declaration -- it draws the report for the month before, files it under *Sent
documents* as the button on the report page does, and puts a reminder on the calendar to
read it.

**Prepared, not sent.** A report is evidence, and a person reads evidence before it goes
anywhere. So a prepared report is kept here only: no store is given a copy and nobody is
mailed it. *Send now* on the document hands it on once it has been read. Handing it on at
once -- to the stores that take reports, and by the person's own outbox to an address they
typed -- is a second switch, and it is off until they turn it on.

**Once per month, whoever runs the pass.** Asked at most once an hour, behind a key in the
cache as `documents.scrub.sweep_if_due` is; and each month is claimed for a person by a
conditional update on the day it was last made for, as `applications.quiet` claims its
silences, so a second scheduler finds nothing to claim. The drawing itself is the errand the
report page sends (`slow.render_a_report`), done by the worker where there is one and here
where there is not, and in the person's own language and time zone either way.

**A missed day is not a missed month.** The report is due from the chosen day on, not only on
it, so a scheduler that was stopped on the third makes the report when it comes back. In a
month shorter than the day chosen, the last day of the month is the day.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging

from django.core.cache import cache
from django.utils import timezone

from postulo.accounts.models import Profile, ReportSchedule
from postulo.core import errands, preferences

from . import reports
from .models import Reminder

logger = logging.getLogger(__name__)

#: How long one look leaves the next pass alone. The day is what is chosen, so once an hour
#: is soon enough and saves reading every profile every five minutes.
CHECK_EVERY_SECONDS = 3600
GATE = "applications:reports-checked"

#: The calendar identifier of the reminder to read one month's report, per person.
REMINDER_UID = "postulo-report-{month:%Y-%m}"


def last_month(today: dt.date) -> reports.Period:
    first = today.replace(day=1) - dt.timedelta(days=1)
    return reports.month_period(first.year, first.month)


def entry_sources(user) -> list[str]:
    """The plugins that have placed entries on this person's calendar, by name."""
    return sorted(
        set(
            Reminder.objects.filter(owner=user, retired_at__isnull=True)
            .exclude(source="")
            .values_list("source", flat=True)
        )
    )


def due_period(profile, *, today: dt.date | None = None) -> reports.Period | None:
    """The month to make a report for now, or None: not chosen, not yet, or made already.

    Read in the person's own zone, which the caller puts in force.
    """
    today = today or timezone.localdate()
    month = last_month(today)
    made = profile.report_made_for
    if made is not None and made >= month.start:
        return None
    if profile.report_schedule == ReportSchedule.DAY:
        day = min(profile.report_day or 1, calendar.monthrange(today.year, today.month)[1])
        return month if today.day >= day else None
    if profile.report_schedule == ReportSchedule.ENTRY and profile.report_entry_source:
        zone = timezone.get_current_timezone()
        lower = timezone.make_aware(dt.datetime.combine(today, dt.time.min), zone)
        last = today + dt.timedelta(days=profile.report_days_before or 0)
        upper = timezone.make_aware(dt.datetime.combine(last, dt.time.max), zone)
        coming = Reminder.objects.filter(
            owner_id=profile.user_id,
            source=profile.report_entry_source,
            retired_at__isnull=True,
            due_at__range=(lower, upper),
        )
        return month if coming.exists() else None
    return None


def claim(profile, period: reports.Period) -> bool:
    """Take this month for this person, or find that somebody else already has.

    Compared with the value that was read, so two passes that both found the month due write
    it once between them, and only the one whose update matched draws the report.
    """
    return bool(
        Profile.objects.filter(pk=profile.pk, report_made_for=profile.report_made_for).update(
            report_made_for=period.start
        )
    )


def make_due() -> int:
    """Make every report that has fallen due. Returns how many were sent off to be drawn."""
    profiles = (
        Profile.objects.exclude(report_schedule=ReportSchedule.NEVER)
        # A deactivated account is made nothing, as it is announced nothing (#575).
        .filter(user__is_active=True)
        .select_related("user")
        .order_by("pk")
    )
    made = 0
    for profile in profiles:
        try:
            with preferences.as_person(profile.user):
                period = due_period(profile)
                if period is None or not claim(profile, period):
                    continue
                errands.send(
                    "report_pdf", profile.user, query=reports.as_query(period), scheduled=True
                )
            made += 1
        except Exception:
            # One person's report that could not be sent off is not everybody else's.
            logger.exception("Could not make the scheduled report for profile %s", profile.pk)
    return made


def make_if_due() -> int:
    """`make_due`, at most once an hour: the one the scheduler's pass calls.

    A cache that is not answering costs a look on every pass, as it does for the sweep, and
    never the pass; the claim keeps a month from being made twice either way.
    """
    try:
        due = cache.add(GATE, True, CHECK_EVERY_SECONDS)
    except Exception:  # a cache that is down is no reason to skip a month
        due = True
    return make_due() if due else 0
