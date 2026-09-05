"""The applications table: what it can show, sort by and narrow on."""

from django.utils.translation import gettext_lazy as _

from postulo.core.tables import Column, Table, register

from .models import Channel, Priority, Status


@register
class ApplicationsTable(Table):
    name = "applications"
    default_sort = "-created"
    extra_params = ("q", "status", "state", "tag")
    noun = (_("application"), _("applications"))
    columns = (
        Column(
            "role",
            _("Role"),
            sort=("posting__title",),
            filter="text",
            lookups=("posting__title",),
            default=True,
        ),
        Column(
            "company",
            _("Company"),
            sort=("posting__company__name",),
            filter="text",
            lookups=("posting__company__name",),
            default=True,
        ),
        Column(
            "location",
            _("Location"),
            sort=("posting__location",),
            filter="text",
            lookups=("posting__location",),
            default=True,
        ),
        # Status and tags narrow from the form above the table, which the board shares;
        # a second control for the same question would only confuse.
        Column("status", _("Status"), sort=("status",), default=True),
        Column(
            "applied",
            _("Applied"),
            sort=("applied_at",),
            newest_first=True,
            filter="date",
            lookups=("applied_at__date",),
            default=True,
        ),
        Column(
            "deadline",
            _("Deadline"),
            sort=("deadline",),
            filter="date",
            lookups=("deadline",),
        ),
        Column(
            "priority",
            _("Priority"),
            sort=("priority",),
            newest_first=True,
            filter="choice",
            lookups=("priority",),
            choices=tuple((str(value), label) for value, label in Priority.choices),
        ),
        Column(
            "channel",
            _("Applied through"),
            sort=("channel",),
            filter="choice",
            lookups=("channel",),
            choices=tuple(Channel.choices),
        ),
        Column(
            "salary",
            _("Salary"),
            sort=("posting__salary_max", "posting__salary_min"),
            newest_first=True,
            numeric=True,
        ),
        Column("tags", _("Tags")),
        Column(
            "last_activity",
            _("Last activity"),
            sort=("last_activity_at",),
            newest_first=True,
        ),
        Column(
            "next_reminder",
            _("Next reminder"),
            sort=("next_reminder_at",),
        ),
        Column(
            "next_interview",
            _("Next interview"),
            sort=("next_interview_at",),
        ),
        Column("created", _("Recorded"), sort=("created_at",), newest_first=True),
    )


#: Offered by the status filter above the table.
STATUSES = Status.choices
