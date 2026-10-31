"""Interviews: the diary, readable by a calendar sync and writable by an agent."""

from django.http import HttpResponse
from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications import agenda, ical
from postulo.applications.models import (
    SETTLED_OUTCOMES,
    Application,
    Appointment,
    Interview,
    InterviewKind,
)
from postulo.applications.services import (
    InterviewRefused,
    reschedule_interview,
    schedule_interview,
    settle_interview,
)
from postulo.jobs.models import Contact

from ..auth import actor_of, scope
from ..paging import AFTER_ID, UPDATED_SINCE, Moment, Page, RowId, changed_since
from ..problems import refuses
from ..schemas import InterviewIn, InterviewOut, InterviewOutcomeIn, InterviewPatch, interview_out
from .common import choice_or_422, owned, owned_or_404

router = Router(tags=["interviews"], auth=scope("read"))


def _queryset(request):
    return owned(request, Interview.objects).with_display_data()


@router.get("", response=list[InterviewOut], summary="List interviews")
@paginate(Page, row=interview_out)
def list_interviews(
    request,
    state: str = Query(
        "upcoming",
        description="upcoming (default: scheduled and not over), scheduled, past or all",
    ),
    application: RowId | None = Query(None, description="Only this application's"),
    since: Moment | None = Query(None, description="Starting on or after this moment"),
    updated_since: Moment | None = Query(None, description=UPDATED_SINCE),
    after_id: RowId | None = Query(None, description=AFTER_ID),
):
    interviews = _queryset(request)
    if state == "upcoming":
        interviews = interviews.upcoming()
    elif state == "scheduled":
        interviews = interviews.scheduled().order_by("starts_at", "pk")
    elif state == "past":
        interviews = interviews.exclude(pk__in=interviews.upcoming()).order_by("-starts_at", "-pk")
    elif state == "all":
        interviews = interviews.order_by("starts_at", "pk")
    else:
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s.")
            % {"field": "'state'", "choices": ["upcoming", "scheduled", "past", "all"]},
        )
    if application:
        interviews = interviews.filter(application_id=application)
    if since:
        interviews = interviews.filter(starts_at__gte=since)
    return changed_since(interviews, updated_since, after_id)


@router.get("/calendar.ics", summary="Everything still ahead, as an iCalendar file")
def calendar_feed(request):
    text = ical.calendar(
        _queryset(request).upcoming(),
        url_for=lambda i: request.build_absolute_uri(i.application.get_absolute_url()),
        days=agenda.dated_days(request.auth.owner, request.build_absolute_uri),
        appointments=Appointment.objects.for_user(request.auth.owner)
        .with_display_data()
        .upcoming(),
        appointment_url_for=lambda a: request.build_absolute_uri(a.get_absolute_url()),
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


def _not_at_the_company() -> HttpError:
    return HttpError(
        422,
        _("%(field)s must all be people at the application's company.")
        % {"field": "'contact_ids'"},
    )


def _ends_before_it_starts() -> HttpError:
    return HttpError(
        422,
        _("%(later)s must be after %(earlier)s.")
        % {"later": "'ends_at'", "earlier": "'starts_at'"},
    )


def _people(request, ids: list[int]) -> list[Contact]:
    """The caller's own contacts with these ids; an id that is not one of them is a 422.

    One answer for somebody else's contact and for one that does not exist, so the refusal
    confirms nothing about another account. Whether they are at the right company is the
    service's to say (#330).
    """
    if not ids:
        return []
    people = list(Contact.objects.for_user(request.auth.owner).filter(pk__in=ids))
    if len(people) != len(set(ids)):
        raise _not_at_the_company()
    return people


def _contacts(request, application: Application, ids: list[int]) -> list[Contact]:
    """The application's company's people with these ids; anyone else is a 422."""
    people = _people(request, ids)
    if any(person.company_id != application.posting.company_id for person in people):
        raise _not_at_the_company()
    return people


def _refused(refusal: InterviewRefused, payload: InterviewIn) -> HttpError:
    """What `schedule_interview` refused, in the words this call has always answered with.

    The service decides (#330); the API only keeps its own wording, which names the field as
    the payload spells it -- a client written against these words should not find them changed.
    """
    if refusal.field == "kind":
        try:
            choice_or_422(payload.kind, InterviewKind, field="kind")
        except HttpError as error:
            return error
    if refusal.field == "ends_at":
        return _ends_before_it_starts()
    if refusal.field == "contacts":
        return _not_at_the_company()
    return HttpError(422, str(refusal))


@router.post("", response={201: InterviewOut}, auth=scope("write"), summary="Schedule an interview")
@refuses(404)  # an `application_id` that is not the caller's
def add_interview(request, payload: InterviewIn):
    application = owned(request, Application.objects).filter(pk=payload.application_id).first()
    if application is None:
        raise HttpError(404, _("No such application."))
    try:
        # The kind, the end and the people are the service's to refuse (#330), so this call,
        # the form and a plugin are refused alike.
        interview = schedule_interview(
            application,
            kind=payload.kind,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            location=payload.location,
            notes=payload.notes,
            contacts=_people(request, payload.contact_ids),
            remind=payload.remind,
            actor=actor_of(request),
        )
    except InterviewRefused as refusal:
        raise _refused(refusal, payload) from refusal
    return Status(
        201, interview_out(request, owned_or_404(request, _queryset(request), interview.pk))
    )


@router.patch(
    "/{int:pk}", response=InterviewOut, auth=scope("write"), summary="Change an interview"
)
def change_interview(request, pk: int, payload: InterviewPatch):
    interview = owned_or_404(request, _queryset(request), pk)
    data = {
        name: value for name, value in payload.dict(exclude_unset=True).items() if value is not None
    }
    if "kind" in data:
        choice_or_422(data["kind"], InterviewKind, field="kind")
    contact_ids = data.pop("contact_ids", None)
    starts_at = data.pop("starts_at", interview.starts_at)
    ends_at = data.pop("ends_at", None) or (
        interview.ends_at if starts_at == interview.starts_at else starts_at + interview.duration
    )
    if ends_at <= starts_at:
        raise _ends_before_it_starts()
    # Looked up before anything is saved, so a refusal leaves the interview as it was (#435).
    contacts = (
        None if contact_ids is None else _contacts(request, interview.application, contact_ids)
    )
    for name, value in data.items():
        setattr(interview, name, value)
    interview.save()
    if contacts is not None:
        interview.contacts.set(contacts)
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
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s.")
            % {"field": "'outcome'", "choices": sorted(SETTLED_OUTCOMES)},
        )
    settle_interview(interview, payload.outcome, note=payload.note, actor=actor_of(request))
    return interview_out(request, owned_or_404(request, _queryset(request), pk))
