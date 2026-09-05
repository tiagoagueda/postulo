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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from django.db.models import Q
from django.urls import reverse
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
    """``12 May 2026``, without a leading zero and without platform-specific strftime flags."""
    return f"{moment.day} {moment:%b %Y}"


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


# ----------------------------------------------------------------- per model


def search_listings(user, query: str) -> Iterable[Hit]:
    from postulo.jobs.models import JobPosting

    rows = (
        JobPosting.objects.for_user(user)
        .select_related("company")
        .filter(contains(query, "title", "description", "location", "source"))
        .order_by("-noted_at")
    )
    for posting in rows:
        yield Hit(
            kind="listings",
            id=posting.pk,
            title=posting.title,
            subtitle=posting.company.name,
            url=posting.get_absolute_url(),
            excerpt=excerpt(_first_match(query, posting.description, posting.location), query),
            in_title=query.lower() in posting.title.lower(),
        )


def search_applications(user, query: str) -> Iterable[Hit]:
    from postulo.applications.models import Application

    rows = (
        Application.objects.for_user(user)
        .select_related("posting", "posting__company")
        .filter(
            contains(
                query, "posting__title", "posting__company__name", "events__summary", "events__body"
            )
        )
        .distinct()
        .order_by("-created_at")
    )
    for application in rows:
        title = application.posting.title
        event = (
            application.events.filter(contains(query, "summary", "body"))
            .order_by("-occurred_at")
            .first()
        )
        passage = ""
        if event is not None:
            passage = _first_match(query, event.summary, event.body)
            passage = f"{_day(event.occurred_at)}: {passage}" if passage else ""
        yield Hit(
            kind="applications",
            id=application.pk,
            title=title,
            subtitle=f"{application.posting.company.name} · {application.get_status_display()}",
            url=application.get_absolute_url(),
            excerpt=excerpt(passage, query) if passage else "",
            in_title=query.lower() in title.lower()
            or query.lower() in application.posting.company.name.lower(),
        )


def search_companies(user, query: str) -> Iterable[Hit]:
    from postulo.jobs.models import Company

    rows = (
        Company.objects.for_user(user)
        .prefetch_related("industries")
        .filter(
            contains(
                query,
                "name",
                "notes",
                "location",
                "industries__name",
                "website",
                "identifiers__value",
            )
        )
        .distinct()
        .order_by("name")
    )
    for company in rows:
        yield Hit(
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


def search_contacts(user, query: str) -> Iterable[Hit]:
    from postulo.jobs.models import Contact

    rows = (
        Contact.objects.for_user(user)
        .select_related("company")
        .filter(contains(query, "name", "role", "email", "notes"))
        .order_by("name")
    )
    for contact in rows:
        yield Hit(
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


def search_reminders(user, query: str) -> Iterable[Hit]:
    from postulo.applications.models import Reminder

    rows = (
        Reminder.objects.for_user(user)
        .select_related("application", "application__posting")
        .filter(contains(query, "summary"))
        .order_by("-due_at")
    )
    for reminder in rows:
        yield Hit(
            kind="reminders",
            id=reminder.pk,
            title=reminder.summary,
            subtitle=reminder.application.posting.title if reminder.application else "",
            url=(
                reminder.application.get_absolute_url()
                if reminder.application
                else reverse("applications:reminder_list")
            ),
            in_title=True,
        )


def search_letters(user, query: str) -> Iterable[Hit]:
    from postulo.documents.models import CoverLetter

    rows = (
        CoverLetter.objects.for_user(user)
        .filter(contains(query, "name", "subject", "body"))
        .order_by("-created_at")
    )
    for letter in rows:
        yield Hit(
            kind="letters",
            id=letter.pk,
            title=letter.name,
            subtitle=letter.subject,
            url=letter.get_absolute_url(),
            excerpt=excerpt(_first_match(query, letter.body, letter.subject), query),
            in_title=query.lower() in letter.name.lower(),
        )


def search_cvs(user, query: str) -> Iterable[Hit]:
    from postulo.documents.models import CV

    rows = CV.objects.for_user(user).filter(contains(query, "name", "headline", "summary"))
    for cv in rows:
        yield Hit(
            kind="cvs",
            id=cv.pk,
            title=cv.name,
            subtitle=cv.headline,
            url=cv.get_absolute_url(),
            excerpt=excerpt(_first_match(query, cv.summary, cv.headline), query),
            in_title=query.lower() in cv.name.lower(),
        )


def search_uploads(user, query: str) -> Iterable[Hit]:
    from postulo.documents.models import UploadedDocument

    rows = (
        UploadedDocument.objects.for_user(user)
        .filter(contains(query, "title", "notes"))
        .order_by("-created_at")
    )
    for upload in rows:
        yield Hit(
            kind="uploads",
            id=upload.pk,
            title=upload.title,
            subtitle=upload.get_kind_display(),
            url=upload.get_absolute_url(),
            excerpt=excerpt(_first_match(query, upload.notes), query) if upload.notes else "",
            in_title=query.lower() in upload.title.lower(),
        )


def search_sent(user, query: str) -> Iterable[Hit]:
    """The text of what was actually sent: "what did I claim?" without opening a PDF."""
    from postulo.documents.models import RenderedDocument

    rows = (
        RenderedDocument.objects.for_user(user)
        .select_related("application", "application__posting", "application__posting__company")
        .filter(contains(query, "title", "source_text"))
        .order_by("-rendered_at")
    )
    for sent in rows:
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
        yield Hit(
            kind="sent",
            id=sent.pk,
            title=sent.title,
            subtitle=subtitle,
            url=url,
            excerpt=excerpt(_first_match(query, sent.source_text), query),
            in_title=query.lower() in sent.title.lower(),
        )


def search_career(user, query: str) -> Iterable[Hit]:
    from postulo.resume import models as resume

    sections = [
        (
            resume.Experience,
            "experience",
            ("organisation", "role", "summary", "highlights"),
            lambda r: f"{r.role} · {r.organisation}",
        ),
        (
            resume.Education,
            "education",
            ("institution", "qualification", "field_of_study", "highlights"),
            lambda r: f"{r.qualification} · {r.institution}",
        ),
        (resume.Project, "projects", ("name", "role", "summary", "highlights"), lambda r: r.name),
        (resume.Certification, "certifications", ("name", "issuer"), lambda r: r.name),
        (resume.Skill, "skills", ("name",), lambda r: r.name),
    ]
    overview = reverse("resume:overview")
    for model, section, fields, title_of in sections:
        for row in model.objects.for_user(user).filter(contains(query, *fields)):
            texts = [getattr(row, name, "") or "" for name in fields]
            title = title_of(row)
            yield Hit(
                kind="career",
                id=row.pk,
                title=title,
                subtitle=str(model._meta.verbose_name),
                url=f"{overview}#{section}",
                excerpt=excerpt(_first_match(query, *texts[2:], *texts[:2]), query),
                in_title=query.lower() in title.lower(),
            )


#: Every group, in the order the page shows them: (kind, label, function, "more" URL name
#: and whether that page takes the query as ``q``).
GROUPS: tuple[tuple[str, str, Callable, str, bool], ...] = (
    ("applications", _("Applications"), search_applications, "applications:list", True),
    ("listings", _("Listings"), search_listings, "listings:list", False),
    ("companies", _("Companies"), search_companies, "jobs:company_list", True),
    ("contacts", _("People"), search_contacts, "jobs:company_list", False),
    ("reminders", _("Reminders"), search_reminders, "applications:reminder_list", False),
    ("sent", _("Text you sent"), search_sent, "documents:rendered_list", False),
    ("letters", _("Cover letters"), search_letters, "documents:letter_list", False),
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
        hits = sorted(function(user, query), key=lambda hit: (not hit.in_title,))
        if not hits:
            continue
        more_url = reverse(more_name)
        if takes_query:
            more_url = f"{more_url}?q={query}"
        groups.append(
            Group(
                kind=kind, label=str(label), hits=hits[:limit], total=len(hits), more_url=more_url
            )
        )
    return groups
