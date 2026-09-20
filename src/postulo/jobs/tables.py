"""The tables this app draws: companies, and the listings waiting to be decided."""

from django.utils.translation import gettext_lazy as _

from postulo.core.tables import Column, Table, register

from . import identifiers
from .models import (
    CompanyKind,
    DiscardReason,
    EmploymentType,
    RemoteType,
)


@register
class CompaniesTable(Table):
    name = "companies"
    label = _("Companies")
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
        # they are counted from. The name itself opens the company, as a name does in
        # every other list; the pencil beside it is what renames (#252).
        Column(
            "name",
            _("Name"),
            sort=("name",),
            filter="text",
            lookups=("name",),
            default=True,
            editable="name",
            edit_label=_("Rename %(what)s"),
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
        # The counts narrow by a least and a most: "companies I have applied to more than
        # once" is the obvious question, and it could not be asked (#173).
        Column(
            "postings",
            _("Postings"),
            sort=("posting_count",),
            newest_first=True,
            numeric=True,
            default=True,
            filter="number",
            lookups=("posting_count",),
        ),
        Column(
            "applications",
            _("Applications"),
            sort=("application_count",),
            newest_first=True,
            numeric=True,
            default=True,
            filter="number",
            lookups=("application_count",),
        ),
        # The company this one is part of, as recorded -- one step up, not the top of the
        # chain. A group is the answer to a question about *one* company, and the company
        # page is where it can afford to walk the chain; a column that walked it for every
        # row would be ten joins per page for a fact the row above already states (#138).
        Column(
            "parent", _("Part of"), sort=("parent__name",), filter="text", lookups=("parent__name",)
        ),
        # Employer or employment service (#202). Off by default -- most people have one
        # office and many employers, and the name cell says which is which -- and here so
        # the table can be narrowed to either.
        Column(
            "kind",
            _("Kind"),
            sort=("kind",),
            filter="choice",
            lookups=("kind",),
            choices=tuple(CompanyKind.choices),
        ),
        Column(
            "contacts",
            _("People"),
            sort=("contact_count",),
            newest_first=True,
            numeric=True,
            filter="number",
            lookups=("contact_count",),
        ),
        Column("website", _("Website"), sort=("website",), filter="text", lookups=("website",)),
        Column(
            "careers",
            _("Careers page"),
            sort=("careers_url",),
            filter="text",
            lookups=("careers_url",),
        ),
        # Free text: an alphabetical order of somebody's notes means nothing, so there is no
        # sort; the filter matches within them, which is what a note is for.
        Column("notes", _("Notes"), filter="text", lookups=("notes",)),
        # One optional column per identifier scheme, hidden until asked for. Each is an
        # annotation of the same name on the queryset (`with_table_data`), so it sorts and
        # narrows like a column of the company's own (#173).
        *(
            Column(
                f"id_{key}",
                scheme.label,
                sort=(f"id_{key}",),
                filter="text",
                lookups=(f"id_{key}",),
            )
            for key, scheme in identifiers.schemes().items()
            if key != identifiers.OTHER
        ),
        # Moments rather than days, so the date pair narrows by the day they fall on.
        Column(
            "last_activity",
            _("Last activity"),
            sort=("last_activity_at",),
            newest_first=True,
            filter="date",
            lookups=("last_activity_at",),
            datetime=True,
        ),
        Column(
            "created",
            _("Added"),
            sort=("created_at",),
            newest_first=True,
            filter="date",
            lookups=("created_at",),
            datetime=True,
        ),
    )


@register
class ListingsTable(Table):
    """Every posting noticed and not yet decided about, as a table (#160).

    The page most like a channel list -- many rows, triaged in bulk, mostly discarded --
    was the one drawing its own rows, so none of what `core/tables.py` gives a list was
    available on it: no sort, no per-column filter, no chosen columns, no bulk action.

    **The state is a column but not a filter.** Which listings to look at is a workflow --
    to decide, shortlisted, discarded, applied, closed -- and it has a control of its own
    above the table, where the counts are. A second `choice` filter for the same question
    would be two controls disagreeing about which rows are on the page, so the column
    shows the state and sorts by it and narrows nothing (`state` is in `extra_params`
    instead, so *Clear* and the empty state still know it is a filter).
    """

    name = "listings"
    label = _("Listings")
    #: Soonest deadline first, which is the order a page for deciding wants: the ones
    #: with no closing date follow, because `ordering` puts nulls last everywhere.
    default_sort = "closes"
    #: The state tabs above the table, which narrow the list without being a column.
    extra_params = ("state",)
    noun = (_("listing"), _("listings"))
    columns = (
        # The role, which opens the posting; the pencil beside it renames where it sits,
        # exactly as a company's name does (#135, #252).
        Column(
            "title",
            _("Role"),
            sort=("title",),
            filter="text",
            lookups=("title",),
            default=True,
            editable="title",
            edit_label=_("Rename %(what)s"),
        ),
        Column(
            "company",
            _("Company"),
            sort=("company__name",),
            filter="text",
            lookups=("company__name",),
            default=True,
        ),
        Column(
            "location",
            _("Location"),
            sort=("location",),
            filter="text",
            lookups=("location",),
            default=True,
        ),
        # A day rather than a moment, so the pair narrows on the date column itself.
        Column(
            "closes",
            _("Closes"),
            sort=("closes_at",),
            filter="date",
            lookups=("closes_at",),
            default=True,
        ),
        # Sorted by what the cell says rather than by what the row stores: see
        # `with_state_order`. Narrowed by the tabs above, not here.
        Column("state", _("State"), sort=("state_order",), default=True),
        Column(
            "noted",
            _("Noted"),
            sort=("noted_at",),
            newest_first=True,
            filter="date",
            lookups=("noted_at",),
            datetime=True,
        ),
        Column(
            "source",
            _("Found via"),
            sort=("source",),
            filter="text",
            lookups=("source",),
        ),
        Column(
            "remote",
            _("Working arrangement"),
            sort=("remote_type",),
            filter="choice",
            lookups=("remote_type",),
            choices=tuple(RemoteType.choices),
        ),
        Column(
            "employment",
            _("Employment type"),
            sort=("employment_type",),
            filter="choice",
            lookups=("employment_type",),
            choices=tuple(EmploymentType.choices),
        ),
        # Currency first, then the figure brought to a year: see `with_salary_order`. No
        # filter, because a bound would have to be in some currency and some period, and
        # the honest version of that control is a question this table is not asking.
        Column(
            "salary",
            _("Salary"),
            sort=("salary_currency", "salary_year_max", "salary_year_min"),
            newest_first=True,
            numeric=True,
        ),
        Column(
            "posted",
            _("Posted"),
            sort=("posted_at",),
            newest_first=True,
            filter="date",
            lookups=("posted_at",),
        ),
        # Why a discarded one was let go. Off by default -- it says nothing about the rows
        # on the page most of the time -- and here because "show me everything I turned
        # down over the pay" is a real question and there was no way to ask it.
        Column(
            "discarded",
            _("Why discarded"),
            sort=("discard_reason",),
            filter="choice",
            lookups=("discard_reason",),
            choices=tuple(DiscardReason.choices),
        ),
        Column(
            "applications",
            _("Applications"),
            sort=("application_count",),
            newest_first=True,
            numeric=True,
            filter="number",
            lookups=("application_count",),
        ),
        Column("url", _("Address"), sort=("url",), filter="text", lookups=("url",)),
        # Free text: an alphabetical order of a posting's description means nothing, so
        # there is no sort; the filter matches within it, which is how somebody finds the
        # three listings that mentioned a technology.
        Column("description", _("Description"), filter="text", lookups=("description",)),
    )
