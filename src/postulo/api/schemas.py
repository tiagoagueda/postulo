"""The shapes the API speaks: what goes out, and what may come in.

Output schemas are built from model instances by hand rather than through ModelSchema, so
the API's surface is exactly what is written here and a new model field never leaks into
it by accident. Inputs are validated by pydantic the same way the plugin contract is.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.urls import reverse
from ninja import Field, Schema

# ------------------------------------------------------------------ companies


class IdentifierOut(Schema):
    scheme: str = Field(
        description="wikidata, lei, register, linkedin, crunchbase, opencorporates, other"
    )
    value: str
    label: str = Field(default="", description="What the identifier is, for scheme 'other'")
    url: str = Field(default="", description="Where the value links, when the scheme has a home")


class IdentifierIn(Schema):
    scheme: str = Field(max_length=20)
    value: str = Field(max_length=200, description="A pasted address is accepted; the id is kept")
    label: str = Field(default="", max_length=60)


class CompanyOut(Schema):
    id: int
    name: str
    website: str = ""
    careers_url: str = ""
    location: str = ""
    industries: list[str] = Field(default_factory=list)
    identifiers: list[IdentifierOut] = Field(default_factory=list)
    notes: str = ""
    created_at: dt.datetime


class ContactOut(Schema):
    id: int
    company_id: int
    name: str
    role: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    notes: str = ""


class CompanyDetailOut(CompanyOut):
    contacts: list[ContactOut]
    listing_ids: list[int]


class CompanyIn(Schema):
    name: str = Field(max_length=200)
    website: str = Field(default="", max_length=200)
    careers_url: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    industries: list[str] = Field(
        default_factory=list, description="Names; unknown ones join the owner's vocabulary."
    )
    identifiers: list[IdentifierIn] = Field(
        default_factory=list,
        description="Added to the company; a Wikidata id also matches an existing company.",
    )
    notes: str = ""


class CompanyPatch(Schema):
    name: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)
    careers_url: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    industries: list[str] | None = Field(default=None, description="Replaces the whole list")
    identifiers: list[IdentifierIn] | None = Field(
        default=None, description="Replaces the whole list"
    )
    notes: str | None = None


class ContactIn(Schema):
    name: str = Field(max_length=200)
    role: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=40)
    linkedin_url: str = Field(default="", max_length=200)
    notes: str = ""


# ------------------------------------------------------------------- listings


class CompanyRef(Schema):
    id: int
    name: str


class ListingOut(Schema):
    id: int
    company: CompanyRef
    title: str
    location: str = ""
    remote_type: str = ""
    employment_type: str = ""
    url: str = ""
    source: str = ""
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = ""
    salary_period: str = ""
    posted_at: dt.date | None = None
    closes_at: dt.date | None = None
    closed_at: dt.datetime | None = None
    state: str
    discard_reason: str = ""
    noted_at: dt.datetime
    decided_at: dt.datetime | None = None
    application_ids: list[int]
    web_url: str


class ListingDetailOut(ListingOut):
    description: str = ""


class ListingIn(Schema):
    """The posting half of intake: company by name, and what the listing says."""

    company_name: str = Field(max_length=200)
    company_wikidata: str = Field(
        default="",
        max_length=200,
        description="The employer's Wikidata id, when known: a stronger match than the name.",
    )
    title: str = Field(max_length=250)
    url: str = Field(default="", max_length=500)
    location: str = Field(default="", max_length=200)
    remote_type: str = Field(default="", max_length=20)
    employment_type: str = Field(default="", max_length=20)
    source: str = Field(default="", max_length=120)
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = Field(default="EUR", max_length=3)
    salary_period: str = Field(default="year", max_length=10)
    closes_at: dt.date | None = None
    description: str = ""

    def posting_data(self) -> dict:
        """Only the listing's own fields — the one-step schema below adds more."""
        return {
            name: getattr(self, name)
            for name in ListingIn.model_fields
            if name not in ("company_name", "company_wikidata")
        }


class DiscardIn(Schema):
    reason: str = Field(default="other", max_length=20)


# --------------------------------------------------------------- applications


class ApplicationDetailsIn(Schema):
    """The person's side of an application."""

    status: str = "applied"
    channel: str = ""
    priority: int = 2
    deadline: dt.date | None = None
    tags: list[str] = Field(default_factory=list, description="Tag names; unknown ones are made.")

    def application_data(self) -> dict:
        return {
            "status": self.status,
            "channel": self.channel,
            "priority": self.priority,
            "deadline": self.deadline,
        }


class ApplicationIn(ListingIn, ApplicationDetailsIn):
    """Record an application in one step: the listing and the person's side together."""


class EventOut(Schema):
    id: int
    kind: str
    occurred_at: dt.datetime
    summary: str = ""
    body: str = ""
    from_status: str = ""
    to_status: str = ""
    actor: str = ""


class ReminderOut(Schema):
    id: int
    application_id: int | None = None
    summary: str
    due_at: dt.datetime
    done_at: dt.datetime | None = None
    notified_at: dt.datetime | None = None


class ListingRef(Schema):
    id: int
    title: str
    company: CompanyRef


class InterviewOut(Schema):
    id: int
    uid: str = Field(description="Stable across edits; what a calendar keys the meeting by")
    application_id: int
    kind: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    location: str = ""
    contact_ids: list[int]
    notes: str = ""
    outcome: str
    reminder_id: int | None = None
    web_url: str
    calendar_url: str


class InterviewIn(Schema):
    application_id: int
    kind: str = "video"
    starts_at: dt.datetime
    ends_at: dt.datetime | None = Field(
        default=None, description="An hour after the start if unset"
    )
    location: str = Field(default="", max_length=500)
    contact_ids: list[int] = Field(default_factory=list)
    notes: str = ""
    remind: bool = Field(default=True, description="Make a reminder for the day before")


class InterviewPatch(Schema):
    kind: str | None = None
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    location: str | None = Field(default=None, max_length=500)
    contact_ids: list[int] | None = None
    notes: str | None = None


class InterviewOutcomeIn(Schema):
    outcome: str = Field(description="done, cancelled or no_show")
    note: str = ""


class ApplicationOut(Schema):
    id: int
    listing: ListingRef
    status: str
    channel: str = ""
    priority: int
    applied_at: dt.datetime | None = None
    deadline: dt.date | None = None
    closed_at: dt.datetime | None = None
    contact_id: int | None = None
    tags: list[str]
    next_interview_at: dt.datetime | None = None
    created_at: dt.datetime
    web_url: str


class ApplicationDetailOut(ApplicationOut):
    events: list[EventOut]
    reminders: list[ReminderOut]
    interviews: list[InterviewOut]
    sent_document_ids: list[int]


class StatusIn(Schema):
    status: str
    note: str = ""


class EventIn(Schema):
    kind: str = "note"
    summary: str = Field(default="", max_length=250)
    body: str = ""
    occurred_at: dt.datetime | None = None


class ReminderIn(Schema):
    application_id: int | None = None
    summary: str = Field(max_length=250)
    due_at: dt.datetime


# ------------------------------------------------------------------ documents


class CVOut(Schema):
    id: int
    name: str
    headline: str = ""
    summary: str = ""
    theme: str
    language: str = ""
    item_count: int


class CVItemOut(Schema):
    kind: str
    label: str
    included: bool


class CVDetailOut(CVOut):
    items: list[CVItemOut]


class LetterOut(Schema):
    id: int
    name: str
    subject: str = ""
    is_template: bool
    theme: str
    created_at: dt.datetime


class LetterDetailOut(LetterOut):
    body: str


class LetterIn(Schema):
    name: str = Field(max_length=120)
    subject: str = Field(default="", max_length=250)
    body: str
    is_template: bool = False


class DocumentOut(Schema):
    id: int
    source: str = Field(description="'upload' for a file you had; 'rendered' for a snapshot")
    kind: str
    title: str
    application_id: int | None = None
    created_at: dt.datetime
    download_url: str


class SearchHitOut(Schema):
    id: int
    title: str
    subtitle: str = ""
    excerpt: str = ""
    web_url: str


class SearchGroupOut(Schema):
    kind: str
    label: str
    total: int
    hits: list[SearchHitOut]


class TokenOut(Schema):
    name: str
    owner: str
    scopes: list[str]
    expires_at: dt.datetime | None = None
    last_used_at: dt.datetime | None = None


# ------------------------------------------------------------------- helpers


def company_ref(company) -> dict:
    return {"id": company.pk, "name": company.name}


def listing_out(request, posting, *, detail: bool = False) -> dict:
    data = {
        "id": posting.pk,
        "company": company_ref(posting.company),
        "title": posting.title,
        "location": posting.location,
        "remote_type": posting.remote_type,
        "employment_type": posting.employment_type,
        "url": posting.url,
        "source": posting.source,
        "salary_min": posting.salary_min,
        "salary_max": posting.salary_max,
        "salary_currency": posting.salary_currency,
        "salary_period": posting.salary_period,
        "posted_at": posting.posted_at,
        "closes_at": posting.closes_at,
        "closed_at": posting.closed_at,
        "state": posting.derived_state,
        "discard_reason": posting.discard_reason,
        "noted_at": posting.noted_at,
        "decided_at": posting.decided_at,
        "application_ids": [a.pk for a in posting.applications.all()],
        "web_url": request.build_absolute_uri(posting.get_absolute_url()),
    }
    if detail:
        data["description"] = posting.description
    return data


def application_out(request, application, *, detail: bool = False) -> dict:
    posting = application.posting
    data = {
        "id": application.pk,
        "listing": {
            "id": posting.pk,
            "title": posting.title,
            "company": company_ref(posting.company),
        },
        "status": application.status,
        "channel": application.channel,
        "priority": application.priority,
        "applied_at": application.applied_at,
        "deadline": application.deadline,
        "closed_at": application.closed_at,
        "contact_id": application.contact_id,
        "tags": [tag.name for tag in application.tags.all()],
        "next_interview_at": getattr(application, "next_interview_at", None),
        "created_at": application.created_at,
        "web_url": request.build_absolute_uri(application.get_absolute_url()),
    }
    if detail:
        data["events"] = [
            {
                "id": event.pk,
                "kind": event.kind,
                "occurred_at": event.occurred_at,
                "summary": event.summary,
                "body": event.body,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "actor": event.actor,
            }
            for event in application.events.all()
        ]
        data["reminders"] = [reminder_out(r) for r in application.reminders.all()]
        data["interviews"] = [interview_out(request, i) for i in application.interviews.all()]
        data["sent_document_ids"] = [d.pk for d in application.rendered_documents.all()]
    return data


def interview_out(request, interview) -> dict:
    return {
        "id": interview.pk,
        "uid": interview.uid,
        "application_id": interview.application_id,
        "kind": interview.kind,
        "starts_at": interview.starts_at,
        "ends_at": interview.ends_at,
        "location": interview.location,
        "contact_ids": [c.pk for c in interview.contacts.all()],
        "notes": interview.notes,
        "outcome": interview.outcome,
        "reminder_id": interview.reminder_id,
        "web_url": request.build_absolute_uri(interview.get_absolute_url()),
        "calendar_url": request.build_absolute_uri(
            reverse("postulo-api:interview_calendar", kwargs={"pk": interview.pk})
        ),
    }


def reminder_out(reminder) -> dict:
    return {
        "id": reminder.pk,
        "application_id": reminder.application_id,
        "summary": reminder.summary,
        "due_at": reminder.due_at,
        "done_at": reminder.done_at,
        "notified_at": reminder.notified_at,
    }


def company_out(company, *, detail: bool = False) -> dict:
    data = {
        "id": company.pk,
        "name": company.name,
        "website": company.website,
        "careers_url": company.careers_url,
        "location": company.location,
        "industries": [industry.name for industry in company.industries.all()],
        "identifiers": [
            {"scheme": i.scheme, "value": i.value, "label": i.label, "url": i.url}
            for i in company.identifiers.all()
        ],
        "notes": company.notes,
        "created_at": company.created_at,
    }
    if detail:
        data["contacts"] = [contact_out(c) for c in company.contacts.all()]
        data["listing_ids"] = [p.pk for p in company.postings.all()]
    return data


def contact_out(contact) -> dict:
    return {
        "id": contact.pk,
        "company_id": contact.company_id,
        "name": contact.name,
        "role": contact.role,
        "email": contact.email,
        "phone": contact.phone,
        "linkedin_url": contact.linkedin_url,
        "notes": contact.notes,
    }


def document_out(request, document, *, source: str) -> dict:
    name = "postulo-api:document_download"
    return {
        "id": document.pk,
        "source": source,
        "kind": document.kind,
        "title": document.title,
        "application_id": getattr(document, "application_id", None),
        "created_at": document.created_at,
        "download_url": request.build_absolute_uri(
            reverse(name, kwargs={"source": source, "pk": document.pk})
        ),
    }
