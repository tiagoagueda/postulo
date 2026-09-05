"""Operations that touch more than one model.

Status changes and intake live here rather than in a view or a signal. A signal would
fire on fixtures, imports and admin edits, where an automatic event entry is usually
wrong; a view would mean the next view has to remember the same steps. A named function
that the caller invokes on purpose is easier to read and easier to test.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.jobs.models import Company, JobPosting

from .models import OPEN_STATUSES, Application, ApplicationEvent, EventKind, Status


def record_event(
    application: Application,
    *,
    kind: str = EventKind.NOTE,
    summary: str = "",
    body: str = "",
    occurred_at=None,
    from_status: str = "",
    to_status: str = "",
) -> ApplicationEvent:
    """Append one entry to an application's timeline."""
    return ApplicationEvent.objects.create(
        application=application,
        kind=kind,
        summary=summary,
        body=body,
        occurred_at=occurred_at or timezone.now(),
        from_status=from_status,
        to_status=to_status,
    )


@transaction.atomic
def change_status(
    application: Application,
    new_status: str,
    *,
    note: str = "",
    occurred_at=None,
) -> ApplicationEvent | None:
    """Move an application to ``new_status`` and record why.

    Returns the event, or ``None`` if the status was already that value — re-saving a
    form should not litter the timeline with entries saying nothing changed.

    Two timestamps are maintained as a side effect, because deriving them from the log
    on every read would be needless work:

    ``applied_at``
        Set the first time the application reaches "applied", and never moved
        afterwards. It is the date you actually applied, which is what response times
        are measured from.

    ``closed_at``
        Set when the outcome is settled, and cleared if the application reopens — which
        does happen, when a company comes back weeks after a rejection.
    """
    previous = application.status
    if previous == new_status:
        return None

    application.status = new_status
    changed = ["status", "updated_at"]

    if new_status == Status.APPLIED and application.applied_at is None:
        application.applied_at = occurred_at or timezone.now()
        changed.append("applied_at")

    if new_status in OPEN_STATUSES:
        if application.closed_at is not None:
            application.closed_at = None
            changed.append("closed_at")
    elif application.closed_at is None:
        application.closed_at = occurred_at or timezone.now()
        changed.append("closed_at")

    application.save(update_fields=changed)

    return record_event(
        application,
        kind=EventKind.STATUS_CHANGE,
        summary=str(
            _("%(old)s → %(new)s")
            % {
                "old": Status(previous).label,
                "new": Status(new_status).label,
            }
        ),
        body=note,
        occurred_at=occurred_at,
        from_status=previous,
        to_status=new_status,
    )


@transaction.atomic
def create_listing(owner, *, company: Company, posting_data: dict) -> JobPosting:
    """Add a listing: a posting the person has noticed and not yet decided about."""
    return JobPosting.objects.create(owner=owner, company=company, **posting_data)


@transaction.atomic
def apply_to_listing(posting: JobPosting, application_data: dict) -> Application:
    """The decision: an application for a listing.

    The listing's derived state becomes *applied* by the mere existence of the
    application; only the moment of decision is written down.
    """
    application_data = dict(application_data)
    status = application_data.pop("status", Status.DRAFT)
    application = Application.objects.create(
        owner=posting.owner, posting=posting, status=Status.DRAFT, **application_data
    )

    record_event(
        application,
        kind=EventKind.NOTE,
        summary=str(_("Application created")),
        occurred_at=application.created_at,
    )
    if status != Status.DRAFT:
        change_status(application, status)

    if posting.decided_at is None:
        posting.decided_at = timezone.now()
        posting.save(update_fields=["decided_at", "updated_at"])
    return application


@transaction.atomic
def create_application(owner, *, company: Company, posting_data: dict, application_data: dict):
    """Record a listing and an application for it in one step.

    Applications are almost always entered while looking at a posting, so asking someone
    to add a listing and then apply to it would be two forms for one thought. Underneath
    it is exactly those two steps, so the data is the same whichever door was used.
    """
    posting = create_listing(owner, company=company, posting_data=posting_data)
    return apply_to_listing(posting, application_data)


def get_or_create_company(owner, name: str) -> Company:
    """Find this owner's company by name, case-insensitively, or add it.

    Matching loosely on the way in avoids ending up with "Acme", "acme" and "ACME" as
    three separate employers after a few weeks of typing.
    """
    name = name.strip()
    existing = Company.objects.for_user(owner).filter(name__iexact=name).first()
    if existing is not None:
        return existing
    return Company.objects.create(owner=owner, name=name)
