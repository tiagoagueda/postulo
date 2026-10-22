"""What a company is, and what it is doing about hiring (#941).

Two things kept apart. The *legal state* is a fact about the company -- active, in
administration, acquired, dissolved -- set by hand or confirmed from Wikidata, and shared with
everybody who can see the company. The *hiring signal* is dated: each entry says what was
reported, as of when, by whom and from where, and the latest one is the signal. Entries older
than `SIGNAL_STALE_AFTER_DAYS` are shown as *as of <date>*, so an old "hiring" does not read
as current.

Postulo also *suggests* hiring when the company has listings that have not closed. That is
worked out as a page is drawn and stored nowhere, so nobody is credited with having said it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from . import structure
from .models import (
    Company,
    CompanyHiringSignal,
    HiringSignalKind,
    JobPosting,
    LegalState,
    shut_condition,
)

#: The tone each state or signal is drawn in: neutral for the facts, amber for the ones that
#: ask the reader to look twice. Never red -- the labels are facts, not verdicts.
STATE_TONES = {
    LegalState.ACTIVE: "grey",
    LegalState.ADMINISTRATION: "amber",
    LegalState.ACQUIRED: "grey",
    LegalState.DISSOLVED: "amber",
}
SIGNAL_TONES = {
    HiringSignalKind.HIRING: "green",
    HiringSignalKind.FREEZE: "grey",
    HiringSignalKind.LAYING_OFF: "amber",
}


def open_listing_count(company, user) -> int:
    """The listings at ``company`` that ``user`` can see and that have not closed: marked
    closed or past their closing date (the same meaning the listings tabs give *closed*)."""
    return (
        JobPosting.objects.visible_to(user)
        .filter(company=company)
        .exclude(shut_condition())
        .count()
    )


def touch(company) -> None:
    """Stamp ``company`` as changed: its signals are part of what the API and an archive say
    about it, so a client asking what changed since it last looked is told (#941)."""
    from django.utils import timezone

    Company.objects.filter(pk=company.pk).update(updated_at=timezone.now())


def latest(signals) -> CompanyHiringSignal | None:
    """The newest of ``signals`` (a queryset, a prefetched manager's list or any iterable):
    by the date it is as of, then by when it was recorded."""
    rows = list(signals)
    return max(rows, key=lambda row: (row.noted_on, row.pk), default=None)


def acquirer_for(company, person):
    """The company this one was acquired by, when the state says so and ``person`` may see
    it -- an acquirer somebody else keeps privately is not named to them (#957)."""
    if company.legal_state != LegalState.ACQUIRED or company.acquired_by_id is None:
        return None
    target = company.acquired_by
    return target if target is not None and structure._seen(target, person) else None


@dataclass
class Status:
    """Everything a page draws about a company's status, worked out once."""

    company: Company
    state: str
    state_label: str
    state_tone: str
    since: date | None
    source: str
    acquirer: Company | None
    signal: CompanyHiringSignal | None
    signal_label: str = ""
    signal_tone: str = "grey"
    stale: bool = False
    open_listings: int = 0
    history: list = field(default_factory=list)

    @property
    def shows_state(self) -> bool:
        return self.state != LegalState.ACTIVE

    @property
    def suggests_hiring(self) -> bool:
        """Listings are open and nobody has a current word against it: a freeze or layoffs
        reported within the stale age outrank a count of listings."""
        current_against = (
            self.signal is not None
            and not self.stale
            and self.signal.kind in (HiringSignalKind.FREEZE, HiringSignalKind.LAYING_OFF)
        )
        fresh_hiring = (
            self.signal is not None
            and not self.stale
            and self.signal.kind == HiringSignalKind.HIRING
        )
        return self.open_listings > 0 and not current_against and not fresh_hiring

    @property
    def open_listings_label(self) -> str:
        return ngettext("%(count)d open listing", "%(count)d open listings", self.open_listings) % {
            "count": self.open_listings
        }

    @property
    def shown(self) -> bool:
        return bool(self.shows_state or self.signal or self.suggests_hiring)


def for_company(company, person, *, signals=None) -> Status:
    """``company``'s status as ``person`` sees it. ``signals`` may be passed when the caller
    has them already (a prefetched ``company.signals.all()``)."""
    rows = list(company.signals.all() if signals is None else signals)
    rows.sort(key=lambda row: (row.noted_on, row.pk), reverse=True)
    newest = rows[0] if rows else None
    return Status(
        company=company,
        state=company.legal_state,
        state_label=str(LegalState(company.legal_state).label),
        state_tone=STATE_TONES.get(company.legal_state, "grey"),
        since=company.legal_state_since,
        source=company.legal_state_source,
        acquirer=acquirer_for(company, person),
        signal=newest,
        signal_label=str(HiringSignalKind(newest.kind).label) if newest else "",
        signal_tone=SIGNAL_TONES.get(newest.kind, "grey") if newest else "grey",
        stale=bool(newest and newest.is_stale),
        open_listings=open_listing_count(company, person),
        history=rows,
    )


def warning(company, signals=None) -> str:
    """The quiet sentence a listing's or an application's page shows when its company is
    dissolved, in administration, or laying people off -- or nothing (#941).

    Worded as the fact and its date, in the neutral words the labels use: it says what was
    recorded, not what to do about it.
    """
    when = company.legal_state_since
    day = date_format(when, "DATE_FORMAT") if when else ""
    if company.legal_state == LegalState.DISSOLVED:
        if day:
            return _("This company was dissolved on %(date)s.") % {"date": day}
        return _("This company is recorded as dissolved.")
    if company.legal_state == LegalState.ADMINISTRATION:
        if day:
            return _("This company has been in administration since %(date)s.") % {"date": day}
        return _("This company is recorded as being in administration.")
    newest = latest(company.signals.all() if signals is None else signals)
    if newest is not None and newest.kind == HiringSignalKind.LAYING_OFF and not newest.is_stale:
        return _("This company was reported laying people off as of %(date)s.") % {
            "date": date_format(newest.noted_on, "DATE_FORMAT")
        }
    return ""


def is_closed_down(company) -> bool:
    """Whether adding a listing for ``company`` should first ask: it is recorded dissolved."""
    return company.legal_state == LegalState.DISSOLVED
