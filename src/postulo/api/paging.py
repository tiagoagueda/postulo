"""How a list answers: one page, and only one page's worth of work.

django-ninja's ``@paginate`` cuts the page out of whatever the view hands it. A view that
hands it a list has already built every row before the cut, so asking for a hundred
applications on a thousand-application account still counted a thousand, ran their
subqueries, walked their tag prefetch and built an absolute address for each — and an agent
walking the whole list paid that on every page, which is quadratic in the size of somebody's
search (#230). Views hand over the queryset now; `Page` shapes the rows that survived the
cut, so the work belongs to the page rather than to the account.

`changed_since` is the other half. Every list could be asked what was *applied* since a
date, and nothing could be asked what had *changed*, so a client with a copy of the search
had no way to catch up but to read all of it again and compare. Given a moment, a list
answers with what has changed at or after it, oldest change first, so a client can take the
`updated_at` of the last row it read and ask again from there. That field is on every row
for exactly this reason; the webhooks that would save the asking are their own feature (#240).

A deletion is not a change anything here can report: the row is gone, and this is a filter
over rows that exist. A client keeping a mirror still has to notice absences itself.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from django.db.models import Q
from ninja.pagination import LimitOffsetPagination

#: What every list says about its cursor, written once so every list says the same thing.
UPDATED_SINCE = (
    "Changed at or after this moment, oldest change first — the cursor for catching up "
    "rather than re-reading. Every row carries the `updated_at` to ask from next time."
)

#: The other half of the cursor. Named separately because it is optional and because a
#: caller has to be told *why* to send it (#245).
AFTER_ID = (
    "The id of the last row already read, sent with `updated_since` from that same row. "
    "Without it a run of rows saved in the same moment cannot be walked past."
)


class Page(LimitOffsetPagination):
    """``limit`` and ``offset``, with the rows shaped after the page is cut, not before.

    ``row`` is called with the request and one record, exactly as the view used to call it,
    and only for the records that are actually going out.
    """

    def __init__(self, *, row: Callable[[Any, Any], dict] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.row = row

    def paginate_queryset(self, queryset, pagination, request, **params):
        page = super().paginate_queryset(queryset, pagination, request, **params)
        if self.row is not None:
            page[self.items_attribute] = [
                self.row(request, record) for record in page[self.items_attribute]
            ]
        return page


def changed_since(queryset, since: dt.datetime | None, after_id: int | None = None):
    """What changed at or after ``since``, oldest first; or the queryset untouched.

    The ordering is the point: a caller catching up reads forward and remembers where it
    got to, and a list ordered by when things were *recorded* cannot be walked that way.
    Ties are broken by id so that two rows saved in the same moment cannot swap places
    between one page and the next.

    With ``after_id`` the cursor names a *position* in that order instead of a moment, and
    that is what makes it walkable (#245). A moment alone cannot get past a group of rows
    sharing one `updated_at`: the caller asks again from the timestamp of the last row it
    read, is handed the same group, and where the group is larger than the page it is
    handed that same page for ever -- looping, or stopping and quietly never reading the
    rest. `updated_at` is `auto_now`, so a bulk edit or an import writes exactly such a run
    of rows, and the faster the machine the longer the run. Asked without ``after_id`` it
    answers as it always did, so a caller that has not been told about it keeps working.
    """
    if since is None:
        return queryset
    queryset = queryset.order_by("updated_at", "pk")
    if after_id is None:
        return queryset.filter(updated_at__gte=since)
    return queryset.filter(Q(updated_at__gt=since) | Q(updated_at=since, pk__gt=after_id))
