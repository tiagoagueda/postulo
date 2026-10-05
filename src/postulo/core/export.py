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
import logging
import zipfile
from io import BytesIO
from typing import Any

from django.db import transaction
from django.utils import timezone

from postulo import __version__

logger = logging.getLogger(__name__)

#: Bumped when the shape changes in a way an importer must notice. 2 added the listing
#: state and dates on postings and the listing a capture became; 3 added interviews under
#: each application, the ``actor`` on events, the table layout on the profile, and a list
#: of ``industries`` on a company where there was one ``industry`` string; 4 replaced the
#: single ``phone`` string on a profile and on a contact with a ``phone_numbers`` list;
#: 5 added ``parent`` on a company, naming the company it belongs to; 9 added
#: ``postal_addresses`` on a profile and on a contact, which had nowhere to go before
#: (#92); 10 replaced ``cv_id``/``cover_letter_id`` on a sent document with a
#: ``source_kind`` and a ``source_ref``, so that a new kind of document is not a new
#: column (#130). The importer still reads every earlier format, filling the new fields in.
#: 13 added ``department`` and ``department_company`` on an application, naming which
#: part of an employer the attempt was aimed at (#138); 14 added ``kind`` on a CV, which
#: is what tells a portfolio from a CV (#133). 15 replaced ``website``, ``linkedin_url``
#: and ``source_repo_url`` on a profile and ``linkedin_url`` on a contact with a
#: ``web_links`` list, each row a kind, an address and whether it is the primary of its
#: kind (#189). 16 added ``kind`` on a company, which tells the employment service a
#: person is registered with from an employer (#202), and ``show_career_order`` on the
#: profile (#203). 17 added ``language`` on an upload and on a sent document, which each
#: now record what they are in rather than having it guessed for them (#283). 18 added
#: ``isco_code`` on a posting, the ISCO-08 unit group its title matches in the ESCO
#: classification beside the title itself (#266). 19 added ``plain_text`` on a sent
#: document: the words of a CV without the page they were set in, which is what two
#: versions are compared by (#236). 20 added ``end_reason`` on a timeline entry, which
#: is why the application it ended did; ``referred_by_id`` and ``through_agency`` on an
#: application; and a ``contacts`` list at the top of the file for the people recorded at
#: no company, who had been left out of the archive because every contact in it was
#: written under one (#239). 21 added ``page`` on a capture: what it kept of the page it
#: was read from, as the names of two files in the media folder and what each is, or
#: nothing where nothing was kept (#256). 22 added ``nav_order`` and ``hidden_nav_items``
#: on the profile: the main navigation as the person arranged it, the order and what they
#: switched off, which had never travelled with the account (#299). 23 added ``events`` on a
#: posting: its history, each entry with who it came from as a contact's id in this file
#: and what it points at as an ``artefact_kind`` and an ``artefact_ref`` -- a capture or an
#: upload, by its id here (#270). 24 added ``esco_uri`` on a skill, the ESCO skill its
#: name matches in the classification, beside the name itself (#266). 25 added
#: ``remembered_places`` at the top of the file: where the person's own corrections showed
#: a field to be on a site, and how many reviews in a row each place has been wrong (#267).
#: 26 added ``form_of_address`` and ``pronouns`` on the profile, each the text somebody
#: chose or typed; an archive without them restores both blank (#309). 27 added ``prints``
#: on a CV: which of the profile's details it prints, kind by kind, with a chosen row named
#: by what it says -- a number, an address, a scheme and its value, and for an identifier of
#: the scheme *other* its name as well -- because the profile's rows carry no id in this
#: file; an archive without it restores every choice at its default, which is what every CV
#: printed before there was one (#308). 28 writes every language code as a BCP 47 tag in
#: its canonical form -- ``pt-BR`` where an archive used to say ``pt-br`` -- and Serbian as
#: ``sr-Cyrl``, which is what the list calls it now. No field moved: an older archive is
#: read as it was written, and its codes are respelt on the way in (#337). 29 added
#: ``service`` on a web link, the key of the service the address is on, blank for
#: *Other*; an archive without it has each link's service worked out from its address,
#: and a key this instance does not know restores as *Other* (#305). 30 added the rest of
#: the profile's preferences -- ``keyboard_shortcuts``, ``nav_underline``, ``density``,
#: ``plugins_off``, ``keep_page_source``, ``keep_page_rendering`` and
#: ``closing_notice_days`` -- and made the importer restore a field the file names whatever
#: its value, so a switch set off and a dashboard cleared come back as they were (#464). An
#: archive without them restores each at its default.
#: 31 added ``reminders`` at the top of the file: the reminders about no application,
#: which used to travel only inside the application they were about and so were in no
#: archive (#334).
#: 32 added ``sent_upload_ids`` on an application, the files it went out with, and
#: ``sent_to`` on a sent document, the words saying where it went; an archive without them
#: restores both empty, as they were (#469).
#: 33 added ``interview_id`` on an event: the interview settling wrote it for, by its id in
#: this file; an archive without it restores every entry untied (#448).
#: 34 added ``birth_date``, ``birth_place`` and ``birth_country`` on the profile: when and where
#: the person was born, as an ISO 8601 reduced date, the town typed and a country's code,
#: each read back through the column's own rule; and ``birth_date`` and ``birth_place`` among
#: a CV's ``prints``. An archive without them restores each blank and off (#679).
#: 35 added ``nationalities`` and ``nationality_scope`` on the profile: the countries somebody
#: is a citizen of as a list of two-letter codes, or only whether they are a citizen of the EU,
#: the EEA or Switzerland, each read back through the one rule the page is held to; and
#: ``nationality`` among a CV's ``prints``. An archive without them restores both blank and off
#: (#680).
#: 36 added ``gender`` on the profile: the word somebody gave or typed, as text, read back
#: through the column's own bounds; and ``gender`` among a CV's ``prints``. An archive without
#: them restores it blank and off (#681).
#: 37 added the preference ``show_key_hints``: whether the key badges are drawn; an
#: archive without it restores it on, the default (#658).
#: 38 added ``identifier_order`` and ``hidden_identifiers`` on the profile: which identifier
#: schemes the person sees and in what order, read back through the same filter as the
#: navigation's pair (#672).
#: 39 added ``save_as_you_go`` to the profile: a preference, so a restore keeps it (#656).
#: 40 added ``description_format`` on a listing, *plain* or *markdown*; an archive without
#: it restores every description as plain text, as it was written (#665).
#: 41 added ``messaging_handles`` on the profile and on each contact: how somebody is
#: reached on Matrix, XMPP, Signal, Telegram, Threema or another service, each with its
#: ``service`` key (blank for *Other*), its ``label``, its ``handle`` and whether it is
#: the primary; an archive without them restores none (#682).
FORMAT_VERSION = 41

#: The version of the *candidate* document: one person's own record and nothing else (#181).
#:
#: A number of its own rather than `FORMAT_VERSION`, because it is a different document with
#: a different reader, and the two change for different reasons: eighteen revisions of the
#: whole-account archive were mostly about postings, applications and documents, none of
#: which this carries. What it does carry is **the same blocks, built by the same code** --
#: `_profile_block`, `_identifier_rows` and `_resume_block` below -- so there is one spelling
#: of a person's career and not two to keep in step. Bump this when any of those blocks
#: changes shape; `tests/test_candidate_file.py` holds a fingerprint of them and fails until
#: somebody has decided.
#:
#: It is written under its own key, `candidate_format`, which is the marker: a document that
#: has it is a candidate file, and the archive's importer, which looks for `format`, refuses
#: one without being taught to.
#:
#: 2 added ``esco_uri`` on a skill (#266), which is written and never read back: it follows
#: the name, and the importing side works it out again from the name it is given. 3 added
#: ``form_of_address`` and ``pronouns`` on the profile, read back as the other details are:
#: filled where blank, kept where not (#309). 4 added ``service`` on a web link (#305),
#: read back as a claim: kept where the importing side offers that service and the address
#: is one of its addresses, *Other* otherwise, and worked out from the address's host in a
#: file that says nothing. 5 added ``birth_date``, ``birth_place`` and ``birth_country`` on the
#: profile, each read back as the other details are -- filled where blank, kept where not --
#: and a date only through the column's own rule, so a malformed one is a refused row (#679).
#: 6 added ``nationalities`` and ``nationality_scope`` on the profile: each listed code is
#: read back as a row of its own, added or already there, and the scope only while there is
#: no list (#680).
#: 7 added ``gender`` on the profile, read back as the form of address is: filled where blank,
#: kept where not (#681).
#: 8 added ``messaging_handles`` on the profile, read back as a
#: claim as a link's service is: a handle is kept under the service the importing side
#: knows and the handle is one of its handles, and under *Other* otherwise (#682).
CANDIDATE_FORMAT = 8

MANIFEST_NAME = "postulo.json"
MEDIA_PREFIX = "media/"

# What is written for each kind of record. Declared here so the assembly below reads as
# the shape of the document rather than as a wall of field names.
PROFILE_FIELDS = (
    # Before the name, and apart from it (#309): stored as the text, so they travel as it.
    "form_of_address",
    "pronouns",
    # The most identifying things a profile holds, optional and printed only where a CV says so
    # (#679): the date as its ISO 8601 reduced form, the place as typed and its country's code.
    "birth_date",
    "birth_place",
    "birth_country",
    # What the person is a citizen of (#680): the list of codes, and the scope that stands in
    # for it while there is none. Both are read back through `core.personal`.
    "nationalities",
    "nationality_scope",
    # In the person's own words, as the text itself (#681), like the two before the name.
    "gender",
    "headline",
    # As typed. A blank is a blank here, not the address's town and country: that is worked
    # out when it is printed, and writing it in would pin it to today's primary address.
    "location",
    "language",
    "record_language",
    "time_zone",
    "theme",
    "table_settings",
    # An arrangement belongs to the account, so it travels with the account (#123). The
    # seen set goes too: without it a restore would announce every widget in Postulo as
    # new to somebody who has been reading their own dashboard for a year.
    "dashboard_widgets",
    "dashboard_known",
    # The main navigation as it was arranged (#299): the keys placed, in order, and the ones
    # switched off. Both are read back through `navigation.known_keys`, so an archive that
    # names an item this instance does not have is passed over rather than trusted.
    "nav_order",
    "hidden_nav_items",
    # The identifiers' arrangement (#672), read back through `identifier_order.known_keys`.
    "identifier_order",
    "hidden_identifiers",
    "quiet_after_days",
    "use_gravatar",
    "show_career_order",
    # The rest of how the account behaves (#464). Each is a choice somebody made, and two
    # are privacy choices (what a capture keeps) or an accessibility one (single-key
    # shortcuts, WCAG 2.1.4): a restore that quietly reset them would undo the choice.
    "keyboard_shortcuts",
    "show_key_hints",
    "nav_underline",
    "density",
    "plugins_off",
    "keep_page_source",
    "keep_page_rendering",
    "closing_notice_days",
    "save_as_you_go",
)
#: What the candidate document takes from the profile: what a CV prints, and the language
#: the career is written in, which its translations are translations *from*. Everything
#: else in `PROFILE_FIELDS` is how Postulo behaves for one account on one instance -- a
#: theme, a dashboard, a time zone -- and is not the candidate's to carry anywhere (#181).
#: The form of address and the pronouns go with the name they belong beside (#309).
CANDIDATE_PROFILE_FIELDS = (
    "form_of_address",
    "pronouns",
    "birth_date",
    "birth_place",
    "birth_country",
    "nationalities",
    "nationality_scope",
    "gender",
    "headline",
    "location",
    "record_language",
)
#: Which block of the archive a career entry's translations point into, by model name.
#: The same map the importer reads the other way round, kept here because this is where the
#: block names are decided (#131).
TRANSLATION_SECTIONS = {
    "experience": "experience",
    "education": "education",
    "project": "projects",
    "skillgroup": "skill_groups",
    "skill": "skills",
    "certification": "certifications",
    "languageskill": "languages",
    "link": "links",
}
TAG_FIELDS = ("id", "name", "slug", "colour", "icon")
COMPANY_FIELDS = (
    "id",
    "name",
    "kind",
    "website",
    "careers_url",
    "location",
    "notes",
    "logo_source",
    "logo_source_url",
    "logo_fetched_at",
    "created_at",
)
CONTACT_FIELDS = ("id", "name", "role", "email", "notes")
#: A contact's team, written as a name beside them rather than as a table of its own: a
#: department belongs to one company, so the company's block is where it can be resolved.
#: What one telephone number is, in the file. Every number a holder has, in order, with
#: the primary marked -- not the primary alone.
PHONE_NUMBER_FIELDS = ("kind", "label", "number", "is_primary", "verified_at", "is_recovery")
#: A messaging handle has no verification either, and is never a way back in (#682).
MESSAGING_FIELDS = ("service", "label", "handle", "is_primary")
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
#: What one address on the web is, in the file: every link a holder has, of every kind, in
#: order, with the primary of each kind marked -- not the primaries alone (#189). The
#: service is the key of the one the address is on, and blank for *Other* (#305).
WEB_LINK_FIELDS = ("kind", "service", "label", "url", "is_primary")
POSTING_FIELDS = (
    "id",
    "title",
    "isco_code",
    "location",
    "remote_type",
    "employment_type",
    "url",
    "source",
    "description",
    "description_format",
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
    # Who referred the person, as a contact's id in this file (#239). The agency beside it
    # is written by name in the assembly below, as a company's parent is.
    "referred_by_id",
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
    # Why the application ended, on the entry that ended it (#239). Here rather than on
    # the application because here is where it is: the log is the account of what happened.
    "end_reason",
    # The interview settling wrote it for, by its id in this file (#448).
    "interview_id",
    "actor",
    "created_at",
)
#: One entry in a listing's history (#270). Who it came from is a contact's id in this file,
#: as an application's contact is; what it points at is written beside it by kind and id.
LISTING_EVENT_FIELDS = (
    "id",
    "kind",
    "occurred_at",
    "summary",
    "body",
    "contact_id",
    "external_id",
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
OFFER_FIELDS = (
    "id",
    "base_amount",
    "currency",
    "period",
    "variable_pay",
    "equity",
    "benefits",
    "location",
    "holidays",
    "starts_on",
    "answer_by",
    "notes",
    "reminder_id",
    "created_at",
)
CV_FIELDS = (
    "id",
    "name",
    # A CV or a portfolio: the same model and the same machinery, told apart by this (#133).
    # Written by name, so an archive from before format 14 restores as a CV, which is what
    # every one of them was.
    "kind",
    "headline",
    "summary",
    "theme",
    "language",
    "show_contact_details",
)
LETTER_FIELDS = ("id", "name", "kind", "subject", "body", "theme", "is_template", "language")
UPLOAD_FIELDS = (
    "id",
    "title",
    "kind",
    "notes",
    "version",
    "replaces_id",
    "created_at",
    # Format 17 (#283). An archive written before it simply has no key, and the importer
    # builds the row from what is there, so an older one restores with the field blank --
    # which is what blank means here anyway: nobody has said.
    "language",
)
SENT_FIELDS = (
    "id",
    "title",
    "kind",
    "application_id",
    "checksum",
    "rendered_at",
    "language",
    # Where it went, kept as text so a sent PDF can still be placed after its application
    # is deleted (#469). Absent from an archive written before format 32.
    "sent_to",
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

#: The career, block by block: what each is called in the file and what is written for each
#: entry. Read by both documents -- the whole account's and the candidate's -- and by the
#: candidate file's reader, so that a field added here is a field all three know about.
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
    # The ESCO skill the name matches, beside the name (#266): written for whoever reads the
    # file, and never read back -- the importers work it out again from the name.
    "skills": ("id", "name", "esco_uri", "group_id", "order"),
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
    "links": (
        "id",
        "title",
        "url",
        "kind",
        "description",
        "order",
        "check_status",
        "check_detail",
        "checked_at",
    ),
}
#: Which model each of those blocks is read from, by name: `postulo.resume.models` cannot
#: be imported while this module is, and a name is all the builder needs.
RESUME_MODELS = {
    "experience": "Experience",
    "education": "Education",
    "projects": "Project",
    "skill_groups": "SkillGroup",
    "skills": "Skill",
    "certifications": "Certification",
    "languages": "LanguageSkill",
    "links": "Link",
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


def _web_links(holder) -> list[dict]:
    """Every link this holder has, of every kind. Same rule as the numbers above."""
    return [_fields(row, WEB_LINK_FIELDS) for row in holder.web_links.all()]


def _messaging_handles(holder) -> list[dict]:
    """Every handle this holder has. Same rule as the numbers above: what is recorded, not
    what the interface is showing."""
    return [_fields(row, MESSAGING_FIELDS) for row in holder.messaging_handles.all()]


def _profile_block(profile, names: tuple[str, ...] = PROFILE_FIELDS) -> dict:
    """The profile and the rows that hang off it, or nothing for an account without one.

    ``names`` is which of the profile's own columns to write. The whole-account archive
    writes all of them; the candidate document writes the ones that are about the person
    rather than about the account (#181). The numbers, the addresses and the links are the
    same rows in both.
    """
    if not profile:
        return {}
    return {
        **_fields(profile, names),
        "phone_numbers": _phone_numbers(profile),
        "postal_addresses": _postal_addresses(profile),
        "web_links": _web_links(profile),
        "messaging_handles": _messaging_handles(profile),
    }


def _identifier_rows(profile) -> list[dict]:
    """An ORCID is one of the few things in here that means the same to somebody else's
    software, so it travels with the rest."""
    if not profile:
        return []
    return [
        {"scheme": row.scheme, "value": row.value, "label": row.label}
        for row in profile.identifiers.all()
    ]


def _resume_block(user) -> dict:
    """The career record: every entry of every kind, and what they say in other languages."""
    from postulo.resume import models as resume

    block: dict[str, Any] = {
        key: [
            _fields(item, names)
            for item in getattr(resume, RESUME_MODELS[key]).objects.for_user(user)
        ]
        for key, names in RESUME_FIELDS.items()
    }
    # What those entries say in other languages. A section name and a local id rather than a
    # content type: a content type is this instance's row number for a model and means
    # nothing in the file, whereas "experience #3" is resolvable anywhere (#131).
    block["translations"] = [
        {
            "section": TRANSLATION_SECTIONS[row.content_type.model],
            "ref": row.object_id,
            "language": row.language,
            "field": row.field,
            "text": row.text,
        }
        for row in resume.Translation.objects.for_user(user).select_related("content_type")
        if row.content_type.model in TRANSLATION_SECTIONS and row.text.strip()
    ]
    return block


def _contact(contact) -> dict:
    """One person, with everything recorded about how to reach them."""
    return {
        **_fields(contact, CONTACT_FIELDS),
        "department": contact.department.name if contact.department_id else "",
        "phone_numbers": _phone_numbers(contact),
        "postal_addresses": _postal_addresses(contact),
        "web_links": _web_links(contact),
        "messaging_handles": _messaging_handles(contact),
    }


def _plugin_data(user) -> dict:
    from postulo.plugins import data

    try:
        return data.export_sections(user)
    except Exception:  # pragma: no cover - an archive is worth more than a tidy section
        # Each plugin is already asked inside its own guard; this is for the walk itself
        # failing, and it is said rather than only marked in the archive (#371).
        logger.exception("The plugins' section of the archive could not be built")
        return {"carried": {}, "not_carried": ["unknown"]}


def _kept_page(capture) -> dict | None:
    """What a capture kept of its page, as the archive names it; or nothing (#256).

    **Names, sizes and checksums, and none of the page.** The source is a stranger's
    markup, and the manifest is a document other programs read: so the manifest says where
    the file is and what it should hash to, and the text itself travels as the file it
    already is -- gzipped, under a name ending ``.txt.gz``, which nothing opens as a page.
    Every value here is one Postulo made: the names are generated, the kind is one of four,
    and who drew it is one of two.
    """
    page = capture.kept_page
    if page is None:
        return None
    return {
        "source_file": f"{MEDIA_PREFIX}{page.source.name}" if page.source else "",
        "source_size": page.source_size if page.source else 0,
        "source_checksum": page.source_checksum if page.source else "",
        "rendering_file": f"{MEDIA_PREFIX}{page.rendering.name}" if page.rendering else "",
        "rendering_type": page.rendering_type if page.rendering else "",
        "rendering_size": page.rendering_size if page.rendering else 0,
        "rendering_checksum": page.rendering_checksum if page.rendering else "",
        "rendered_by": page.rendered_by if page.rendering else "",
        "kept_at": _value(page.created_at),
    }


def _listing_event(event) -> dict:
    """One entry in a listing's history, and what it points at by kind and local id (#270).

    What it points at is a capture or an upload, each of which the archive carries under
    its own id; a kind and an id rather than a content type, which is this instance's row
    number for a model and means nothing in the file -- the rule `_source_of` follows for a
    sent document. An entry whose capture or file has gone points at nothing and keeps its
    words, as it does here.
    """
    from django.contrib.contenttypes.models import ContentType

    pointed = event.artefact_type_id and event.artefact_id
    return {
        **_fields(event, LISTING_EVENT_FIELDS),
        "artefact_kind": (
            ContentType.objects.get_for_id(event.artefact_type_id).model if pointed else ""
        ),
        "artefact_ref": event.artefact_id if pointed else None,
    }


def counts(user) -> dict[str, int]:
    """How many of each kind of record an export would carry, without building one.

    The two pages that say what is in an archive — *Export everything*, and the one that
    asks whether you really mean to delete your account — used to answer by assembling the
    whole document and measuring its lists: every record the account owns, read, nested and
    turned into JSON, so that eight numbers could be printed. On SQLite that was done while
    holding the write lock, which is #220 in a single page.

    Ten `COUNT(*)` queries instead. They agree with the document's own counts because
    everything here is filtered by owner exactly as the document's queries are, and
    `tests/test_export.py` holds the two together.
    """
    from postulo.applications.models import Application, Interview
    from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument
    from postulo.jobs.models import Capture, CapturedPage, Company, FieldHint, ListingEvent

    return {
        # The pages captures kept, which are files in the archive like any other (#256).
        "captured_pages": CapturedPage.objects.for_user(user).count(),
        # Where the person's corrections showed a field to be, per site (#267).
        "remembered_places": FieldHint.objects.for_user(user).count(),
        "companies": Company.objects.for_user(user).count(),
        # What arrived about each listing (#270): carried under its posting, and gone
        # with the account like the rest.
        "listing_events": ListingEvent.objects.for_user(user).count(),
        "applications": Application.objects.for_user(user).count(),
        "interviews": Interview.objects.for_user(user).count(),
        "cvs": CV.objects.for_user(user).count(),
        "cover_letters": CoverLetter.objects.for_user(user).count(),
        "uploads": UploadedDocument.objects.for_user(user).count(),
        "sent_documents": RenderedDocument.objects.for_user(user).count(),
        "captures": Capture.objects.for_user(user).count(),
    }


def build_document(user) -> dict:
    """Assemble everything belonging to ``user`` as one nested document."""
    from postulo.accounts.models import Profile
    from postulo.applications.models import Reminder
    from postulo.core.models import Tag
    from postulo.documents import printing
    from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument
    from postulo.jobs.models import Capture, Company, Contact, FieldHint

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
            "profile": _profile_block(profile),
            "identifiers": _identifier_rows(profile),
            # The uploaded picture travels with the files; a Gravatar copy is refetched.
            "avatar_file": (
                f"{MEDIA_PREFIX}{profile.avatar.name}" if profile and profile.avatar else ""
            ),
        },
        "tags": [_fields(tag, TAG_FIELDS) for tag in Tag.objects.for_user(user)],
        # The career, built by the function the candidate document calls too, so there is
        # one spelling of it and not two to keep in step (#181).
        "resume": _resume_block(user),
        # The people recorded at no company (#239). Every contact in the file used to be
        # written under its company, so somebody at none -- a friend, a former colleague,
        # exactly who refers people -- was in the account and not in the archive.
        "contacts": [
            _contact(contact)
            for contact in Contact.objects.for_user(user).filter(company__isnull=True)
        ],
        # The reminders about no application (#334). Every other reminder is written under
        # the application it is about, so one about none was in the account and not in
        # the archive.
        "reminders": [
            _fields(reminder, REMINDER_FIELDS)
            for reminder in Reminder.objects.for_user(user).filter(application__isnull=True)
        ],
        "companies": [],
        "documents": {},
        "captures": [],
    }

    # --------------------------------------------------- companies and the rest
    companies = Company.objects.for_user(user).prefetch_related(
        "industries",
        "identifiers",
        "contacts",
        "postings__events",
        "postings__applications__events",
        "postings__applications__reminders",
        "postings__applications__interviews__contacts",
        "postings__applications__department__company",
        "postings__applications__through_agency",
        "postings__applications__sent_links",
        "postings__applications__sent_uploads",
        "departments",
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
                # The teams inside this company, as records of their own. They used to
                # travel only as a name beside a contact, which silently dropped every
                # department nobody had been recorded at -- and a team you applied to before
                # you knew anybody there is exactly the ordinary case (#138).
                "departments": [team.name for team in company.departments.all()],
                "identifiers": [
                    {"scheme": i.scheme, "value": i.value, "label": i.label}
                    for i in company.identifiers.all()
                ],
                "contacts": [_contact(contact) for contact in company.contacts.all()],
                "postings": [
                    {
                        **_fields(posting, POSTING_FIELDS),
                        # Its history (#270), under the listing it belongs to. The
                        # applications below read it through their posting and carry no
                        # copy of it, so it is written once, here.
                        "events": [_listing_event(event) for event in posting.events.all()],
                        "applications": [
                            {
                                **_fields(application, APPLICATION_FIELDS),
                                # Which part of the employer this was aimed at, by name
                                # and by the company holding it -- a department may sit
                                # at another company in the same group, and an id means
                                # nothing in another instance. Resolved in a second pass
                                # on import, as the ownership tree is (#138).
                                "department": (
                                    application.department.name if application.department_id else ""
                                ),
                                "department_company": (
                                    application.department.company.name
                                    if application.department_id
                                    else ""
                                ),
                                # The agency it went through, by name for the reason the
                                # parent of a company is: an id means nothing in another
                                # instance, and a name is what the importer can match
                                # against the companies it has just made (#239).
                                "through_agency": (
                                    application.through_agency.name
                                    if application.through_agency_id
                                    else ""
                                ),
                                "tags": [tag.slug for tag in application.tags.all()],
                                "sent_link_ids": [link.pk for link in application.sent_links.all()],
                                # The files it went out with, by their id in this file (#469).
                                "sent_upload_ids": [
                                    upload.pk for upload in application.sent_uploads.all()
                                ],
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
                                "offers": [
                                    _fields(offer, OFFER_FIELDS)
                                    for offer in application.offers.all()
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
            **_fields(cv, CV_FIELDS),
            # Since #308: which of the profile's details this CV prints. Written as
            # what it prints rather than as its columns, and a chosen row by what it says,
            # so it can be found again among the rows an import has just made.
            "prints": printing.as_archived(cv),
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
            # What made it, written as a kind and a local id rather than as one of two
            # columns: an archive from a Postulo that has a fourth kind of document is
            # still an archive this one can read the rest of (#130).
            **_source_of(sent),
            "file": f"{MEDIA_PREFIX}{sent.file.name}" if sent.file else "",
            "source_text": sent.source_text,
            # Format 19 (#236). Empty for a letter, whose `source_text` is its words
            # already, and for a CV frozen before its words were kept; an archive written
            # before this has no key, and restores with the field blank, which is the
            # same answer.
            "plain_text": sent.plain_text,
            "copies": _copies(sent),
        }
        for sent in RenderedDocument.objects.for_user(user).select_related("source_type")
    ]

    document["captures"] = [
        {
            **_fields(capture, CAPTURE_FIELDS),
            "data": capture.data,
            "page": _kept_page(capture),
        }
        for capture in Capture.objects.for_user(user).select_related("page")
    ]

    # Format 25 (#267). The places the person's own corrections taught, per site: theirs, so
    # in their archive. A place is how a page names an element -- an id, a class, the words
    # of a label, a heading's position -- and never the value that was found there.
    document["remembered_places"] = [
        {"host": hint.host, "field": hint.field, "place": hint.place, "misses": hint.misses}
        for hint in FieldHint.objects.for_user(user).order_by("host", "field")
    ]

    document["counts"] = {
        "captured_pages": sum(1 for capture in document["captures"] if capture["page"]),
        "remembered_places": len(document["remembered_places"]),
        "companies": len(document["companies"]),
        "listing_events": sum(
            len(posting["events"])
            for company in document["companies"]
            for posting in company["postings"]
        ),
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


def build_candidate_document(user) -> dict:
    """One person's own record and nothing else, as a document of its own (#181).

    The whole-account archive is all or nothing: somebody who wants to carry their career to
    another Postulo, keep it in a repository or hand it to a script had to take every
    application they ever made with it. The career is the part that is *theirs* and
    portable; the applications are a record of one job hunt.

    **The same blocks, from the same builders.** `account.profile`, `account.identifiers`
    and `resume` are what `build_document` writes, produced by the functions it calls, so a
    reader of one reads the other's and a field added to either is added to both.

    **What is left out, and why.** The picture, because JSON cannot carry one and a zip
    would stop the file being something a person can open in a text editor -- which is most
    of the point. The address the account signs in with and its username, because they are
    the account's and another instance has its own. And everything in `PROFILE_FIELDS` that
    says how Postulo behaves rather than who somebody is.
    """
    from postulo.accounts.models import Profile

    # Read afresh rather than through the instance cached on the user, which may be stale.
    profile = Profile.objects.filter(user=user).first()
    return {
        "postulo": {
            "candidate_format": CANDIDATE_FORMAT,
            "version": __version__,
            "exported_at": timezone.now().isoformat(),
            "note": (
                "One person's own record as Postulo holds it: their details and their "
                "career, and nothing about where they applied. The picture is not in "
                "it. Identifiers are local to this file and exist so the parts can be "
                "reconnected; they are not meaningful anywhere else."
            ),
        },
        "account": {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "profile": _profile_block(profile, CANDIDATE_PROFILE_FIELDS),
            "identifiers": _identifier_rows(profile),
        },
        "resume": _resume_block(user),
    }


def candidate_counts(user) -> dict[str, int]:
    """How many of each kind of row the candidate document would carry, without building it.

    The same bargain `counts` strikes for the archive (#220): the page that offers the file
    says what is in it, and says so by counting.
    """
    from postulo.accounts.models import Profile
    from postulo.resume import models as resume

    found = {
        key: getattr(resume, name).objects.for_user(user).count()
        for key, name in RESUME_MODELS.items()
    }
    # Only the ones that say something, which is what the document writes: a translation
    # somebody cleared is a row that prints the original (#131).
    found["translations"] = (
        resume.Translation.objects.for_user(user)
        .filter(content_type__model__in=list(TRANSLATION_SECTIONS), text__regex=r"\S")
        .count()
    )
    profile = Profile.objects.filter(user=user).first()
    for key in (
        "phone_numbers",
        "postal_addresses",
        "web_links",
        "messaging_handles",
        "identifiers",
    ):
        found[key] = getattr(profile, key).count() if profile else 0
    return found


def candidate_filename(user) -> str:
    stamp = timezone.localdate().isoformat()
    local_part = user.email.split("@")[0]
    return f"postulo-candidate-{local_part}-{stamp}.json"


def _source_of(sent) -> dict:
    """Which authored thing a render came from, as a kind and a local id.

    Empty where nothing made it: an uploaded file came from a file, and a render whose
    source was deleted has none either. Both are ordinary states rather than gaps.
    """
    if not sent.source_type_id or not sent.source_id:
        return {"source_kind": "", "source_ref": None}
    return {
        "source_kind": sent.source_type.model,
        "source_ref": sent.source_id,
    }


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
    # What captures kept of their pages (#256): the source as gzipped text, the rendering
    # as the picture or PDF it is. Copied as they are -- nothing here unpacks the source.
    for capture in document.get("captures", []):
        page = capture.get("page") or {}
        for key in ("source_file", "rendering_file"):
            if page.get(key):
                names.append(page[key][len(MEDIA_PREFIX) :])
    return names


def write_archive(user, target=None) -> BytesIO:
    """Build the complete export as a zip.

    Returns an in-memory buffer when no target is given. An export is one person's job
    search: measured in megabytes, not gigabytes, so holding it in memory is reasonable
    and streaming would be more machinery than the size justifies.

    The manifest is read inside one transaction and the files outside it. The view no longer
    runs in a transaction of its own (#220), and an archive whose JSON names an application
    the same archive's other half has never heard of would be worse than a slow one — so the
    reading of records keeps the consistency `ATOMIC_REQUESTS` used to give it. Copying the
    files is the long part and never had it: those bytes are on disk, not in the database.
    """
    from django.core.files.storage import default_storage

    with transaction.atomic():
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
