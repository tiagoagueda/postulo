"""Reminders: the nudges a person set for themselves."""

import datetime as dt

from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.models import Application, Reminder
from postulo.applications.services import postpone_reminder

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Page, changed_since
from ..schemas import ReminderIn, ReminderOut, ReminderPatch, reminder_out
from .common import owned, owned_or_404

router = Router(tags=["reminders"], auth=scope("read"))


@router.get("", response=list[ReminderOut], summary="List reminders")
@paginate(Page, row=lambda request, reminder: reminder_out(reminder))
def list_reminders(
    request,
    due: bool = Query(False, description="Only outstanding reminders whose time has come"),
    outstanding: bool = Query(False, description="Only reminders not yet done"),
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    reminders = owned(request, Reminder.objects).order_by("due_at")
    if due:
        reminders = reminders.due()
    elif outstanding:
        reminders = reminders.outstanding()
    return changed_since(reminders, updated_since, after_id)


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


@router.patch("/{int:pk}", response=ReminderOut, auth=scope("write"), summary="Change a reminder")
def change_reminder(request, pk: int, payload: ReminderPatch):
    """Change what a reminder says, when it falls due, or which application it is about.

    A reminder could be made and it could be ticked off, and that was all -- through the
    interface and through here alike (#238). A due time typed wrongly could only be ticked
    off and written again, which loses the fact that it was ever set.

    Moving the time clears the *announced* stamp, through the same service the interface
    uses: a reminder somebody has deliberately moved into the future has not been announced
    at its new time, and leaving the stamp would move it and silence it.
    """
    reminder = owned_or_404(request, Reminder.objects, pk)
    data = payload.dict(exclude_unset=True)
    if "application_id" in data:
        application = None
        if data["application_id"] is not None:
            application = (
                owned(request, Application.objects).filter(pk=data["application_id"]).first()
            )
            if application is None:
                raise HttpError(404, "No such application.")
        reminder.application = application
    if data.get("summary") is not None:
        reminder.summary = data["summary"]
    reminder.save()
    if data.get("due_at") is not None:
        postpone_reminder(reminder, data["due_at"])
    return reminder_out(reminder)


@router.delete("/{int:pk}", response={204: None}, auth=scope("write"), summary="Delete a reminder")
def delete_reminder(request, pk: int):
    """Remove a reminder. The first thing the API deletes, and deliberately the smallest.

    A reminder is a note somebody wrote to themselves, holds no history and is referred to
    by nothing; deleting one loses a line they typed and nothing else. That is not true of
    an application, a company or a timeline entry, each of which is a record of something
    that happened, so *nothing is deleted through the API* remains the rule everywhere
    else (#238).
    """
    owned_or_404(request, Reminder.objects, pk).delete()
    return Status(204, None)
