"""A CV as values, for the formats that carry its structure rather than its words (#1100).

Europass and JSON Resume are not documents to read but records to load: a portal or a tool
takes the jobs, the qualifications and the languages out of them one field at a time. An
`Outline` is the wrong thing to hand such a writer -- it is the words with the setting taken
off, and a job title in it is a heading like any other. This is the other thing a format may
be handed: the same CV, as values.

**The variant, not the record.** What is here is what the CV prints and nothing more: its
chosen entries, in its order, in its language, with its tailored highlights, and the
personal details it prints -- nothing at all where its contact block is off. A format that
carries the structure is still a copy of *this* CV, and a detail the person kept off it is
not put back because a format happens to have a field for it. It is built from
`rendering.build_sections` and `printing.resolve`, the same two answers every renderer is
given, so it costs what the text costs however many entries there are (#559).

**What a format has no place for is said, not dropped in silence.** A writer names the parts
of a CV it leaves out by the keys of `LEFT_OUT`, and the CV's page words them beside the
download, in the reader's language.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from postulo.core import languages, link_services, phones

from . import printing, rendering
from .models import CV, document_language

# ------------------------------------------------------------------ what is handed over


@dataclass(frozen=True)
class Person:
    """The details a CV prints of its owner, as values. Empty means not printed."""

    name: str = ""
    first_name: str = ""
    last_name: str = ""
    #: In the document's language, as the page prints it.
    form_of_address: str = ""
    pronouns: str = ""
    email: str = ""
    #: As the page prints it: grouped the way its own country writes it.
    phone: str = ""
    website: str = ""
    #: The professional network's address and the code repository's, as the themes call them.
    social: str = ""
    repository: str = ""
    #: The service each is on, by its own name -- "LinkedIn", "GitHub" -- where Postulo knows
    #: the address's host (#305); empty otherwise.
    social_service: str = ""
    repository_service: str = ""
    messaging: str = ""
    #: ``(label, value, address)``: the address empty where the scheme has none.
    identifiers: tuple[tuple[str, str, str], ...] = ()
    location: str = ""
    #: An ISO 8601 reduced form -- a year, a year and month, or a date -- as stored (#679).
    birth_date: str = ""
    birth_place: str = ""
    #: ISO 3166-1 codes, in the order held; and the words the page prints for them (#680).
    nationalities: tuple[str, ...] = ()
    nationality_words: tuple[str, ...] = ()
    nationality_scope: str = ""
    gender: str = ""
    #: A ``data:`` address, or empty (#668).
    photo: str = ""


@dataclass(frozen=True)
class Section:
    """A run of entries of one kind, as `rendering.build_sections` groups them."""

    kind: str
    #: The heading, in the document's language.
    label: str
    #: One mapping an entry, of plain values; `_entry` says which keys each kind has.
    entries: tuple[dict, ...] = ()
    #: What the text format writes for the section, line by line: what a format with one
    #: prose field for a kind of entry carries, so that it says what the page does.
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class CVData:
    """One CV as values: what a format that carries structure is handed."""

    #: The BCP 47 tag the document declares.
    language: str
    #: ``cv`` or ``portfolio``.
    kind: str
    #: None where the CV prints no contact block (#512): nobody is named.
    person: Person | None
    headline: str = ""
    summary: str = ""
    sections: tuple[Section, ...] = ()
    #: The sentence, in the document's language, where the CV says references are available
    #: on request (#696); empty where it does not.
    references_on_request: str = ""
    #: What a references heading is called in the document's language, for a format that
    #: says the sentence under a heading where nobody is named.
    references_heading: str = ""

    def section(self, kind: str) -> Section | None:
        return next((one for one in self.sections if one.kind == kind), None)

    def entries(self, kind: str) -> tuple[dict, ...]:
        found = self.section(kind)
        return found.entries if found else ()


#: What of a CV a format may leave out, in the words the page says it with. A writer names
#: what it drops by these keys; a kind of entry is named by its heading (`SECTION_LABELS`).
LEFT_OUT = {
    "summary": _("Summary"),
    "form_of_address": _("Form of address"),
    "pronouns": _("Pronouns"),
    "messaging": _("Messaging"),
    "identifiers": _("Identifiers"),
    "birth_date": _("Date of birth"),
    "birth_place": _("Place of birth"),
    "nationality": _("Nationality"),
    "gender": _("Gender"),
    "photo": _("Photo"),
    "skill_group_names": _("The names of skill groups"),
    "education_highlights": _("Highlights of education"),
    "eqf_level": _("EQF level"),
    "grade": _("Grade"),
    "project_dates": _("Dates of projects"),
    "driving_details": _("Some driving licence details"),
    **rendering.SECTION_LABELS,
}


def left_out(chosen, data: CVData) -> list[str]:
    """What ``chosen`` leaves out of this CV, worded in the reader's language, each once."""
    if chosen.leaves_out is None:
        return []
    said: list[str] = []
    for key in chosen.leaves_out(data):
        label = LEFT_OUT.get(key)
        if label is not None and str(label) not in said:
            said.append(str(label))
    return said


# ---------------------------------------------------------------------- building it


def _date(value) -> dt.date | None:
    return value if isinstance(value, dt.date) else None


def _text(value) -> str:
    return str(value or "").strip()


def _common(item) -> dict:
    """What most entries have: their dates, their summary and where to see them."""
    return {
        "start_date": _date(getattr(item, "start_date", None)),
        "end_date": _date(getattr(item, "end_date", None)),
        "summary": _text(getattr(item, "summary", "")),
        "url": _text(getattr(item, "url", "")),
    }


def _entry(cv: CV, kind: str, entry: rendering.Entry) -> dict:
    """One entry of ``kind`` as values, as this CV prints it."""
    item = entry.item
    highlights = list(entry.highlight_lines)
    if kind == "experience":
        return {
            **_common(item),
            "role": _text(item.role),
            "organisation": _text(item.organisation),
            "location": _text(item.location),
            "highlights": highlights,
        }
    if kind == "education":
        return {
            **_common(item),
            "qualification": _text(item.qualification),
            "field_of_study": _text(item.field_of_study),
            "institution": _text(item.institution),
            "location": _text(item.location),
            "grade": _text(item.grade),
            # Only where the CV prints it, as the page does (#684).
            "eqf_level": item.eqf_level if cv.show_eqf_level and item.eqf_level else None,
            "highlights": highlights,
        }
    if kind == "project":
        return {
            **_common(item),
            "name": _text(item.name),
            "role": _text(item.role),
            "highlights": highlights,
        }
    if kind == "skillgroup":
        return {"name": _text(item.name), "skills": [_text(one) for one in item.skill_names]}
    if kind == "languageskill":
        return {
            "name": _text(getattr(item, "shown_name", "") or item.name),
            "code": _text(item.code),
            "proficiency": _text(item.proficiency),
            "proficiency_label": str(item.get_proficiency_display()) if item.proficiency else "",
        }
    if kind == "certification":
        return {
            "name": _text(item.name),
            "issuer": _text(item.issuer),
            "issued_on": _date(item.issued_on),
            "expires_on": _date(item.expires_on),
            "url": _text(item.credential_url),
        }
    if kind == "honour":
        return {
            **_common(item),
            "title": _text(item.title),
            "awarded_by": _text(item.awarded_by),
            "awarded_on": _date(item.awarded_on),
        }
    if kind == "publication":
        return {
            "title": _text(item.title),
            "date": _text(item.date),
            "publisher": _text(item.publisher) or _text(item.container_title),
            "doi": _text(item.doi),
            "url": _text(item.url),
            "citation": _text(item.citation),
        }
    if kind == "reference":
        # A `Referee`, and only an agreed one: `build_sections` has seen to both (#696).
        return {
            "name": item.name,
            "role": item.role,
            "organisation": item.organisation,
            "relationship": item.relationship,
            "email": item.email,
            "phone": item.phone,
            "line": item.rest,
        }
    if kind == "drivinglicence":
        from postulo.resume import driving

        return {
            "categories": list(driving.ordered(item.categories or [])),
            "other": _text(item.other_categories),
            "country": _text(item.country),
        }
    if kind == "link":
        return {
            "title": _text(item.title),
            "url": _text(item.url),
            "description": _text(item.description),
        }
    # Memberships, courses, events and citation counts: carried by the words of the section.
    return _common(item)


def _lines(cv: CV, section: rendering.Section) -> tuple[str, ...]:
    """The section as the text format writes it, a line a line (#236)."""
    found: list[str] = []
    for block in rendering._section_blocks(cv, section, as_portfolio=False):
        if block.items:
            found.extend(item.strip() for item in block.items if item.strip())
        elif block.text.strip():
            found.extend(line.strip() for line in block.text.splitlines() if line.strip())
    return tuple(found)


def _service(address: str, kind: str) -> str:
    """The name of the service an address is on, where its host says so; else nothing."""
    found = link_services.guess(address, kind) if address else None
    return str(found.label) if found is not None else ""


def _person(cv: CV) -> Person | None:
    """What the CV prints of its owner, or nobody where its contact block is off."""
    if not cv.show_contact_details:
        return None
    owner = cv.owner
    profile = getattr(owner, "profile", None)
    printed = printing.resolve(owner, cv)
    nationalities: tuple[str, ...] = ()
    if cv.show_nationality and profile is not None and printed.nationalities:
        nationalities = tuple(str(code).upper() for code in profile.nationalities or ())
    return Person(
        name=owner.get_full_name() or owner.display_name,
        first_name=_text(owner.first_name),
        last_name=_text(owner.last_name),
        form_of_address=printed.form_of_address,
        pronouns=printed.pronouns,
        email=printed.email,
        phone=phones.readable(printed.phone),
        website=printed.website,
        social=printed.social,
        repository=printed.repository,
        social_service=_service(printed.social, link_services.SOCIAL),
        repository_service=_service(printed.repository, link_services.REPOSITORY),
        messaging=printed.messaging,
        identifiers=tuple(
            (str(row.display_label), _text(row.value), _text(row.url))
            for row in printed.identifiers
        ),
        location=printed.location,
        birth_date=printed.birth_date,
        birth_place=printed.birth_place,
        nationalities=nationalities,
        nationality_words=tuple(printed.nationalities),
        nationality_scope=printed.nationality_scope,
        gender=printed.gender,
        photo=printed.photo,
    )


def cv_data(cv: CV) -> CVData:
    """This CV as values, in its own language: what a format that carries structure is handed."""
    language = document_language(cv)
    with languages.override(language):
        person = _person(cv)
        sections = tuple(
            Section(
                kind=section.kind,
                label=section.label,
                entries=tuple(
                    _entry(cv, section.kind, entry)
                    for entry in section.items
                    if entry.item is not None
                ),
                lines=_lines(cv, section),
            )
            for section in rendering.build_sections(cv)
        )
        headline = _text(cv.headline) or (
            _text(getattr(cv.owner.profile, "headline", "")) if person is not None else ""
        )
        return CVData(
            language=language,
            kind=str(cv.kind),
            person=person,
            headline=headline,
            summary=_text(cv.summary),
            sections=sections,
            references_on_request=(
                str(rendering.REFERENCES_ON_REQUEST) if cv.references_on_request else ""
            ),
            references_heading=str(rendering.SECTION_LABELS["reference"]),
        )
