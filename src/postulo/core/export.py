"""Taking everything with you.

Data ownership that you cannot walk away with is not ownership. An export is one zip
holding a JSON document of every record belonging to one person, plus every file they
uploaded or Postulo rendered for them.

The document is written for a human to be able to read and for a different program to be
able to use. It is nested the way the records actually relate — companies contain
postings, postings contain applications, applications contain their timeline — rather
than being a flat dump of database tables, because the point is that somebody can do
something with it in ten years when Postulo is a memory.
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from io import BytesIO
from typing import Any

from django.utils import timezone

from postulo import __version__

#: Bumped when the shape changes in a way an importer must notice. 2 added the listing
#: state and dates on postings and the listing a capture became; 3 added interviews under
#: each application, the ``actor`` on events, the table layout on the profile, and a list
#: of ``industries`` on a company where there was one ``industry`` string; 4 replaced the
#: single ``phone`` string on a profile and on a contact with a ``phone_numbers`` list;
#: 5 added ``parent`` on a company, naming the company it belongs to; 9 added
#: ``postal_addresses`` on a profile and on a contact, which had nowhere to go before
#: (#92). The importer still reads every earlier format, filling the new fields in.
FORMAT_VERSION = 9

MANIFEST_NAME = "postulo.json"
MEDIA_PREFIX = "media/"

# What is written for each kind of record. Declared here so the assembly below reads as
# the shape of the document rather than as a wall of field names.
PROFILE_FIELDS = (
    "headline",
    "location",
    "website",
    "linkedin_url",
    "source_repo_url",
    "language",
    "time_zone",
    "theme",
    "table_settings",
    "quiet_after_days",
    "use_gravatar",
)
TAG_FIELDS = ("id", "name", "slug", "colour")
COMPANY_FIELDS = (
    "id",
    "name",
    "website",
    "careers_url",
    "location",
    "notes",
    "logo_source",
    "logo_source_url",
    "logo_fetched_at",
    "created_at",
)
CONTACT_FIELDS = ("id", "name", "role", "email", "linkedin_url", "notes")
#: A contact's team, written as a name beside them rather than as a table of its own: a
#: department belongs to one company, so the company's block is where it can be resolved.
#: What one telephone number is, in the file. Every number a holder has, in order, with
#: the primary marked -- not the primary alone.
PHONE_NUMBER_FIELDS = ("kind", "label", "number", "is_primary", "verified_at", "is_recovery")
#: An address has no verification and is never a way back in, so it carries neither: it
#: is the parts, and which one is primary (#92).
POSTAL_ADDRESS_FIELDS = (
    "kind",
    "label",
    "street",
    "postcode",
    "municipality",
    "region",
    "country",
    "is_primary",
)
POSTING_FIELDS = (
    "id",
    "title",
    "location",
    "remote_type",
    "employment_type",
    "url",
    "source",
    "description",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "posted_at",
    "closes_at",
    "closed_at",
    "state",
    "discard_reason",
    "noted_at",
    "decided_at",
    "created_at",
)
APPLICATION_FIELDS = (
    "id",
    "status",
    "channel",
    "priority",
    "applied_at",
    "deadline",
    "closed_at",
    "contact_id",
    "created_at",
)
EVENT_FIELDS = (
    "id",
    "kind",
    "occurred_at",
    "summary",
    "body",
    "from_status",
    "to_status",
    "actor",
    "created_at",
)
REMINDER_FIELDS = ("id", "summary", "due_at", "done_at")
INTERVIEW_FIELDS = (
    "id",
    "uid",
    "kind",
    "starts_at",
    "ends_at",
    "location",
    "notes",
    "outcome",
    "reminder_id",
    "created_at",
)
CV_FIELDS = ("id", "name", "headline", "summary", "theme", "language", "show_contact_details")
LETTER_FIELDS = ("id", "name", "kind", "subject", "body", "theme", "is_template", "language")
UPLOAD_FIELDS = ("id", "title", "kind", "notes", "version", "replaces_id", "created_at")
SENT_FIELDS = (
    "id",
    "title",
    "kind",
    "application_id",
    "cv_id",
    "cover_letter_id",
    "checksum",
    "rendered_at",
)
CAPTURE_FIELDS = (
    "id",
    "url",
    "source_name",
    "source_version",
    "origin",
    "status",
    "posting_id",
    "application_id",
    "created_at",
)

RESUME_FIELDS = {
    "experience": (
        "id",
        "organisation",
        "role",
        "location",
        "start_date",
        "end_date",
        "summary",
        "highlights",
        "order",
    ),
    "education": (
        "id",
        "institution",
        "qualification",
        "field_of_study",
        "location",
        "start_date",
        "end_date",
        "grade",
        "highlights",
        "order",
    ),
    "projects": (
        "id",
        "name",
        "role",
        "url",
        "start_date",
        "end_date",
        "summary",
        "highlights",
        "order",
    ),
    "skill_groups": ("id", "name", "order"),
    "skills": ("id", "name", "group_id", "order"),
    "certifications": (
        "id",
        "name",
        "issuer",
        "issued_on",
        "expires_on",
        "credential_url",
        "order",
    ),
    "languages": ("id", "name", "proficiency", "order"),
}


def _value(value: Any) -> Any:
    """Render a field in a form JSON can hold and a person can read."""
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool | list | dict):
        return value
    return str(value)


def _fields(instance, names: tuple[str, ...]) -> dict:
    return {name: _value(getattr(instance, name)) for name in names}


def _phone_numbers(holder) -> list[dict]:
    """Every number this holder has, whether or not it is currently being offered.

    An export is what somebody leaves with, so it carries what is *recorded* and not what
    the interface happens to be showing. A number kept back because *Several telephone
    numbers* is switched off is still theirs, and an export that quietly dropped it would
    turn a plugin toggle into data loss by the back door.
    """
    return [_fields(row, PHONE_NUMBER_FIELDS) for row in holder.phone_numbers.all()]


def _postal_addresses(holder) -> list[dict]:
    """Every address this holder has. Taking your data out has to include where you live.

    Same rule as the numbers above: what is recorded rather than what the interface is
    showing, so a plugin toggle never becomes data loss by the back door.
    """
    return [_fields(row, POSTAL_ADDRESS_FIELDS) for row in holder.postal_addresses.all()]


def _plugin_data(user) -> dict:
    from postulo.plugins import data

    try:
        return data.export_sections(user)
    except Exception:  # pragma: no cover - an archive is worth more than a tidy section
        return {"carried": {}, "not_carried": ["unknown"]}


def build_document(user) -> dict:
    """Assemble everything belonging to ``user`` as one nested document."""
    from postulo.accounts.models import Profile
    from postulo.core.models import Tag
    from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument
    from postulo.jobs.models import Capture, Company
    from postulo.resume import models as resume

    # Read afresh rather than through the instance cached on the user, which may be stale.
    profile = Profile.objects.filter(user=user).first()

    document: dict[str, Any] = {
        "postulo": {
            "format": FORMAT_VERSION,
            "version": __version__,
            "exported_at": timezone.now().isoformat(),
            "note": (
                "Everything one Postulo account holds. Identifiers are local to this "
                "file and exist so the parts can be reconnected; they are not "
                "meaningful anywhere else."
            ),
        },
        # What the plugins hold, and what they could not carry. A plugin that owns a table
        # says how to put a person's rows in their archive; one that owns a table and cannot
        # say is *named* here rather than passed over, because an archive that is quietly
        # incomplete is discovered on restore and one that says so is discovered while the
        # original still exists (#128).
        "plugins": _plugin_data(user),
        "account": {
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "profile": (
                {
                    **_fields(profile, PROFILE_FIELDS),
                    "phone_numbers": _phone_numbers(profile),
                    "postal_addresses": _postal_addresses(profile),
                }
                if profile
                else {}
            ),
            # An ORCID is one of the few things in here that means the same to somebody
            # else's software, so it travels with the rest.
            "identifiers": (
                [
                    {"scheme": row.scheme, "value": row.value, "label": row.label}
                    for row in profile.identifiers.all()
                ]
                if profile
                else []
            ),
            # The uploaded picture travels with the files; a Gravatar copy is refetched.
            "avatar_file": (
                f"{MEDIA_PREFIX}{profile.avatar.name}" if profile and profile.avatar else ""
            ),
        },
        "tags": [_fields(tag, TAG_FIELDS) for tag in Tag.objects.for_user(user)],
        "resume": {},
        "companies": [],
        "documents": {},
        "captures": [],
    }

    # ------------------------------------------------------------------ career
    resume_sections = {
        "experience": (
            resume.Experience,
            ("id", "organisation", "role", "location", "start_date", "end_date", "summary",
             "highlights", "order"),
        ),
        "education": (
            resume.Education,
            ("id", "institution", "qualification", "field_of_study", "location", "start_date",
             "end_date", "grade", "highlights", "order"),
        ),
        "projects": (
            resume.Project,
            ("id", "name", "role", "url", "start_date", "end_date", "summary", "highlights",
             "order"),
        ),
        "skill_groups": (resume.SkillGroup, ("id", "name", "order")),
        "skills": (resume.Skill, ("id", "name", "group_id", "order")),
        "certifications": (
            resume.Certification,
            ("id", "name", "issuer", "issued_on", "expires_on", "credential_url", "order"),
        ),
        "languages": (resume.LanguageSkill, ("id", "name", "proficiency", "order")),
        "links": (
            resume.Link,
            ("id", "title", "url", "kind", "description", "order", "check_status",
             "check_detail", "checked_at"),
        ),
    }  # fmt: skip
    for key, (model, names) in resume_sections.items():
        document["resume"][key] = [_fields(item, names) for item in model.objects.for_user(user)]

    # --------------------------------------------------- companies and the rest
    companies = Company.objects.for_user(user).prefetch_related(
        "industries",
        "identifiers",
        "contacts",
        "postings__applications__events",
        "postings__applications__reminders",
        "postings__applications__interviews__contacts",
        "postings__applications__sent_links",
    )
    for company in companies:
        document["companies"].append(
            {
                **_fields(company, COMPANY_FIELDS),
                "logo_file": f"{MEDIA_PREFIX}{company.logo.name}" if company.logo else "",
                # By name, not by id: identifiers in this file are local to it, and a name
                # is what the importer can resolve against companies it has just made or
                # matched. An empty string is a company that belongs to nobody.
                "parent": company.parent.name if company.parent_id else "",
                "industries": [industry.name for industry in company.industries.all()],
                "identifiers": [
                    {"scheme": i.scheme, "value": i.value, "label": i.label}
                    for i in company.identifiers.all()
                ],
                "contacts": [
                    {
                        **_fields(contact, CONTACT_FIELDS),
                        "department": contact.department.name if contact.department_id else "",
                        "phone_numbers": _phone_numbers(contact),
                        "postal_addresses": _postal_addresses(contact),
                    }
                    for contact in company.contacts.all()
                ],
                "postings": [
                    {
                        **_fields(posting, POSTING_FIELDS),
                        "applications": [
                            {
                                **_fields(application, APPLICATION_FIELDS),
                                "tags": [tag.slug for tag in application.tags.all()],
                                "sent_link_ids": [link.pk for link in application.sent_links.all()],
                                "events": [
                                    _fields(event, EVENT_FIELDS)
                                    for event in application.events.all()
                                ],
                                "reminders": [
                                    _fields(reminder, REMINDER_FIELDS)
                                    for reminder in application.reminders.all()
                                ],
                                "interviews": [
                                    {
                                        **_fields(interview, INTERVIEW_FIELDS),
                                        "contact_ids": [c.pk for c in interview.contacts.all()],
                                    }
                                    for interview in application.interviews.all()
                                ],
                            }
                            for application in posting.applications.all()
                        ],
                    }
                    for posting in company.postings.all()
                ],
            }
        )

    # --------------------------------------------------------------- documents
    document["documents"]["cvs"] = [
        {
            **_fields(
                cv,
                ("id", "name", "headline", "summary", "theme", "language", "show_contact_details"),
            ),
            "entries": [
                {
                    "kind": item.content_type.model,
                    "ref": item.object_id,
                    "order": item.order,
                    "included": item.is_included,
                    "override_highlights": item.override_highlights,
                }
                for item in cv.items.select_related("content_type").order_by("order", "pk")
            ],
        }
        for cv in CV.objects.for_user(user).prefetch_related("items")
    ]
    document["documents"]["cover_letters"] = [
        _fields(letter, LETTER_FIELDS) for letter in CoverLetter.objects.for_user(user)
    ]
    document["documents"]["uploads"] = [
        {
            **_fields(upload, UPLOAD_FIELDS),
            "file": f"{MEDIA_PREFIX}{upload.file.name}" if upload.file else "",
            "copies": _copies(upload),
        }
        for upload in UploadedDocument.objects.for_user(user).prefetch_related("copies")
    ]
    document["documents"]["sent"] = [
        {
            **_fields(sent, SENT_FIELDS),
            "file": f"{MEDIA_PREFIX}{sent.file.name}" if sent.file else "",
            "source_text": sent.source_text,
            "copies": _copies(sent),
        }
        for sent in RenderedDocument.objects.for_user(user).prefetch_related("copies")
    ]

    document["captures"] = [
        {
            **_fields(capture, CAPTURE_FIELDS),
            "data": capture.data,
        }
        for capture in Capture.objects.for_user(user)
    ]

    document["counts"] = {
        "companies": len(document["companies"]),
        "applications": sum(
            len(posting["applications"])
            for company in document["companies"]
            for posting in company["postings"]
        ),
        "interviews": sum(
            len(application["interviews"])
            for company in document["companies"]
            for posting in company["postings"]
            for application in posting["applications"]
        ),
        "cvs": len(document["documents"]["cvs"]),
        "cover_letters": len(document["documents"]["cover_letters"]),
        "uploads": len(document["documents"]["uploads"]),
        "sent_documents": len(document["documents"]["sent"]),
        "captures": len(document["captures"]),
    }
    return document


def _copies(document) -> list[dict]:
    """Where copies of this document went: the references external stores handed back.

    Only copies that arrived are worth carrying; a pending or failed one is a state of
    this instance, not a fact about the archive.
    """
    return [
        {
            "store": copy.store,
            "label": copy.label,
            "external_id": copy.external_id,
            "external_url": copy.external_url,
            "sent_at": copy.sent_at.isoformat() if copy.sent_at else None,
        }
        for copy in document.copies.all()
        if copy.status == "sent"
    ]


def _media_paths(document: dict) -> list[str]:
    """Every file the document refers to, as a storage name."""
    names = []
    for section in ("uploads", "sent"):
        for entry in document["documents"].get(section, []):
            if entry.get("file"):
                names.append(entry["file"][len(MEDIA_PREFIX) :])
    avatar = document.get("account", {}).get("avatar_file")
    if avatar:
        names.append(avatar[len(MEDIA_PREFIX) :])
    for company in document.get("companies", []):
        if company.get("logo_file"):
            names.append(company["logo_file"][len(MEDIA_PREFIX) :])
    return names


def write_archive(user, target=None) -> BytesIO:
    """Build the complete export as a zip.

    Returns an in-memory buffer when no target is given. An export is one person's job
    search: measured in megabytes, not gigabytes, so holding it in memory is reasonable
    and streaming would be more machinery than the size justifies.
    """
    from django.core.files.storage import default_storage

    document = build_document(user)
    buffer = target if target is not None else BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            MANIFEST_NAME, json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False)
        )
        for name in _media_paths(document):
            try:
                with default_storage.open(name, "rb") as handle:
                    archive.writestr(f"{MEDIA_PREFIX}{name}", handle.read())
            except FileNotFoundError:
                # A record whose file has gone missing should not cost you the export of
                # everything else; the manifest still says the file was expected.
                continue

    if target is None:
        buffer.seek(0)
    return buffer


def suggested_filename(user) -> str:
    stamp = timezone.localdate().isoformat()
    local_part = user.email.split("@")[0]
    return f"postulo-{local_part}-{stamp}.zip"
