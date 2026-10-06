"""Postulo's side of the `gdpr` feature (#297).

The plugin declares the capability; this is what the site does with the answer. The
division is the same one `phone-numbers` and `email-addresses` use: a plugin never asks
whether it is on for somebody, it says what it is, and Postulo asks the policy.

**The subject is another person, and that is the whole of the difference from the archive.**
The account holder's own rights are met where the data is: the account archive carries every
row and file the account owns, and account deletion removes them. What the archive never
reached is the data the instance keeps *on other people* — a contact's number, address,
notes — and the duties that sit with the instance itself rather than with one account. This
module is the answer for both: one contact, everything on it, one document; and the record
of what the instance does with the data it keeps at all.

**The export follows the archive's discipline rather than inventing one.** A plugin that
owns rows says how to put them in a document by answering `export_for`; one that owns rows
and does not answer is *named* in the document as `not_carried` rather than passed over,
because a document that is quietly incomplete is discovered by whoever restores it and the
original is gone by then.

**Erasure is the one act here that deletes, and it says what it deleted.** Off never deletes
anywhere in the plugin system, and that stays true: switching the feature off keeps every
row and merely stops offering these pages. Erasure is an act a person takes about one
contact, and a deletion that does not say what it removed is a guess about its own effect, so
the report carries the counts.

**And it is all or nothing.** While a plugin holds rows about the person and cannot remove
them, the erasure is refused and removes nothing (`ErasureRefused`): a report that says
somebody is gone while rows about them remain is the one thing it must never say (#371).

**Retention is a policy and a dry run, never a janitor.** The setting says how long records
about other people are kept; the dry run says what the policy would touch. Nothing here
deletes on a schedule, because a deletion nobody watched happen is the failure the dry run
exists to prevent, and the operator who has read the report is the one to perform it.

**The record of processing is drawn, not written.** Every installed plugin is a purpose and
every connection is a recipient in its own right, so the Article 30 page is assembled from
the registry and the connections at render time. A second list to keep in sync is how the
page that lies about the instance gets written.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

#: What the document of one contact is, in the shape a program can read it back. Bumped the
#: way the archive's format version is, when the shape changes in a way a reader notices.
#: 2 added ``listing_events``: the entries in listings' histories that name the person as
#: who they came from -- a message they sent, a call with them (#270).
DOCUMENT_NAME = "postulo-contact"
#: 3 added ``applications``, ``referrals`` and ``interviews``: the account holder's records
#: that name the person (#370). 4 added ``messaging_handles``: how they are reached on
#: Matrix, XMPP, Signal, Telegram, Threema or another service (#682). 5 added
#: ``reference_letters``: the letters they wrote, with their dates and where each went (#666).
#: 6 added ``references``: where the person is one of the account holder's referees, with
#: the relationship, the permission, whether details print and the note (#696).
DOCUMENT_VERSION = 6


def is_offered(person=None) -> bool:
    """Whether the site offers the data-protection side at all, for this person."""
    from postulo.plugins.gdpr import GDPR
    from postulo.plugins.policy import decide

    return decide(GDPR, person).on


# ------------------------------------------------------------------- the export


def _contact_rows(contact) -> dict:
    """The contact's own row, the way the archive writes one: what it holds, nothing more."""
    return {
        "id": contact.pk,
        "name": contact.name,
        "role": contact.role,
        "email": contact.email,
        "notes": contact.notes,
        "company": contact.company.name if contact.company_id else "",
        "department": (
            contact.department.name if contact.department_id and contact.department else ""
        ),
        "created_at": contact.created_at.isoformat() if contact.created_at else "",
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else "",
    }


def _phone_rows(contact) -> list[dict]:
    return [
        {
            "number": row.number,
            "kind": row.kind,
            "label": row.label,
            "primary": row.is_primary,
        }
        for row in contact.phone_numbers.all()
    ]


def _address_rows(contact) -> list[dict]:
    return [
        {
            "kind": row.kind,
            "label": row.label,
            "street": row.street,
            "postcode": row.postcode,
            "municipality": row.municipality,
            "region": row.region,
            "country": row.country,
            "primary": row.is_primary,
        }
        for row in contact.postal_addresses.all()
    ]


def _web_link_rows(contact) -> list[dict]:
    return [
        {
            "kind": row.kind,
            "service": row.service,
            "label": row.label,
            "url": row.url,
            "primary": row.is_primary,
        }
        for row in contact.web_links.all()
    ]


def _messaging_rows(contact) -> list[dict]:
    return [
        {
            "service": row.service,
            "label": row.label,
            "handle": row.handle,
            "primary": row.is_primary,
        }
        for row in contact.messaging_handles.all()
    ]


def _listing_event_rows(contact) -> list[dict]:
    """Every entry in a listing's history that names this person as who it came from (#270).

    What they said or sent is data about them as much as about the job, and this document
    is the one that has to be complete. The listing is named by its title and company, the
    way the entry reads on the page; nothing it points at is carried, because a capture or
    a file is the account holder's record rather than something this person gave.
    """
    from postulo.jobs.models import ListingEvent

    return [
        {
            "listing": event.posting.title,
            "company": event.posting.company.name,
            "kind": event.kind,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else "",
            "summary": event.summary,
            "body": event.body,
        }
        for event in ListingEvent.objects.filter(contact=contact).select_related(
            "posting", "posting__company"
        )
    ]


def references(contact) -> dict:
    """Every row of the account holder's that points at this contact, as querysets by kind.

    The one answer to "what refers to this person?", for the document, the erasure and the
    dry run, which used to decide for themselves and disagree (#370). An application names
    them as its main contact or as who referred them, an interview as somebody at it, an
    entry in a listing's history as who it came from. The numbers, addresses and links that
    belong to the person are not references: they are the person's own rows.
    """
    from postulo.applications.models import Application, Interview
    from postulo.documents.models import ReferenceLetter
    from postulo.jobs.models import ListingEvent

    return {
        "applications": Application.objects.filter(contact=contact),
        "referrals": Application.objects.filter(referred_by=contact),
        "interviews": Interview.objects.filter(contacts=contact),
        "listing_events": ListingEvent.objects.filter(contact=contact),
        "reference_letters": ReferenceLetter.objects.filter(referee=contact),
    }


def career_references(contact):
    """The entries of the account holder's career that name this person as a referee (#696).

    Not among `references`, whose rows are kept and lose the person: these are deleted with
    them, being nothing without who they are about.
    """
    from postulo.resume.models import Reference

    return Reference.objects.filter(contact=contact)


def _career_reference_rows(entries) -> list[dict]:
    """What the account holder wrote about this person as a referee: how they know them, what
    has been agreed and whether the details print. The note is the account holder's words
    about them, so it is here (#696)."""
    return [
        {
            "relationship": row.relationship,
            "permission": row.permission,
            "prints_details": row.show_details,
            "note": row.note,
        }
        for row in entries
    ]


def _application_rows(applications) -> list[dict]:
    """Applications named by role and company, the way they read on the page."""
    return [
        {"role": row.posting.title, "company": row.posting.company.name}
        for row in applications.select_related("posting", "posting__company")
    ]


def _interview_rows(interviews) -> list[dict]:
    return [
        {
            "kind": row.kind,
            "starts_at": row.starts_at.isoformat(),
            "role": row.application.posting.title,
            "company": row.application.posting.company.name,
        }
        for row in interviews.select_related("application__posting__company")
    ]


def _reference_letter_rows(letters) -> list[dict]:
    """The letters this person wrote: the file's name, the dates, and where each went (#666).

    The file itself is the account holder's and is not carried; what is about the person is
    that the letter exists, when it was written and which applications it went with.
    """
    return [
        {
            "file": letter.upload.title,
            "file_name": letter.upload.file.name.rsplit("/", 1)[-1] if letter.upload.file else "",
            "written_on": letter.written_on.isoformat() if letter.written_on else "",
            "valid_until": letter.valid_until.isoformat() if letter.valid_until else "",
            "delivery": letter.delivery,
            "applications": _application_rows(letter.upload.applications.all()),
        }
        for letter in letters.select_related("upload")
    ]


def contact_document(contact) -> dict:
    """Everything the instance holds on one other person, in one document.

    The answer to "what do you have on me?". The rows Postulo itself keeps, and then every
    installed plugin's own rows for this person through the archive's discipline: what a
    plugin carries is under `plugins.carried`, and what it owns but could not answer for is
    named under `not_carried` rather than dropped, because the document is the one that has
    to be complete or have to say it is not.
    """
    from postulo.plugins.data import export_sections_for

    plugins = export_sections_for(contact)
    found = references(contact)
    document = {
        "document": DOCUMENT_NAME,
        "version": DOCUMENT_VERSION,
        "contact": _contact_rows(contact),
        "phone_numbers": _phone_rows(contact),
        "postal_addresses": _address_rows(contact),
        "web_links": _web_link_rows(contact),
        "messaging_handles": _messaging_rows(contact),
        "listing_events": _listing_event_rows(contact),
        "applications": _application_rows(found["applications"]),
        "referrals": _application_rows(found["referrals"]),
        "interviews": _interview_rows(found["interviews"]),
        "reference_letters": _reference_letter_rows(found["reference_letters"]),
        "references": _career_reference_rows(career_references(contact)),
        "plugins": plugins,
    }
    if "not_carried" in plugins:
        # Hoisted to the top level so "what could not be carried" is answered in one
        # place, the way the archive answers it.
        document["not_carried"] = plugins["not_carried"]
    return document


# -------------------------------------------------------------------- the erasure


@dataclasses.dataclass
class ErasureReport:
    """What an erasure did, in counts and in a sentence a person can read.

    ``deleted`` is what stopped existing, by kind. ``unlinked`` is what survived but no
    longer points at the contact — an application keeps its history, it loses its main
    contact, or whoever referred the person to it (#239); an entry in a listing's history
    keeps its words and loses who it came from (#270).

    There is no list of what could not be reached. An erasure that would leave rows about
    the person with a plugin does not happen at all, and is an `ErasureRefused` (#371).
    """

    name: str
    deleted: dict[str, int]
    unlinked: dict[str, int]

    def summary(self) -> str:
        """The sentence shown to whoever did it: what went, and what stayed without them."""
        gone = ", ".join(
            _counted_kind(kind, count) for kind, count in self.deleted.items() if count
        )
        parts = []
        if gone:
            parts.append(_("%(name)s is gone, with %(what)s.") % {"name": self.name, "what": gone})
        if self.unlinked.get("applications"):
            parts.append(
                ngettext(
                    "%(count)d application kept, without its main contact.",
                    "%(count)d applications kept, without their main contact.",
                    self.unlinked["applications"],
                )
                % {"count": self.unlinked["applications"]}
            )
        if self.unlinked.get("referrals"):
            parts.append(
                ngettext(
                    "%(count)d application kept, without its referrer.",
                    "%(count)d applications kept, without their referrer.",
                    self.unlinked["referrals"],
                )
                % {"count": self.unlinked["referrals"]}
            )
        if self.unlinked.get("listing_events"):
            # What they said stays in the listing's history, as the account holder's record
            # of what arrived; who said it goes with them (#270).
            parts.append(
                ngettext(
                    "%(count)d entry in a listing's history kept, without who it came from.",
                    "%(count)d entries in listings' histories kept, without who they came from.",
                    self.unlinked["listing_events"],
                )
                % {"count": self.unlinked["listing_events"]}
            )
        if self.unlinked.get("interviews"):
            parts.append(
                ngettext(
                    "%(count)d interview kept, without them among who was there.",
                    "%(count)d interviews kept, without them among who was there.",
                    self.unlinked["interviews"],
                )
                % {"count": self.unlinked["interviews"]}
            )
        if self.unlinked.get("reference_letters"):
            parts.append(
                ngettext(
                    "%(count)d reference letter kept, without its referee.",
                    "%(count)d reference letters kept, without their referee.",
                    self.unlinked["reference_letters"],
                )
                % {"count": self.unlinked["reference_letters"]}
            )
        if self.deleted.get("references"):
            # What was sent stays true: the copy of a CV that named them keeps its words (#217).
            parts.append(_("A CV that was already sent keeps them, as it was sent."))
        return " ".join(parts) or _("Nothing was left to remove.")


def _counted_kind(key: str, count: int) -> str:
    """A number of one deleted kind, in the form its count needs (#391).

    One counted phrase per kind rather than a count beside a noun that is always plural:
    "1 telephone numbers" is wrong in English, and a language with more than two plural
    forms has nowhere to put the others.
    """
    phrase = {
        "phone_numbers": lambda: ngettext(
            "%(count)d telephone number", "%(count)d telephone numbers", count
        ),
        "postal_addresses": lambda: ngettext(
            "%(count)d postal address", "%(count)d postal addresses", count
        ),
        "web_links": lambda: ngettext("%(count)d web link", "%(count)d web links", count),
        "messaging_handles": lambda: ngettext(
            "%(count)d messaging handle", "%(count)d messaging handles", count
        ),
        "references": lambda: ngettext(
            "%(count)d entry among your references",
            "%(count)d entries among your references",
            count,
        ),
        "plugin_rows": lambda: ngettext("%(count)d plugin row", "%(count)d plugin rows", count),
    }.get(key)
    return (phrase() if phrase else f"%(count)d {key}") % {"count": count}


class ErasureRefused(Exception):
    """An erasure that did not happen, and the plugins it would have left rows with.

    Raised when a plugin that owns rows about the person cannot remove them: it has no
    `erase_for` and holds something (or cannot say whether it does), or its `erase_for`
    failed. **Nothing has been removed** when this is raised, including what another plugin
    did remove before the failure, which is undone with the rest.

    The other answer was to delete the contact anyway and name what was left. That keeps a
    faulty plugin from standing between a person and their erasure, and it also produces
    the report this module exists to prevent: "is gone", about somebody a plugin still
    holds rows on, pointing at a contact that is no longer there to erase again. A refusal
    names the plugin while the contact still exists, and the log carries the traceback.

    ``str()`` is the sentence for whoever asked.
    """

    def __init__(self, name: str, plugins: list[str]) -> None:
        self.name = name
        self.plugins = list(plugins)
        super().__init__(
            _(
                "Nothing was removed. %(name)s cannot be erased while these hold rows about "
                "them that could not be removed: %(plugins)s."
            )
            % {"name": name, "plugins": ", ".join(self.plugins)}
        )


def erase_contact(contact) -> ErasureReport:
    """Delete the contact and everything that points at it, and say what was deleted.

    The rows cascade by construction — the generic relations are why a deleted contact has
    never left its numbers behind — so the erasure is the report as much as the deletion:
    each count is taken before the delete that spends it, and the plugins are asked before
    anything goes, in the same transaction, because a row erased while its contact survives
    is a deletion that cannot be undone and the report that said it happened is wrong.

    Raises `ErasureRefused`, having removed nothing, while a plugin holds rows about the
    person that it could not remove.
    """
    from postulo.plugins import data as plugin_data

    with transaction.atomic():
        removed, unable = plugin_data.erase_rows_for(contact)
        if unable:
            # Raised inside the block, so that what the other plugins did remove comes back
            # with it: this block is a savepoint of the request's own transaction.
            raise ErasureRefused(contact.name, unable)
        deleted = {
            "phone_numbers": contact.phone_numbers.count(),
            "postal_addresses": contact.postal_addresses.count(),
            "web_links": contact.web_links.count(),
            "messaging_handles": contact.messaging_handles.count(),
            # Their entries among the referees go with them, and with those the places they
            # had on CVs; a CV already sent keeps its words, as what was sent stays true (#696).
            "references": career_references(contact).count(),
            "plugin_rows": removed,
        }
        # Each is kept and loses the person: an application its main contact (or whoever
        # referred it, #239), an entry in a listing's history who it came from (#270), an
        # interview a seat.
        unlinked = {kind: rows.count() for kind, rows in references(contact).items()}
        name = contact.name
        contact.delete()

    return ErasureReport(name=name, deleted=deleted, unlinked=unlinked)


# --------------------------------------------------------------------- the retention


def retention_days() -> int | None:
    """How long records about other people are kept, or nothing for no limit."""
    from .models import SiteSettings

    return SiteSettings.get().retention_days


def retention_cutoff(days: int | None = None) -> dt.date | None:
    """The date the policy would draw the line at, or nothing with no policy.

    `days` asks about a limit that is not the stored one — the number an administrator has
    typed and not yet saved (#483); left out, the stored limit answers.
    """
    if days is None:
        days = retention_days()
    if not days:
        return None
    return timezone.localdate() - dt.timedelta(days=days)


def retention_dry_run(days: int | None = None) -> dict:
    """What the retention policy would touch, in counts. Deletes nothing — that is the whole point.

    With `days` it is asked of that limit instead of the stored one, so the preview can
    answer for the number on screen before it is saved (#483).

    A dry run that removed a row to check whether it could would be a deletion with an
    apology, so the question is asked of the query set alone: how many contacts are older
    than the line, and what would go with them. **It names nobody.** The page is for the
    staff, and who an account is talking to at which company is that account's job search,
    which administrators are promised never to see (#369): the report is a count of
    contacts, of the accounts that hold them, and of what an erasure would remove. The
    erasure itself is a person's act on their own contact's page.
    """
    from postulo.applications.models import Application, Interview
    from postulo.jobs.models import Contact, ListingEvent
    from postulo.resume.models import Reference

    days = days or retention_days()
    cutoff = retention_cutoff(days)
    if cutoff is None:
        return {"days": None, "cutoff": None, "contacts": 0, "accounts": 0, "would_remove": {}}

    older_than = timezone.make_aware(dt.datetime.combine(cutoff, dt.time.min))
    contacts = Contact.objects.filter(created_at__lt=older_than)
    would_remove = {
        "phone_numbers": sum(c.phone_numbers.count() for c in contacts),
        "postal_addresses": sum(c.postal_addresses.count() for c in contacts),
        "web_links": sum(c.web_links.count() for c in contacts),
        "messaging_handles": sum(c.messaging_handles.count() for c in contacts),
        "references": Reference.objects.filter(contact__in=contacts).count(),
        # Every application that names them, as its contact or as who referred the
        # person: each is kept, and each loses the name (#239).
        "applications_unlinked": Application.objects.filter(
            Q(contact__in=contacts) | Q(referred_by__in=contacts)
        ).count(),
        "interviews_unlinked": Interview.objects.filter(contacts__in=contacts).distinct().count(),
        "listing_events_unlinked": ListingEvent.objects.filter(contact__in=contacts).count(),
    }
    return {
        "days": days,
        "cutoff": cutoff,
        "contacts": contacts.count(),
        "accounts": contacts.values("owner").distinct().count(),
        "would_remove": would_remove,
        "would_remove_line": would_remove_line(would_remove),
    }


def would_remove_line(would_remove: dict) -> str:
    """One contact's row of the dry run as a sentence, each count in its own plural form.

    The same phrases `ErasureReport.summary` uses, so the preview and the report read alike
    (#391).
    """
    removed = ", ".join(
        _counted_kind(kind, would_remove.get(kind, 0))
        for kind in ("phone_numbers", "postal_addresses", "web_links", "messaging_handles")
    )
    kept = would_remove.get("applications_unlinked", 0)
    return _("%(removed)s; %(kept)s") % {
        "removed": removed,
        "kept": ngettext("%(count)d application kept", "%(count)d applications kept", kept)
        % {"count": kept},
    }


# -------------------------------------------------------- the record of processing


def record_of_processing() -> dict:
    """What the instance processes, why, and who receives it — drawn at read time.

    Every installed plugin is a purpose: what it is, and the one line it says it does.
    Every connection is a recipient, counted by plugin and kind: the label and the
    configuration of a connection are its owner's (a mail server and a login name), and an
    administrator is promised never to read them (#369). Neither list is written down
    anywhere to be kept in sync, which is the difference between a record that can lie and
    one that is a mirror.
    """
    from postulo.plugins import registry
    from postulo.plugins.api import description_of, label_of
    from postulo.plugins.models import Connection

    purposes = [
        {
            "name": plugin.name,
            "label": label_of(plugin),
            "kind": plugin.kind,
            "description": description_of(plugin),
        }
        for kind in registry.GROUPS
        for plugin in registry.plugins(kind)
    ]
    recipients = [
        {"kind": row["kind"], "plugin": row["plugin"], "connections": row["n"]}
        for row in Connection.objects.values("kind", "plugin")
        .annotate(n=Count("pk"))
        .order_by("kind", "plugin")
    ]
    return {"purposes": purposes, "recipients": recipients}


# ---------------------------------------------------------------------- the notice


def notice() -> str:
    """The instance's privacy text, or nothing when the operator has written none.

    An instance with no notice to give does not invent one: the text is the operator's
    words, and a page of Postulo's own reassurance would be a promise the operator never
    made.
    """
    from .models import SiteSettings

    return SiteSettings.get().privacy_notice
