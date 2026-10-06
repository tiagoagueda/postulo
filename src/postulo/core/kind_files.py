"""One kind of record as a JSON file of its own, and back in (#659).

Companies, contacts, listings and applications each have a file, written from the same
builders as the whole-account archive and read back through a review page. The archive is
all or nothing and its import only creates; these are for taking out "my companies" or
"my contacts" and putting them into another account, or the same one, without a second
copy of anything.

**The stance is the candidate file's** (`resume.candidate`, #181), which this follows
where it can and shares `core.file_review` with where it need not be repeated:

- *A review, not an action.* `read` turns bytes into something a session can hold, `plan`
  says what adding it would do to this account as it stands, `apply` does that and no more.
- *It merges, and never replaces.* A record the account already holds is left exactly as it
  is. The same file twice adds nothing the second time.
- *Two records are the same when they say the same thing.* The ids of the archive are local
  to it and are not written at all here: nothing in a file names a row of the database, so a
  file cannot name somebody else's. A company is its name, or an identifier it carries; a
  contact is its email, else its name at its company; a listing is its address, else its
  company, title and date; an application is its listing and the day it was sent.
- *The file is a stranger's.* Its size and its rows are bounded before anything is parsed or
  validated; only the keys the writer writes are read (#354); every value goes through the
  model's form, so the import accepts what the pages accept and nothing else; and what it
  says about who owns a row is not read, because it is not written.

**What is left out, and said.** Pictures, logos, uploaded files and what plugins hold: JSON
cannot carry a file, and the archive is where the rest goes. Each file says so in its `note`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from dataclasses import dataclass, field

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from postulo import __version__
from postulo.core import export, file_review, slugs
from postulo.core.addresses import same_url
from postulo.core.file_review import ADD, PRESENT, REFUSED, REPEATED, Refused

#: The same bounds as the spreadsheet import (`core.csv_import`): one search is kilobytes,
#: and a file is read, drawn and validated row by row on whatever machine this runs on.
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
#: What one application may carry of its own: its timeline and its interviews.
MAX_NESTED = 200
#: What one contact may carry of each way of being reached.
MAX_DETAILS = 20

#: What each file starts at, and what its marker is called in the header. A block that
#: changes shape is a new version, which `tests/test_kind_files.py` holds fast.
FORMATS = {"companies": 1, "contacts": 1, "listings": 1, "applications": 1}

#: Telephone numbers are not read with a say in whether they were confirmed or are a way
#: back in (#142, #144): neither is written.
NOT_THE_FILES_TO_SAY = ("verified_at", "is_recovery")

# ------------------------------------------------------------------ what each file holds

COMPANY_FIELDS = ("name", "kind", "website", "careers_url", "location", "notes")
IDENTIFIER_FIELDS = ("scheme", "value", "label")
CONTACT_FIELDS = ("name", "role", "email", "notes")
CONTACT_DETAILS: dict[str, tuple[str, ...]] = {
    "phone_numbers": tuple(
        name for name in export.PHONE_NUMBER_FIELDS if name not in NOT_THE_FILES_TO_SAY
    ),
    "postal_addresses": export.POSTAL_ADDRESS_FIELDS,
    "web_links": export.WEB_LINK_FIELDS,
    "messaging_handles": export.MESSAGING_FIELDS,
}
#: Of the archive's listing, what is the person's own saying. Left out: what Postulo works
#: out (`isco_code`), when it was noted and decided and closed, and its history.
LISTING_FIELDS = tuple(
    name
    for name in export.POSTING_FIELDS
    if name
    not in (
        "id",
        "isco_code",
        "closed_at",
        "noted_at",
        "decided_at",
        "created_at",
    )
)
APPLICATION_FIELDS = ("status", "channel", "priority", "applied_at", "deadline", "closed_at")
#: A timeline entry, less what points at something else in the archive: its id, the
#: interview it was written for, and when it was recorded.
EVENT_FIELDS = tuple(
    name for name in export.EVENT_FIELDS if name not in ("id", "interview_id", "created_at")
)
INTERVIEW_FIELDS = tuple(
    name
    for name in export.INTERVIEW_FIELDS
    if name not in ("id", "uid", "reminder_id", "created_at")
)

_S = "scalar"


def _list(sub, cap: int = MAX_NESTED):
    return ("list", sub, cap)


def _scalars(names) -> dict:
    return dict.fromkeys(names, _S)


_LISTING = {"company": _S, **_scalars(LISTING_FIELDS)}
#: What is read of each file's rows: the keys the writer writes and no others.
SCHEMAS: dict[str, dict] = {
    "companies": {
        **_scalars(COMPANY_FIELDS),
        "parent": _S,
        "industries": _list(_S),
        "departments": _list(_S),
        "identifiers": _list(_scalars(IDENTIFIER_FIELDS)),
    },
    "contacts": {
        **_scalars(CONTACT_FIELDS),
        "company": _S,
        "department": _S,
        **{block: _list(_scalars(names), MAX_DETAILS) for block, names in CONTACT_DETAILS.items()},
    },
    "listings": _LISTING,
    "applications": {
        "company": _S,
        "listing": _scalars(LISTING_FIELDS),
        **_scalars(APPLICATION_FIELDS),
        "department": _S,
        "through_agency": _S,
        "events": _list(_scalars(EVENT_FIELDS)),
        "interviews": _list(_scalars(INTERVIEW_FIELDS)),
    },
}

NOTES = {
    "companies": (
        "Companies as Postulo holds them, with their industries, departments and "
        "identifiers. Logos and the people recorded at each company are not in it: the "
        "people are a file of their own. Nothing in it is meaningful anywhere else but as "
        "what it says."
    ),
    "contacts": (
        "People as Postulo holds them, each with the company they are at by name, and how "
        "to reach them. Pictures are not in it."
    ),
    "listings": (
        "Listings as Postulo holds them, each with its company by name. What happened "
        "to a listing, and anything captured from its page, is not in it."
    ),
    "applications": (
        "Applications as Postulo holds them, each with its listing, its company by name, "
        "its timeline and its interviews. Reminders, offers, the files an application went "
        "out with and who it was shown to are not in it."
    ),
}


# ------------------------------------------------------------------------ the writers


def _company_row(company) -> dict:
    return {
        **export._fields(company, COMPANY_FIELDS),
        "parent": company.parent.name if company.parent_id else "",
        "industries": [industry.name for industry in company.industries.all()],
        "departments": [team.name for team in company.departments.all()],
        "identifiers": [
            {"scheme": i.scheme, "value": i.value, "label": i.label}
            for i in company.identifiers.all()
        ],
    }


def _contact_row(contact) -> dict:
    """The archive's block for one person, less its id, and with its company by name."""
    block = export._contact(contact)
    return {
        **{name: block[name] for name in CONTACT_FIELDS},
        "company": contact.company.name if contact.company_id else "",
        "department": block["department"],
        **{
            name: [{key: row[key] for key in fields if key in row} for row in block[name]]
            for name, fields in CONTACT_DETAILS.items()
        },
    }


def _listing_row(posting) -> dict:
    return export._fields(posting, LISTING_FIELDS)


def _listing_file_row(posting) -> dict:
    return {"company": posting.company.name, **_listing_row(posting)}


def _application_row(application) -> dict:
    posting = application.posting
    return {
        "company": posting.company.name,
        "listing": _listing_row(posting),
        **export._fields(application, APPLICATION_FIELDS),
        "department": application.department.name if application.department_id else "",
        "through_agency": (
            application.through_agency.name if application.through_agency_id else ""
        ),
        "events": [export._fields(event, EVENT_FIELDS) for event in application.events.all()],
        "interviews": [
            export._fields(interview, INTERVIEW_FIELDS)
            for interview in application.interviews.all()
        ],
    }


def _queryset(user, kind: str):
    from postulo.applications.models import Application
    from postulo.jobs.models import Company, Contact, JobPosting

    if kind == "companies":
        return (
            Company.objects.for_user(user)
            .select_related("parent")
            .prefetch_related("industries", "identifiers", "departments")
            .order_by("name_key")
        )
    if kind == "contacts":
        return (
            Contact.objects.for_user(user)
            .select_related("company", "department")
            .prefetch_related(*CONTACT_DETAILS)
            .order_by("name", "pk")
        )
    if kind == "listings":
        return JobPosting.objects.for_user(user).select_related("company").order_by("pk")
    return (
        Application.objects.for_user(user)
        .select_related("posting__company", "department", "through_agency")
        .prefetch_related("events", "interviews")
        .order_by("pk")
    )


_ROW = {
    "companies": _company_row,
    "contacts": _contact_row,
    "listings": _listing_file_row,
    "applications": _application_row,
}


def build_document(user, kind: str) -> dict:
    """One kind of record belonging to ``user``, as a document of its own."""
    return {
        "postulo": {
            f"{kind}_format": FORMATS[kind],
            "version": __version__,
            "exported_at": timezone.now().isoformat(),
            "note": NOTES[kind]
            + " Identifiers are not written: a record is the same as another by what it says.",
        },
        kind: [_ROW[kind](record) for record in _queryset(user, kind)],
    }


def counts(user) -> dict[str, int]:
    """How many of each kind a file would carry: one `COUNT(*)` each, as the archive's page."""
    return {kind: _queryset(user, kind).order_by().count() for kind in FORMATS}


def filename(user, kind: str) -> str:
    stamp = timezone.localdate().isoformat()
    return f"postulo-{kind}-{user.email.split('@')[0]}-{stamp}.json"


# ------------------------------------------------------------------------ the readers


def _not_one_of_these(kind: str) -> str:
    return _(
        "That is not a file this page reads. It reads the JSON file it offers to download "
        "for this kind of record, as Postulo writes it, and nothing else."
    )


def _prune(value, schema):
    """What is read of ``value`` under ``schema``, and nothing else. ``None`` for a misfit."""
    if schema == _S:
        return file_review.kept(value)
    if isinstance(schema, tuple):
        _tag, sub, cap = schema
        if not isinstance(value, list):
            return []
        rows = [_prune(item, sub) for item in value[:cap]]
        return [row for row in rows if row is not None]
    if not isinstance(value, dict):
        return None
    return {name: _prune(value[name], sub) for name, sub in schema.items() if name in value}


def read(kind: str, data: bytes, name: str = "") -> dict:
    """What is in the file, as something a session can hold. Raises `Refused` with why.

    Nothing here looks at the database and nothing is believed: this is the shape of the
    file and no more. The rows are cut to `MAX_ROWS` before any of them is looked at.
    """
    if not data:
        raise Refused(_("That file is empty."))
    if len(data) > MAX_BYTES:
        raise Refused(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // (1024 * 1024)}
        )
    try:
        document = json.loads(data.decode("utf-8-sig"), parse_constant=file_review.no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise Refused(_not_one_of_these(kind)) from error

    header = document.get("postulo") if isinstance(document, dict) else None
    if not isinstance(header, dict):
        raise Refused(_not_one_of_these(kind))
    marker = f"{kind}_format"
    version = header.get(marker)
    if version is None:
        if "format" in header:
            raise Refused(
                _(
                    "That is the export of a whole account. This page reads only the file "
                    "it offers to download itself, which holds one kind of record."
                )
            )
        if "candidate_format" in header:
            raise Refused(
                _(
                    "That is somebody's own record, details and career. It is read from "
                    "Your record as a file, under Your career."
                )
            )
        if any(other != marker and other in header for other in map(_marker, FORMATS)):
            raise Refused(
                _(
                    "That file holds another kind of record. Read it from the page of the "
                    "kind it holds."
                )
            )
        raise Refused(_not_one_of_these(kind))
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise Refused(_not_one_of_these(kind))

    rows = document.get(kind)
    if not isinstance(rows, list):
        raise Refused(_not_one_of_these(kind))
    kept = [_prune(row, SCHEMAS[kind]) for row in rows[:MAX_ROWS]]
    return {
        "kind": kind,
        "format": min(version, 2**31 - 1),
        "version": file_review.short(header.get("version"), 20),
        "exported_at": file_review.short(header.get("exported_at")),
        "filename": file_review.short(name, 80),
        "rows": [row for row in kept if row is not None],
        "unreadable": sum(1 for row in kept if row is None),
        "cut": max(len(rows) - MAX_ROWS, 0),
    }


def _marker(kind: str) -> str:
    return f"{kind}_format"


def is_empty(held: dict) -> bool:
    return not held.get("rows") and not held.get("unreadable")


def is_held(held, kind: str) -> bool:
    """Whether what a session holds is what `read` leaves there, for this kind."""
    return (
        isinstance(held, dict) and held.get("kind") == kind and isinstance(held.get("rows"), list)
    )


# ------------------------------------------------------------------------- validating

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FORMS: dict[tuple, type] = {}


def _form_for(model, names: tuple[str, ...]):
    key = (model, names)
    if key not in _FORMS:
        _FORMS[key] = forms.modelform_factory(model, fields=names)
    return _FORMS[key]


def _through_the_form(model, names: tuple[str, ...], row: dict) -> tuple[dict, list[str]]:
    """The model's own form, handed what the file says of ``names``: what it cleaned, and
    what was wrong. An absent key is what a blank form would hold; a key that holds
    something that is not text is wrong, whatever the form would have made of it."""
    form_class = _form_for(model, names)
    data = {}
    problems: list[str] = []
    for name in names:
        value = row.get(name)
        if isinstance(value, list):
            problems.append(_("%(field)s: this is not text.") % {"field": _label(model, name)})
            value = ""
        elif value is None:
            model_field = model._meta.get_field(name)
            value = model_field.get_default() if model_field.has_default() else ""
            if value is None:
                value = ""
        data[name] = value
    form = form_class(data)
    for name in names:
        if isinstance(form.fields[name], forms.DateField) and not isinstance(
            form.fields[name], forms.DateTimeField
        ):
            text = data[name]
            if isinstance(text, str) and text and not _ISO_DATE.match(text):
                problems.append(
                    _("%(field)s: this is not a date written as 2026-09-28.")
                    % {"field": _label(model, name)}
                )
                data[name] = ""
    if problems:
        return {}, problems
    if not form.is_valid():
        return {}, [
            f"{_label(model, name)}: {' '.join(messages)}"[:200]
            for name, messages in form.errors.items()
        ]
    return {name: form.cleaned_data[name] for name in names}, []


def _label(model, name: str) -> str:
    return str(model._meta.get_field(name).verbose_name).capitalize()


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _detail_rows(rows) -> list[dict]:
    """The ways of reaching somebody, as the restorers expect them: text where text is
    meant and a yes or no only for ``is_primary``. A number or a flag where a number's
    digits should be is not text, and is held as nothing rather than failing the import."""
    kept = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        kept.append(
            {
                key: (bool(value) if key == "is_primary" else _text(value, 500))
                for key, value in entry.items()
            }
        )
    return kept


# --------------------------------------------------------------------- the verdicts


@dataclass
class Verdict:
    outcome: str
    label: str = ""
    sub: str = ""
    start: dt.date | None = None
    end: dt.date | None = None
    notes: list[str] = field(default_factory=list)
    #: What `apply` makes of an `ADD`; nothing is looked up again from the file.
    clean: dict = field(default_factory=dict)


def _company_named(user, name: str):
    from postulo.jobs.models import Company

    return Company.objects.for_user(user).filter(name_key=slugs.name_key(name)).first()


def _company_name(row: dict) -> str:
    from postulo.jobs.models import Company

    return _text(row.get("company"), Company._meta.get_field("name").max_length)


class _Run:
    """What one pass over a file remembers: what it has already said, so a repeat is told."""

    def __init__(self, user):
        self.user = user
        self.seen: set = set()
        #: Companies this pass would make, by their key, so the page says so once.
        self.new_companies: set[str] = set()

    def company_note(self, name: str) -> list[str]:
        """The note for a row whose company is not in the account: it is made with the row."""
        if not name:
            return []
        key = slugs.name_key(name)
        if _company_named(self.user, name) is not None:
            return []
        self.new_companies.add(key)
        return [
            _("The company %(name)s is not in your account, and is added with it.") % {"name": name}
        ]


def _moment_date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value if isinstance(value, dt.date) else None


# -- companies


def _assess_company(run: _Run, row: dict) -> Verdict:
    from postulo.jobs.models import Company

    clean, problems = _through_the_form(Company, COMPANY_FIELDS, row)
    name = _text(row.get("name"), 200)
    if problems or not name:
        return Verdict(
            REFUSED, label=name or _("(no name)"), notes=problems or [_("It has no name.")]
        )
    key = slugs.name_key(name)
    verdict = Verdict(ADD, label=name, sub=clean.get("location", ""))
    if ("company", key) in run.seen:
        verdict.outcome = REPEATED
        return verdict
    run.seen.add(("company", key))
    if _company_named(run.user, name) is not None:
        verdict.outcome = PRESENT
        return verdict
    for entry in row.get("identifiers") or []:
        scheme, value = entry.get("scheme"), entry.get("value")
        if isinstance(scheme, str) and isinstance(value, str) and scheme != "other":
            if Company.by_identifier(run.user, scheme, value) is not None:
                verdict.outcome = PRESENT
                verdict.notes.append(_("A company with the same identifier is already yours."))
                return verdict
    verdict.clean = {
        "fields": {**clean, "name": name},
        "parent": _text(row.get("parent"), 200),
        "industries": [
            _text(name, 160) for name in row.get("industries") or [] if _text(name, 160)
        ],
        "departments": [
            _text(name, 120) for name in row.get("departments") or [] if _text(name, 120)
        ],
        "identifiers": [entry for entry in row.get("identifiers") or [] if isinstance(entry, dict)],
    }
    return verdict


def _create_company(user, verdict: Verdict, parents: dict) -> int:
    from postulo.core import identifiers as scheme_registry
    from postulo.jobs.models import Company, Department, Industry
    from postulo.jobs.services import set_identifiers

    held = verdict.clean
    company = Company.objects.create(owner=user, **held["fields"])
    if held["industries"]:
        company.industries.add(*Industry.named(user, held["industries"]))
    for name in held["departments"]:
        Department.objects.get_or_create(
            owner=user,
            company=company,
            name_key=slugs.name_key(name),
            defaults={"name": name},
        )
    for entry in held["identifiers"]:
        named, value = _text(entry.get("scheme"), 40), _text(entry.get("value"), 200)
        if not named or not value:
            continue
        scheme, label = scheme_registry.as_restored(
            named, _text(entry.get("label"), 120), scheme_registry.COMPANY, value=value
        )
        try:
            set_identifiers(company, [(scheme, value, label)])
        except ValidationError:
            # Already on another company here: the record is worth more than the id.
            continue
    if held["parent"]:
        parents[company.pk] = held["parent"]
    return 1


def _settle_parents(user, parents: dict) -> None:
    """The ownership tree, once every company in the file exists: a name that matches
    nothing is left alone, and a company is never made its own parent."""
    from postulo.jobs.models import Company

    for child_pk, name in parents.items():
        parent = _company_named(user, name)
        if parent is not None and parent.pk != child_pk:
            Company.objects.filter(pk=child_pk).update(parent=parent)


# -- contacts


def _assess_contact(run: _Run, row: dict) -> Verdict:
    from postulo.jobs.models import Contact

    clean, problems = _through_the_form(Contact, CONTACT_FIELDS, row)
    name = _text(row.get("name"), 200)
    company_name = _company_name(row)
    if problems or not name:
        return Verdict(
            REFUSED, label=name or _("(no name)"), notes=problems or [_("It has no name.")]
        )
    email = clean.get("email", "").strip()
    key = (
        ("email", email.casefold())
        if email
        else ("name", name.casefold(), slugs.name_key(company_name))
    )
    verdict = Verdict(
        ADD, label=name, sub=" · ".join(p for p in (clean.get("role"), company_name) if p)
    )
    if ("contact", *key) in run.seen:
        verdict.outcome = REPEATED
        return verdict
    run.seen.add(("contact", *key))

    mine = Contact.objects.for_user(run.user)
    if email:
        found = mine.filter(email__iexact=email).exists()
    else:
        company = _company_named(run.user, company_name) if company_name else None
        if company_name and company is None:
            found = False
        else:
            found = mine.filter(name__iexact=name, company=company).exists()
    if found:
        verdict.outcome = PRESENT
        return verdict
    verdict.notes += run.company_note(company_name)
    verdict.clean = {
        "fields": {**clean, "name": name},
        "company": company_name,
        "department": _text(row.get("department"), 120),
        "details": {block: _detail_rows(row.get(block)) for block in CONTACT_DETAILS},
    }
    return verdict


def _create_contact(user, verdict: Verdict) -> int:
    from postulo.applications.services import get_or_create_company
    from postulo.core import importer, phone_numbers
    from postulo.jobs.models import Contact, Department

    held = verdict.clean
    company = get_or_create_company(user, held["company"]) if held["company"] else None
    contact = Contact.objects.create(owner=user, company=company, **held["fields"])
    if held["department"] and company is not None:
        department, _made = Department.objects.get_or_create(
            owner=user,
            company=company,
            name_key=slugs.name_key(held["department"]),
            defaults={"name": held["department"]},
        )
        contact.department = department
        contact.save(update_fields=["department", "updated_at"])
    details = held["details"]
    # A number somebody else on this instance holds is left off, and what is told about it
    # is what a form would tell, and no more often (#142): it is asked through the same
    # allowance, and nothing is said of it here.
    numbers = [
        row
        for row in importer._phone_rows({"phone_numbers": details["phone_numbers"]})
        if not phone_numbers.refusal(user, _text(row.get("number"), 40))
    ]
    importer._restore_phone_numbers(contact, user, numbers)
    importer._restore_postal_addresses(
        contact, user, importer._address_rows({"postal_addresses": details["postal_addresses"]})
    )
    importer._restore_web_links(
        contact, user, importer._link_rows({"web_links": details["web_links"]})
    )
    importer._restore_messaging_handles(
        contact, user, importer._messaging_rows({"messaging_handles": details["messaging_handles"]})
    )
    return 1


# -- listings and applications


def _find_listing(user, company_name: str, clean: dict):
    """The posting this account already holds that the file's says the same of: by its
    address where it has one, else its company, its title and its date (`posted_at`)."""
    from postulo.jobs.models import JobPosting

    mine = JobPosting.objects.for_user(user)
    key = same_url(clean.get("url") or "")
    if key:
        return mine.filter(url_key=key).first()
    company = _company_named(user, company_name)
    if company is None:
        return None
    return mine.filter(
        company=company, title__iexact=clean.get("title", ""), posted_at=clean.get("posted_at")
    ).first()


def _listing_key(company_name: str, clean: dict) -> tuple:
    url = same_url(clean.get("url") or "")
    if url:
        return ("url", url)
    return (
        "title",
        slugs.name_key(company_name),
        clean.get("title", "").casefold(),
        clean.get("posted_at"),
    )


def _clean_listing(row: dict) -> tuple[dict, list[str]]:
    from postulo.jobs.models import DiscardReason, JobPosting, ListingState

    names = tuple(n for n in LISTING_FIELDS if n not in ("state", "discard_reason"))
    clean, problems = _through_the_form(JobPosting, names, row)
    if not problems and not clean.get("title", "").strip():
        problems.append(_("It has no title."))
    state = row.get("state")
    clean["state"] = state if state in ListingState.values else ListingState.NEW
    reason = row.get("discard_reason")
    clean["discard_reason"] = reason if reason in DiscardReason.values else ""
    return clean, problems


def _assess_listing(run: _Run, row: dict) -> Verdict:
    company_name = _company_name(row)
    clean, problems = _clean_listing(row)
    title = _text(row.get("title"), 250)
    if not company_name:
        problems = [_("It has no company."), *problems]
    if problems:
        return Verdict(REFUSED, label=title or _("(no title)"), sub=company_name, notes=problems)
    verdict = Verdict(ADD, label=clean["title"], sub=company_name, start=clean.get("posted_at"))
    key = ("listing", *_listing_key(company_name, clean))
    if key in run.seen:
        verdict.outcome = REPEATED
        return verdict
    run.seen.add(key)
    if _find_listing(run.user, company_name, clean) is not None:
        verdict.outcome = PRESENT
        return verdict
    verdict.notes += run.company_note(company_name)
    verdict.clean = {"company": company_name, "fields": clean}
    return verdict


def _create_listing(user, verdict: Verdict) -> int:
    from postulo.applications.services import create_listing, get_or_create_company

    held = verdict.clean
    company = get_or_create_company(user, held["company"])
    create_listing(user, company=company, posting_data=held["fields"])
    return 1


def _assess_application(run: _Run, row: dict) -> Verdict:
    from postulo.applications.models import Application, ApplicationEvent, Interview

    company_name = _company_name(row)
    listing_row = row.get("listing") if isinstance(row.get("listing"), dict) else {}
    listing, problems = _clean_listing(listing_row)
    clean, more = _through_the_form(Application, APPLICATION_FIELDS, row)
    problems += more
    if not company_name:
        problems = [_("It has no company."), *problems]
    if problems:
        return Verdict(
            REFUSED,
            label=_text(listing_row.get("title"), 250) or _("(no title)"),
            sub=company_name,
            notes=problems,
        )
    applied = _moment_date(clean.get("applied_at"))
    verdict = Verdict(ADD, label=listing["title"], sub=company_name, start=applied)
    key = ("application", *_listing_key(company_name, listing), applied)
    if key in run.seen:
        verdict.outcome = REPEATED
        return verdict
    run.seen.add(key)

    posting = _find_listing(run.user, company_name, listing)
    if posting is not None:
        for existing in Application.objects.for_user(run.user).filter(posting=posting):
            if _moment_date(existing.applied_at) == applied:
                verdict.outcome = PRESENT
                return verdict
        verdict.notes.append(_("The listing is already yours; the application is added to it."))
    else:
        verdict.notes += run.company_note(company_name)

    events, left_out = [], 0
    for entry in row.get("events") or []:
        done, bad = _through_the_form(ApplicationEvent, EVENT_FIELDS, entry)
        if bad:
            left_out += 1
        else:
            events.append(done)
    interviews = []
    for entry in row.get("interviews") or []:
        names = ("kind", "starts_at", "ends_at", "location", "notes", "outcome")
        done, bad = _through_the_form(Interview, names, entry)
        if bad or done["ends_at"] < done["starts_at"]:
            left_out += 1
        else:
            interviews.append(done)
    if left_out:
        verdict.notes.append(
            ngettext(
                "One entry of its timeline or one interview cannot be added, and is left out.",
                "%(count)s entries of its timeline or interviews cannot be added, and are "
                "left out.",
                left_out,
            )
            % {"count": left_out}
        )
    verdict.clean = {
        "company": company_name,
        "listing": listing,
        "fields": clean,
        "department": _text(row.get("department"), 120),
        "through_agency": _text(row.get("through_agency"), 200),
        "events": events,
        "interviews": interviews,
    }
    return verdict


def _create_application(user, verdict: Verdict, provenance: str) -> int:
    from postulo.applications.models import (
        Application,
        ApplicationEvent,
        EventKind,
        Interview,
    )
    from postulo.applications.services import get_or_create_company, record_event
    from postulo.jobs.models import Department, JobPosting

    held = verdict.clean
    company = get_or_create_company(user, held["company"])
    posting = _find_listing(user, held["company"], held["listing"])
    if posting is None:
        posting = JobPosting.objects.create(owner=user, company=company, **held["listing"])
    if posting.decided_at is None:
        posting.decided_at = timezone.now()
        posting.save(update_fields=["decided_at", "updated_at"])
    application = Application.objects.create(owner=user, posting=posting, **held["fields"])
    if held["department"]:
        department = (
            Department.objects.for_user(user)
            .filter(company=company, name_key=slugs.name_key(held["department"]))
            .first()
        )
        if department is not None:
            Application.objects.filter(pk=application.pk).update(department=department)
    if held["through_agency"]:
        agency = _company_named(user, held["through_agency"])
        if agency is not None:
            Application.objects.filter(pk=application.pk).update(through_agency=agency)
    for event in held["events"]:
        ApplicationEvent.objects.create(application=application, **event)
    for interview in held["interviews"]:
        Interview.objects.create(owner=user, application=application, **interview)
    # Every imported application says which file it came from, as the spreadsheet import's
    # does, so provenance is never in doubt.
    record_event(
        application,
        kind=EventKind.OTHER,
        summary=provenance,
        occurred_at=application.applied_at,
        actor=provenance,
    )
    return 1


_ASSESS = {
    "companies": _assess_company,
    "contacts": _assess_contact,
    "listings": _assess_listing,
    "applications": _assess_application,
}


# ---------------------------------------------------------------------------- the plan

TITLES = {
    "companies": gettext_lazy("Companies"),
    "contacts": gettext_lazy("Contacts"),
    "listings": gettext_lazy("Listings"),
    "applications": gettext_lazy("Applications"),
}


def _verdicts(user, held: dict) -> list[Verdict]:
    run = _Run(user)
    assess = _ASSESS[held["kind"]]
    return [assess(run, row) for row in held["rows"]]


def plan(user, held: dict) -> dict:
    """What adding the held file would do to this account as it stands. Writes nothing."""
    kind = held["kind"]
    verdicts = _verdicts(user, held)
    tally = Counter(verdict.outcome for verdict in verdicts)
    notes = []
    if held.get("cut"):
        notes.append(
            _("The file holds more than %(limit)s rows. The first %(limit)s are read.")
            % {"limit": MAX_ROWS}
        )
    if held.get("unreadable"):
        notes.append(
            ngettext(
                "One row is not a record, and is not read.",
                "%(count)s rows are not records, and are not read.",
                held["unreadable"],
            )
            % {"count": held["unreadable"]}
        )
    if held.get("format", 1) > FORMATS[kind]:
        notes.append(
            _(
                "This file was written by a newer Postulo than this one. What this one "
                "knows of it is read, and the rest is not."
            )
        )
    return {
        "kind": kind,
        "version": held.get("version", ""),
        "exported_at": _moment(held.get("exported_at")),
        "summary": [
            (str(file_review.OUTCOMES[outcome]), tally[outcome])
            for outcome in (ADD, PRESENT, REPEATED, REFUSED)
            if tally[outcome]
        ],
        "notes": notes,
        "adds": bool(tally[ADD]),
        "sections": [
            {
                "key": kind,
                "title": TITLES[kind],
                "rows": [
                    {
                        "label": verdict.label,
                        "sub": verdict.sub,
                        "start": verdict.start,
                        "end": verdict.end,
                        "outcome": verdict.outcome,
                        "outcome_label": file_review.OUTCOMES[verdict.outcome],
                        "notes": verdict.notes,
                    }
                    for verdict in verdicts
                ],
            }
        ]
        if verdicts
        else [],
    }


def _moment(text):
    from django.utils.dateparse import parse_datetime

    return parse_datetime(text) if isinstance(text, str) and text else None


@dataclass
class Report:
    added: int = 0


def apply(user, held: dict) -> Report:
    """Add what is new, in one transaction, and nothing else. The plan is worked out again
    against the account as it is now: what was shown is not what is trusted."""
    kind = held["kind"]
    report = Report()
    provenance = str(_("Imported from %(file)s") % {"file": held.get("filename") or kind})[:120]
    parents: dict = {}
    with transaction.atomic():
        for verdict in _verdicts(user, held):
            if verdict.outcome != ADD:
                continue
            if kind == "companies":
                report.added += _create_company(user, verdict, parents)
            elif kind == "contacts":
                report.added += _create_contact(user, verdict)
            elif kind == "listings":
                report.added += _create_listing(user, verdict)
            else:
                report.added += _create_application(user, verdict, provenance)
        if parents:
            _settle_parents(user, parents)
    return report
