"""The tables this app draws: companies, and the listings waiting to be decided."""

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy

from postulo.core.tables import Column, ExtraFilter, Table, day_text, register

from . import identifiers, industries
from .models import (
    CompanyKind,
    DiscardReason,
    EmploymentType,
    LegalState,
    ListingState,
    RemoteType,
)

#: The tabs above the listings table, in words: the page draws them, and an export of the
#: table says which one it was taken on (#341). Here rather than beside the page, because the
#: table is below the page and both read them.
TAB_LABELS = {
    "undecided": _("To decide"),
    ListingState.SHORTLISTED: _("Shortlisted"),
    ListingState.DISCARDED: _("Discarded"),
    "applied": _("Applied"),
    "closed": _("Closed"),
    "all": _("Everything"),
}


class SectorChoices:
    """The 22 NACE sections as a choice filter's ``(value, label)`` pairs, read each time
    they are listed so the names are the reader's language's, not the one the module was
    imported in (#897)."""

    def __iter__(self):
        return iter(industries.sections())


@register
class CompaniesTable(Table):
    name = "companies"
    label = _("Companies")
    default_sort = "name"
    #: `group` narrows to a whole ownership tree rather than one link of it, so it is the
    #: table's half of the company page's *across the group* (#138). Handled by the view
    #: rather than by a lookup, because "everything in this group" is a walk and not a join.
    extra_params = ("q", "group", "whose")
    #: Two questions the *Status* column carries (#941), applied by the view because what
    #: *hiring* means is a reading of two things (a current signal, or listings not closed).
    extra_filters = (
        ExtraFilter("hiring", _("Hiring"), kind="flag", columns=("status",)),
        ExtraFilter("live", _("Exclude dissolved"), kind="flag", columns=("status",)),
    )
    changed_message = ngettext_lazy(
        "%(count)d company changed.", "%(count)d companies changed.", "count"
    )
    search_label = _("Search companies")
    rows_from = "postulo.jobs.views.CompanyListView"
    page_name = "jobs:company_list"

    def arranged(self, columns):
        """The identifier columns in the order set under Settings → Appearance (#672).

        They keep the places the identifier columns hold among the others, and are
        reordered among themselves, so the chooser lists the schemes as the person
        arranged them. None is a default column, so a scheme switched off there has
        nothing to lose here; it stays in the chooser.
        """
        from postulo.core import identifier_order

        profile = getattr(getattr(self.request, "user", None), "profile", None)
        if profile is None:
            return columns
        ranks = {key: place for place, key in enumerate(identifier_order.order_of(profile))}
        ids = sorted(
            (c for c in columns if c.key.startswith("id_")),
            key=lambda c: ranks.get(c.key.removeprefix("id_"), len(ranks)),
        )
        queue = iter(ids)
        return tuple(next(queue) if c.key.startswith("id_") else c for c in columns)

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
        # The town of its primary address (#702), annotated by `with_table_data`.
        Column(
            "location",
            _("Location"),
            sort=("location_town",),
            filter="text",
            lookups=("location_town",),
            default=True,
        ),
        # Its industries' NACE sections (#897): read off them, so a company in two sectors is
        # in both and there is no single value to sort by; the filter is the 22 sections in
        # the reader's language.
        Column(
            "sector",
            _("Sector"),
            filter="choice",
            lookups=("industries__section",),
            choices=SectorChoices(),
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
        # What the company is and what it is doing about hiring (#941): the legal state
        # when it is not active, then the latest signal or the count of open listings.
        Column(
            "status",
            _("Status"),
            sort=("legal_state",),
            filter="choice",
            lookups=("legal_state",),
            choices=tuple(LegalState.choices),
            default=True,
        ),
        # Employer or employment service (#202). Off by default -- most people have one
        # office and many employers, and the name cell says which is which -- and here so
        # the table can be narrowed to either.
        Column(
            "kind",
            _("Type"),
            sort=("kind",),
            filter="choice",
            lookups=("kind",),
            choices=tuple(CompanyKind.choices),
        ),
        # Added by the career form and not yet used for new work (#683): left out of the table
        # unless this filter asks for them, as the pickers leave them out.
        Column(
            "from_career",
            _("From your career"),
            sort=("from_career",),
            filter="choice",
            lookups=("from_career",),
            choices=(("1", _("Yes")), ("0", _("No"))),
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
        # narrows like a column of the company's own (#173). The schemes Postulo ships:
        # this list is settled when the module is imported, before a row can be read, so
        # one an instance defined for itself has no column (#311).
        *(
            Column(
                f"id_{key}",
                scheme.label,
                sort=(f"id_{key}",),
                filter="text",
                lookups=(f"id_{key}",),
            )
            for key, scheme in identifiers.shipped().items()
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

    def param_in_words(self, name: str, value: str) -> tuple[str, str] | None:
        """Whose companies, and the group the table is narrowed to (#341)."""
        if name == "whose":
            return _whose_in_words(self.request, value)
        if name == "group":
            return str(_("Group")), value
        return super().param_in_words(name, value)

    def text(self, row, column: Column) -> str:
        """Each cell as `company_row.html` draws it, in words (#341). Every industry, where
        the page counts the ones past four; a link's address, where the page draws a word."""
        key = column.key
        if key.startswith("id_"):
            identifier = row.identifier(key.removeprefix("id_"))
            return identifier.value if identifier is not None else ""
        match key:
            case "name":
                return row.name
            case "location":
                return row.location
            case "sector":
                return ", ".join(str(name) for _letter, name, *_look in row.sector_looks)
            case "industry":
                return ", ".join(industry.name for industry in row.industries.all())
            case "postings":
                return str(getattr(row, "posting_count", 0) or 0)
            case "applications":
                return str(getattr(row, "application_count", 0) or 0)
            case "contacts":
                return str(getattr(row, "contact_count", 0) or 0)
            case "parent":
                return row.parent.name if row.parent and getattr(row, "parent_seen", False) else ""
            case "status":
                return _company_status(row)
            case "kind":
                return str(row.get_kind_display())
            case "from_career":
                return str(_("Yes") if row.from_career else _("No"))
            case "website":
                return row.website
            case "careers":
                return row.careers_url
            case "notes":
                return row.notes or ""
            case "last_activity":
                return day_text(getattr(row, "last_activity_at", None))
            case "created":
                return day_text(row.created_at)
        return ""


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
    #: The state tabs above the table, which narrow the list without being a column, and
    #: the masthead's search box, which narrows it by role, company and place (#313).
    extra_params = ("state", "q", "whose")
    changed_message = ngettext_lazy(
        "%(count)d listing changed.", "%(count)d listings changed.", "count"
    )
    search_label = _("Search listings")
    rows_from = "postulo.jobs.listing_views.ListingListView"
    page_name = "listings:list"
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

    def param_in_words(self, name: str, value: str) -> tuple[str, str] | None:
        """The tab and whose listings (#341). The tab is said by `in_words` whether or not
        the address names it, since a bare address is *To decide*."""
        if name == "whose":
            return _whose_in_words(self.request, value)
        if name == "state":
            return None
        return super().param_in_words(name, value)

    def in_words(self) -> list[dict[str, str]]:
        """The tab first: a list of listings is always one tab's, *To decide* unless the
        address names another, and a list that did not say so would read as all of them."""
        from .models import LISTING_FILTERS

        wanted = self.params.get("state", "undecided")
        if wanted not in LISTING_FILTERS and wanted != "all":
            wanted = "undecided"
        named = TAB_LABELS.get(wanted) or dict(ListingState.choices).get(wanted, wanted)
        tab = {"label": str(_("Showing")), "value": str(named)}
        return [tab, *super().in_words()]

    def text(self, row, column: Column) -> str:
        """Each cell as `listing_row.html` draws it, in words (#341). The buttons at the end
        of a row are not a column, so there is nothing of them to leave out."""
        match column.key:
            case "title":
                return row.title
            case "company":
                return row.company.name if row.company_id else ""
            case "location":
                return row.location
            case "closes":
                return day_text(row.closes_at)
            case "state":
                return str(row.derived_state_label)
            case "noted":
                return day_text(getattr(row, "noted_at", None))
            case "source":
                return row.source
            case "remote":
                return str(row.get_remote_type_display()) if row.remote_type else ""
            case "employment":
                return str(row.get_employment_type_display()) if row.employment_type else ""
            case "salary":
                return row.salary_display
            case "posted":
                return day_text(row.posted_at)
            case "discarded":
                return str(row.get_discard_reason_display()) if row.discard_reason else ""
            case "applications":
                return str(getattr(row, "application_count", 0) or 0)
            case "url":
                return row.url
            case "description":
                return row.description_text
        return ""


def _whose_in_words(request, value: str) -> tuple[str, str] | None:
    """Whose records a list holds, in words, where the address chose and may (#896, #341)."""
    from . import whose

    offered = dict(whose.offered(request.user))
    if not value or value not in offered:
        return None
    return str(_("Whose")), str(offered[value])


def _company_status(company) -> str:
    """The status cell in words (#941): the legal state when it is not active, the latest
    hiring signal -- as of its date, once stale -- and the listings still open where nobody
    has a current word."""
    from django.utils.translation import gettext, ngettext

    said = []
    if company.legal_state != LegalState.ACTIVE:
        said.append(str(company.get_legal_state_display()))
    kind = getattr(company, "signal_kind", "")
    if kind:
        signal = company.signal_kind_label
        if not getattr(company, "signal_now", ""):
            # The page's own words, so the export says it as the cell does.
            when = gettext("as of %(date)s") % {
                "date": day_text(getattr(company, "signal_on", None))
            }
            signal = f"{signal} · {when}"
        said.append(signal)
    count = getattr(company, "open_listing_count", 0) or 0
    if count and not getattr(company, "signal_now", ""):
        said.append(
            ngettext("%(counter)s open listing", "%(counter)s open listings", count)
            % {"counter": count}
        )
    return " · ".join(said)
