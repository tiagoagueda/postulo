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

#: Bumped when the shape changes in a way an importer must notice.
FORMAT_VERSION = 1

MANIFEST_NAME = "postulo.json"
MEDIA_PREFIX = "media/"

# What is written for each kind of record. Declared here so the assembly below reads as
# the shape of the document rather than as a wall of field names.
PROFILE_FIELDS = (
    "headline",
    "phone",
    "location",
    "website",
    "linkedin_url",
    "source_repo_url",
    "language",
    "time_zone",
    "theme",
)
TAG_FIELDS = ("id", "name", "slug", "colour")
COMPANY_FIELDS = (
    "id",
    "name",
    "website",
    "careers_url",
    "location",
    "industry",
    "notes",
    "created_at",
)
CONTACT_FIELDS = ("id", "name", "role", "email", "phone", "linkedin_url", "notes")
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
    "created_at",
)
REMINDER_FIELDS = ("id", "summary", "due_at", "done_at")
CV_FIELDS = ("id", "name", "headline", "summary", "theme", "language", "show_contact_details")
LETTER_FIELDS = ("id", "name", "subject", "body", "theme", "is_template")
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


def build_document(user) -> dict:
    """Assemble everything belonging to ``user`` as one nested document."""
    from postulo.core.models import Tag
    from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument
    from postulo.jobs.models import Capture, Company
    from postulo.resume import models as resume

    profile = getattr(user, "profile", None)

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
        "account": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "profile": _fields(profile, PROFILE_FIELDS) if profile else {},
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
    }  # fmt: skip
    for key, (model, names) in resume_sections.items():
        document["resume"][key] = [_fields(item, names) for item in model.objects.for_user(user)]

    # --------------------------------------------------- companies and the rest
    companies = Company.objects.for_user(user).prefetch_related(
        "contacts", "postings__applications__events", "postings__applications__reminders"
    )
    for company in companies:
        document["companies"].append(
            {
                **_fields(company, COMPANY_FIELDS),
                "contacts": [
                    _fields(
                        contact,
                        ("id", "name", "role", "email", "phone", "linkedin_url", "notes"),
                    )
                    for contact in company.contacts.all()
                ],
                "postings": [
                    {
                        **_fields(posting, POSTING_FIELDS),
                        "applications": [
                            {
                                **_fields(application, APPLICATION_FIELDS),
                                "tags": [tag.slug for tag in application.tags.all()],
                                "events": [
                                    _fields(event, EVENT_FIELDS)
                                    for event in application.events.all()
                                ],
                                "reminders": [
                                    _fields(reminder, REMINDER_FIELDS)
                                    for reminder in application.reminders.all()
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
            **_fields(
                upload, ("id", "title", "kind", "notes", "version", "replaces_id", "created_at")
            ),
            "file": f"{MEDIA_PREFIX}{upload.file.name}" if upload.file else "",
        }
        for upload in UploadedDocument.objects.for_user(user)
    ]
    document["documents"]["sent"] = [
        {
            **_fields(sent, SENT_FIELDS),
            "file": f"{MEDIA_PREFIX}{sent.file.name}" if sent.file else "",
            "source_text": sent.source_text,
        }
        for sent in RenderedDocument.objects.for_user(user)
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
        "cvs": len(document["documents"]["cvs"]),
        "cover_letters": len(document["documents"]["cover_letters"]),
        "uploads": len(document["documents"]["uploads"]),
        "sent_documents": len(document["documents"]["sent"]),
        "captures": len(document["captures"]),
    }
    return document


def _media_paths(document: dict) -> list[str]:
    """Every file the document refers to, as a storage name."""
    names = []
    for section in ("uploads", "sent"):
        for entry in document["documents"].get(section, []):
            if entry.get("file"):
                names.append(entry["file"][len(MEDIA_PREFIX) :])
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
