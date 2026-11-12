"""A contact's own page (#1097): who they are, and everything that happened with them.

Before a call to a recruiter one wants to see, in one place, the applications they were the
main contact on or put the person forward for, the interviews they were at, the meetings
they held, what they sent about a listing and the letters they wrote. Each of those rows
already names the contact by a foreign key; this page reads them and nothing else, and never
matches a name in a text, since a name in a note is not a fact about who was there.

**Read-only.** Nothing here writes: the details are changed on the edit page, the reader's
own note through its own form.

**Shared contacts.** A contact shared with somebody is one row and as many histories as
readers (#896): every row read here is the reader's own, through `for_user()`, so a person
sees their own applications and interviews with a shared recruiter and never a colleague's.

**A fixed number of queries.** One per kind of row, however long the history is; the
applications that name them as the main contact and the ones that name them as who
referred the person are one query, sorted out here. `tests/test_contact_page.py` pins it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import DetailView

from postulo.applications.models import Application, Appointment, Interview
from postulo.core.mixins import READ, SharedObjectMixin
from postulo.documents.models import ReferenceLetter
from postulo.resume.models import Reference

from .models import Contact, ListingEvent


@dataclasses.dataclass(frozen=True)
class Entry:
    """One line of a contact's history: what it was, when, and the page it belongs to."""

    #: What kind of row it came from, for the page's `data-` attribute and the tests.
    kind: str
    #: The badge: "Interview", "Main contact", "Message".
    label: str
    title: str
    #: When it happened, as the row keeps it: a moment, a day, or nothing recorded.
    when: dt.datetime | dt.date | None
    #: Where it sits in the history: ``when``, or when the row was written for an undated one.
    order: dt.datetime
    url: str
    #: The listing and the company it was about, when it was about one.
    listing: str = ""
    company: str = ""

    @property
    def has_time(self) -> bool:
        return isinstance(self.when, dt.datetime)


def _moment(value: dt.datetime | dt.date) -> dt.datetime:
    """A day as the start of that day where the reader is, so days and moments sort together."""
    if isinstance(value, dt.datetime):
        return value
    return timezone.make_aware(dt.datetime.combine(value, dt.time.min))


def history(contact: Contact, person) -> list[Entry]:
    """Every row of ``person``'s that names ``contact``, newest first.

    Five queries whatever the length: the applications (main contact or referrer, in one),
    the interviews, the appointments, the entries of listings' histories and the reference
    letters. Each row is the reader's own (`for_user`), whoever else may see the contact.
    """
    entries: list[Entry] = []

    applications = (
        Application.objects.for_user(person)
        .filter(Q(contact=contact) | Q(referred_by=contact))
        .select_related("posting__company")
    )
    for application in applications:
        when = application.applied_at or application.created_at
        posting = application.posting
        roles = []
        if application.contact_id == contact.pk:
            roles.append(("application", _("Main contact")))
        if application.referred_by_id == contact.pk:
            roles.append(("referral", _("Referred you")))
        for kind, label in roles:
            entries.append(
                Entry(
                    kind=kind,
                    label=label,
                    title=posting.title,
                    company=posting.company.name,
                    when=when,
                    order=when,
                    url=application.get_absolute_url(),
                )
            )

    interviews = (
        Interview.objects.for_user(person)
        .filter(contacts=contact)
        .select_related("application__posting__company")
    )
    for interview in interviews:
        posting = interview.application.posting
        entries.append(
            Entry(
                kind="interview",
                label=_("Interview"),
                title=interview.get_kind_display(),
                listing=posting.title,
                company=posting.company.name,
                when=interview.starts_at,
                order=interview.starts_at,
                url=interview.get_absolute_url(),
            )
        )

    appointments = (
        Appointment.objects.for_user(person).filter(contact=contact).select_related("company")
    )
    for appointment in appointments:
        entries.append(
            Entry(
                kind="appointment",
                label=_("Appointment"),
                title=appointment.get_how_display(),
                company=appointment.company.name,
                when=appointment.starts_at,
                order=appointment.starts_at,
                url=appointment.get_absolute_url(),
            )
        )

    events = (
        ListingEvent.objects.for_user(person)
        .filter(contact=contact)
        .select_related("posting__company")
    )
    for event in events:
        posting = event.posting
        entries.append(
            Entry(
                kind="listing_event",
                label=event.get_kind_display(),
                title=event.summary or event.get_kind_display(),
                listing=posting.title,
                company=posting.company.name,
                when=event.occurred_at,
                order=event.occurred_at,
                url=f"{posting.get_absolute_url()}#history",
            )
        )

    letters = (
        ReferenceLetter.objects.for_user(person).filter(referee=contact).select_related("upload")
    )
    for letter in letters:
        entries.append(
            Entry(
                kind="reference_letter",
                label=_("Reference letter"),
                title=letter.upload.title,
                when=letter.written_on,
                order=_moment(letter.written_on) if letter.written_on else letter.created_at,
                url=letter.upload.get_absolute_url(),
            )
        )

    entries.sort(key=lambda entry: entry.order, reverse=True)
    return entries


class ContactDetailView(SharedObjectMixin, DetailView):
    """The contact's page: details, the reader's own note, and their history (#1097)."""

    access = READ
    model = Contact
    template_name = "jobs/contact_detail.html"
    context_object_name = "contact"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .with_notes(self.request.user)
            .select_related("company", "department")
            .prefetch_related("phone_numbers", "postal_addresses", "web_links", "messaging_handles")
        )

    def get_context_data(self, **kwargs) -> dict:
        from postulo.core import gdpr, messaging_handles, phone_numbers, web_links

        from . import sharing

        context = super().get_context_data(**kwargs)
        person = self.request.user
        contact = self.object
        context["sharing"] = sharing.panel(contact, person)
        context["can_write"] = (
            Contact.objects.visible_to(person, write=True, everyone=True)
            .filter(pk=contact.pk)
            .exists()
        )
        context["history"] = history(contact, person)
        # The career entry that names them as a referee, at most one (#696): not a moment in
        # the history, so it is said beside the details.
        context["career_reference"] = (
            Reference.objects.for_user(person).filter(contact=contact).first()
        )
        context["gdpr_offered"] = gdpr.is_offered(person)
        context["several_numbers"] = phone_numbers.several_allowed(person)
        context["several_links"] = web_links.offered_kinds(person)
        context["messaging_offered"] = messaging_handles.several_allowed(person)
        return context
