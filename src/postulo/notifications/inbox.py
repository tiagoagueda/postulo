"""Where a browser notification waits for a Postulo tab, and how a tab collects it (#209).

The browser notifier calls :func:`leave`; an open tab calls :func:`collect` through
``notifications:waiting``. Everything is scoped to the person, and nothing else reads it.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

#: How long an uncollected notice is kept. A week covers somebody who closed the laptop on
#: Friday; past that the notice is about something they have already seen elsewhere.
KEPT_FOR = dt.timedelta(days=7)

#: Two notices the same within this window are one. A person with two browsers in tab-only
#: mode has two connections, and each would otherwise leave its own copy for the same tab.
SAME_WITHIN = dt.timedelta(minutes=1)

#: The most one collection hands a tab. Anything past it is collected on the next one.
AT_ONCE = 20

#: What fits in a notification. Operating systems cut far shorter than this anyway.
TITLE_LENGTH = 300
BODY_LENGTH = 1000


def leave(user, notification) -> bool:
    """Keep ``notification`` for ``user``'s next open tab. False if it was already waiting."""
    from .models import BrowserNotice

    now = timezone.now()
    mine = BrowserNotice.objects.for_user(user)
    mine.filter(shown_at__isnull=False).delete()
    mine.filter(created_at__lt=now - KEPT_FOR).delete()

    title = str(notification.title)[:TITLE_LENGTH]
    body = str(notification.body or "")[:BODY_LENGTH]
    url = str(notification.url or "")[:500]
    if mine.filter(
        shown_at__isnull=True,
        event=notification.event,
        title=title,
        body=body,
        url=url,
        created_at__gte=now - SAME_WITHIN,
    ).exists():
        return False
    BrowserNotice.objects.create(
        owner=user, event=notification.event, title=title, body=body, url=url
    )
    return True


def collect(user) -> list[dict]:
    """Hand ``user``'s waiting notices to a tab, oldest first, and mark them shown.

    Marked shown by id rather than by "everything waiting", so a notice left between the read
    and the update is not marked shown without having been handed to anybody.
    """
    from .models import BrowserNotice

    waiting = list(
        BrowserNotice.objects.for_user(user)
        .filter(shown_at__isnull=True)
        .order_by("created_at")[:AT_ONCE]
    )
    if not waiting:
        return []
    BrowserNotice.objects.for_user(user).filter(
        pk__in=[notice.pk for notice in waiting], shown_at__isnull=True
    ).update(shown_at=timezone.now())
    return [
        {
            "title": notice.title,
            "body": notice.body,
            "url": notice.url,
            "tag": f"notice-{notice.pk}",
        }
        for notice in waiting
    ]
