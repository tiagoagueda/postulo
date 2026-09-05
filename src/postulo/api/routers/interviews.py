"""Interviews: the diary, readable by a calendar sync and writable by an agent."""

import datetime as dt

from django.http import HttpResponse
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications import ical
from postulo.applications.models import (
    SETTLED_OUTCOMES,
    Application,
    Interview,
    InterviewKind,
)
from postulo.applications.services import (
    reschedule_interview,
    schedule_interview,
    settle_interview,
)
from postulo.jobs.models import Contact

from ..auth import actor_of, scope
from ..schemas import InterviewIn, InterviewOut, InterviewOutcomeIn, InterviewPatch, interview_out
from .common import choice_or_422, owned, owned_or_404

router = Router(tags=["interviews"], auth=scope("read"))


def _queryset(request):
    return owned(request, Interview.objects).with_display_data()


@router.get("", response=list[InterviewOut], summary="List interviews")
@paginate
def list_interviews(
    request,
    state: str = Query(
        "upcoming",
        description="upcoming (default: scheduled and not over), scheduled, past or all",
    ),
    application: int | None = Query(None, description="Only this application's"),
    since: dt.datetime | None = Query(None, description="Starting on or after this moment"),
):
    interviews = _queryset(request)
    if state == "upcoming":
        interviews = interviews.upcoming()
    elif state == "scheduled":
        interviews = interviews.scheduled().order_by("starts_at")
    elif state == "past":
        interviews = interviews.exclude(pk__in=interviews.upcoming()).order_by("-starts_at")
    elif state == "all":
        interviews = interviews.order_by("starts_at")
    else:
        raise HttpError(422, "'state' must be upcoming, scheduled, past or all.")
    if application:
        interviews = interviews.filter(application_id=application)
    if since:
        interviews = interviews.filter(starts_at__gte=since)
    return [interview_out(request, i) for i in interviews]


@router.get("/calendar.ics", summary="Everything still ahead, as an iCalendar file")
def calendar_feed(request):
    text = ical.calendar(
        _queryset(request).upcoming(),
        url_for=lambda i: request.build_absolute_uri(i.application.get_absolute_url()),
    )
    return HttpResponse(text, content_type="text/calendar; charset=utf-8")


@router.get("/{int:pk}", response=InterviewOut, summary="One interview")
def get_interview(request, pk: int):
    return interview_out(request, owned_or_404(request, _queryset(request), pk))


@router.get(
    "/{int:pk}/calendar.ics",
    url_name="interview_calendar",
    summary="One interview, as an iCalendar file",
)
def interview_calendar(request, pk: int):
    interview = owned_or_404(request, _queryset(request), pk)
    text = ical.calendar(
        [interview],
        url_for=lambda i: request.build_absolute_uri(i.application.get_absolute_url()),
    )
    response = HttpResponse(text, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="interview-{pk}.ics"'
    return response


def _contacts(request, application: Application, ids: list[int]) -> list[Contact]:
    """The application's company's people with these ids; anyone else is a 422."""
    if not ids:
        return []
    people = list(
        owned(request, Contact.objects).filter(pk__in=ids, company=application.posting.company_id)
    )
    if len(people) != len(set(ids)):
        raise HttpError(422, "'contact_ids' must all be people at the application's company.")
    return people


@router.post("", response={201: InterviewOut}, auth=scope("write"), summary="Schedule an interview")
def add_interview(request, payload: InterviewIn):
    application = owned(request, Application.objects).filter(pk=payload.application_id).first()
    if application is None:
        raise HttpError(404, "No such application.")
    choice_or_422(payload.kind, InterviewKind, field="kind")
    if payload.ends_at is not None and payload.ends_at <= payload.starts_at:
        raise HttpError(422, "'ends_at' must be after 'starts_at'.")
    interview = schedule_interview(
        application,
        kind=payload.kind,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        location=payload.location,
        notes=payload.notes,
        contacts=_contacts(request, application, payload.contact_ids),
        remind=payload.remind,
        actor=actor_of(request),
    )
    return Status(
        201, interview_out(request, owned_or_404(request, _queryset(request), interview.pk))
    )


@router.patch(
    "/{int:pk}", response=InterviewOut, auth=scope("write"), summary="Change an interview"
)
def change_interview(request, pk: int, payload: InterviewPatch):
    interview = owned_or_404(request, _queryset(request), pk)
    data = payload.dict(exclude_unset=True)
    if "kind" in data:
        choice_or_422(data["kind"], InterviewKind, field="kind")
    contact_ids = data.pop("contact_ids", None)
    starts_at = data.pop("starts_at", interview.starts_at)
    ends_at = data.pop("ends_at", None) or (
        interview.ends_at if starts_at == interview.starts_at else starts_at + interview.duration
    )
    if ends_at <= starts_at:
        raise HttpError(422, "'ends_at' must be after 'starts_at'.")
    for name, value in data.items():
        setattr(interview, name, value)
    interview.save()
    if contact_ids is not None:
        interview.contacts.set(_contacts(request, interview.application, contact_ids))
    reschedule_interview(interview, starts_at=starts_at, ends_at=ends_at, actor=actor_of(request))
    return interview_out(request, owned_or_404(request, _queryset(request), pk))


@router.post(
    "/{int:pk}/outcome",
    response=InterviewOut,
    auth=scope("write"),
    summary="Record how it went: done, cancelled or no_show",
)
def record_outcome(request, pk: int, payload: InterviewOutcomeIn):
    interview = owned_or_404(request, _queryset(request), pk)
    if payload.outcome not in SETTLED_OUTCOMES:
        raise HttpError(422, f"'outcome' must be one of {sorted(SETTLED_OUTCOMES)}.")
    settle_interview(interview, payload.outcome, note=payload.note, actor=actor_of(request))
    return interview_out(request, owned_or_404(request, _queryset(request), pk))
