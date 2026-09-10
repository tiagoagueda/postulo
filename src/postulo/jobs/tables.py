"""The companies table: what it can show, sort by and narrow on."""

from django.utils.translation import gettext_lazy as _

from postulo.core.tables import Column, Table, register

from . import identifiers


@register
class CompaniesTable(Table):
    name = "companies"
    default_sort = "name"
    #: `group` narrows to a whole ownership tree rather than one link of it, so it is the
    #: table's half of the company page's *across the group* (#138). Handled by the view
    #: rather than by a lookup, because "everything in this group" is a walk and not a join.
    extra_params = ("q", "group")
    noun = (_("company"), _("companies"))
    columns = (
        # The one column here that can be changed where it sits, and the one worth
        # choosing first: it has a real refusal to place -- two companies of one name
        # in one account -- which is the question #135 exists to answer. The counts
        # cannot be edited because they are counts, and the dates belong to the rows
        # they are counted from.
        Column(
            "name",
            _("Name"),
            sort=("name",),
            filter="text",
            lookups=("name",),
            default=True,
            editable="name",
        ),
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
        # The company this one is part of, as recorded -- one step up, not the top of the
        # chain. A group is the answer to a question about *one* company, and the company
        # page is where it can afford to walk the chain; a column that walked it for every
        # row would be ten joins per page for a fact the row above already states (#138).
        Column(
            "parent", _("Part of"), sort=("parent__name",), filter="text", lookups=("parent__name",)
        ),
        Column("contacts", _("People"), sort=("contact_count",), newest_first=True, numeric=True),
        Column("website", _("Website"), filter="text", lookups=("website",)),
        Column("careers", _("Careers page")),
        Column("notes", _("Notes"), filter="text", lookups=("notes",)),
        # One optional column per identifier scheme, hidden until asked for.
        *(
            Column(f"id_{key}", scheme.label)
            for key, scheme in identifiers.schemes().items()
            if key != identifiers.OTHER
        ),
        Column(
            "last_activity",
            _("Last activity"),
            sort=("last_activity_at",),
            newest_first=True,
        ),
        Column("created", _("Added"), sort=("created_at",), newest_first=True),
    )
