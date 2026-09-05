"""Applications: read them, record them, move them along. Writes go through the services."""

import datetime as dt

from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.pagination import paginate

from postulo.applications.models import Application, Channel, EventKind
from postulo.applications.models import Status as ApplicationStatus
from postulo.applications.services import (
    change_status,
    create_application,
    get_or_create_company,
    record_event,
)

from ..auth import actor_of, scope
from ..schemas import (
    ApplicationDetailOut,
    ApplicationIn,
    ApplicationOut,
    EventIn,
    EventOut,
    StatusIn,
    application_out,
)
from .common import choice_or_422, owned, owned_or_404, priority_or_422, tags_named

router = Router(tags=["applications"], auth=scope("read"))


@router.get("", response=list[ApplicationOut], summary="List applications")
@paginate
def list_applications(
    request,
    status: str | None = Query(None, description="One status, e.g. applied"),
    company: int | None = Query(None, description="A company id"),
    since: dt.date | None = Query(None, description="Applied on or after this date"),
    open_only: bool = Query(False, description="Only applications still undecided"),
):
    applications = owned(request, Application.objects).with_display_data().order_by("-created_at")
    if status:
        applications = applications.filter(status=status)
    if company:
        applications = applications.filter(posting__company_id=company)
    if since:
        applications = applications.filter(applied_at__date__gte=since)
    if open_only:
        applications = applications.open()
    return [application_out(request, a) for a in applications]


@router.post(
    "", response={201: ApplicationDetailOut}, auth=scope("write"), summary="Record an application"
)
def record_application(request, payload: ApplicationIn):
    """The one-step door: a listing and the application for it, as the web form does it."""
    owner = request.auth.owner
    choice_or_422(payload.status, ApplicationStatus, field="status")
    choice_or_422(payload.channel, Channel, field="channel", allow_blank=True)
    priority_or_422(payload.priority)
    company = get_or_create_company(owner, payload.company_name)
    application = create_application(
        owner,
        company=company,
        posting_data=payload.posting_data(),
        application_data=payload.application_data(),
        actor=actor_of(request),
    )
    application.tags.set(tags_named(owner, payload.tags))
    return Status(201, application_out(request, _detail(request, application.pk), detail=True))


def _detail(request, pk: int) -> Application:
    return owned_or_404(
        request,
        Application.objects.select_related("posting", "posting__company").prefetch_related(
            "tags", "events", "reminders", "rendered_documents"
        ),
        pk,
    )


@router.get(
    "/{int:pk}", response=ApplicationDetailOut, summary="One application, with its timeline"
)
def get_application(request, pk: int):
    return application_out(request, _detail(request, pk), detail=True)


@router.post(
    "/{int:pk}/status",
    response=ApplicationDetailOut,
    auth=scope("write"),
    summary="Move an application to another status",
)
def set_status(request, pk: int, payload: StatusIn):
    application = _detail(request, pk)
    choice_or_422(payload.status, ApplicationStatus, field="status")
    change_status(application, payload.status, note=payload.note, actor=actor_of(request))
    return application_out(request, _detail(request, pk), detail=True)


@router.post(
    "/{int:pk}/events",
    response={201: EventOut},
    auth=scope("write"),
    summary="Add an entry to the timeline",
)
def add_event(request, pk: int, payload: EventIn):
    application = _detail(request, pk)
    kind = choice_or_422(payload.kind, EventKind, field="kind")
    if kind == EventKind.STATUS_CHANGE:
        from ninja.errors import HttpError

        raise HttpError(422, _("Use the status endpoint to change the status."))
    event = record_event(
        application,
        kind=kind,
        summary=payload.summary,
        body=payload.body,
        occurred_at=payload.occurred_at,
        actor=actor_of(request),
    )
    return Status(
        201,
        {
            "id": event.pk,
            "kind": event.kind,
            "occurred_at": event.occurred_at,
            "summary": event.summary,
            "body": event.body,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "actor": event.actor,
        },
    )
