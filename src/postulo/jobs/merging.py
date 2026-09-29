"""Two records of one company, or of one person, made into one (#239).

`jobs.duplicates` says which records look like the same one. This is what happens when
somebody agrees: everything the other record held moves into the one that is kept, and the
other is deleted.

**Shown before it is done.** `plan_companies` and `plan_contacts` work out what a merge
would do and change nothing; the confirmation page draws the plan, and the merge itself
begins by making the same plan from the same functions, so what was shown and what is done
cannot come to differ except by the record having changed in between.

**Nothing is discarded without saying so.** Everything that can hang off a record moves:
postings, contacts, identifiers, departments, the logo, the companies that are part of it,
the applications that went through it as an agency; for a person, their telephone numbers,
addresses and links, the applications they are the contact for or referred the person to,
and the interviews they sat in. Where a field holds one value and both records have one,
**the kept record's wins** -- it is the one somebody chose to keep -- and the other's is
shown on the confirmation page and written into the note the merge leaves on the kept
record, so it is still there to be read. Notes are text and are simply joined.

**The timeline says it happened.** Every application the merge touches gets an entry
naming both records and saying what the merged one was to it, because an application
whose employer changed name overnight with nothing on its timeline is a record that
cannot explain itself.

**One transaction, and a refusal rather than a loss.** The whole merge is atomic. Before
the other record is deleted, Django is asked what would be deleted with it; anything the
merge did not expect to find there -- a table a plugin owns, a relation added after this
was written -- stops the merge and rolls all of it back. A merge that cannot move
something does nothing, rather than moving the rest and deleting what it could not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import router, transaction
from django.db.models.deletion import Collector
from django.utils import formats, timezone
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from . import identifiers
from .models import Company, CompanyIdentifier, Contact, Department, JobPosting

#: How many names a line of the plan prints before it says how many more there are.
NAMES_SHOWN = 5

#: How long a timeline entry's first line may be; the column's own limit.
SUMMARY_LENGTH = 250


class CannotMerge(Exception):
    """The merge could not be finished, and so did nothing. It says why, as a sentence.

    ``what`` names the kinds of record it found and did not know how to move, where that
    was the reason; it is empty where the reason was something else.
    """

    def __init__(self, why: str, what: list[str] | None = None):
        self.why = why
        self.what = list(what or [])
        super().__init__(why)


def _gone() -> CannotMerge:
    """One of the two was deleted, or merged away, between the page and the press."""
    return CannotMerge(str(_("Nothing was merged: one of the two is no longer there.")))


# ------------------------------------------------------------------------ the plan


@dataclass
class Moved:
    """One kind of thing that moves: how many, and the first few by name."""

    label: str
    count: int
    names: list[str] = field(default_factory=list)

    @property
    def more(self) -> int:
        """How many are not named, because a line is not a list."""
        return max(0, self.count - len(self.names))


@dataclass
class Filled:
    """A field the kept record has nothing in, and takes from the other."""

    label: str
    value: str


@dataclass
class Difference:
    """A field both records fill in, differently. The kept record's stays."""

    label: str
    kept: str
    other: str


@dataclass
class Plan:
    """What merging ``other`` into ``kept`` does. Making one changes nothing."""

    kept: object
    other: object
    #: What the two were called when the plan was made. The merge changes the kept record
    #: -- it may take the other's company -- and what is written afterwards has to name
    #: the two as they were when somebody agreed to merge them.
    kept_called: str = ""
    other_called: str = ""
    moves: list[Moved] = field(default_factory=list)
    fills: list[Filled] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    #: What cannot move and goes with the other record, each as a sentence.
    left_behind: list[str] = field(default_factory=list)
    #: The applications that get a timeline entry: each one's key, and what the record
    #: being merged was to it, as sentences.
    touched: dict[int, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kept_called = self.kept_called or _called(self.kept)
        self.other_called = self.other_called or _called(self.other)

    @property
    def applications(self) -> int:
        return len(self.touched)

    def note(self) -> str:
        """What the merge writes on the kept record: that it happened, and what the other
        record said that the kept one says differently.

        On the record rather than only on the timelines, because a company nobody has
        applied to has no timeline, and what it held has to be kept somewhere.
        """
        lines = [
            _("Merged with %(name)s on %(date)s.")
            % {
                "name": self.other_called,
                "date": formats.date_format(timezone.localdate(), "DATE_FORMAT"),
            }
        ]
        if self.differences:
            lines.append(_("It said these differently, and what is here was kept:"))
            lines.extend(f"{row.label}: {row.other}" for row in self.differences)
        if self.left_behind:
            lines.append(_("Not carried over:"))
            lines.extend(self.left_behind)
        theirs = (getattr(self.other, "notes", "") or "").strip()
        if theirs:
            lines.append(_("Its notes:"))
            lines.append(theirs)
        return "\n".join(lines)


def _called(record) -> str:
    """A record by name, with where a person works beside it so two of one name differ."""
    company = getattr(record, "company", None) if isinstance(record, Contact) else None
    return f"{record.name} ({company.name})" if company is not None else str(record.name)


def _label(model, name: str) -> str:
    return str(capfirst(model._meta.get_field(name).verbose_name))


def _moved(label, queryset, name) -> Moved | None:
    """A line of the plan for the rows of ``queryset``, or nothing where there are none."""
    count = queryset.count()
    if not count:
        return None
    return Moved(
        label=str(label), count=count, names=[str(name(row)) for row in queryset[:NAMES_SHOWN]]
    )


def _same_owner(kept, other) -> None:
    """Two different records of one person's. Everything else is a mistake in the caller:
    the views look both up among the person's own before they get here."""
    if type(kept) is not type(other):
        raise ValueError("A merge is of two records of one kind.")
    if kept.pk is None or other.pk is None or kept.pk == other.pk:
        raise ValueError("A merge needs two different records.")
    if kept.owner_id != other.owner_id:
        raise ValueError("Both records have to belong to the same person.")


def _say(plan: Plan, applications, sentence: str) -> None:
    """``sentence`` on every one of ``applications``, as what the merged record was to it."""
    for pk in applications.values_list("pk", flat=True):
        roles = plan.touched.setdefault(pk, [])
        if sentence not in roles:
            roles.append(sentence)


def _join_notes(record, note: str) -> None:
    mine = (record.notes or "").strip()
    record.notes = f"{mine}\n\n{note}" if mine else note


def _write_entries(plan: Plan) -> None:
    """One timeline entry on every application the merge touched."""
    from postulo.applications.models import Application, EventKind
    from postulo.applications.services import record_event

    if not plan.touched:
        return
    summary = _("%(other)s was merged into %(kept)s") % {
        "other": plan.other_called,
        "kept": plan.kept_called,
    }
    applications = Application.objects.filter(owner_id=plan.kept.owner_id, pk__in=plan.touched)
    for application in applications:
        record_event(
            application,
            kind=EventKind.NOTE,
            summary=str(summary)[:SUMMARY_LENGTH],
            body=" ".join(plan.touched[application.pk]),
        )


def _refuse_what_is_left(record, expected: set) -> None:
    """Stop, if deleting ``record`` would delete anything the merge did not mean it to.

    Django is asked what goes with the record, exactly as it would be on a delete. What
    the merge moved is no longer among it; what it deliberately leaves -- an identifier
    the kept company has its own of, a link it already lists -- is ``expected``. Anything
    else is something this module does not know about, and the merge is undone rather
    than finished at its expense.
    """
    collector = Collector(using=router.db_for_write(type(record), instance=record))
    collector.collect([record])
    found = {model for model, rows in collector.data.items() if rows and model not in expected}
    found |= {
        queryset.model
        for queryset in collector.fast_deletes
        if queryset.model not in expected and queryset.exists()
    }
    if found:
        what = sorted(str(model._meta.verbose_name_plural) for model in found)
        raise CannotMerge(
            str(
                _("Nothing was merged: Postulo does not know how to move the %(what)s it holds.")
                % {"what": ", ".join(what)}
            ),
            what,
        )


# ----------------------------------------------------------------------- companies


def _identifier_moves(kept, other) -> tuple[list, list, list]:
    """The other company's identifiers, sorted by what becomes of each.

    *Moving*: the kept company has none of that scheme. *Differing*: it has one, with
    another value, and keeps its own -- a company carries one identifier per scheme -- so
    the other's is for the note. *Already there*: the same identifier, which only *Other*
    can be on two companies at once, and which needs nothing doing.
    """
    ours = list(kept.identifiers.all())
    by_scheme = {row.scheme: row for row in ours if row.scheme != identifiers.OTHER}
    others = {
        (row.label.casefold(), row.value.casefold())
        for row in ours
        if row.scheme == identifiers.OTHER
    }
    moving, differing, already = [], [], []
    for row in other.identifiers.all():
        if row.scheme == identifiers.OTHER:
            same = (row.label.casefold(), row.value.casefold()) in others
            (already if same else moving).append(row)
        elif row.scheme not in by_scheme:
            moving.append(row)
        elif by_scheme[row.scheme].value.casefold() == row.value.casefold():
            already.append(row)
        else:
            differing.append((row, by_scheme[row.scheme]))
    return moving, differing, already


def _department_moves(kept, other) -> tuple[list, list]:
    """The other company's departments: the ones that move, and the ones that fold into a
    department of the same name the kept company already has."""
    ours = {team.name.casefold(): team for team in kept.departments.all()}
    moving, folding = [], []
    for team in other.departments.all():
        twin = ours.get(team.name.casefold())
        if twin is None:
            moving.append(team)
        else:
            folding.append((team, twin))
    return moving, folding


def _above(company) -> set[int]:
    """The keys of every company above this one in its ownership chain."""
    found: set[int] = set()
    node = company.parent
    while node is not None and node.pk not in found and len(found) <= Company.MAX_DEPTH:
        found.add(node.pk)
        node = node.parent
    return found


def _parent_after(kept, other):
    """(the parent the kept company ends up with, whether that is a change).

    The kept company keeps the parent it has. It takes the other's where it has none, or
    where its parent *was* the other -- which is about to stop existing. Never one that
    would close a loop: a company cannot end up part of something that is part of it.
    """
    theirs = other.parent if other.parent_id and other.parent_id != kept.pk else None
    if theirs is not None and theirs.pk in {c.pk for c in kept.descendants()}:
        theirs = None
    if kept.parent_id == other.pk:
        return theirs, True
    if kept.parent_id is None and theirs is not None:
        return theirs, True
    return kept.parent, False


def plan_companies(kept, other) -> Plan:
    """What merging the company ``other`` into ``kept`` would do."""
    from postulo.applications.models import Application

    _same_owner(kept, other)
    plan = Plan(kept=kept, other=other)
    owner = kept.owner_id

    sent = Application.objects.filter(owner_id=owner).select_related("posting", "posting__company")
    postings = JobPosting.objects.filter(owner_id=owner, company=other)
    applications = sent.filter(posting__company=other)
    through = sent.filter(through_agency=other)
    aimed = sent.filter(department__company=other)
    moving_ids, differing_ids, _already = _identifier_moves(kept, other)
    moving_teams, folding_teams = _department_moves(kept, other)
    children = Company.objects.filter(owner_id=owner, parent=other).exclude(pk=kept.pk)
    industries = other.industries.exclude(pk__in=kept.industries.values("pk"))

    lines = [
        _moved(_("Postings"), postings, lambda row: row.title),
        _moved(_("Applications for them"), applications, lambda row: row.posting.title),
        _moved(_("People"), Contact.objects.filter(owner_id=owner, company=other), str),
        _moved(_("Companies that are part of it"), children, str),
        _moved(_("Applications that went through it as an agency"), through, str),
        _moved(_("Industries"), industries, str),
    ]
    plan.moves = [line for line in lines if line is not None]
    if moving_teams or folding_teams:
        teams = [team.name for team in moving_teams] + [team.name for team, _twin in folding_teams]
        plan.moves.append(
            Moved(label=str(_("Departments")), count=len(teams), names=teams[:NAMES_SHOWN])
        )
    if moving_ids:
        plan.moves.append(
            Moved(
                label=str(_("Identifiers")),
                count=len(moving_ids),
                names=[str(row) for row in moving_ids[:NAMES_SHOWN]],
            )
        )

    # ------------------------------------------------ the fields that hold one value
    for name in ("website", "careers_url", "location"):
        ours, theirs = getattr(kept, name), getattr(other, name)
        if theirs and not ours:
            plan.fills.append(Filled(_label(Company, name), theirs))
        elif theirs and ours and theirs != ours:
            plan.differences.append(Difference(_label(Company, name), ours, theirs))
    if kept.kind != other.kind:
        plan.differences.append(
            Difference(
                _label(Company, "kind"),
                str(kept.get_kind_display()),
                str(other.get_kind_display()),
            )
        )
    parent, changes = _parent_after(kept, other)
    if changes and parent is not None:
        plan.fills.append(Filled(_label(Company, "parent"), parent.name))
    # What the other was part of, where the kept company does not end up part of it: it
    # has a parent of its own, or taking this one would close a loop. Said either way.
    if other.parent_id and other.parent_id != kept.pk:
        if parent is None or parent.pk != other.parent_id:
            ours = kept.parent.name if kept.parent_id and kept.parent_id != other.pk else ""
            plan.differences.append(Difference(_label(Company, "parent"), ours, other.parent.name))
    for other_row, kept_row in differing_ids:
        plan.differences.append(
            Difference(str(kept_row.scheme_label), kept_row.value, other_row.value)
        )

    if other.logo and not kept.logo:
        plan.fills.append(Filled(_label(Company, "logo"), str(_("The one it has"))))
    elif other.logo and kept.logo:
        plan.left_behind.append(
            _("Its logo: %(name)s has one of its own, and a picture cannot be kept in a note.")
            % {"name": kept.name}
        )

    _say(plan, applications, _("It was the employer on the posting."))
    _say(plan, through, _("It was the agency this went through."))
    _say(plan, aimed, _("It held the department this was for."))
    return plan


@transaction.atomic
def merge_companies(kept, other) -> Plan:
    """Make the company ``other`` part of ``kept``, and delete it. Returns what was done.

    Both rows are read again under a lock, so two merges of the same company -- two tabs,
    a double click -- run one after the other and the second finds its record gone.
    """
    from postulo.applications.models import Application

    _same_owner(kept, other)
    locked = {
        row.pk: row
        for row in Company.objects.select_for_update().filter(
            owner_id=kept.owner_id, pk__in=[kept.pk, other.pk]
        )
    }
    if len(locked) != 2:
        raise _gone()
    kept, other = locked[kept.pk], locked[other.pk]

    plan = plan_companies(kept, other)
    owner = kept.owner_id
    now = timezone.now()

    # The applications whose answer changes -- a new employer, a new agency -- are stamped,
    # so a client that asks the API what changed since it last looked is told.
    Application.objects.filter(owner_id=owner, pk__in=plan.touched).update(updated_at=now)
    Application.objects.filter(owner_id=owner, through_agency=other).update(through_agency=kept)
    JobPosting.objects.filter(owner_id=owner, company=other).update(company=kept, updated_at=now)

    # Departments before the people in them, so nobody is ever in a team at another
    # company than their own: a team of the same name takes the other's people and
    # applications, and the rest move as they are.
    moving_teams, folding_teams = _department_moves(kept, other)
    for theirs, ours in folding_teams:
        Contact.objects.filter(department=theirs).update(department=ours, updated_at=now)
        Application.objects.filter(department=theirs).update(department=ours)
        theirs.delete()
    Department.objects.filter(pk__in=[team.pk for team in moving_teams]).update(
        company=kept, updated_at=now
    )
    Contact.objects.filter(owner_id=owner, company=other).update(company=kept, updated_at=now)

    moving_ids, _differing, _already = _identifier_moves(kept, other)
    CompanyIdentifier.objects.filter(pk__in=[row.pk for row in moving_ids]).update(
        company=kept, updated_at=now
    )

    # The companies that were part of the other are part of the kept one now -- except one
    # the kept company is itself part of, which takes the other's place above it instead.
    # Putting it under the kept company would make each part of the other.
    above = _above(kept)
    parent, _changes = _parent_after(kept, other)
    children = Company.objects.filter(owner_id=owner, parent=other).exclude(pk=kept.pk)
    children.filter(pk__in=above).update(parent=other.parent, updated_at=now)
    children.exclude(pk__in=above).update(parent=kept, updated_at=now)

    kept.industries.add(*other.industries.all())

    kept.parent = parent
    for name in ("website", "careers_url"):
        if not getattr(kept, name):
            setattr(kept, name, getattr(other, name))
    if other.location and not kept.location:
        # The place goes with the words for it, a person's correction included: left
        # behind, the kept company would guess again at what the other had been told.
        for name in (
            "location",
            "location_lat",
            "location_lon",
            "location_resolved_from",
            "location_resolved_at",
            "location_resolved_by",
        ):
            setattr(kept, name, getattr(other, name))
    unwanted = ""
    if other.logo and not kept.logo:
        kept.logo = other.logo.name
        kept.logo_source = other.logo_source
        kept.logo_source_url = other.logo_source_url
        kept.logo_fetched_at = other.logo_fetched_at
    elif other.logo:
        unwanted = other.logo.name
    _join_notes(kept, plan.note())
    kept.save()

    _write_entries(plan)
    _refuse_what_is_left(other, expected={Company, CompanyIdentifier, Company.industries.through})
    other.delete()
    if unwanted:
        # After the transaction and not inside it: a file cannot be rolled back, and a
        # merge undone at the last step must not have deleted a picture on the way.
        transaction.on_commit(lambda: other.logo.storage.delete(unwanted))
    return plan


# ------------------------------------------------------------------------ contacts


def _link_moves(kept, other) -> tuple[list, list]:
    """The other person's links: the ones that move, and the ones the kept one already
    lists at the same address, which a holder may not list twice."""
    ours = {row.url for row in kept.web_links.all()}
    moving, already = [], []
    for row in other.web_links.all():
        (already if row.url in ours else moving).append(row)
    return moving, already


def _held_by_plugins(contact) -> list[str]:
    """The plugins that hold rows of their own about this person, by name.

    Asked the way the archive asks (`export_for`): a plugin that owns a table says what it
    holds about somebody. One that owns a table and cannot say is named as well, because
    *nothing* and *cannot tell* are different answers and only the first is reassuring.
    """
    from postulo.plugins import data
    from postulo.plugins.registry import GROUPS
    from postulo.plugins.registry import plugins as installed

    found: list[str] = []
    for kind in GROUPS:
        for plugin in installed(kind):
            if not data.owned_labels(plugin):
                continue
            exporter = getattr(plugin, "export_for", None)
            try:
                holds = exporter is None or bool(list(exporter(contact) or []))
            except Exception:
                holds = True
            if holds:
                found.append(str(getattr(plugin, "label", plugin.name)))
    return sorted(set(found))


def plan_contacts(kept, other) -> Plan:
    """What merging the person ``other`` into ``kept`` would do."""
    from postulo.applications.models import Application, Interview

    _same_owner(kept, other)
    plan = Plan(kept=kept, other=other)
    owner = kept.owner_id

    sent = Application.objects.filter(owner_id=owner).select_related("posting", "posting__company")
    main = sent.filter(contact=other)
    referred = sent.filter(referred_by=other)
    interviews = Interview.objects.filter(owner_id=owner, contacts=other).select_related(
        "application__posting__company"
    )
    met = sent.filter(interviews__contacts=other).distinct()
    moving_links, _already = _link_moves(kept, other)

    lines = [
        _moved(_("Telephone numbers"), other.phone_numbers.all(), lambda row: row.number),
        _moved(_("Postal addresses"), other.postal_addresses.all(), lambda row: row.one_line()),
        _moved(_("Applications they are the main contact for"), main, str),
        _moved(_("Applications they referred you to"), referred, str),
        _moved(_("Interviews they were at"), interviews, str),
    ]
    plan.moves = [line for line in lines if line is not None]
    if moving_links:
        plan.moves.insert(
            min(2, len(plan.moves)),
            Moved(
                label=str(_("Web links")),
                count=len(moving_links),
                names=[row.url for row in moving_links[:NAMES_SHOWN]],
            ),
        )

    for name in ("role", "email"):
        ours, theirs = getattr(kept, name), getattr(other, name)
        if theirs and not ours:
            plan.fills.append(Filled(_label(Contact, name), theirs))
        elif theirs and ours and theirs.casefold() != ours.casefold():
            plan.differences.append(Difference(_label(Contact, name), ours, theirs))

    theirs = other.department.name if other.department_id else ""
    if other.company_id and not kept.company_id:
        plan.fills.append(Filled(_label(Contact, "company"), other.company.name))
        if theirs:
            plan.fills.append(Filled(_label(Contact, "department"), theirs))
    elif other.company_id and other.company_id != kept.company_id:
        where = f"{other.company.name} · {theirs}" if theirs else other.company.name
        plan.differences.append(Difference(_label(Contact, "company"), kept.company.name, where))
    elif theirs and not kept.department_id:
        plan.fills.append(Filled(_label(Contact, "department"), theirs))
    elif theirs and other.department_id != kept.department_id:
        plan.differences.append(
            Difference(_label(Contact, "department"), kept.department.name, theirs)
        )

    for plugin in _held_by_plugins(other):
        plan.left_behind.append(
            _("What %(plugin)s holds about them, which Postulo cannot move.") % {"plugin": plugin}
        )

    _say(plan, main, _("They were the main contact."))
    _say(plan, referred, _("They referred you."))
    _say(plan, met, _("They were at an interview."))
    return plan


def _move_held(rows, kept, *, kinds: bool, now) -> None:
    """Hand the rows a person holds -- numbers, addresses, links -- to ``kept``.

    One primary per holder, or per holder and kind for a link, and the kept person's is
    the one that stays: a row arriving beside a primary stops being one, and a row
    arriving where there is none keeps the flag it came with.
    """
    ours = type(rows[0]).objects.filter(
        content_type_id=rows[0].content_type_id, object_id=kept.pk, is_primary=True
    )
    taken = set(ours.values_list("kind", flat=True)) if kinds else set()
    has_primary = ours.exists()
    for row in rows:
        beside = (row.kind in taken) if kinds else has_primary
        type(row).objects.filter(pk=row.pk).update(
            object_id=kept.pk, is_primary=row.is_primary and not beside, updated_at=now
        )


@transaction.atomic
def merge_contacts(kept, other) -> Plan:
    """Make the person ``other`` the same record as ``kept``, and delete the other."""
    from postulo.applications.models import Application, Interview
    from postulo.core.models import WebLink

    _same_owner(kept, other)
    locked = {
        row.pk: row
        for row in Contact.objects.select_for_update()
        .filter(owner_id=kept.owner_id, pk__in=[kept.pk, other.pk])
        .select_related("company", "department")
    }
    if len(locked) != 2:
        raise _gone()
    kept, other = locked[kept.pk], locked[other.pk]

    plan = plan_contacts(kept, other)
    owner = kept.owner_id
    now = timezone.now()

    Application.objects.filter(owner_id=owner, pk__in=plan.touched).update(updated_at=now)
    Application.objects.filter(owner_id=owner, contact=other).update(contact=kept)
    Application.objects.filter(owner_id=owner, referred_by=other).update(referred_by=kept)

    # An interview both were at has the kept person once, not twice.
    seats = Interview.contacts.through.objects
    both = seats.filter(contact=kept).values("interview_id")
    seats.filter(contact=other, interview_id__in=both).delete()
    seats.filter(contact=other).update(contact=kept)

    moving_links, _already = _link_moves(kept, other)
    for rows, kinds in (
        (list(other.phone_numbers.all()), False),
        (list(other.postal_addresses.all()), False),
        (moving_links, True),
    ):
        if rows:
            _move_held(rows, kept, kinds=kinds, now=now)

    for name in ("role", "email"):
        if not getattr(kept, name):
            setattr(kept, name, getattr(other, name))
    if other.company_id and not kept.company_id:
        kept.company, kept.department = other.company, other.department
    elif other.company_id == kept.company_id and not kept.department_id:
        kept.department = other.department
    _join_notes(kept, plan.note())
    kept.save()

    _write_entries(plan)
    _refuse_what_is_left(other, expected={Contact, WebLink, Interview.contacts.through})
    other.delete()
    return plan


# ----------------------------------------------------------------------- in words


def done(plan: Plan) -> str:
    """The sentence shown once it is done: what was merged, and how far it reached."""
    said = _("%(other)s has been merged into %(kept)s.") % {
        "other": plan.other_called,
        "kept": plan.kept_called,
    }
    if not plan.applications:
        return str(said)
    reached = ngettext(
        "%(count)d application has a line on its timeline saying so.",
        "%(count)d applications have a line on their timelines saying so.",
        plan.applications,
    ) % {"count": plan.applications}
    return f"{said} {reached}"
