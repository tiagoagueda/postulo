"""Which of Postulo's marks a page shows, and in which files (#1238).

The mark is served as a vector first -- ``brand/logo.svg``, sharp at any zoom -- with PNGs
behind it for what does not take one. Two things change which files those are:

- **June.** The interface shows the pride mark for the month, in the person's own time zone
  (the instance's for a visitor), which `preferences.apply` has already put in force. No
  setting: it is the month, not a choice. The tab and the header change; the icons a
  phone installs do not, because an icon put on a home screen in June stays there all year.
- **The moving mark**, which a person switches on under Appearance. The page offers the
  moving file only to a browser whose system does not ask for less motion, and the still one
  otherwise, so the choice never overrides that request.

The files themselves are made by ``scripts/brand.py`` from ``assets/brand/``.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

#: The month the pride mark is shown in.
PRIDE_MONTH = 6


def pride_month(now: datetime.datetime | None = None) -> bool:
    """Whether it is June where the person is: the active time zone's date, not the server's."""
    return timezone.localtime(now).month == PRIDE_MONTH


def files(*, moving: bool = False, now: datetime.datetime | None = None) -> dict:
    """The static paths a page draws the mark from.

    ``svg`` is the still vector; ``moving`` the animated one, or empty when the person has
    not asked for it; ``png`` the header's fallback; ``favicon_16`` and ``favicon_32`` the
    tab's, for a browser that does not take the vector as an icon.
    """
    pride = "-pride" if pride_month(now) else ""
    return {
        "pride": bool(pride),
        "svg": f"brand/logo{pride}.svg",
        "moving": f"brand/logo{pride}-drift.svg" if moving else "",
        "png": f"brand/logo{pride}-64.png",
        "favicon_16": f"brand/favicon{pride}-16.png",
        "favicon_32": f"brand/favicon{pride}-32.png",
    }
