"""The applications table: what it can show, sort by and narrow on."""

from typing import ClassVar

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy

from postulo.core.models import Tag
from postulo.core.tables import Column, ExtraFilter, Table, register

from .models import Channel, Priority, Status


@register
class ApplicationsTable(Table):
    name = "applications"
    label = _("Applications")
    default_sort = "-created"
    #: A table or a board of the same rows (#102); the board is drawn by the same view.
    shapes = ("table", "board")
    SHAPE_LABELS: ClassVar[dict[str, str]] = {"table": _("Table"), "board": _("Board")}
    #: The masthead's box (#313). The other four are `extra_filters`, below.
    extra_params = ("q",)
    #: The four questions that were a form above the table, and are a form above the board
    #: still (#314). Each sits in the header of the column that shows what it asks about,
    #: in this order within a header; the view narrows by them in both shapes
    #: (`ApplicationFilterMixin`), so nothing about what they mean is said here.
    #:
    #: *Gone quiet* goes with the status because no column shows it: it is a fact about a
    #: live application, as its status and its outcome are. *Tag* goes to the tags column,
    #: and, while that column is off, as it is until somebody adds it, to the role, under
    #: which the tags are drawn then.
    extra_filters = (
        ExtraFilter("status", _("Status"), choices=tuple(Status.choices), columns=("status",)),
        ExtraFilter(
            "state",
            _("Outcome"),
            choices=(("open", _("Still live")), ("closed", _("Settled"))),
            columns=("status",),
        ),
        ExtraFilter("quiet", _("Gone quiet"), kind="flag", columns=("status",)),
        ExtraFilter("tag", _("Tag"), columns=("tags", "role")),
    )
    changed_message = ngettext_lazy(
        "%(count)d application changed.", "%(count)d applications changed.", "count"
    )
    search_label = _("Search applications")
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
        # The status has no filter of its own kind: its header holds the status, the outcome
        # and *Gone quiet*, which the view narrows by for the board as well (`extra_filters`).
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
            # Currency first, then the figure brought to a year: see `with_salary_order`.
            sort=("posting__salary_currency", "salary_year_max", "salary_year_min"),
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

    def choices_for(self, extra: ExtraFilter) -> tuple:
        """A person's own tags for *Tag*, by slug, which is what the address holds. Somebody
        with no tags is offered no list, as the form above the table offered none.

        *Status* offers, first, the set of statuses the address names, while it names more
        than one (#709): the board leaves several columns open with one value,
        ``applied,interviewing``, the switch carries it here, and the table narrows to all
        of them. A list that could not show that value would say *Any* over a narrowed
        table, and the next filter would send *Any* and widen it."""
        if extra.name == "status":
            asked = self.params.get(extra.name, "").strip()
            named = [status.strip() for status in asked.split(",") if status.strip()]
            if len(named) < 2:
                return extra.choices
            labels = dict(extra.choices)
            together = ", ".join(str(labels.get(status, status)) for status in named)
            return ((asked, together), *extra.choices)
        if extra.name != "tag":
            return super().choices_for(extra)
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return ()
        return tuple(Tag.objects.for_user(user).values_list("slug", "name"))
