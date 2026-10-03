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

from .export import CV_FIELDS, MANIFEST_NAME, MEDIA_PREFIX, TRANSLATION_SECTIONS


class ArchiveError(Exception):
    """The archive cannot be read, or must not be applied."""


@dataclass
class ImportReport:
    companies: int = 0
    postings: int = 0
    listing_events: int = 0
    applications: int = 0
    events: int = 0
    reminders: int = 0
    interviews: int = 0
    resume_items: int = 0
    cvs: int = 0
    cover_letters: int = 0
    uploads: int = 0
    sent_documents: int = 0
    captured_pages: int = 0
    remembered_places: int = 0
    tags: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        return [
            f"{value} {name.replace('_', ' ')}"
            for name, value in vars(self).items()
            if isinstance(value, int) and value
        ]


def _kept_as_other(key: str, value: str, subject: str, whose: str = "") -> str:
    """The report's line for an identifier that was restored as *Other* (#311).

    `!r`, and cut short: both are whatever the file says, and this line is printed.
    """
    from . import identifiers

    why = (
        "this instance has no such kind"
        if identifiers.find(key, subject) is None
        else "its kind does not accept that value on this instance"
    )
    return (
        f"{whose + ': ' if whose else ''}Identifier {key[:40]!r} {value[:60]!r}: {why}, "
        "so it is kept as Other, named by its key"
    )


def _source_of(entry: dict, cvs: dict, letters: dict):
    """What made a sent document, in whichever shape the archive has it.

    Format 10 writes ``source_kind`` and ``source_ref``; every earlier one wrote one of two
    id columns, and an archive made last month is still an archive (#130). Both keys are
    removed from ``entry``, because what remains goes straight to a model that has neither.

    Returns ``None`` where the archive names nothing, or names something this archive did
    not carry — a render with no source is an ordinary state rather than a gap.
    """
    cv = cvs.get(entry.pop("cv_id", None))
    letter = letters.get(entry.pop("cover_letter_id", None))
    kind = (entry.pop("source_kind", "") or "").lower()
    ref = entry.pop("source_ref", None)
    if kind == "cv":
        return cvs.get(ref) or cv
    if kind == "coverletter":
        return letters.get(ref) or letter
    return cv or letter


def _restore_copies(user, entries: list, document) -> None:
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
            document=document,
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


def _wanted_username(account: dict, user) -> tuple[str, str]:
    """The username the archive asks for, held to the rules signing up holds one to.

    Two things come back: the name to take if nobody has it, and -- where the archive asks
    for one this instance does not accept -- a line for the report saying so. Both are
    empty where it asks for none, or for the one this account already has: an account's own
    name is not judged again, whatever it is.

    The check is allauth's own `clean_username`, the one the sign-up and account pages call,
    so there is one set of rules (#321): an archive is a file anybody can edit, and the
    operator running the command that reads it is no reason to let through a name every
    page turns away. But the name is the one thing in an archive the account can do without,
    so a name that fails is left alone, as a taken one always was, and the rest is restored.
    ``createsuperuser`` never sees the list of reserved names, so an ``admin`` is in
    archives Postulo wrote itself, and refusing those whole would cost a person their backup
    over the one field they can retype.

    Refused outright only where there is no name to keep: an account that has none of its
    own cannot be left with the archive's, and is not left without one silently.
    """
    from allauth.account.adapter import get_adapter

    wanted = str(account.get("username") or "").strip().casefold()
    if not wanted or wanted == user.username:
        return "", ""
    try:
        get_adapter().clean_username(wanted, shallow=True)
    except ValidationError as error:
        reason = " ".join(str(message) for message in error.messages)
        # `!r`, and cut short: the name is whatever the file says, line breaks included, and
        # this line is printed.
        if not user.username:
            raise ArchiveError(
                f"The archive asks for the username {wanted[:60]!r}, which this instance does "
                f"not accept: {reason} This account has no username of its own to keep "
                f"instead. Change the name in {MANIFEST_NAME}, or remove it."
            ) from error
        return "", f"The username {wanted[:60]!r}: {reason} The account keeps “{user.username}”."
    return wanted, ""


#: What the list called a language before format 28, where it calls it something else now.
#: Serbian said nothing about its script until the list said which one the catalogue is in.
RENAMED_SINCE_27 = {"sr": "sr-Cyrl"}


def _languages_as_written(document: dict, report: ImportReport) -> None:
    """Every language code in the archive, written the way Postulo writes one (#337).

    An archive written before format 28 says `pt-br`, and one edited by hand says anything.
    The columns put a code in its canonical form by themselves; what they cannot know is
    which archive a bare `sr` came from, and that is settled here: from an older one it is
    the Serbian the list offered, which is `sr-Cyrl` now. From a newer one it is what
    somebody declared, and stays.

    What is not shaped like a language at all is left blank and named in the report, as
    a username this instance does not accept is: the rest of the archive is restored.
    """
    from . import languages

    try:
        older = int((document.get("postulo") or {}).get("format")) < 28
    except (TypeError, ValueError):
        older = True

    def written(holder, key: str, what: str) -> None:
        value = holder.get(key) if isinstance(holder, dict) else None
        if not value:
            return
        if not languages.well_formed(value):
            holder[key] = ""
            # `!r`, and cut short: it is whatever the file says, and this line is printed.
            report.skipped.append(
                f"{what}: {str(value)[:40]!r} is not a language code, and was left blank"
            )
            return
        code = languages.tag(value)
        holder[key] = RENAMED_SINCE_27.get(code, code) if older else code

    profile = (document.get("account") or {}).get("profile")
    written(profile, "language", "The language you read Postulo in")
    written(profile, "record_language", "The language of your career record")
    for row in (document.get("resume") or {}).get("translations") or []:
        section = row.get("section") if isinstance(row, dict) else ""
        written(row, "language", f"The language of a translation of {str(section)[:40]}")
    documents = document.get("documents") or {}
    for kind in ("cvs", "cover_letters", "uploads", "sent"):
        for entry in documents.get(kind) or []:
            name = (
                (entry.get("name") or entry.get("title") or "") if isinstance(entry, dict) else ""
            )
            written(entry, "language", f"The language of “{str(name)[:60]}”")


def account_is_empty(user) -> bool:
    from postulo.applications.models import Application
    from postulo.documents.models import CV
    from postulo.jobs.models import Company
    from postulo.resume.models import Experience

    return not any(
        model.objects.for_user(user).exists() for model in (Application, Company, CV, Experience)
    )


#: What an address row must have something in to be worth restoring at all.
ADDRESS_PARTS = ("street", "postcode", "municipality", "region", "country")


def _phone_rows(entry: dict) -> list[dict]:
    """Take the telephone numbers out of a record, in whichever shape the file has them.

    Format 4 writes a ``phone_numbers`` list. Everything before it wrote one ``phone``
    string, and an archive made last month is still an archive: that string becomes the
    single primary number it always was. Both keys are removed from ``entry``, because
    what remains is passed straight to a model that no longer has a ``phone`` field.
    """
    rows = entry.pop("phone_numbers", None) or []
    legacy = (entry.pop("phone", "") or "").strip()
    if not rows and legacy:
        rows = [{"number": legacy, "is_primary": True}]
    return [row for row in rows if (row.get("number") or "").strip()]


def _address_rows(entry: dict) -> list[dict]:
    """Take the postal addresses out of a record. Format 9 is the first to carry any.

    Removed from ``entry`` because what remains goes straight to a model that has no such
    field. An archive from before format 9 simply has none, which is not a loss: there was
    nowhere to keep one (#92).
    """
    rows = entry.pop("postal_addresses", None) or []
    return [row for row in rows if any((row.get(part) or "").strip() for part in ADDRESS_PARTS)]


#: The single columns an archive before format 15 wrote, and the kind each becomes. A
#: contact only ever had the LinkedIn one; popping the others off it costs nothing.
LEGACY_LINK_COLUMNS = (
    ("website", "website"),
    ("linkedin_url", "social"),
    ("source_repo_url", "repository"),
)


def _link_rows(entry: dict) -> list[dict]:
    """Take the web links out of a record, in whichever shape the file has them.

    Format 15 writes a ``web_links`` list. Everything before it wrote a column per kind --
    ``website``, ``linkedin_url``, ``source_repo_url`` -- and each of those becomes the
    single primary link of its kind it always was (#189). Every key is removed from
    ``entry``, because what remains is passed straight to a model that has none of them.
    """
    from postulo.core.web_links import KINDS

    rows = entry.pop("web_links", None) or []
    for column, kind in LEGACY_LINK_COLUMNS:
        legacy = (entry.pop(column, "") or "").strip()
        if legacy and not any(row.get("kind") == kind for row in rows):
            rows.append({"kind": kind, "url": legacy, "is_primary": True})
    return [row for row in rows if (row.get("url") or "").strip() and row.get("kind") in KINDS]


def _restore_web_links(holder, owner, rows: list[dict]) -> None:
    """Recreate a holder's links.

    Unique per holder and kind, so nothing here can collide with anybody else's; an address
    repeated under one kind within one import is dropped, and the first row of each kind to
    claim the primary keeps it. The same address under two kinds is two links (#457).

    **The service** (#305). Format 29 writes one on every link, blank for *Other*, and it is
    kept where this instance knows it and the address is one of its addresses; a key it
    does not know -- a plugin the other instance had -- restores as *Other*, which loses
    nothing but the word. An archive from before then says nothing, and each link is read
    as an address pasted with no service chosen: the host says which, where it says
    anything. No row is refused either way (`web_links.chosen_in_a_file`).
    """
    from postulo.core import link_services, web_links
    from postulo.core.models import WebLink

    primary_taken: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        url = (row.get("url") or "").strip()[:500]
        kind = row.get("kind")
        if (kind, url) in seen:
            continue
        seen.add((kind, url))
        is_primary = bool(row.get("is_primary")) and kind not in primary_taken
        if is_primary:
            primary_taken.add(kind)
        chosen = web_links.chosen_in_a_file(row, kind, url)
        service, label = link_services.settle(kind, chosen, url, str(row.get("label") or "")[:60])
        WebLink.objects.create(
            owner=owner,
            holder=holder,
            kind=kind,
            service=service,
            label=label,
            url=url,
            is_primary=is_primary,
        )


def _restore_postal_addresses(holder, owner, rows: list[dict]) -> None:
    """Recreate a holder's addresses.

    No skipping, and that is the difference from the numbers above. Addresses are unique
    per owner rather than across the instance, so an archive can never collide with
    somebody else's -- two people at one address is a household. Within one import a
    repeated address is dropped, because listing the same one twice is the mistake the
    constraint exists to catch.
    """
    from postulo.core.models import PostalAddress

    primary_taken = False
    seen: set[str] = set()
    for row in rows:
        address = PostalAddress(
            owner=owner,
            holder=holder,
            kind=(row.get("kind") or ""),
            label=(row.get("label") or ""),
            street=(row.get("street") or ""),
            postcode=(row.get("postcode") or ""),
            municipality=(row.get("municipality") or ""),
            region=(row.get("region") or ""),
            country=(row.get("country") or ""),
            is_primary=bool(row.get("is_primary")) and not primary_taken,
        )
        comparable = address.comparable_form()
        if comparable in seen:
            continue
        seen.add(comparable)
        if address.is_primary:
            primary_taken = True
        address.save()


def _restore_phone_numbers(holder, owner, rows: list[dict]) -> None:
    """Recreate a holder's numbers, skipping any this instance already has.

    Numbers are unique across the instance, so an archive carrying one somebody here
    already holds cannot be imported as it stands. Skipping that row is the only answer
    that neither fails the whole import nor takes a number away from whoever already had
    it, and the rest of the archive lands intact.
    """
    from postulo.core.models import PhoneNumber
    from postulo.core.phone_numbers import taken_elsewhere

    primary_taken = False
    for row in rows:
        number = (row.get("number") or "").strip()
        if taken_elsewhere(number):
            continue
        wants_primary = bool(row.get("is_primary")) and not primary_taken
        # `verified_at` is read from the file and thrown away, deliberately and visibly.
        # An archive is a claim, and a claim of verification made by another instance is a
        # claim this one never checked; landing it verified would import a way back into an
        # account from a file (#142). Nothing here sets it, and this comment is why.
        row.pop("verified_at", None)
        # And with it the recovery flag, which means nothing without the verification it
        # rests on: importing one would give an archive the power to nominate a way into an
        # account on an instance that never confirmed the number (#144).
        row.pop("is_recovery", None)
        PhoneNumber.objects.create(
            owner=owner,
            holder=holder,
            number=number,
            kind=(row.get("kind") or ""),
            label=(row.get("label") or ""),
            is_primary=wants_primary,
        )
        primary_taken = primary_taken or wants_primary


@transaction.atomic
def load(user, archive: zipfile.ZipFile, *, force: bool = False) -> ImportReport:
    """Create everything in ``archive`` under ``user``.

    Runs in one transaction: a failure half way through leaves the account exactly as it
    was, rather than partly overwritten by a file that turned out to be broken.
    """
    from postulo.accounts.models import PersonIdentifier
    from postulo.applications.models import (
        Application,
        ApplicationEvent,
        Interview,
        Offer,
        Reminder,
    )
    from postulo.core import identifiers as scheme_registry
    from postulo.core.models import Tag, TagIcon, nearest_tone
    from postulo.documents import printing
    from postulo.documents.models import (
        CV,
        CoverLetter,
        CVItem,
        CVKind,
        RenderedDocument,
        UploadedDocument,
    )
    from postulo.jobs import identifiers
    from postulo.jobs.models import (
        Capture,
        Company,
        CompanyKind,
        Contact,
        Department,
        Industry,
        JobPosting,
    )
    from postulo.jobs.services import set_identifiers
    from postulo.resume import models as resume

    document = read_manifest(archive)
    if not force and not account_is_empty(user):
        raise ArchiveError(
            "This account already holds a job search. Importing would add a second copy "
            "of everything rather than merging the two. Use an empty account, or pass "
            "--force if a duplicate is genuinely what you want."
        )

    account = document.get("account") or {}
    # Asked before anything is written, so a refusal leaves nothing to roll back.
    wanted, not_taken = _wanted_username(account, user)

    report = ImportReport()
    if not_taken:
        report.skipped.append(not_taken)
    _languages_as_written(document, report)
    from django.contrib.contenttypes.models import ContentType

    # ------------------------------------------------------------------ profile
    if account.get("first_name") or account.get("last_name"):
        user.first_name = account.get("first_name") or user.first_name
        user.last_name = account.get("last_name") or user.last_name
        user.save(update_fields=["first_name", "last_name"])
    # The username travels too, but the account importing already has one, and taking
    # somebody else's on this instance is out of the question: keep it when it is free.
    if wanted:
        taken = type(user)._default_manager.exclude(pk=user.pk).filter(username=wanted)
        if not taken.exists():
            user.username = wanted
            user.save(update_fields=["username"])
    profile_data = dict(account.get("profile") or {})
    numbers = _phone_rows(profile_data)
    addresses = _address_rows(profile_data)
    links = _link_rows(profile_data)
    profile = getattr(user, "profile", None)
    if profile and profile_data:
        for name, value in profile_data.items():
            if hasattr(profile, name) and value:
                setattr(profile, name, value)
        profile.save()
    if profile:
        _restore_phone_numbers(profile, user, numbers)
        _restore_postal_addresses(profile, user, addresses)
        _restore_web_links(profile, user, links)
    for row in account.get("identifiers") or []:
        scheme = (row.get("scheme") or "").strip()
        value = (row.get("value") or "").strip()
        if not profile or not scheme or not value:
            continue
        # get_or_create rather than create: importing an archive twice should not raise
        # on the one-per-scheme constraint. A scheme the other instance defined for itself
        # and this one does not have is restored as *Other*, named by its key, and so is a
        # value the scheme here refuses: nothing is dropped, nothing is stored under a
        # scheme it does not fit, and the report says which (#311).
        named = scheme
        scheme, label = scheme_registry.as_restored(
            named, row.get("label") or "", scheme_registry.PERSON, value=value
        )
        if scheme != named:
            report.skipped.append(_kept_as_other(named, value, scheme_registry.PERSON))
        else:
            # As typing it here would have written it: the scheme's own spelling.
            known = scheme_registry.find(scheme, scheme_registry.PERSON)
            value = known.normalise(value) if known is not None else value
        found = {"profile": profile, "scheme": scheme, "value": value}
        if scheme == identifiers.OTHER:
            # *Other* is the one scheme somebody may hold several of, and two of them can
            # share a value under two names. Found by scheme and value alone, the second
            # was taken for the first and never made -- and a CV that had chosen it then
            # printed the other one (#308).
            found["label"] = label
        PersonIdentifier.objects.get_or_create(**found, defaults={"label": label})

    avatar_file = account.get("avatar_file") or ""
    if profile and avatar_file:
        content = _extract(archive, avatar_file)
        if content is not None:
            profile.avatar.save(avatar_file.rsplit("/", 1)[-1], ContentFile(content), save=True)

    # --------------------------------------------------------------------- tags
    # An archive written before #285 carries whatever its owner typed into a free-text
    # colour box, and one written by a later Postulo than this may carry an icon this one
    # has never heard of. Both are read for what they can mean and neither is refused: an
    # import that rejects a whole account over the shade of a label would be absurd.
    known_icons = {choice.value for choice in TagIcon}
    tags_by_slug: dict[str, Tag] = {}
    for entry in document.get("tags", []):
        tag, created = Tag.objects.get_or_create(
            owner=user,
            slug=entry.get("slug") or entry.get("name", "").lower(),
            defaults={
                "name": entry.get("name", ""),
                "colour": nearest_tone(entry.get("colour", "")),
                "icon": entry.get("icon", "") if entry.get("icon", "") in known_icons else "",
            },
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
        "links": resume.Link,
    }
    date_fields = {"start_date", "end_date", "issued_on", "expires_on"}
    moment_fields = {"checked_at"}

    for key, model in section_models.items():
        resume_map[key] = {}
        for entry in document.get("resume", {}).get(key, []):
            old_id = entry.pop("id", None)
            values = {}
            for name, value in entry.items():
                if name in date_fields:
                    values[name] = _d(value)
                elif name in moment_fields:
                    values[name] = _dt(value)
                else:
                    values[name] = value
            created = model.objects.create(owner=user, **values)
            resume_map[key][old_id] = created
            report.resume_items += 1

    # Skills come after their groups, so the group reference can be resolved.
    resume_map["skills"] = {}
    for entry in document.get("resume", {}).get("skills", []):
        old_id = entry.pop("id", None)
        group_id = entry.pop("group_id", None)
        # The ESCO skill follows the name and is worked out again from it when the skill is
        # saved, in this instance's classification; what the file says it was is not asked
        # (format 24, #266). Taken out rather than handed over and overwritten, so that
        # nothing a file says ever reaches the column by way of the constructor.
        entry.pop("esco_uri", None)
        group = resume_map["skill_groups"].get(group_id)
        created = resume.Skill.objects.create(owner=user, group=group, **entry)
        resume_map["skills"][old_id] = created
        report.resume_items += 1

    # And what those entries say in other languages, once every entry has a new identity to
    # point at. Absent from an archive written before format 11, which is not a problem: a
    # career with no translations is what every career was until then (#131).
    for row in document.get("resume", {}).get("translations", []):
        target = resume_map.get(row.get("section", ""), {}).get(row.get("ref"))
        text = (row.get("text") or "").strip()
        # A translation into nothing is one whose language the archive did not name, or
        # named as something that is not a language; that was said in the report above.
        if target is None or not text or not row.get("language"):
            if target is None:
                report.skipped.append(
                    f"Translation of {row.get('section')}#{row.get('ref')}: no such record"
                )
            continue
        resume.Translation.objects.update_or_create(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
            language=row.get("language", ""),
            field=row.get("field", ""),
            defaults={"text": text, "owner": user},
        )

    # ------------------------------------------------ companies and their work
    contacts: dict[int, Contact] = {}
    postings: dict[int, JobPosting] = {}
    applications: dict[int, Application] = {}

    #: Company primary key -> the name of the company it should belong to. Filled while
    #: reading and applied afterwards, because a parent may appear later in the file than
    #: its child does.
    wants_parent: dict[int, str] = {}

    #: The department each application named, as (company name, department name).
    #: Applied after the tree, because a department at a parent company is only
    #: findable once that company and its departments exist (#138).
    wants_department: dict[int, tuple[str, str]] = {}

    #: Who referred the person, as the contact's id in the file, and the agency it went
    #: through, by name. Both applied once every company and every contact in the file
    #: exists: a referrer is as often at another company as at this one, and an agency is
    #: another company by definition, so either may come later in the file than the
    #: application that names it (#239).
    wants_referrer: dict[int, int] = {}
    wants_agency: dict[int, str] = {}

    #: Each listing's history, as the file wrote it, held until the end: an entry may point
    #: at a capture or an upload, and those are made last (#270).
    listing_histories: list[tuple[JobPosting, list]] = []

    def restore_contact(entry: dict, company) -> None:
        """One person from the file, at ``company`` or at none."""
        old_id = entry.pop("id", None)
        numbers = _phone_rows(entry)
        contact_addresses = _address_rows(entry)
        contact_links = _link_rows(entry)
        department_name = (entry.pop("department", "") or "").strip()[:120]
        contact = Contact.objects.create(owner=user, company=company, **entry)
        if department_name and company is not None:
            department, _made = Department.objects.get_or_create(
                owner=user, company=company, name=department_name
            )
            contact.department = department
            contact.save(update_fields=["department"])
        _restore_phone_numbers(contact, user, numbers)
        _restore_postal_addresses(contact, user, contact_addresses)
        _restore_web_links(contact, user, contact_links)
        contacts[old_id] = contact

    # The people recorded at no company, which format 20 is the first to carry (#239).
    for contact_entry in document.get("contacts", []):
        restore_contact(contact_entry, None)

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
        # Format 13 writes the teams as records of their own. Before it they travelled
        # only as a name beside a contact, so a team nobody had been recorded at was
        # silently dropped -- which is exactly the ordinary case (#138).
        department_names = [
            str(name).strip()[:120]
            for name in (company_entry.pop("departments", None) or [])
            if str(name).strip()
        ]
        logo_name = company_entry.pop("logo_file", "")
        # Resolved after every company in the file exists: a parent may be named before it
        # has been read, and an archive written before format 5 names none at all.
        parent_name = (company_entry.pop("parent", "") or "").strip()
        company_entry["logo_fetched_at"] = _dt(company_entry.get("logo_fetched_at"))

        # A company is an identity keyed by its name, which is why intake matches on
        # it too. Importing attaches to one that already exists rather than colliding
        # with the per-owner unique name — the postings and applications underneath are
        # what actually get duplicated when an import is forced. An identifier both
        # sides carry is a stronger match than the spelling.
        name = company_entry.pop("name", "")
        # Format 16 writes the kind. An archive without it, or with a kind this Postulo
        # does not know, is employers all the way down, which is what it was (#202).
        if company_entry.get("kind") not in CompanyKind.values:
            company_entry.pop("kind", None)
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
            logo = _extract(archive, logo_name) if logo_name else None
            if logo is not None:
                company.logo.save(logo_name.rsplit("/", 1)[-1], ContentFile(logo), save=True)
        if parent_name:
            wants_parent[company.pk] = parent_name
        if industry_names:
            company.industries.add(*Industry.named(user, industry_names))
        for department_name in department_names:
            Department.objects.get_or_create(owner=user, company=company, name=department_name)
        for entry in identifier_entries:
            # A scheme only the other instance defined comes back as *Other*, named by
            # its key, as a person's does, and so does a value the scheme here refuses
            # (#311). It used to be dropped, with nothing said.
            named, value = entry.get("scheme", ""), entry.get("value", "")
            if not named or not value:
                # Nothing to restore, as for a person's: an entry with no kind or no value.
                continue
            scheme, label = scheme_registry.as_restored(
                named, entry.get("label", ""), scheme_registry.COMPANY, value=value
            )
            if scheme != named:
                report.skipped.append(
                    _kept_as_other(named, value, scheme_registry.COMPANY, company.name)
                )
            try:
                set_identifiers(company, [(scheme, value, label)])
            except ValidationError as refused:
                # Already on another company here, or with no name to go by: the record
                # is still worth having, the id is not worth refusing it over. Said, all
                # the same: what a file held and the account does not is never silent.
                report.skipped.append(
                    f"{company.name}: identifier {str(named)[:40]!r} {str(value)[:60]!r} "
                    f"was left out: {' '.join(refused.messages)[:200]}"
                )
                continue

        for contact_entry in contact_entries:
            restore_contact(contact_entry, company)

        for posting_entry in posting_entries:
            application_entries = posting_entry.pop("applications", [])
            # Format 23 (#270). Taken out before the row is made, as every nested list is;
            # an archive written before it has no key and a listing with no history.
            history_entries = posting_entry.pop("events", None) or []
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
            if isinstance(history_entries, list) and history_entries:
                listing_histories.append((posting, history_entries))

            for application_entry in application_entries:
                event_entries = application_entry.pop("events", [])
                reminder_entries = application_entry.pop("reminders", [])
                interview_entries = application_entry.pop("interviews", [])
                offer_entries = application_entry.pop("offers", [])
                tag_slugs = application_entry.pop("tags", [])
                sent_link_ids = application_entry.pop("sent_link_ids", [])
                old_id = application_entry.pop("id", None)
                application_entry.pop("created_at", None)
                contact_id = application_entry.pop("contact_id", None)
                # The department may be at another company in the same group, which may
                # not exist yet. Held and resolved after the ownership tree, below (#138).
                wants_department[old_id] = (
                    (application_entry.pop("department_company", "") or "").strip(),
                    (application_entry.pop("department", "") or "").strip(),
                )
                # Format 20 (#239). An archive from before it names neither, and both
                # keys are taken out either way: what is left goes straight to the model.
                referrer_id = application_entry.pop("referred_by_id", None)
                if referrer_id is not None:
                    wants_referrer[old_id] = referrer_id
                agency_name = (application_entry.pop("through_agency", "") or "").strip()
                if agency_name:
                    wants_agency[old_id] = agency_name

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
                if sent_link_ids:
                    application.sent_links.set(
                        [
                            resume_map["links"][link_id]
                            for link_id in sent_link_ids
                            if link_id in resume_map.get("links", {})
                        ]
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
                for offer_entry in offer_entries:
                    offer_entry.pop("id", None)
                    offer_entry.pop("created_at", None)
                    reminder_id = offer_entry.pop("reminder_id", None)
                    Offer.objects.create(
                        owner=user,
                        application=application,
                        reminder=reminders.get(reminder_id),
                        starts_on=_d(offer_entry.pop("starts_on", None)),
                        answer_by=_d(offer_entry.pop("answer_by", None)),
                        **offer_entry,
                    )

    # The ownership tree, once every company in the file has been made or matched. A name
    # that matches nothing is left alone rather than guessed at, and a company is never made
    # its own parent — an archive can say anything, and `Company.clean` is not run here.
    if wants_parent:
        by_name = {
            company.name.casefold(): company
            for company in Company.objects.for_user(user).filter(
                name__in=set(wants_parent.values())
            )
        }
        for child_pk, name in wants_parent.items():
            parent = by_name.get(name.casefold())
            if parent is not None and parent.pk != child_pk:
                Company.objects.filter(pk=child_pk).update(parent=parent)

    # And then which part of an employer each application was aimed at, once both the
    # departments and the tree they hang off exist. A department the archive names but the
    # file does not hold is left unattached rather than invented: the application keeps its
    # company, which is the employer of record and was never in doubt (#138).
    for old_application_id, (company_name, department_name) in wants_department.items():
        application = applications.get(old_application_id)
        if application is None or not department_name or not company_name:
            continue
        department = (
            Department.objects.for_user(user)
            .filter(company__name__iexact=company_name, name__iexact=department_name)
            .first()
        )
        if department is not None:
            Application.objects.filter(pk=application.pk).update(department=department)

    # Who referred the person and the agency it went through, now that everybody and every
    # company in the file exists. One the file names and does not hold is left empty
    # rather than guessed at, as a department is (#239).
    for old_application_id, old_contact_id in wants_referrer.items():
        application = applications.get(old_application_id)
        referrer = contacts.get(old_contact_id)
        if application is not None and referrer is not None:
            Application.objects.filter(pk=application.pk).update(referred_by=referrer)
    if wants_agency:
        agencies = {
            company.name.casefold(): company
            for company in Company.objects.for_user(user).filter(
                name__in=set(wants_agency.values())
            )
        }
        for old_application_id, name in wants_agency.items():
            application = applications.get(old_application_id)
            agency = agencies.get(name.casefold())
            if application is not None and agency is not None:
                Application.objects.filter(pk=application.pk).update(through_agency=agency)

    # ---------------------------------------------------------------- documents
    documents = document.get("documents", {})

    cvs: dict[int, CV] = {}
    for cv_entry in documents.get("cvs", []):
        entries = cv_entry.pop("entries", [])
        old_id = cv_entry.pop("id", None)
        # A CV is content rather than an identity, so a clash gets a new name instead of
        # being merged into whatever happens to share its title.
        cv_entry["name"] = _free_cv_name(user, cv_entry.get("name", ""))
        # An archive can say anything, and one written before format 14 says nothing at all.
        # A kind Postulo does not know restores as a CV, which is what every archive before
        # this held and the honest reading of an unknown one (#133).
        if cv_entry.get("kind") not in {value for value, _label in CVKind.choices}:
            cv_entry.pop("kind", None)
        # Which of the person's details the CV prints (#308). Taken out before
        # the row is made, and read below by what each row says.
        prints = cv_entry.pop("prints", None)
        # **Only what an archive is defined to carry reaches the model.** Everything left
        # in the entry used to be handed to `create` as it stood, and a CV now has columns
        # that point at rows: a file naming one by id -- `pinned_phone_id` -- would have
        # had somebody else's row stored on this person's CV. A choice travels in
        # `prints`, by content, and is looked up among the owner's own rows; a column
        # named any other way is left out and said to have been.
        not_carried = sorted(set(cv_entry) - set(CV_FIELDS))
        for name in not_carried:
            cv_entry.pop(name)
        cv = CV.objects.create(owner=user, **cv_entry)
        cvs[old_id] = cv
        report.cvs += 1
        if not_carried:
            # `repr`, cut short, and no more than ten of them: a key's name is whatever the
            # file says, as long as the file likes and line breaks included, and the report
            # is printed for the operator to read -- as the username is (#321).
            named = ", ".join(repr(name[:40]) for name in not_carried[:10])
            if len(not_carried) > 10:
                named += f" and {len(not_carried) - 10} more"
            report.skipped.append(
                f"CV {cv.name}: {named}: not something an archive carries, and left out"
            )
        # A row the file names that this account does not have -- a number another account
        # here already holds, an address that is not one of this account's confirmed ones
        # -- leaves that kind at its default, which follows the profile. So does an answer
        # that cannot be read at all, and it is said in the same words: what a file holds
        # there is not something to stop an import over, and not something to pass over.
        for kind in printing.restore(cv, prints):
            if kind in printing.SWITCHES:
                report.skipped.append(
                    f"CV {cv.name}: whether it prints this ({kind}) is not said as a yes "
                    "or no, so it is left as a new CV has it"
                )
                continue
            report.skipped.append(
                f"CV {cv.name}: what it had chosen to print ({kind}) is not among your "
                "details here, so it follows your details instead"
            )

        for item in entries:
            section = TRANSLATION_SECTIONS.get(item.get("kind", ""))
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
        _restore_copies(user, copies, upload)
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
        source = _source_of(sent_entry, cvs, letters)
        rendered_at = _dt(sent_entry.pop("rendered_at", None))
        copies = sent_entry.pop("copies", [])

        sent = RenderedDocument(
            owner=user,
            application=application,
            source=source,
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
        _restore_copies(user, copies, sent)
        report.sent_documents += 1

    # ----------------------------------------------------------------- captures
    captures: dict[int, Capture] = {}
    for capture_entry in document.get("captures", []):
        old_capture_id = capture_entry.pop("id", None)
        capture_entry.pop("created_at", None)
        # Format 21 (#256). Taken out before the row is made, because what is left goes
        # straight to a model that has no such column; an archive written before it has
        # no key, and a capture that kept nothing has an empty one.
        kept = capture_entry.pop("page", None)
        application = applications.get(capture_entry.pop("application_id", None))
        posting = postings.get(capture_entry.pop("posting_id", None))
        if posting is None and application is not None:
            posting = application.posting
        capture = Capture.objects.create(
            owner=user, application=application, posting=posting, **capture_entry
        )
        captures[old_capture_id] = capture
        if isinstance(kept, dict):
            report.captured_pages += _restore_kept_page(archive, capture, kept, report)

    # ------------------------------------------------------ listings' histories
    # Last, because an entry may point at a capture or an upload and both now exist (#270).
    pointable = {"capture": captures, "uploadeddocument": uploads}
    for posting, entries in listing_histories:
        report.listing_events += _restore_listing_history(
            posting, entries, contacts=contacts, pointable=pointable
        )

    # ------------------------------------------------------ remembered places
    # Format 25 (#267); an archive written before it has none, which is where every account
    # started. Each row is checked rather than believed, exactly as a place read back from
    # the database is: a file somebody hands over can say anything.
    for row in document.get("remembered_places") or []:
        report.remembered_places += _restore_remembered_place(user, row, report)

    return report


def _restore_listing_history(posting, entries: list, *, contacts: dict, pointable: dict) -> int:
    """Put back one listing's history. Returns how many entries came back.

    Rows are made directly, as the timeline's are, and the file is believed about nothing
    it could get wrong: the words are held to the lengths `jobs.history` keeps, an unknown
    kind is *other*, a contact or a thing pointed at that the file does not carry is left
    out and the entry keeps its words -- which is what a deleted file leaves it with anyway
    -- and a pointer of the wrong kind is dropped rather than trusted. A message id repeated
    for one listing is one entry, as it would have been when it was bound.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone

    from postulo.jobs.history import (
        ACTOR_MAX_CHARS,
        EXTERNAL_ID_MAX_CHARS,
        SUMMARY_MAX_CHARS,
        bounded,
        one_line,
    )
    from postulo.jobs.models import ListingEvent, ListingEventKind

    def an_id(value) -> int | None:
        """An id as the file wrote it, or nothing: a list or a word is not a key to look up."""
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def a_moment(value):
        try:
            return _dt(value) if isinstance(value, str) else None
        except ValueError:
            return None

    made = 0
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if kind not in ListingEventKind.values:
            kind = ListingEventKind.OTHER
        # Looked up only by an id the entry actually gives: a record the file wrote with no
        # id of its own is kept under `None`, and an entry naming nothing must not find it.
        artefact_ref = an_id(entry.get("artefact_ref"))
        artefact = (
            pointable.get(str(entry.get("artefact_kind") or ""), {}).get(artefact_ref)
            if artefact_ref is not None
            else None
        )
        contact_ref = an_id(entry.get("contact_id"))
        is_capture = artefact is not None and artefact._meta.label_lower == "jobs.capture"
        if artefact is not None and is_capture != (kind == ListingEventKind.CAPTURE):
            artefact = None
        external_id = one_line(entry.get("external_id"))[:EXTERNAL_ID_MAX_CHARS]
        if external_id:
            if external_id in seen:
                continue
            seen.add(external_id)
        ListingEvent.objects.create(
            posting=posting,
            kind=kind,
            occurred_at=a_moment(entry.get("occurred_at")) or timezone.now(),
            summary=one_line(entry.get("summary"))[:SUMMARY_MAX_CHARS],
            body=bounded(entry.get("body")),
            contact=contacts.get(contact_ref) if contact_ref is not None else None,
            external_id=external_id,
            actor=one_line(entry.get("actor"))[:ACTOR_MAX_CHARS],
            artefact_type=(
                ContentType.objects.get_for_model(artefact) if artefact is not None else None
            ),
            artefact_id=artefact.pk if artefact is not None else None,
        )
        made += 1
    return made


def _restore_remembered_place(user, row, report) -> int:
    """Put back one remembered place, if it is one. One if it came back, else nought."""
    from postulo.jobs.models import FieldHint, HintField
    from postulo.jobs.remembered import host_of
    from postulo.plugins.registry import clean_place

    if not isinstance(row, dict):
        report.skipped.append("A remembered place: not one")
        return 0
    raw_host = row.get("host")
    host = host_of(f"https://{raw_host}/") if isinstance(raw_host, str) else ""
    field = row.get("field")
    place = clean_place(row.get("place"))
    misses = row.get("misses")
    misses = misses if isinstance(misses, int) and not isinstance(misses, bool) else 0
    if not host or host != raw_host or field not in HintField.values or place is None:
        report.skipped.append(f"A remembered place for {str(raw_host)[:60]!r}: not one")
        return 0
    # One place per site and field. An account that already has one -- an import forced
    # into an account in use -- keeps its own: an import creates and never merges.
    _hint, created = FieldHint.objects.get_or_create(
        owner=user,
        host=host,
        field=field,
        defaults={
            "place": place,
            "misses": min(max(misses, 0), FieldHint.DROPPED_AFTER - 1),
        },
    )
    return int(created)


def _restore_kept_page(archive: zipfile.ZipFile, capture, kept: dict, report) -> int:
    """Put back what a capture kept of its page. One if anything came back, else nought.

    Nothing the manifest says about the files is believed: `jobs.pages.restore` measures
    and checks each one again, and names it afresh under the account importing it. What
    the manifest is read for is where in the archive to look, and what kind of thing the
    rendering is supposed to be.
    """
    from postulo.core import site
    from postulo.jobs import pages

    # Packed text is smaller than the text, except where it will not pack at all, and then
    # it is larger by a few bytes a block: the cap and a little over covers both.
    source = _extract_within(
        archive, str(kept.get("source_file") or ""), site.capture_source_max_bytes() + 65_536
    )
    rendering = _extract_within(
        archive, str(kept.get("rendering_file") or ""), site.capture_rendering_max_bytes()
    )
    if source is None and rendering is None:
        return 0
    page, left_out = pages.restore(
        capture,
        source=source,
        rendering=rendering,
        rendering_type=str(kept.get("rendering_type") or ""),
        rendered_by=str(kept.get("rendered_by") or ""),
    )
    report.skipped.extend(f"Capture “{capture}”: {line}" for line in left_out)
    return 1 if page is not None else 0


def _extract_within(archive: zipfile.ZipFile, stored_name: str, limit: int) -> bytes | None:
    """A file from the archive, unless it would unpack to more than ``limit`` bytes.

    Asked of the archive's own directory before anything is unpacked: a zip entry says how
    large it will be, and one that says more than the cap is not read to find out.
    """
    if not stored_name or not stored_name.startswith(MEDIA_PREFIX):
        return None
    try:
        described = archive.getinfo(stored_name)
    except KeyError:
        return None
    if described.file_size > limit:
        return None
    with archive.open(described) as handle:
        # One byte past the limit, so a directory that understated a file is caught here
        # rather than believed.
        content = handle.read(limit + 1)
    return None if len(content) > limit else content


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
