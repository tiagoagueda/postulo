"""Reminders: the nudges a person set for themselves."""

from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.models import Application, Reminder

from ..auth import scope
from ..schemas import ReminderIn, ReminderOut, reminder_out
from .common import owned, owned_or_404

router = Router(tags=["reminders"], auth=scope("read"))


@router.get("", response=list[ReminderOut], summary="List reminders")
@paginate
def list_reminders(
    request,
    due: bool = Query(False, description="Only outstanding reminders whose time has come"),
    outstanding: bool = Query(False, description="Only reminders not yet done"),
):
    reminders = owned(request, Reminder.objects).order_by("due_at")
    if due:
        reminders = reminders.due()
    elif outstanding:
        reminders = reminders.outstanding()
    return [reminder_out(r) for r in reminders]


@router.post("", response={201: ReminderOut}, auth=scope("write"), summary="Add a reminder")
def add_reminder(request, payload: ReminderIn):
    owner = request.auth.owner
    application = None
    if payload.application_id is not None:
        application = owned(request, Application.objects).filter(pk=payload.application_id).first()
        if application is None:
            raise HttpError(404, "No such application.")
    reminder = Reminder.objects.create(
        owner=owner, application=application, summary=payload.summary, due_at=payload.due_at
    )
    return Status(201, reminder_out(reminder))


@router.post(
    "/{int:pk}/complete", response=ReminderOut, auth=scope("write"), summary="Mark a reminder done"
)
def complete_reminder(request, pk: int):
    reminder = owned_or_404(request, Reminder.objects, pk)
    reminder.complete()
    return reminder_out(reminder)
