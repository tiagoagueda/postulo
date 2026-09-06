"""Reading an export back in.

The counterpart to :mod:`postulo.core.export`, and the reason an export is worth having:
data you can take out but not put back is a souvenir, not a copy.

Import creates records; it never merges. Working out whether the "Aperture Science" in a
file is the same one already in the database is a judgement Postulo is not in a position
to make, and getting it wrong silently would be worse than not trying. So an import into
an account that already holds a job search refuses unless somebody insists.

Identifiers in the file are local to it. Everything is created in dependency order and
old identifiers are mapped to new records as they go.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime

from .export import MANIFEST_NAME, MEDIA_PREFIX


class ArchiveError(Exception):
    """The archive cannot be read, or must not be applied."""


@dataclass
class ImportReport:
    companies: int = 0
    postings: int = 0
    applications: int = 0
    events: int = 0
    reminders: int = 0
    interviews: int = 0
    resume_items: int = 0
    cvs: int = 0
    cover_letters: int = 0
    uploads: int = 0
    sent_documents: int = 0
    tags: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        return [
            f"{value} {name.replace('_', ' ')}"
            for name, value in vars(self).items()
            if isinstance(value, int) and value
        ]


def _restore_copies(user, entries: list, **document) -> None:
    """Keep the references to copies in external stores, with no connection behind them.

    The archive says where a document went; the connection that took it there is a
    secret-bearing thing the person recreates by hand. The reference survives on its own.
    """
    from postulo.documents.models import DocumentCopy

    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("store"):
            continue
        DocumentCopy.objects.create(
            owner=user,
            connection=None,
            store=str(entry.get("store", ""))[:60],
            label=str(entry.get("label", ""))[:100],
            status="sent",
            external_id=str(entry.get("external_id", ""))[:500],
            external_url=str(entry.get("external_url", ""))[:500],
            sent_at=_dt(entry.get("sent_at")),
            next_attempt_at=None,
            **document,
        )


def _dt(value):
    return parse_datetime(value) if value else None


def _d(value):
    return parse_date(value) if value else None


def read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError as exc:
        raise ArchiveError(f"No {MANIFEST_NAME} in that archive.") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ArchiveError(f"{MANIFEST_NAME} is not valid JSON.") from exc

    header = document.get("postulo") or {}
    if "format" not in header:
        raise ArchiveError("That does not look like a Postulo export.")
    return document


def account_is_empty(user) -> bool:
    from postulo.applications.models import Application
    from postulo.documents.models import CV
    from postulo.jobs.models import Company
    from postulo.resume.models import Experience

    return not any(
        model.objects.for_user(user).exists() for model in (Application, Company, CV, Experience)
    )


@transaction.atomic
def load(user, archive: zipfile.ZipFile, *, force: bool = False) -> ImportReport:
    """Create everything in ``archive`` under ``user``.

    Runs in one transaction: a failure half way through leaves the account exactly as it
    was, rather than partly overwritten by a file that turned out to be broken.
    """
    from postulo.applications.models import Application, ApplicationEvent, Interview, Reminder
    from postulo.core.models import Tag
    from postulo.documents.models import CV, CoverLetter, CVItem, RenderedDocument, UploadedDocument
    from postulo.jobs import identifiers
    from postulo.jobs.models import Capture, Company, Contact, Industry, JobPosting
    from postulo.jobs.services import set_identifiers
    from postulo.resume import models as resume

    document = read_manifest(archive)
    if not force and not account_is_empty(user):
        raise ArchiveError(
            "This account already holds a job search. Importing would add a second copy "
            "of everything rather than merging the two. Use an empty account, or pass "
            "--force if a duplicate is genuinely what you want."
        )

    report = ImportReport()
    from django.contrib.contenttypes.models import ContentType

    # ------------------------------------------------------------------ profile
    account = document.get("account") or {}
    if account.get("first_name") or account.get("last_name"):
        user.first_name = account.get("first_name") or user.first_name
        user.last_name = account.get("last_name") or user.last_name
        user.save(update_fields=["first_name", "last_name"])
    # The username travels too, but the account importing already has one, and taking
    # somebody else's on this instance is out of the question: keep it when it is free.
    wanted = (account.get("username") or "").strip().casefold()
    if wanted and wanted != user.username:
        taken = type(user)._default_manager.exclude(pk=user.pk).filter(username=wanted)
        if not taken.exists():
            user.username = wanted
            user.save(update_fields=["username"])
    profile_data = account.get("profile") or {}
    profile = getattr(user, "profile", None)
    if profile and profile_data:
        for name, value in profile_data.items():
            if hasattr(profile, name) and value:
                setattr(profile, name, value)
        profile.save()
    avatar_file = account.get("avatar_file") or ""
    if profile and avatar_file:
        content = _extract(archive, avatar_file)
        if content is not None:
            profile.avatar.save(avatar_file.rsplit("/", 1)[-1], ContentFile(content), save=True)

    # --------------------------------------------------------------------- tags
    tags_by_slug: dict[str, Tag] = {}
    for entry in document.get("tags", []):
        tag, created = Tag.objects.get_or_create(
            owner=user,
            slug=entry.get("slug") or entry.get("name", "").lower(),
            defaults={"name": entry.get("name", ""), "colour": entry.get("colour", "")},
        )
        tags_by_slug[tag.slug] = tag
        report.tags += int(created)

    # ------------------------------------------------------------------- career
    resume_map: dict[str, dict[int, object]] = {}
    section_models = {
        "experience": resume.Experience,
        "education": resume.Education,
        "projects": resume.Project,
        "skill_groups": resume.SkillGroup,
        "certifications": resume.Certification,
        "languages": resume.LanguageSkill,
    }
    date_fields = {"start_date", "end_date", "issued_on", "expires_on"}

    for key, model in section_models.items():
        resume_map[key] = {}
        for entry in document.get("resume", {}).get(key, []):
            old_id = entry.pop("id", None)
            values = {
                name: (_d(value) if name in date_fields else value) for name, value in entry.items()
            }
            created = model.objects.create(owner=user, **values)
            resume_map[key][old_id] = created
            report.resume_items += 1

    # Skills come after their groups, so the group reference can be resolved.
    resume_map["skills"] = {}
    for entry in document.get("resume", {}).get("skills", []):
        old_id = entry.pop("id", None)
        group_id = entry.pop("group_id", None)
        group = resume_map["skill_groups"].get(group_id)
        created = resume.Skill.objects.create(owner=user, group=group, **entry)
        resume_map["skills"][old_id] = created
        report.resume_items += 1

    # ------------------------------------------------ companies and their work
    contacts: dict[int, Contact] = {}
    postings: dict[int, JobPosting] = {}
    applications: dict[int, Application] = {}

    for company_entry in document.get("companies", []):
        contact_entries = company_entry.pop("contacts", [])
        posting_entries = company_entry.pop("postings", [])
        company_entry.pop("id", None)
        company_entry.pop("created_at", None)
        # Format 3 carries a list; earlier formats had one string, possibly a typed list.
        industry_names = company_entry.pop("industries", None)
        if industry_names is None:
            industry_names = Industry.split(company_entry.get("industry", ""))
        company_entry.pop("industry", None)
        identifier_entries = company_entry.pop("identifiers", None) or []

        # A company is an identity keyed by its name, which is why intake matches on
        # it too. Importing attaches to one that already exists rather than colliding
        # with the per-owner unique name — the postings and applications underneath are
        # what actually get duplicated when an import is forced. An identifier both
        # sides carry is a stronger match than the spelling.
        name = company_entry.pop("name", "")
        company = None
        for entry in identifier_entries:
            if entry.get("scheme") != identifiers.OTHER:
                company = Company.by_identifier(
                    user, entry.get("scheme", ""), entry.get("value", "")
                )
                if company is not None:
                    break
        if company is None:
            company = Company.objects.for_user(user).filter(name__iexact=name).first()
        if company is None:
            company = Company.objects.create(owner=user, name=name, **company_entry)
            report.companies += 1
        if industry_names:
            company.industries.add(*Industry.named(user, industry_names))
        for entry in identifier_entries:
            try:
                set_identifiers(
                    company,
                    [(entry.get("scheme", ""), entry.get("value", ""), entry.get("label", ""))],
                )
            except ValidationError:
                # Malformed in the file, or already on another company here: the record
                # is still worth having, the id is not worth refusing it over.
                continue

        for contact_entry in contact_entries:
            old_id = contact_entry.pop("id", None)
            contacts[old_id] = Contact.objects.create(owner=user, company=company, **contact_entry)

        for posting_entry in posting_entries:
            application_entries = posting_entry.pop("applications", [])
            old_posting_id = posting_entry.pop("id", None)
            created_at = _dt(posting_entry.pop("created_at", None))
            for name in ("posted_at", "closes_at"):
                posting_entry[name] = _d(posting_entry.get(name))
            posting_entry["closed_at"] = _dt(posting_entry.get("closed_at"))
            # Format 1 predates listings: a posting then existed only under an application,
            # so it was noted and decided when it was created.
            posting_entry["noted_at"] = _dt(posting_entry.get("noted_at")) or created_at
            posting_entry["decided_at"] = _dt(posting_entry.get("decided_at")) or (
                created_at if application_entries else None
            )
            if posting_entry.get("noted_at") is None:
                posting_entry.pop("noted_at")
            posting_entry.setdefault("state", "new")
            posting_entry.setdefault("discard_reason", "")

            posting = JobPosting.objects.create(owner=user, company=company, **posting_entry)
            postings[old_posting_id] = posting
            report.postings += 1

            for application_entry in application_entries:
                event_entries = application_entry.pop("events", [])
                reminder_entries = application_entry.pop("reminders", [])
                interview_entries = application_entry.pop("interviews", [])
                tag_slugs = application_entry.pop("tags", [])
                old_id = application_entry.pop("id", None)
                application_entry.pop("created_at", None)
                contact_id = application_entry.pop("contact_id", None)

                application_entry["applied_at"] = _dt(application_entry.get("applied_at"))
                application_entry["closed_at"] = _dt(application_entry.get("closed_at"))
                application_entry["deadline"] = _d(application_entry.get("deadline"))

                application = Application.objects.create(
                    owner=user,
                    posting=posting,
                    contact=contacts.get(contact_id),
                    **application_entry,
                )
                applications[old_id] = application
                report.applications += 1

                if tag_slugs:
                    application.tags.set(
                        [tags_by_slug[slug] for slug in tag_slugs if slug in tags_by_slug]
                    )

                for event_entry in event_entries:
                    event_entry.pop("id", None)
                    event_entry.pop("created_at", None)
                    event_entry["occurred_at"] = _dt(event_entry.get("occurred_at"))
                    ApplicationEvent.objects.create(application=application, **event_entry)
                    report.events += 1

                reminders: dict[int, Reminder] = {}
                for reminder_entry in reminder_entries:
                    old_reminder_id = reminder_entry.pop("id", None)
                    reminders[old_reminder_id] = Reminder.objects.create(
                        owner=user,
                        application=application,
                        summary=reminder_entry.get("summary", ""),
                        due_at=_dt(reminder_entry.get("due_at")),
                        done_at=_dt(reminder_entry.get("done_at")),
                    )
                    report.reminders += 1

                for interview_entry in interview_entries:
                    interview_entry.pop("id", None)
                    interview_entry.pop("created_at", None)
                    contact_ids = interview_entry.pop("contact_ids", [])
                    reminder_id = interview_entry.pop("reminder_id", None)
                    # The calendar identifier travels, so a calendar that knew the meeting
                    # still does; a forced duplicate on the same instance gets a fresh one.
                    uid = interview_entry.pop("uid", None)
                    if uid and Interview.objects.filter(owner=user, uid=uid).exists():
                        uid = None
                    interview = Interview.objects.create(
                        owner=user,
                        application=application,
                        reminder=reminders.get(reminder_id),
                        starts_at=_dt(interview_entry.pop("starts_at", None)),
                        ends_at=_dt(interview_entry.pop("ends_at", None)),
                        **({"uid": uid} if uid else {}),
                        **interview_entry,
                    )
                    interview.contacts.set([contacts[i] for i in contact_ids if i in contacts])
                    report.interviews += 1

    # ---------------------------------------------------------------- documents
    documents = document.get("documents", {})

    cvs: dict[int, CV] = {}
    content_types = {
        "experience": "experience",
        "education": "education",
        "project": "projects",
        "skillgroup": "skill_groups",
        "certification": "certifications",
        "languageskill": "languages",
    }
    for cv_entry in documents.get("cvs", []):
        entries = cv_entry.pop("entries", [])
        old_id = cv_entry.pop("id", None)
        # A CV is content rather than an identity, so a clash gets a new name instead of
        # being merged into whatever happens to share its title.
        cv_entry["name"] = _free_cv_name(user, cv_entry.get("name", ""))
        cv = CV.objects.create(owner=user, **cv_entry)
        cvs[old_id] = cv
        report.cvs += 1

        for item in entries:
            section = content_types.get(item.get("kind", ""))
            target = resume_map.get(section, {}).get(item.get("ref")) if section else None
            if target is None:
                report.skipped.append(
                    f"CV entry {item.get('kind')}#{item.get('ref')} on {cv.name}: no such record"
                )
                continue
            CVItem.objects.create(
                owner=user,
                cv=cv,
                content_type=ContentType.objects.get_for_model(target),
                object_id=target.pk,
                order=item.get("order", 0),
                is_included=item.get("included", True),
                override_highlights=item.get("override_highlights", ""),
            )

    letters: dict[int, CoverLetter] = {}
    for letter_entry in documents.get("cover_letters", []):
        old_id = letter_entry.pop("id", None)
        letters[old_id] = CoverLetter.objects.create(owner=user, **letter_entry)
        report.cover_letters += 1

    uploads: dict[int, UploadedDocument] = {}
    pending_supersedes: list[tuple[UploadedDocument, int]] = []
    for upload_entry in documents.get("uploads", []):
        old_id = upload_entry.pop("id", None)
        replaces_id = upload_entry.pop("replaces_id", None)
        stored_name = upload_entry.pop("file", "")
        upload_entry.pop("created_at", None)
        copies = upload_entry.pop("copies", [])

        upload = UploadedDocument(owner=user, **upload_entry)
        content = _extract(archive, stored_name)
        if content is None:
            report.skipped.append(f"File for “{upload.title}” was not in the archive")
        else:
            upload.file.save(stored_name.rsplit("/", 1)[-1], ContentFile(content), save=False)
        upload.save()
        _restore_copies(user, copies, upload=upload)
        uploads[old_id] = upload
        report.uploads += 1
        if replaces_id:
            pending_supersedes.append((upload, replaces_id))

    for upload, replaces_id in pending_supersedes:
        earlier = uploads.get(replaces_id)
        if earlier is not None:
            upload.replaces = earlier
            upload.save(update_fields=["replaces"])

    for sent_entry in documents.get("sent", []):
        sent_entry.pop("id", None)
        stored_name = sent_entry.pop("file", "")
        application = applications.get(sent_entry.pop("application_id", None))
        cv = cvs.get(sent_entry.pop("cv_id", None))
        letter = letters.get(sent_entry.pop("cover_letter_id", None))
        rendered_at = _dt(sent_entry.pop("rendered_at", None))
        copies = sent_entry.pop("copies", [])

        sent = RenderedDocument(
            owner=user,
            application=application,
            cv=cv,
            cover_letter=letter,
            **sent_entry,
        )
        if rendered_at:
            sent.rendered_at = rendered_at
        content = _extract(archive, stored_name)
        if content is None:
            report.skipped.append(f"File for “{sent.title}” was not in the archive")
        else:
            sent.file.save(stored_name.rsplit("/", 1)[-1], ContentFile(content), save=False)
        sent.save()
        _restore_copies(user, copies, rendered=sent)
        report.sent_documents += 1

    # ----------------------------------------------------------------- captures
    for capture_entry in document.get("captures", []):
        capture_entry.pop("id", None)
        capture_entry.pop("created_at", None)
        application = applications.get(capture_entry.pop("application_id", None))
        posting = postings.get(capture_entry.pop("posting_id", None))
        if posting is None and application is not None:
            posting = application.posting
        Capture.objects.create(
            owner=user, application=application, posting=posting, **capture_entry
        )

    return report


def _free_cv_name(user, name: str) -> str:
    """Return ``name``, or the first variation of it that is not taken."""
    from postulo.documents.models import CV

    taken = set(CV.objects.for_user(user).values_list("name", flat=True))
    if name not in taken:
        return name
    for suffix in range(2, 100):
        candidate = f"{name} ({suffix})"
        if candidate not in taken:
            return candidate
    return f"{name} ({len(taken)})"


def _extract(archive: zipfile.ZipFile, stored_name: str) -> bytes | None:
    if not stored_name or not stored_name.startswith(MEDIA_PREFIX):
        return None
    try:
        return archive.read(stored_name)
    except KeyError:
        return None
