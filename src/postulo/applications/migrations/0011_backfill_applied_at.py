"""Give applications that were sent a date they were sent on (#222).

`applied_at` was written only when an application passed through the literal *Applied*, so
anybody who recorded a reply they already had — straight to *Interviewing*, or to *Rejected* —
has applications that count in no figure measured from that date: the sent total, the reply
and interview times, the sources, the months, the report an employment office reads.

The log already knows. Every status change is an `ApplicationEvent` with `to_status`, so the
first move into a status that means *sent* is when it went out, and that is what this writes.

An application with no such event keeps a null date rather than being given today's: that is
a row whose history does not say when it was sent, and inventing a date would put it in the
wrong month rather than leaving it honestly unknown.
"""

from __future__ import annotations

from django.db import migrations

#: Status values that mean it was actually sent. Written out rather than imported: a migration
#: describes the world as it was, and `SENT_STATUSES` is free to change afterwards.
SENT = (
    "applied",
    "acknowledged",
    "screening",
    "interviewing",
    "assessment",
    "offer",
    "accepted",
    "rejected",
    "ghosted",
)


def fill(apps, schema_editor) -> None:
    application_model = apps.get_model("applications", "Application")
    event_model = apps.get_model("applications", "ApplicationEvent")

    for application in application_model.objects.filter(applied_at__isnull=True).exclude(
        status="draft"
    ):
        first = (
            event_model.objects.filter(application=application, to_status__in=SENT)
            .order_by("occurred_at", "pk")
            .first()
        )
        if first is None:
            continue
        application.applied_at = first.occurred_at
        application.save(update_fields=["applied_at"])


class Migration(migrations.Migration):
    dependencies = [("applications", "0010_channel_employment_service")]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
