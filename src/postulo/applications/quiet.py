"""Noticing silence.

An employer that simply stops replying is the most common ending, and ``Status.GHOSTED``
exists to record it — but nothing noticed the silence before it was named. An application
has *gone quiet* when it is open, was actually sent, nothing has happened to it for a
while, and nothing is planned: no reminder ahead, no interview in the diary. If the person
has already planned the next step it is not quiet, it is waiting.

The threshold is per person, because the right number depends on the market and the
person's patience; the default follows recruiters' own advice, which clusters around two
to three weeks. Everything that shows or counts quiet applications uses the one predicate
here, so the dashboard, the board, the table, the figures and the notifier all agree.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.notifications.base import Notification, absolute_url
from postulo.notifications.service import notify

from .models import Application

#: Days without activity after which an open application counts as quiet, unless the
#: person has chosen otherwise under Settings.
DEFAULT_QUIET_AFTER_DAYS = 21

#: How far out *Snooze* sets its reminder.
SNOOZE_DAYS = 14

#: At most this many titles are named in one announcement.
NAMED_IN_ANNOUNCEMENT = 5


def threshold_for(user) -> int:
    """The person's own threshold, or the default."""
    profile = getattr(user, "profile", None)
    days = getattr(profile, "quiet_after_days", None)
    return days if isinstance(days, int) and days > 0 else DEFAULT_QUIET_AFTER_DAYS


def quiet_applications(user, at=None):
    """The person's applications that have gone quiet, longest silence first."""
    return (
        Application.objects.for_user(user)
        .with_display_data()
        .quiet(threshold_for(user), at=at)
        .order_by("last_activity_at", "pk")
    )


def announce_quiet_applications(at=None) -> tuple[int, int]:
    """Tell each person about the applications that have newly gone quiet.

    One message per person per pass, naming the applications. Each application is
    announced once per silence: the stamp is compared with the last activity, so one
    that woke up and went quiet again is announced again, and one that stayed quiet is
    not repeated every pass. Returns (applications stamped, deliveries made).
    """
    now = at or timezone.now()
    owners = (
        Application.objects.filter(owner__is_active=True).values_list("owner", flat=True).distinct()
    )
    stamped = 0
    delivered = 0
    for owner_id in owners:
        rows = list(
            Application.objects.filter(owner_id=owner_id)
            .select_related("owner", "owner__profile", "posting", "posting__company")
            .quiet(threshold_for(_owner_of(owner_id)), at=now)
            .order_by("last_activity_at", "pk")
        )
        fresh = [
            row
            for row in rows
            if row.quiet_announced_at is None or row.quiet_announced_at < row.last_activity_at
        ]
        if not fresh:
            continue
        owner = fresh[0].owner
        delivered += notify(owner, _announcement(fresh, now))
        for row in fresh:
            row.quiet_announced_at = now
            row.save(update_fields=["quiet_announced_at", "updated_at"])
            stamped += 1
    return stamped, delivered


def _owner_of(owner_id):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.select_related("profile").get(pk=owner_id)


def _announcement(applications: list[Application], now) -> Notification:
    count = len(applications)
    lines = [
        _("%(role)s at %(company)s — %(days)s days")
        % {
            "role": row.posting.title,
            "company": row.posting.company.name,
            "days": (now - row.last_activity_at).days,
        }
        for row in applications[:NAMED_IN_ANNOUNCEMENT]
    ]
    if count > NAMED_IN_ANNOUNCEMENT:
        lines.append(_("and %(more)s more") % {"more": count - NAMED_IN_ANNOUNCEMENT})
    return Notification(
        event="went_quiet",
        title=ngettext(
            "%(count)s application has gone quiet",
            "%(count)s applications have gone quiet",
            count,
        )
        % {"count": count},
        body="\n".join(lines),
        url=absolute_url(reverse("applications:list") + "?quiet=1"),
    )


def quiet_by(applications: list[Application], key) -> dict[str, int]:
    """Count quiet applications grouped by ``key(application)``, for the figures."""
    counts: dict[str, int] = defaultdict(int)
    for application in applications:
        counts[key(application)] += 1
    return dict(counts)


def snooze_until(now=None) -> dt.datetime:
    return (now or timezone.now()) + dt.timedelta(days=SNOOZE_DAYS)
