"""Noticing silence.

An employer that simply stops replying is the most common ending, and ``Status.GHOSTED``
exists to record it — but nothing noticed the silence before it was named. An application
has *gone quiet* when it is open, was actually sent, nothing has happened to it for a
while, and nothing is planned: no reminder ahead, no interview in the diary. If the person
has already planned the next step it is not quiet, it is waiting.

The threshold is per person, because the right number depends on the market and the
person's patience; the default follows recruiters' own advice, which clusters around two
to three weeks. A person may also give a status, or what an application was applied
through, a number of its own (#1099): a status's number comes first, then the channel's,
then the usual one. Everything that shows or counts quiet applications uses the one predicate
here, so the dashboard, the board, the table, the figures and the notifier all agree.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Exists, F, OuterRef, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ngettext

from postulo.notifications.base import (
    NAMED_IN_ANNOUNCEMENT,
    Notification,
    absolute_url,
    announcement_body,
    announcement_key,
)
from postulo.notifications.service import notify

from .models import (
    QUIET_SETTABLE_STATUSES,
    QUIET_STATUSES,
    Application,
    Channel,
    QuietRule,
    quiet_days_from,
)

logger = logging.getLogger(__name__)

#: Days without activity after which an open application counts as quiet, unless the
#: person has chosen otherwise under Settings.
DEFAULT_QUIET_AFTER_DAYS = 21

#: How far out *Snooze* sets its reminder.
SNOOZE_DAYS = 14


def threshold_for(user) -> int:
    """The person's own usual threshold, or the default."""
    profile = getattr(user, "profile", None)
    days = getattr(profile, "quiet_after_days", None)
    return days if isinstance(days, int) and days > 0 else DEFAULT_QUIET_AFTER_DAYS


def rule_for(user) -> QuietRule:
    """The person's whole rule: the usual number, and the ones they set by status and channel.

    Read off the profile the caller already holds, so asking it costs no query where the
    profile came with the person (#1120's owners do), and one where it did not -- as
    `threshold_for` always has.
    """
    profile = getattr(user, "profile", None)
    return QuietRule(
        days=threshold_for(user),
        by_status=quiet_days_from(
            getattr(profile, "quiet_days_by_status", None), set(QUIET_SETTABLE_STATUSES)
        )
        or {},
        by_channel=quiet_days_from(
            getattr(profile, "quiet_days_by_channel", None), set(Channel.values)
        )
        or {},
    )


def quiet_applications(user, at=None):
    """The person's applications that have gone quiet, longest silence first."""
    return (
        Application.objects.for_user(user)
        .with_display_data()
        .quiet(rule_for(user), at=at)
        .order_by("last_activity_at", "pk")
    )


@dataclass(frozen=True)
class Outlook:
    """When one application counts as quiet, for its own page (#1099)."""

    #: The days its silence may last, and which setting gave them: status, channel or usual.
    days: int
    source: str
    #: The day it counts as quiet if nothing happens before then.
    on: dt.datetime
    #: Whether it is quiet now, and whether something planned keeps it from being so.
    is_quiet: bool
    waiting: bool


def outlook(application, user, at=None, *, afresh: bool = False) -> Outlook | None:
    """When ``application`` counts as quiet under ``user``'s rule, or None where it never can.

    Reads the annotations `with_activity` makes, as the one condition does; an application
    in a status that cannot go quiet -- a draft, an ending -- has no outlook at all.
    ``afresh`` reads them again, for a row that has just been written to.
    """
    if application.status not in QUIET_STATUSES:
        return None
    if afresh or not hasattr(application, "last_activity_at"):
        # Drawn from a row read without the annotations, as a swapped card is: one query
        # for the three, on this one application.
        read = (
            Application.objects.for_user(user)
            .filter(pk=application.pk)
            .with_activity(at=at)
            .values("last_activity_at", "has_future_reminder", "has_unsettled_interview")
            .first()
        )
        for name, value in (read or {}).items():
            setattr(application, name, value)
    last = getattr(application, "last_activity_at", None)
    if last is None:
        return None
    now = at or timezone.now()
    days, source = rule_for(user).days_for(application.status, application.channel)
    on = last + dt.timedelta(days=days)
    waiting = bool(
        getattr(application, "has_future_reminder", False)
        or getattr(application, "has_unsettled_interview", False)
    )
    return Outlook(
        days=days, source=source, on=on, is_quiet=on < now and not waiting, waiting=waiting
    )


def announce_quiet_applications(at=None) -> tuple[int, int]:
    """Tell each person about the applications that have newly gone quiet.

    One message per person per pass, naming the applications. Each application is
    announced once per silence: the stamp is compared with the last activity, so one
    that woke up and went quiet again is announced again, and one that stayed quiet is
    not repeated every pass. Returns (applications stamped, deliveries made).
    """
    now = at or timezone.now()
    # Read once, with the profile that holds each person's threshold and language, and only
    # the people who have an application in a status that can go quiet at all (#1120): an
    # account of drafts and endings is not asked anything. An EXISTS rather than a join, so
    # nobody comes back once per application.
    owners = (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(Exists(Application.objects.filter(owner=OuterRef("pk"), status__in=QUIET_STATUSES)))
        .select_related("profile")
        .order_by("pk")
    )
    stamped = 0
    delivered = 0
    for owner in owners:
        # Announced once per silence, and the database is asked only for the ones still to
        # be said (#1120): an application that went quiet weeks ago was read again on every
        # pass, with its listing and its company, to find the stamp it already had.
        fresh = list(
            Application.objects.filter(owner=owner)
            .select_related("posting", "posting__company")
            .quiet(rule_for(owner), at=now)
            .filter(
                Q(quiet_announced_at__isnull=True) | Q(quiet_announced_at__lt=F("last_activity_at"))
            )
            .order_by("last_activity_at", "pk")
        )
        if not fresh:
            continue
        # Claimed before the message goes, and only the rows this process actually claimed
        # are named in it (#221). The stamp is compared with the value that was read, so a
        # second scheduler -- or this one restarted mid-pass -- finds nothing to claim and
        # stays silent, instead of sending the same list again.
        claimed = [
            row
            for row in fresh
            if Application.objects.filter(
                pk=row.pk, quiet_announced_at=row.quiet_announced_at
            ).update(quiet_announced_at=now, updated_at=now)
        ]
        if not claimed:
            continue
        stamped += len(claimed)
        try:
            # Worded when it is sent rather than before, so the count, the days and the
            # "and N more" come out in the language the person reads (#223).
            delivered += notify(owner, lambda claimed=claimed: _announcement(claimed, now))
        except Exception:
            logger.exception("Could not announce quiet applications for owner %s", owner.pk)
    return stamped, delivered


def _announcement(applications: list[Application], now) -> Notification:
    count = len(applications)
    lines = [
        ngettext(
            "%(role)s at %(company)s — %(days)s day",
            "%(role)s at %(company)s — %(days)s days",
            (now - row.last_activity_at).days,
        )
        % {
            "role": row.posting.title,
            "company": row.posting.company.name,
            "days": (now - row.last_activity_at).days,
        }
        for row in applications[:NAMED_IN_ANNOUNCEMENT]
    ]
    return Notification(
        event="went_quiet",
        title=ngettext(
            "%(count)s application has gone quiet",
            "%(count)s applications have gone quiet",
            count,
        )
        % {"count": count},
        body=announcement_body(lines, count),
        url=absolute_url(reverse("applications:list") + "?quiet=1"),
        # One announcement per person per pass, named by the applications it is about and
        # the stamp this pass wrote, so a notifier that retries does not say it twice (#229)
        # and one that went quiet again is not mistaken for one that was said (#413).
        key=announcement_key("went_quiet", now.isoformat(), *(row.pk for row in applications)),
        occurred_at=now,
        data={"application_ids": [row.pk for row in applications], "count": count},
    )


def snooze_until(now=None) -> dt.datetime:
    later = (now or timezone.now()) + dt.timedelta(days=SNOOZE_DAYS)
    return later.replace(second=0, microsecond=0)
