"""One search box over everything a person wrote or kept.

Every model that holds text a person might remember is searched the same way: case-
insensitive containment on its text fields, through ``for_user()``, so a result is never
shown that its own list would not show. Hits come back grouped by kind, each group capped
and counted, each hit with the passage the term was found in.

Portable first. Postulo runs on SQLite by default and PostgreSQL optionally, and a personal
instance holds thousands of rows, not millions, so containment is fast enough and needs no
index, extension or extra table. Each per-model function below is the seam where SQLite's
FTS5 or PostgreSQL's ``SearchVector`` can be plugged in when that stops being true,
without touching the page.

Ranking is light and deliberate: within a group, a hit in the title comes before a hit in
the body, and otherwise the newest first.

**The counting and the capping happen in SQL** (#231). They used to happen in Python: every
matching row of every model was loaded, turned into a `Hit`, sorted, and then five of them
were shown. Searching *engineer* over three hundred applications and four hundred listings
loaded seven hundred rows -- including four hundred full descriptions -- to draw fifty
lines, and the application group cost one further query *per matching row* to find the event
the term was in. Each group now asks two questions: how many, and the first five. The
ranking that decided which five is an `ORDER BY`, and the excerpt is a subquery on the row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.urls import reverse
from django.utils import formats
from django.utils.translation import gettext_lazy as _

#: How many hits a group shows on the page before "more".
GROUP_LIMIT = 5

#: Characters kept either side of the term in an excerpt.
EXCERPT_RADIUS = 80

#: Shorter than this is a mistake, not a query.
MIN_QUERY_LENGTH = 2


@dataclass
class Hit:
    kind: str
    title: str
    url: str
    excerpt: str = ""
    subtitle: str = ""
    #: Whether the term was found in the title itself; ranks ahead of body hits.
    in_title: bool = False
    id: int = 0


@dataclass
class Found:
    """One group's answer: how many there are, and the few that are shown.

    Two numbers rather than a list, because they are two different questions and only one of
    them needs the rows. A group that says *and 48 more* got that 48 from a `COUNT`, not from
    building forty-eight `Hit`s and throwing them away.
    """

    total: int = 0
    hits: list[Hit] = field(default_factory=list)


@dataclass
class Group:
    kind: str
    label: str
    hits: list[Hit] = field(default_factory=list)
    total: int = 0
    more_url: str = ""

    @property
    def has_more(self) -> bool:
        return self.total > len(self.hits)


def excerpt(text: str, query: str, radius: int = EXCERPT_RADIUS) -> str:
    """The passage around the first occurrence of ``query``, or the start of the text."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    lowered = text.lower()
    position = lowered.find(query.lower())
    if position == -1:
        return text[: radius * 2] + ("…" if len(text) > radius * 2 else "")
    start = max(0, position - radius)
    end = min(len(text), position + len(query) + radius)
    piece = text[start:end]
    if start > 0:
        piece = "…" + piece
    if end < len(text):
        piece = piece + "…"
    return piece


def _day(moment) -> str:
    """The date, written the way the reader's language writes one.

    It used to be ``f"{moment.day} {moment:%b %Y}"``, which takes the month's name from the
    C locale: an English month in the middle of a French search result, and a day-month-year
    order for a language that puts the year first (#225).
    """
    return formats.date_format(moment, "DATE_FORMAT")


def contains(query: str, *fields: str) -> Q:
    """``icontains`` on any of the fields."""
    condition = Q()
    for name in fields:
        condition |= Q(**{f"{name}__icontains": query})
    return condition


def _first_match(query: str, *texts: str) -> str:
    """The first of ``texts`` that holds the term, for the excerpt."""
    lowered = query.lower()
    for text in texts:
        if text and lowered in text.lower():
            return text
    return next((text for text in texts if text), "")


def ranked(rows, query: str, title_field: str, *order: str):
    """``rows`` with title hits first and the group's own order under them, decided in SQL.

    The ordering has to be the database's, or the cap below is a lie: taking the first five
    of an unranked query and *then* putting the title hits first shows five arbitrary rows
    rearranged, not the five best. `alias` rather than `annotate` because nothing reads the
    number -- it exists to be sorted on.
    """
    return rows.alias(
        title_hit=Case(
            When(**{f"{title_field}__icontains": query}, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("title_hit", *order)


def take(rows, limit: int, build) -> Found:
    """How many rows match, and ``build`` applied to the first ``limit`` of them.

    Two queries per group, whatever a person has. The `COUNT` is what the *and N more* line
    needs and the only thing that needs all of them.
    """
    return Found(total=rows.count(), hits=[build(row) for row in rows[:limit]])


# ----------------------------------------------------------------- per model


def search_listings(user, query: str, limit: int) -> Found:
    from postulo.jobs.models import JobPosting

    rows = ranked(
        JobPosting.objects.for_user(user)
        .select_related("company")
        .filter(contains(query, "title", "description", "location", "source")),
        query,
        "title",
        "-noted_at",
    )

    def build(posting) -> Hit:
        return Hit(
            kind="listings",
            id=posting.pk,
            title=posting.title,
            subtitle=posting.company.name,
            url=posting.get_absolute_url(),
            excerpt=excerpt(_first_match(query, posting.description, posting.location), query),
            in_title=query.lower() in posting.title.lower(),
        )

    return take(rows, limit, build)


def search_applications(user, query: str, limit: int) -> Found:
    """Applications, with the passage from the timeline entry the term was in.

    The entry used to be a query per matching row, which is the shape the whole issue is
    about: three hundred applications, three hundred queries, five of them drawn (#231). It
    is three correlated subqueries on the row now, so the passage arrives with the row.

    `Exists` rather than a join and `.distinct()`. An application with four matching entries
    was four rows the database then had to de-duplicate, and the `COUNT` above paid for that
    twice: once to find them, once to fold them back into one.
    """
    from postulo.applications.models import Application, ApplicationEvent

    matching = (
        ApplicationEvent.objects.filter(application=OuterRef("pk"))
        .filter(contains(query, "summary", "body"))
        .order_by("-occurred_at")
    )
    rows = ranked(
        Application.objects.for_user(user)
        .select_related("posting", "posting__company")
        .filter(
            Q(posting__title__icontains=query)
            | Q(posting__company__name__icontains=query)
            | Exists(matching)
        )
        .annotate(
            hit_summary=Subquery(matching.values("summary")[:1]),
            hit_body=Subquery(matching.values("body")[:1]),
            hit_at=Subquery(matching.values("occurred_at")[:1]),
        ),
        query,
        "posting__title",
        "-created_at",
    )

    def build(application) -> Hit:
        title = application.posting.title
        passage = ""
        if application.hit_at is not None:
            passage = _first_match(query, application.hit_summary or "", application.hit_body or "")
            passage = f"{_day(application.hit_at)}: {passage}" if passage else ""
        return Hit(
            kind="applications",
            id=application.pk,
            title=title,
            subtitle=f"{application.posting.company.name} · {application.get_status_display()}",
            url=application.get_absolute_url(),
            excerpt=excerpt(passage, query) if passage else "",
            in_title=query.lower() in title.lower()
            or query.lower() in application.posting.company.name.lower(),
        )

    return take(rows, limit, build)


def search_companies(user, query: str, limit: int) -> Found:
    from postulo.jobs.models import Company, CompanyIdentifier, Industry

    industry = Industry.objects.filter(companies=OuterRef("pk"), name__icontains=query)
    identifier = CompanyIdentifier.objects.filter(company=OuterRef("pk"), value__icontains=query)
    rows = ranked(
        Company.objects.for_user(user)
        .prefetch_related("industries")
        .filter(
            contains(query, "name", "notes", "location", "website")
            | Q(Exists(industry))
            | Q(Exists(identifier))
        ),
        query,
        "name",
        "name",
    )

    def build(company) -> Hit:
        return Hit(
            kind="companies",
            id=company.pk,
            title=company.name,
            subtitle=" · ".join(
                part for part in (company.location, company.industry_names) if part
            ),
            url=company.get_absolute_url(),
            excerpt=excerpt(_first_match(query, company.notes), query) if company.notes else "",
            in_title=query.lower() in company.name.lower(),
        )

    return take(rows, limit, build)


def search_contacts(user, query: str, limit: int) -> Found:
    from postulo.jobs.models import Contact

    rows = ranked(
        Contact.objects.for_user(user)
        .select_related("company")
        .filter(contains(query, "name", "role", "email", "notes")),
        query,
        "name",
        "name",
    )

    def build(contact) -> Hit:
        return Hit(
            kind="contacts",
            id=contact.pk,
            title=contact.name,
            subtitle=" · ".join(
                part
                for part in (contact.role, contact.company.name if contact.company else "")
                if part
            ),
            url=contact.company.get_absolute_url()
            if contact.company
            else reverse("jobs:company_list"),
            excerpt=excerpt(_first_match(query, contact.notes, contact.email), query),
            in_title=query.lower() in contact.name.lower(),
        )

    return take(rows, limit, build)


def search_reminders(user, query: str, limit: int) -> Found:
    from postulo.applications.models import Reminder

    rows = (
        Reminder.objects.for_user(user)
        .select_related("application", "application__posting")
        .filter(contains(query, "summary"))
        .order_by("-due_at")
    )

    def build(reminder) -> Hit:
        return Hit(
            kind="reminders",
            id=reminder.pk,
            title=reminder.summary,
            subtitle=reminder.application.posting.title if reminder.application else "",
            url=(
                reminder.application.get_absolute_url()
                if reminder.application
                else reverse("applications:reminder_list")
            ),
            # A reminder is one line the person wrote, so a match is always a title match;
            # there is nothing else to rank against and no `ranked()` call above.
            in_title=True,
        )

    return take(rows, limit, build)


def search_letters(user, query: str, limit: int) -> Found:
    from postulo.documents.models import CoverLetter

    rows = ranked(
        CoverLetter.objects.for_user(user).filter(contains(query, "name", "subject", "body")),
        query,
        "name",
        "-created_at",
    )

    def build(letter) -> Hit:
        return Hit(
            kind="letters",
            id=letter.pk,
            title=letter.name,
            subtitle=letter.subject,
            url=letter.get_absolute_url(),
            excerpt=excerpt(_first_match(query, letter.body, letter.subject), query),
            in_title=query.lower() in letter.name.lower(),
        )

    return take(rows, limit, build)


def search_cvs(user, query: str, limit: int) -> Found:
    from postulo.documents.models import CV

    rows = ranked(
        CV.objects.for_user(user).filter(contains(query, "name", "headline", "summary")),
        query,
        "name",
        "name",
    )

    def build(cv) -> Hit:
        return Hit(
            kind="cvs",
            id=cv.pk,
            title=cv.name,
            subtitle=cv.headline,
            url=cv.get_absolute_url(),
            excerpt=excerpt(_first_match(query, cv.summary, cv.headline), query),
            in_title=query.lower() in cv.name.lower(),
        )

    return take(rows, limit, build)


def search_uploads(user, query: str, limit: int) -> Found:
    from postulo.documents.models import UploadedDocument

    rows = ranked(
        UploadedDocument.objects.for_user(user).filter(contains(query, "title", "notes")),
        query,
        "title",
        "-created_at",
    )

    def build(upload) -> Hit:
        return Hit(
            kind="uploads",
            id=upload.pk,
            title=upload.title,
            subtitle=upload.get_kind_display(),
            url=upload.get_absolute_url(),
            excerpt=excerpt(_first_match(query, upload.notes), query) if upload.notes else "",
            in_title=query.lower() in upload.title.lower(),
        )

    return take(rows, limit, build)


def search_sent(user, query: str, limit: int) -> Found:
    """The text of what was actually sent: "what did I claim?" without opening a PDF."""
    from postulo.documents.models import RenderedDocument

    rows = ranked(
        RenderedDocument.objects.for_user(user)
        .select_related("application", "application__posting", "application__posting__company")
        .filter(contains(query, "title", "source_text")),
        query,
        "title",
        "-rendered_at",
    )

    def build(sent) -> Hit:
        application = sent.application
        when = _day(sent.rendered_at)
        if application is not None:
            subtitle = str(
                _("in the %(kind)s you sent to %(company)s on %(when)s")
                % {
                    "kind": sent.get_kind_display().lower(),
                    "company": application.posting.company.name,
                    "when": when,
                }
            )
            url = application.get_absolute_url()
        else:
            subtitle = str(
                _("in the %(kind)s rendered on %(when)s")
                % {"kind": sent.get_kind_display().lower(), "when": when}
            )
            url = reverse("documents:rendered_list")
        return Hit(
            kind="sent",
            id=sent.pk,
            title=sent.title,
            subtitle=subtitle,
            url=url,
            excerpt=excerpt(_first_match(query, sent.source_text), query),
            in_title=query.lower() in sent.title.lower(),
        )

    return take(rows, limit, build)


def search_career(user, query: str, limit: int) -> Found:
    """Five models under one heading, each counted and capped on its own.

    Ten queries rather than two, and that is the price of the heading: the five have no
    common table to count across and no field to order against each other by. It is a fixed
    ten -- five experiences or five hundred, the page asks the same questions.
    """
    from postulo.resume import models as resume

    sections = [
        (
            resume.Experience,
            "experience",
            ("organisation", "role", "summary", "highlights"),
            "role",
            lambda r: f"{r.role} · {r.organisation}",
        ),
        (
            resume.Education,
            "education",
            ("institution", "qualification", "field_of_study", "highlights"),
            "qualification",
            lambda r: f"{r.qualification} · {r.institution}",
        ),
        (
            resume.Project,
            "projects",
            ("name", "role", "summary", "highlights"),
            "name",
            lambda r: r.name,
        ),
        (resume.Certification, "certifications", ("name", "issuer"), "name", lambda r: r.name),
        (resume.Skill, "skills", ("name",), "name", lambda r: r.name),
    ]
    overview = reverse("resume:overview")
    found = Found()
    for model, section, fields, title_field, title_of in sections:
        rows = ranked(
            model.objects.for_user(user).filter(contains(query, *fields)),
            query,
            title_field,
            "pk",
        )

        def build(row, section=section, fields=fields, title_of=title_of) -> Hit:
            texts = [getattr(row, name, "") or "" for name in fields]
            title = title_of(row)
            return Hit(
                kind="career",
                id=row.pk,
                title=title,
                subtitle=str(row._meta.verbose_name),
                url=f"{overview}#{section}",
                excerpt=excerpt(_first_match(query, *texts[2:], *texts[:2]), query),
                in_title=query.lower() in title.lower(),
            )

        part = take(rows, limit, build)
        found.total += part.total
        found.hits += part.hits
    found.hits = sorted(found.hits, key=lambda hit: not hit.in_title)[:limit]
    return found


#: Every group, in the order the page shows them: (kind, label, function, "more" URL name
#: and whether that page takes the query as ``q``).
GROUPS: tuple[tuple[str, str, Callable, str, bool], ...] = (
    ("applications", _("Applications"), search_applications, "applications:list", True),
    ("listings", _("Listings"), search_listings, "listings:list", False),
    ("companies", _("Companies"), search_companies, "jobs:company_list", True),
    ("contacts", _("People"), search_contacts, "jobs:company_list", False),
    ("reminders", _("Reminders"), search_reminders, "applications:reminder_list", False),
    ("sent", _("Text you sent"), search_sent, "documents:rendered_list", False),
    ("letters", _("Letters"), search_letters, "documents:letter_list", False),
    ("cvs", _("CVs"), search_cvs, "documents:cv_list", False),
    ("uploads", _("Files"), search_uploads, "documents:upload_list", False),
    ("career", _("Career record"), search_career, "resume:overview", False),
)


def clean_query(raw: str) -> str:
    return " ".join((raw or "").split())[:200]


def search(user, raw_query: str, *, limit: int = GROUP_LIMIT) -> list[Group]:
    """Every group with at least one hit, capped at ``limit`` hits each, title hits first."""
    query = clean_query(raw_query)
    if len(query) < MIN_QUERY_LENGTH:
        return []
    groups: list[Group] = []
    for kind, label, function, more_name, takes_query in GROUPS:
        found = function(user, query, limit)
        if not found.total:
            continue
        more_url = reverse(more_name)
        if takes_query:
            more_url = f"{more_url}?q={query}"
        groups.append(
            Group(
                kind=kind,
                label=str(label),
                hits=found.hits,
                total=found.total,
                more_url=more_url,
            )
        )
    return groups
