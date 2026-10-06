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

import contextvars
import json
import logging
import re
import zipfile
import zlib
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_date, parse_datetime

from . import language_names, personal, phones, slugs
from .export import (
    APPLICATION_FIELDS,
    CAPTURE_FIELDS,
    COMPANY_FIELDS,
    CONTACT_FIELDS,
    CV_FIELDS,
    EVENT_FIELDS,
    FORMAT_VERSION,
    INTERVIEW_FIELDS,
    LETTER_FIELDS,
    LINKED_TO_A_COMPANY,
    MANIFEST_NAME,
    MEDIA_PREFIX,
    OFFER_FIELDS,
    POSTING_FIELDS,
    PROFILE_FIELDS,
    RESUME_FIELDS,
    RESUME_MODELS,
    SENT_FIELDS,
    TRANSLATION_SECTIONS,
    UPLOAD_FIELDS,
)

# The manifest is text and every row of the person's data is in it: generous, but a cap.
MANIFEST_MAX_BYTES = 256 * 1024 * 1024


#: The career columns that hold a date, which an archive writes as text.
DATE_FIELDS = {
    "start_date",
    "end_date",
    "issued_on",
    "expires_on",
    "first_issued_on",
    "awarded_on",
}


def resume_section_models() -> dict:
    """Each career block of an archive and the model it restores into.

    Derived from `export.RESUME_MODELS`, which the archive is written from, so a section
    added there is read back here; skills are left out because they are restored inside
    their groups. `tests/test_career_sections.py` holds this against the registry.
    """
    from postulo.resume import models as resume

    return {
        block: getattr(resume, name) for block, name in RESUME_MODELS.items() if block != "skills"
    }


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


#: What `_profile_value` returns for a value the field would not take.
_REFUSED = object()

#: The preferences added to the file in format 30, which are the ones checked against their
#: field. The older keys are set as they always were, apart from the blanks and nulls.
_CHECKED_PROFILE_FIELDS = frozenset(
    {
        "keyboard_shortcuts",
        "show_key_hints",
        "nav_underline",
        "density",
        "keep_page_source",
        "keep_page_rendering",
        "closing_notice_days",
        "save_as_you_go",
        # The personal details (#679, #680): each held to its column's own rule, the one the
        # form, the API and the candidate file are held to, and a value it refuses is left
        # out and said in the report (`_PERSONAL_FIELDS`).
        "birth_date",
        "birth_place",
        "birth_country",
        "nationality_scope",
        "gender",
    }
)
#: The checked fields a refusal of which the report says: they are the person's own facts,
#: and an archive that carried one the page would not take has lost something to say so.
_PERSONAL_FIELDS = frozenset(
    {
        "birth_date",
        "birth_place",
        "birth_country",
        "nationalities",
        "nationality_scope",
        "gender",
    }
)


def _profile_value(profile, name: str, value):
    """What to set on the profile for one key of the file, or `_REFUSED`.

    A key the file names is restored whatever its value: a switch somebody turned off is
    `False`, a dashboard somebody cleared is `[]`, and passing over either hands back the
    default they changed (#464). An archive is a claim, so each value is checked against
    the field before it is believed -- a choice for `density`, the range for a number, the
    plugins this instance has for `plugins_off`. A blank text is "nobody said", and is
    left to the default unless the field is allowed to be blank.
    """
    column = type(profile)._meta.get_field(name)
    if value is None:
        return _REFUSED
    if isinstance(value, str) and not value and not column.blank:
        return _REFUSED
    if name == "nationalities":
        # One function holds a list of nationalities to the rule at every door (#680).
        if not isinstance(value, list):
            return _REFUSED
        try:
            return personal.clean_nationalities(value)
        except ValidationError:
            return _REFUSED
    if name in {"identifier_order", "hidden_identifiers"}:
        from postulo.core import identifier_order

        return identifier_order.known_keys(value)
    if name == "plugins_off":
        if not isinstance(value, list):
            return _REFUSED
        from postulo.plugins.policy import GOVERNED_KINDS
        from postulo.plugins.registry import plugins

        installed = {plugin.name for kind in GOVERNED_KINDS for plugin in plugins(kind)}
        return sorted({item for item in value if isinstance(item, str) and item in installed})
    if name in _CHECKED_PROFILE_FIELDS:
        wanted = (
            bool
            if column.get_internal_type() == "BooleanField"
            else (str if column.get_internal_type() == "CharField" else int)
        )
        if type(value) is not wanted:
            return _REFUSED
        if isinstance(value, str) and "\x00" in value:
            # What no text column may hold, and what a form refuses (#679).
            return _REFUSED
        try:
            return column.clean(value, profile)
        except ValidationError:
            return _REFUSED
    return value


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


def _carried(entry: dict, names, report: ImportReport, what: str, *extra: str) -> dict:
    """Only what an archive is defined to carry for this kind of row (#354).

    An entry goes to a model's constructor, and a file can say anything: ``owner_id`` would
    win over the ``owner=`` passed beside it, and ``posting_id`` would attach a row to
    somebody else's listing. So every row is built from the names the exporter writes for
    it (``names``, and ``extra`` for what it writes beside them), and never from a key that
    is an ``id`` or ends in ``_id``: a relation is resolved through the importer's own maps
    of what it has made, after the entry has been read, or not at all. What is left out is
    said in the report, cut short, as a CV's is.
    """
    allowed = (set(names) | set(extra)) - {"id"}
    kept = {
        name: value
        for name, value in entry.items()
        if name in allowed and not str(name).endswith("_id")
    }
    left_out = sorted(str(name) for name in entry if name not in kept)
    if left_out:
        named = ", ".join(repr(name[:40]) for name in left_out[:10])
        if len(left_out) > 10:
            named += f" and {len(left_out) - 10} more"
        report.skipped.append(f"{what}: {named}: not something an archive carries, and left out")
    return kept


def _eqf_level(value, report: ImportReport, entry: dict) -> int | None:
    """An education entry's level, kept when it is a whole number from 1 to 8 (#684).

    Anything else -- a nine, a word, a fraction -- is dropped, and the report says so, since
    a row written straight to the model would otherwise keep it as written.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 8:
        return value
    name = str(entry.get("qualification") or "")[:80]
    report.skipped.append(
        f"Education entry {name!r}: an EQF level of {str(value)[:20]!r} is not a whole number "
        "from 1 to 8, and was left out"
    )
    return None


def _course_hours(value, report: ImportReport, entry: dict) -> int | None:
    """A course's hours, kept when they are a whole number the page would take (#695).

    The form's own bounds, from the model: a file cannot store what the page would refuse.
    """
    if value is None:
        return None
    from postulo.resume.models import Course

    low, high = Course.HOURS_MIN, Course.HOURS_MAX
    if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
        return value
    name = str(entry.get("title") or "")[:80]
    report.skipped.append(
        f"Course {name!r}: hours of {str(value)[:20]!r} are not a whole number from {low} to "
        f"{high:,}, and were left out"
    )
    return None


def _event_choice(value, choices, report: ImportReport, entry: dict, label: str) -> str:
    """An event's role or type, kept when this version lists it, else *not stated* (#694).

    A later Postulo may list a role this one has not heard of; what cannot be drawn is blank
    and prints nothing, and the report says so, because inventing another would be a claim.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, str) and value in choices.values:
        return value
    name = str(entry.get("event") or "")[:80]
    report.skipped.append(
        f"Event {name!r}: the {label} {str(value)[:20]!r} is not one this version lists, and "
        "was left as not stated"
    )
    return ""


def _licence_as_written(values: dict, report: ImportReport) -> dict | None:
    """A driving licence as a form would have kept it, or nothing where it cannot be (#691).

    A row written straight to the model would keep a code outside the fifteen, a country
    that is not on the list and a note as long as a file likes. So the codes are held to the
    table and put in its order, the country to the list, and the note to its length; a code
    that is not one, or no category at all, is a licence that is not restored, and the report
    says which.
    """
    from postulo.resume import driving

    held = values.get("categories")
    note = values.get("other_categories")
    note = note.strip()[: driving.MAX_OTHER] if isinstance(note, str) else ""
    try:
        driving.validate(held if held is not None else [])
    except ValidationError as exc:
        shown = str(held)[:40]
        report.skipped.append(
            f"A driving licence with the categories {shown}: {exc.messages[0]}, and left out"
        )
        return None
    if not held and not note:
        report.skipped.append(
            "A driving licence with no category: it says nothing, and was left out"
        )
        return None
    country = values.get("country")
    if not isinstance(country, str) or country not in phones.BY_CODE:
        if country:
            report.skipped.append(
                f"A driving licence: {str(country)[:20]!r} is not a country on the list, and "
                "was left blank"
            )
        country = ""
    return {
        **values,
        "categories": driving.ordered(held or []),
        "other_categories": note,
        "country": country,
    }


def _dt(value):
    return parse_datetime(value) if value else None


def _d(value):
    return parse_date(value) if value else None


def read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        archive.getinfo(MANIFEST_NAME)
    except KeyError as exc:
        raise ArchiveError(f"No {MANIFEST_NAME} in that archive.") from exc
    raw = _read_within(archive, MANIFEST_NAME, MANIFEST_MAX_BYTES)
    if raw is None:
        raise ArchiveError(f"{MANIFEST_NAME} is larger than an export of Postulo makes.")
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ArchiveError(f"{MANIFEST_NAME} is not valid JSON.") from exc

    header = document.get("postulo") or {}
    if "format" not in header:
        raise ArchiveError("That does not look like a Postulo export.")
    # Every format so far has added keys and sections, so an older archive reads as it is
    # and a newer one cannot be read as if it were current: a new key on a record ends in
    # a traceback and a new section is dropped while the import reports success. Refuse it
    # before anything is written, as the backup reader does.
    fmt = header["format"]
    if not isinstance(fmt, int) or isinstance(fmt, bool) or not 1 <= fmt <= FORMAT_VERSION:
        raise ArchiveError(
            f"This archive is export format {fmt!r}; this version of Postulo reads "
            f"1 to {FORMAT_VERSION}."
        )
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
    for row in (document.get("resume") or {}).get("languages") or []:
        written(row, "code", "The code of a language in your career record")
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
    """Whether the account holds none of what an archive restores wholesale.

    The profile's own telephone numbers, addresses, links and identifiers do not count:
    somebody who signed up and filled in *Your details* is still importing into an empty
    account, and the importer keeps those rows and adds the archive's beside them (#375).
    """
    from postulo.applications.models import Application
    from postulo.documents.models import CV
    from postulo.jobs.models import Company
    from postulo.resume.models import Experience

    return not any(
        model.objects.for_user(user).exists() for model in (Application, Company, CV, Experience)
    )


#: What an address row must have something in to be worth restoring at all.
#: A calendar identifier worth keeping: anything else could carry a line break into a feed.
SAFE_UID = re.compile(r"[A-Za-z0-9@._-]{1,64}")


def _restored_uid(model, user, raw) -> dict:
    """``{"uid": …}`` for a record whose calendar identifier can travel, or nothing.

    Kept where it is plainly safe and not already held by this owner; a forced duplicate on
    the same instance, a file from a later Postulo and an identifier somebody typed by hand
    each get a fresh one, which is what the column's default mints.
    """
    if (
        isinstance(raw, str)
        and SAFE_UID.fullmatch(raw)
        and not model.objects.filter(owner=user, uid=raw).exists()
    ):
        return {"uid": raw}
    return {}


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


def _messaging_rows(entry: dict) -> list[dict]:
    """Take the messaging handles out of a record. Format 34 is the first to write any; the
    key is removed from ``entry`` because what remains is passed straight to a model that
    has no such field (#682)."""
    from postulo.core.messaging_handles import MAX_PER_HOLDER

    rows = entry.pop("messaging_handles", None)
    if not isinstance(rows, list):
        return []
    # A file with thousands of them is not a person's contact details: the first few are
    # kept and the rest are not read.
    return [row for row in rows[:MAX_PER_HOLDER] if isinstance(row, dict)]


def _held(model, holder):
    """The rows of ``model`` this holder already has: none, for a contact made a moment ago."""
    from django.contrib.contenttypes.models import ContentType

    return model.objects.filter(
        content_type=ContentType.objects.get_for_model(holder), object_id=holder.pk
    )


def _skipped(report, what: str, value: str, why: str) -> None:
    """Say a row was left out. `!r`, and cut short: the value is whatever the file says."""
    if report is not None:
        report.skipped.append(f"The {what} {value[:60]!r} {why}")


def _restore_web_links(holder, owner, rows: list[dict], report=None) -> None:
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

    # What the holder already has comes first (#375): the primary it has stays the primary,
    # and a link it already holds is not made twice. `report` is where a skip is said.
    held = _held(WebLink, holder)
    primary_taken: set[str] = {link.kind for link in held if link.is_primary}
    seen: set[tuple[str, str]] = {(link.kind, link.url) for link in held}
    for row in rows:
        url = (row.get("url") or "").strip()[:500]
        kind = row.get("kind")
        if (kind, url) in seen:
            _skipped(report, "link", url, "is already among your details, and was not added again.")
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


def _restore_messaging_handles(holder, owner, rows: list[dict], report=None) -> None:
    """Recreate a holder's messaging handles (#682).

    The service is a claim, as a link's is: kept where this instance offers it and the
    handle is one of its handles, and *Other* -- named by the key the file carried --
    otherwise. No row is refused for its service. What the holder already has comes first:
    the primary it has stays the primary, and a handle it already holds is not made twice.
    """
    from postulo.core import messaging_handles
    from postulo.core.models import MessagingHandle

    held = _held(MessagingHandle, holder)
    primary_taken = held.filter(is_primary=True).exists()
    seen: set[tuple[str, str]] = {(row.service, row.comparable) for row in held}
    for row in rows:
        settled = messaging_handles.read_from_a_file(row)
        if settled is None:
            _skipped(report, "messaging handle", str(row.get("handle") or ""), "could not be read.")
            continue
        key = (
            settled["service"],
            MessagingHandle.fold(settled["service"], settled["label"], settled["handle"]),
        )
        if key in seen:
            _skipped(
                report,
                "messaging handle",
                settled["handle"],
                "is already among your details, and was not added again.",
            )
            continue
        seen.add(key)
        is_primary = settled["is_primary"] and not primary_taken
        primary_taken = primary_taken or is_primary
        MessagingHandle.objects.create(
            owner=owner,
            holder=holder,
            service=settled["service"],
            label=settled["label"],
            handle=settled["handle"],
            is_primary=is_primary,
        )


def _restore_postal_addresses(holder, owner, rows: list[dict], report=None) -> None:
    """Recreate a holder's addresses.

    No skipping, and that is the difference from the numbers above. Addresses are unique
    per owner rather than across the instance, so an archive can never collide with
    somebody else's -- two people at one address is a household. Within one import a
    repeated address is dropped, because listing the same one twice is the mistake the
    constraint exists to catch. The same goes for one the owner already has, on this holder
    or another, and a holder that already has a primary keeps it (#375).
    """
    from postulo.core.models import PostalAddress

    primary_taken = _held(PostalAddress, holder).filter(is_primary=True).exists()
    seen: set[str] = set(
        PostalAddress.objects.filter(owner=owner).values_list("comparable", flat=True)
    )
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
            named = ", ".join(
                part for part in (address.street, address.postcode, address.municipality) if part
            )
            _skipped(
                report, "address", named, "is already among your details, and was not added again."
            )
            continue
        seen.add(comparable)
        if address.is_primary:
            primary_taken = True
        address.save()


def _restore_references(user, entries, contacts: dict, report: ImportReport) -> dict:
    """The referees an archive carries, as ``{id in the file: entry made}`` (#696).

    The contact is the file's local id, looked up among the contacts this import has just
    made, so it can only ever be one of this account's. The permission is read as one of its
    three words and is *Not asked* for anything else; a switch is read as a yes or no.
    """
    from postulo.resume import models as resume

    made: dict = {}
    if not isinstance(entries, list):
        return made
    taken = set(resume.Reference.objects.filter(owner=user).values_list("contact_id", flat=True))
    permissions = set(resume.ReferencePermission.values)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        contact = contacts.get(entry.get("contact_id"))
        if contact is None:
            report.skipped.append(
                f"Reference #{entry.get('id')}: its contact is not in the file, and it was left out"
            )
            continue
        if contact.pk in taken:
            report.skipped.append(
                f"Reference to {contact.name}: you already have one for them, and it was left out"
            )
            continue
        permission = entry.get("permission")
        order = entry.get("order")
        made[entry.get("id")] = resume.Reference.objects.create(
            owner=user,
            contact=contact,
            relationship=str(entry.get("relationship") or "")[:200],
            permission=permission if permission in permissions else "not_asked",
            show_details=entry.get("show_details") is True,
            note=str(entry.get("note") or ""),
            order=order if isinstance(order, int) and order >= 0 else 0,
        )
        taken.add(contact.pk)
        report.resume_items += 1
    return made


def _restore_phone_numbers(
    holder, owner, rows: list[dict], report: ImportReport | None = None, whose: str = ""
) -> None:
    """Recreate a holder's numbers, skipping any this instance already has.

    Numbers are unique across the instance, so an archive carrying one somebody here
    already holds cannot be imported as it stands. Skipping that row is the only answer
    that neither fails the whole import nor takes a number away from whoever already had
    it, and the rest of the archive lands intact. Each skip is a line in ``report``, naming
    the record in the archive (``whose``) and the last digits of the number, never the
    account that holds it (#562).
    """
    from postulo.core.models import PhoneNumber
    from postulo.core.phone_numbers import taken_elsewhere

    primary_taken = _held(PhoneNumber, holder).filter(is_primary=True).exists()
    for row in rows:
        number = (row.get("number") or "").strip()
        if taken_elsewhere(number):
            if report is not None:
                ending = "".join(ch for ch in number if ch.isdigit())[-2:]
                report.skipped.append(
                    f"Telephone number ending {ending} {whose}: "
                    "already recorded on this instance, not restored"
                )
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


def load(user, archive: zipfile.ZipFile, *, force: bool = False) -> ImportReport:
    """Create everything in ``archive`` under ``user``.

    Runs in one transaction: a failure half way through leaves the account exactly as it
    was, rather than partly overwritten by a file that turned out to be broken. A row the
    database refuses that the importer did not foresee is an `ArchiveError`, a sentence
    for the operator rather than a traceback (#375).
    """
    # Every file written on the way, so that a failure takes them back with the rows (#663):
    # the transaction rolls the rows back and has no say over the bytes beside them.
    written: list[str] = []
    marker = _written.set(written)
    try:
        return _load(user, archive, force=force)
    except BaseException as error:
        _take_back(written)
        if isinstance(error, IntegrityError):
            raise ArchiveError(
                f"The archive clashes with what the account already holds: {error}. "
                "Nothing was imported."
            ) from error
        raise
    finally:
        _written.reset(marker)


#: The files `load` has written so far, set for the length of one import.
_written: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "importer_written", default=None
)

logger = logging.getLogger(__name__)


def _wrote(*names: str) -> None:
    held = _written.get()
    if held is not None:
        held.extend(name for name in names if name)


def _take_back(names: list[str]) -> None:
    """Remove every file an import that failed had written. Never raises."""
    from postulo.documents import filestore

    for name in names:
        try:
            filestore.delete(name)
        except OSError:  # pragma: no cover - a file already gone is the outcome wanted
            logger.warning("Could not remove %s after a failed import", name, exc_info=True)


def _file_problem(stored_name: str, content: bytes, said: dict, *, uploaded: bool) -> str | None:
    """Why a file unpacked from the archive is not to be kept, or nothing where it is.

    Nothing in an archive is taken on trust, as for a kept page: the figures it says are
    measured against the bytes that arrived, and an upload is held to the same extension
    list and the same look-alike check a form applies (#663).
    """
    from postulo.documents import integrity

    size, checksum = integrity.digest_of_bytes(content)
    said_size, said_checksum = said.get("size"), said.get("checksum")
    if isinstance(said_checksum, str) and said_checksum and said_checksum != checksum:
        return "it does not match the checksum the archive recorded for it"
    if isinstance(said_size, int) and not isinstance(said_size, bool) and said_size != size:
        return "it is not the size the archive recorded for it"
    if uploaded:
        extension = integrity.extension_of(stored_name)
        if extension not in integrity.EXTENSIONS:
            return "it is not a type of file an upload may be"
        if integrity.problem_with_bytes(extension, content):
            return "its content is not what its name says"
    return None


@transaction.atomic
def _load(user, archive: zipfile.ZipFile, *, force: bool = False) -> ImportReport:
    from postulo.accounts.models import PersonIdentifier
    from postulo.applications.models import (
        Application,
        ApplicationEvent,
        Interview,
        InterviewKind,
        InterviewOutcome,
        Offer,
        Reminder,
    )
    from postulo.core import identifiers as scheme_registry
    from postulo.core import pictures
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
        CompanyLogo,
        Contact,
        Department,
        Industry,
        JobPosting,
    )
    from postulo.jobs.services import set_identifiers
    from postulo.resume import models as resume
    from postulo.resume import publications

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
    handles = _messaging_rows(profile_data)
    profile = getattr(user, "profile", None)
    if profile and profile_data:
        for name, value in profile_data.items():
            if name not in PROFILE_FIELDS or not hasattr(profile, name):
                continue
            settled = _profile_value(profile, name, value)
            if settled is not _REFUSED:
                setattr(profile, name, settled)
            elif name in _PERSONAL_FIELDS:
                label = str(type(profile)._meta.get_field(name).verbose_name)
                report.skipped.append(
                    f"Your {label} {str(value)[:60]!r} is not one this page would take, "
                    "and was left out"
                )
        profile.save()
    if profile:
        _restore_phone_numbers(profile, user, numbers, report, "on the profile")
        _restore_postal_addresses(profile, user, addresses, report)
        _restore_web_links(profile, user, links, report)
        _restore_messaging_handles(profile, user, handles, report)
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
        if scheme != identifiers.OTHER:
            # One of each scheme per profile: the value the profile has stays (#375).
            kept = PersonIdentifier.objects.filter(profile=profile, scheme=scheme).first()
            if kept is not None:
                if kept.value != value:
                    _skipped(
                        report,
                        "identifier",
                        value,
                        f"was not added: your details already hold another under {scheme}.",
                    )
                continue
        PersonIdentifier.objects.get_or_create(**found, defaults={"label": label})

    avatar_file = account.get("avatar_file") or ""
    if profile and avatar_file:
        from postulo.accounts import avatars

        content = _extract_within(archive, avatar_file, avatars.MAX_UPLOAD_BYTES)
        if content is not None:
            # The same checks the profile form applies: decoded, cropped and written out
            # again, so what an archive carries is not kept as it came (#466).
            try:
                processed = avatars.process(content)
            except avatars.UnusableImage as exc:
                report.skipped.append(f"The profile picture: {exc}, and left out")
            else:
                avatars.store(profile, avatars.ProfilePicture.UPLOAD, processed)
        elif avatar_file:
            report.skipped.append("The profile picture was too large, or not in the archive")

    cv_photo_file = account.get("cv_photo_file") or ""
    if profile and cv_photo_file:
        from postulo.accounts import avatars

        content = _extract_within(archive, cv_photo_file, avatars.MAX_UPLOAD_BYTES)
        if content is not None:
            # Decoded and written out again, as the profile form does, and not cropped (#668).
            try:
                processed = avatars.process_cv_photo(content)
            except avatars.UnusableImage as exc:
                report.skipped.append(f"The CV photo: {exc}, and left out")
            else:
                avatars.store(profile, avatars.ProfilePicture.CV, processed)
        else:
            report.skipped.append("The CV photo was too large, or not in the archive")

    # --------------------------------------------------------------------- tags
    # An archive written before #285 carries whatever its owner typed into a free-text
    # colour box, and one written by a later Postulo than this may carry an icon this one
    # has never heard of. Both are read for what they can mean and neither is refused: an
    # import that rejects a whole account over the shade of a label would be absurd.
    known_icons = {choice.value for choice in TagIcon}
    tags_by_slug: dict[str, Tag] = {}
    known_tags = list(Tag.objects.filter(owner=user))
    for entry in document.get("tags", []):
        name = entry.get("name", "")
        tag = slugs.same_name(known_tags, name)
        created = tag is None
        if created:
            tag = Tag.objects.create(
                owner=user,
                name=slugs.collapse(name)[:60],
                colour=nearest_tone(entry.get("colour", "")),
                icon=entry.get("icon", "") if entry.get("icon", "") in known_icons else "",
            )
            known_tags.append(tag)
        # The slug the archive wrote is what its applications name the tag by, whatever
        # slug this instance gave the row: an archive written with an empty one still
        # finds its tags (#356).
        tags_by_slug[entry["slug"] if "slug" in entry else name.lower()] = tag
        tags_by_slug.setdefault(tag.slug, tag)
        report.tags += int(created)

    # ------------------------------------------------------------------- career
    resume_map: dict[str, dict[int, object]] = {}
    section_models = resume_section_models()
    held_keys = set(resume.Publication.objects.for_user(user).values_list("cite_key", flat=True))
    date_fields = DATE_FIELDS
    moment_fields = {"checked_at"}
    #: Experience primary key -> the name of the company it links to, applied once every
    #: company in the file has been made or matched (#683).
    wants_company: dict[tuple[str, int], str] = {}

    for key, model in section_models.items():
        resume_map[key] = {}
        for entry in document.get("resume", {}).get(key, []):
            old_id = entry.pop("id", None)
            # The company an experience links to, by name (format 46), taken out before the
            # constructor sees the entry and linked once the companies exist. An archive
            # without it restores every entry unlinked (#683).
            company_name = entry.pop("company", "") if key in LINKED_TO_A_COMPANY else ""
            values = {}
            for name, value in _carried(
                entry, RESUME_FIELDS[key], report, f"A {key} entry"
            ).items():
                if name in date_fields:
                    values[name] = _d(value)
                elif name in moment_fields:
                    values[name] = _dt(value)
                elif name == "eqf_level":
                    values[name] = _eqf_level(value, report, entry)
                elif name == "hours":
                    values[name] = _course_hours(value, report, entry)
                elif key == "participations" and name in ("role", "kind"):
                    values[name] = _event_choice(
                        value,
                        resume.ParticipationRole if name == "role" else resume.ParticipationKind,
                        report,
                        entry,
                        name,
                    )
                else:
                    values[name] = value
            if key == "publications":
                # What no page has looked at is held to what a page would have (#687).
                values = publications.sanitise(values, held_keys)
                held_keys.add(values.get("cite_key", ""))

            if key == "driving_licences":
                # Held to the table of categories, as a page would have held it (#691).
                values = _licence_as_written(values, report)
                if values is None:
                    continue
            if key == "languages":
                # A code is read as a claim, as any language code is: shaped like a tag or
                # nothing. An archive from before format 50 has none, and a name that is
                # exactly one language is given the code it stands for (#689).
                values["code"] = values.get("code") or language_names.match(values.get("name", ""))
            created = model.objects.create(owner=user, **values)
            if isinstance(company_name, str) and company_name.strip():
                wants_company[(key, created.pk)] = company_name
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
        created = resume.Skill.objects.create(
            owner=user,
            group=group,
            **_carried(entry, RESUME_FIELDS["skills"], report, "A skill"),
        )
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

    #: The files each application went out with, as their ids in the file; applied once the
    #: uploads have been made (#469).
    wants_sent_uploads: list[tuple[Application, list]] = []

    #: Each listing's history, as the file wrote it, held until the end: an entry may point
    #: at a capture or an upload, and those are made last (#270).
    listing_histories: list[tuple[JobPosting, list]] = []

    def restore_contact(entry: dict, company) -> None:
        """One person from the file, at ``company`` or at none."""
        old_id = entry.pop("id", None)
        numbers = _phone_rows(entry)
        contact_addresses = _address_rows(entry)
        contact_links = _link_rows(entry)
        contact_handles = _messaging_rows(entry)
        department_name = (entry.pop("department", "") or "").strip()[:120]
        contact = Contact.objects.create(
            owner=user, company=company, **_carried(entry, CONTACT_FIELDS, report, "A contact")
        )
        if department_name and company is not None:
            department, _made = Department.objects.get_or_create(
                owner=user,
                company=company,
                name_key=slugs.name_key(department_name),
                defaults={"name": department_name},
            )
            contact.department = department
            contact.save(update_fields=["department"])
        _restore_phone_numbers(contact, user, numbers, report, f"on the contact “{contact.name}”")
        _restore_postal_addresses(contact, user, contact_addresses, report)
        _restore_web_links(contact, user, contact_links, report)
        _restore_messaging_handles(contact, user, contact_handles, report)
        contacts[old_id] = contact

    # The reminders about no application, which format 31 is the first to carry (#334).
    for reminder_entry in document.get("reminders") or []:
        due_at = _dt(reminder_entry.get("due_at"))
        if due_at is None:
            report.skipped.append(
                "A reminder about no application: it has no date, and was left out"
            )
            continue
        Reminder.objects.create(
            owner=user,
            application=None,
            summary=reminder_entry.get("summary", ""),
            due_at=due_at,
            done_at=_dt(reminder_entry.get("done_at")),
            **_restored_uid(Reminder, user, reminder_entry.get("uid")),
        )
        report.reminders += 1

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
        company_entry = _carried(company_entry, COMPANY_FIELDS, report, "A company")
        company_entry["logo_fetched_at"] = _dt(company_entry.get("logo_fetched_at"))
        # A switch, read as one: anything but a true is not marked. An archive from before
        # format 44 says nothing, and every company in it is one that is not (#683).
        company_entry["from_career"] = company_entry.get("from_career") is True

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
            company = Company.objects.for_user(user).filter(name_key=slugs.name_key(name)).first()
        if company is None:
            company = Company.objects.create(owner=user, name=name, **company_entry)
            report.companies += 1
            from postulo.jobs import logos

            logo = _extract_within(archive, logo_name, logos.MAX_BYTES) if logo_name else None
            if logo is not None:
                try:
                    processed = logos.process(logo)
                except logos.UnusableLogo as exc:
                    report.skipped.append(f"Logo of “{name}”: {exc}, and left out")
                else:
                    # Written as it arrived, stamp and source included, rather than as a
                    # fetch that happened now: through the one function that writes a
                    # picture, so what an archive carries is checked as an upload is (#662).
                    with transaction.atomic():
                        pictures.keep(CompanyLogo, processed, company=company)
                        company.has_logo = True
                        company.save(update_fields=["has_logo"])
            elif logo_name:
                report.skipped.append(f"Logo of “{name}” was too large, or not in the archive")
        if parent_name:
            wants_parent[company.pk] = parent_name
        if industry_names:
            company.industries.add(*Industry.named(user, industry_names))
        for department_name in department_names:
            Department.objects.get_or_create(
                owner=user,
                company=company,
                name_key=slugs.name_key(department_name),
                defaults={"name": department_name},
            )
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
            # A file from before format 34 has none, and one that says something this
            # instance does not know is read as plain text rather than refused (#665).
            if posting_entry.get("description_format") not in ("plain", "markdown"):
                posting_entry.pop("description_format", None)

            posting = JobPosting.objects.create(
                owner=user,
                company=company,
                **_carried(posting_entry, POSTING_FIELDS, report, "A listing"),
            )
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
                sent_upload_ids = application_entry.pop("sent_upload_ids", None) or []
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
                    **_carried(application_entry, APPLICATION_FIELDS, report, "An application"),
                )
                applications[old_id] = application
                report.applications += 1
                # Held until the uploads exist, as an upload's predecessor is (#469).
                if sent_upload_ids:
                    wants_sent_uploads.append((application, sent_upload_ids))

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

                tied_events: list[tuple[ApplicationEvent, int]] = []
                for event_entry in event_entries:
                    event_entry.pop("id", None)
                    event_entry.pop("created_at", None)
                    tied_to = event_entry.pop("interview_id", None)
                    event_entry["occurred_at"] = _dt(event_entry.get("occurred_at"))
                    event = ApplicationEvent.objects.create(
                        application=application,
                        **_carried(event_entry, EVENT_FIELDS, report, "A timeline event"),
                    )
                    if tied_to is not None:
                        tied_events.append((event, tied_to))
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
                        **_restored_uid(Reminder, user, reminder_entry.get("uid")),
                    )
                    report.reminders += 1

                imported_interviews: dict[int, Interview] = {}
                for interview_entry in interview_entries:
                    old_interview_id = interview_entry.pop("id", None)
                    interview_entry.pop("created_at", None)
                    contact_ids = interview_entry.pop("contact_ids", [])
                    reminder_id = interview_entry.pop("reminder_id", None)
                    # The calendar identifier travels, so a calendar that knew the meeting
                    # still does; a forced duplicate on the same instance gets a fresh one.
                    uid = interview_entry.pop("uid", None)
                    if uid and (
                        not isinstance(uid, str)
                        or not SAFE_UID.fullmatch(uid)
                        or Interview.objects.filter(owner=user, uid=uid).exists()
                    ):
                        uid = None
                    # A kind or outcome from a later Postulo, or typed by hand, is one this
                    # version can name no better than the last of each (#450).
                    if interview_entry.get("kind") not in InterviewKind.values:
                        interview_entry["kind"] = InterviewKind.OTHER
                    if interview_entry.get("outcome") not in InterviewOutcome.values:
                        interview_entry["outcome"] = InterviewOutcome.SCHEDULED
                    interview = Interview.objects.create(
                        owner=user,
                        application=application,
                        reminder=reminders.get(reminder_id),
                        starts_at=_dt(interview_entry.pop("starts_at", None)),
                        ends_at=_dt(interview_entry.pop("ends_at", None)),
                        **({"uid": uid} if uid else {}),
                        **_carried(interview_entry, INTERVIEW_FIELDS, report, "An interview"),
                    )
                    interview.contacts.set([contacts[i] for i in contact_ids if i in contacts])
                    imported_interviews[old_interview_id] = interview
                    report.interviews += 1
                for event, tied_to in tied_events:
                    if tied_to in imported_interviews:
                        event.interview = imported_interviews[tied_to]
                        event.save(update_fields=["interview"])
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
                        **_carried(offer_entry, OFFER_FIELDS, report, "An offer"),
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

    # The companies the career names, by name like an agency: one the file does not hold
    # leaves the entry unlinked and its text as written, and nothing is added (#683).
    if wants_company:
        named = {
            company.name_key: company
            for company in Company.objects.for_user(user).filter(
                name_key__in={slugs.name_key(name) for name in wants_company.values()}
            )
        }
        for (key, entry_pk), name in wants_company.items():
            company = named.get(slugs.name_key(name))
            if company is not None:
                section_models[key].objects.filter(pk=entry_pk).update(company=company)

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
            .filter(
                company__name_key=slugs.name_key(company_name),
                name_key=slugs.name_key(department_name),
            )
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

    # The people who will vouch for the person, now that every contact in the file exists and
    # before the CVs, whose entries may point at them (#696). Always *Not asked* unless the
    # file says otherwise in one of the three words; one whose contact is not in the file is
    # skipped and named, and so is one this account already has an entry for.
    resume_map["references"] = _restore_references(
        user, document.get("resume", {}).get("references") or [], contacts, report
    )

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
            if item.get("kind") == "reference":
                # Not a section the candidate file or the translations know (#696).
                section = "references"
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
        letters[old_id] = CoverLetter.objects.create(
            owner=user, **_carried(letter_entry, LETTER_FIELDS, report, "A cover letter")
        )
        report.cover_letters += 1

    uploads: dict[int, UploadedDocument] = {}
    pending_supersedes: list[tuple[UploadedDocument, int]] = []
    for upload_entry in documents.get("uploads", []):
        old_id = upload_entry.pop("id", None)
        replaces_id = upload_entry.pop("replaces_id", None)
        stored_name = upload_entry.pop("file", "")
        upload_entry.pop("created_at", None)
        copies = upload_entry.pop("copies", [])
        letter_entry = upload_entry.pop("reference_letter", None)
        proves_entry = upload_entry.pop("proves", None)
        # The archive's figures are measured against the bytes and never written to the row:
        # the row's own are worked out from what was kept (#663).
        figures = {name: upload_entry.pop(name, None) for name in ("checksum", "size")}

        upload = UploadedDocument(
            owner=user, **_carried(upload_entry, UPLOAD_FIELDS, report, "An upload")
        )
        from postulo.documents import proofs
        from postulo.documents.forms import MAX_UPLOAD_BYTES

        if isinstance(proves_entry, dict):
            section = proves_entry.get("section")
            target = resume_map.get(section, {}).get(proves_entry.get("ref"))
            if target is not None and proofs.name_of(target):
                proofs.set_proof(upload, target)
            else:
                report.skipped.append(
                    f"File “{upload.title}” proves {section}#{proves_entry.get('ref')}: "
                    "no such record, so it proves nothing"
                )

        content = _extract_within(archive, stored_name, MAX_UPLOAD_BYTES)
        if content is None:
            report.skipped.append(f"File for “{upload.title}” was too large, or not in the archive")
        elif problem := _file_problem(stored_name, content, figures, uploaded=True):
            report.skipped.append(f"File for “{upload.title}” was left out: {problem}")
        else:
            upload.file.save(stored_name.rsplit("/", 1)[-1], ContentFile(content), save=False)
            _wrote(upload.file.name)
        upload.save()
        _restore_copies(user, copies, upload)
        if isinstance(letter_entry, dict):
            _restore_reference_letter(user, upload, letter_entry, contacts)
        uploads[old_id] = upload
        report.uploads += 1
        if replaces_id:
            pending_supersedes.append((upload, replaces_id))

    for upload, replaces_id in pending_supersedes:
        earlier = uploads.get(replaces_id)
        if earlier is not None:
            upload.replaces = earlier
            upload.save(update_fields=["replaces"])

    for application, old_upload_ids in wants_sent_uploads:
        application.sent_uploads.set(
            [uploads[i] for i in old_upload_ids if isinstance(i, int) and i in uploads]
        )

    for sent_entry in documents.get("sent", []):
        sent_entry.pop("id", None)
        stored_name = sent_entry.pop("file", "")
        application = applications.get(sent_entry.pop("application_id", None))
        source = _source_of(sent_entry, cvs, letters)
        rendered_at = _dt(sent_entry.pop("rendered_at", None))
        copies = sent_entry.pop("copies", [])
        # A boolean, or the default: an archive from before format 42 has no key, and a value
        # that is not one is not a reason to refuse the file (#480).
        with_properties = sent_entry.pop("with_properties", True) is not False

        figures = {"checksum": sent_entry.get("checksum")}
        sent = RenderedDocument(
            owner=user,
            application=application,
            source=source,
            with_properties=with_properties,
            **_carried(
                sent_entry, SENT_FIELDS, report, "A sent document", "source_text", "plain_text"
            ),
        )
        if rendered_at:
            sent.rendered_at = rendered_at
        from postulo.documents.forms import MAX_UPLOAD_BYTES

        content = _extract_within(archive, stored_name, MAX_UPLOAD_BYTES)
        if content is None:
            report.skipped.append(f"File for “{sent.title}” was too large, or not in the archive")
        elif problem := _file_problem(stored_name, content, figures, uploaded=False):
            report.skipped.append(f"File for “{sent.title}” was left out: {problem}")
        else:
            sent.file.save(stored_name.rsplit("/", 1)[-1], ContentFile(content), save=False)
            _wrote(sent.file.name)
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
            owner=user,
            application=application,
            posting=posting,
            **_carried(capture_entry, CAPTURE_FIELDS, report, "A capture", "data"),
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


def _restore_reference_letter(user, upload, entry: dict, contacts: dict) -> None:
    """The record of who wrote a reference letter, once the contacts exist (#666).

    A referee the file does not carry is left blank, a date that does not read is left
    empty, and a delivery it does not know is the default: the letter is still there, which
    is what matters.
    """
    from postulo.documents.models import ReferenceDelivery, ReferenceLetter

    def day(value):
        try:
            return _d(value) if isinstance(value, str) else None
        except ValueError:
            return None

    referee_id = entry.get("referee_id")
    delivery = entry.get("delivery")
    ReferenceLetter.objects.create(
        owner=user,
        upload=upload,
        referee=contacts.get(referee_id) if isinstance(referee_id, int) else None,
        written_on=day(entry.get("written_on")),
        valid_until=day(entry.get("valid_until")),
        delivery=delivery if delivery in ReferenceDelivery.values else ReferenceDelivery.YOU,
    )


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
    if page is not None:
        _wrote(page.source.name, page.rendering.name)
    return 1 if page is not None else 0


def _extract_within(archive: zipfile.ZipFile, stored_name: str, limit: int) -> bytes | None:
    """A file from the archive, unless it would unpack to more than ``limit`` bytes.

    Asked of the archive's own directory before anything is unpacked: a zip entry says how
    large it will be, and one that says more than the cap is not read to find out.
    """
    if not stored_name or not stored_name.startswith(MEDIA_PREFIX):
        return None
    return _read_within(archive, stored_name, limit)


def _read_within(archive: zipfile.ZipFile, stored_name: str, limit: int) -> bytes | None:
    try:
        described = archive.getinfo(stored_name)
    except KeyError:
        return None
    if described.file_size > limit:
        return None
    try:
        with archive.open(described) as handle:
            # One byte past the limit, so a directory that understated a file is caught here
            # rather than believed.
            content = handle.read(limit + 1)
    except (zipfile.BadZipFile, EOFError, zlib.error, NotImplementedError):
        # A directory that lies about an entry, or an entry that will not unpack: left out.
        return None
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
