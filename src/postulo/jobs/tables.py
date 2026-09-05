"""The companies table: what it can show, sort by and narrow on."""

from django.utils.translation import gettext_lazy as _

from postulo.core.tables import Column, Table, register


@register
class CompaniesTable(Table):
    name = "companies"
    default_sort = "name"
    extra_params = ("q",)
    noun = (_("company"), _("companies"))
    columns = (
        Column("name", _("Name"), sort=("name",), filter="text", lookups=("name",), default=True),
        Column(
            "location",
            _("Location"),
            sort=("location",),
            filter="text",
            lookups=("location",),
            default=True,
        ),
        # Several per company, so no single value to sort by; the filter matches any.
        Column(
            "industry",
            _("Industries"),
            filter="text",
            lookups=("industries__name",),
            default=True,
        ),
        Column(
            "postings",
            _("Postings"),
            sort=("posting_count",),
            newest_first=True,
            numeric=True,
            default=True,
        ),
        Column(
            "applications",
            _("Applications"),
            sort=("application_count",),
            newest_first=True,
            numeric=True,
            default=True,
        ),
        Column("contacts", _("People"), sort=("contact_count",), newest_first=True, numeric=True),
        Column("website", _("Website"), filter="text", lookups=("website",)),
        Column("careers", _("Careers page")),
        Column("notes", _("Notes"), filter="text", lookups=("notes",)),
        Column(
            "last_activity",
            _("Last activity"),
            sort=("last_activity_at",),
            newest_first=True,
        ),
        Column("created", _("Added"), sort=("created_at",), newest_first=True),
    )
