"""Noticing that a listing is about to close.

`JobPosting.closes_at` has been a column since the beginning and nothing ever acted on it.
The dashboard counted the ones closing this week, which is only seen by somebody already
looking at Postulo — and a listing closing on Friday is exactly the thing somebody finds out
about on Saturday (#238).

So: one message, a configurable number of days before, about the listings the person has not
decided about yet. **Undecided is the whole of it.** A listing already applied to is not
urgent, a discarded one is not wanted, and a closed one has closed; `undecided()` is the
predicate the dashboard and the listings table already share, and this uses that rather than
a fourth opinion about what an open listing is.

**Announced once, per listing, whatever happens next.** The stamp is written before the
message goes and only if writing it changed a row, which is #221's rule: two schedulers
running together announce a closing once between them rather than once each. A listing whose
date is *moved* is announced again, because the stamp is compared against the date it was
written for — moving a deadline is news.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.notifications.base import Notification, absolute_url
from postulo.notifications.service import notify

from .models import JobPosting

logger = logging.getLogger(__name__)

#: How many days before a listing closes the message goes, unless the person has chosen
#: otherwise. Three: long enough to write something and sleep on it, short enough that the
#: message is still about this week.
DEFAULT_NOTICE_DAYS = 3

#: At most this many titles are named in one message. The same number the quiet announcement
#: uses, for the same reason: a notification is a line on a lock screen.
NAMED_IN_ANNOUNCEMENT = 5


def notice_days_for(user) -> int:
    """The person's own notice, or the default."""
    profile = getattr(user, "profile", None)
    days = getattr(profile, "closing_notice_days", None)
    return days if isinstance(days, int) and days > 0 else DEFAULT_NOTICE_DAYS


def closing_for(user, at=None):
    """The person's undecided listings closing within their notice, soonest first."""
    today = at or timezone.localdate()
    return (
        JobPosting.objects.for_user(user)
        .undecided()
        .filter(
            closes_at__gte=today,
            closes_at__lte=today + dt.timedelta(days=notice_days_for(user)),
        )
        .order_by("closes_at", "pk")
    )


def announce_closing_postings(at=None) -> tuple[int, int]:
    """Tell each person about the listings of theirs that are about to close.

    One message per person per pass, naming the listings. Returns (listings stamped,
    deliveries made) -- the shape every announcer in the scheduler returns.
    """
    today = at or timezone.localdate()
    now = timezone.now()
    # Only the people who have a listing closing at all, so a pass over an instance with a
    # thousand accounts and three dated listings asks three questions rather than a thousand.
    owners = (
        JobPosting.objects.filter(owner__is_active=True, closes_at__gte=today)
        .values_list("owner", flat=True)
        .distinct()
    )
    stamped = 0
    delivered = 0
    for owner_id in owners:
        owner = _owner_of(owner_id)
        rows = list(closing_for(owner, at=today).select_related("company"))
        # Announced again when the date moves, and never twice for the same date. The stamp
        # holds the date it was written *for*, so "already told them about the 30th" and
        # "the 30th became the 24th" are different answers.
        fresh = [row for row in rows if row.closing_announced_for != row.closes_at]
        claimed = [
            row
            for row in fresh
            if JobPosting.objects.filter(
                pk=row.pk, closing_announced_for=row.closing_announced_for
            ).update(closing_announced_for=row.closes_at, updated_at=now)
        ]
        if not claimed:
            continue
        stamped += len(claimed)
        try:
            delivered += notify(owner, lambda claimed=claimed: _announcement(claimed, today))
        except Exception:
            logger.exception("Could not announce closing listings for owner %s", owner_id)
    return stamped, delivered


def _owner_of(owner_id):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.select_related("profile").get(pk=owner_id)


def _announcement(postings: list[JobPosting], today: dt.date) -> Notification:
    """Worded when it is sent, so the count and the days come out in the reader's own
    language -- the rule #223 set for every announcement here."""
    count = len(postings)
    lines = []
    for posting in postings[:NAMED_IN_ANNOUNCEMENT]:
        days = (posting.closes_at - today).days
        lines.append(
            _("%(role)s at %(company)s — %(closing)s")
            % {
                "role": posting.title,
                "company": posting.company.name,
                "closing": (
                    _("today")
                    if days <= 0
                    else ngettext("in %(days)s day", "in %(days)s days", days) % {"days": days}
                ),
            }
        )
    if count > NAMED_IN_ANNOUNCEMENT:
        lines.append(_("and %(more)s more") % {"more": count - NAMED_IN_ANNOUNCEMENT})
    return Notification(
        event="posting_closing",
        title=ngettext(
            "%(count)s listing you are considering closes soon",
            "%(count)s listings you are considering close soon",
            count,
        )
        % {"count": count},
        body="\n".join(lines),
        url=absolute_url(reverse("listings:list")),
        # One message per person per pass, named by the listings it is about, so a notifier
        # that retries does not say it twice (#229).
        key="posting_closing:" + ",".join(str(row.pk) for row in postings),
        occurred_at=timezone.now(),
        data={
            "posting_ids": [row.pk for row in postings],
            "count": count,
            "closes_at": [row.closes_at.isoformat() for row in postings],
        },
    )
