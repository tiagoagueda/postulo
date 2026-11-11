"""Writing a CV as Europass, the other half of `reader` (#1100).

**Which Europass.** europass.europa.eu has no file of its own to hand out but the PDF with a
Candidate XML attached, and that is a document its editor draws; the one Europass written
down as data, with a published JSON Schema, is the **SkillsPassport JSON** -- schema 3.3.0,
Cedefop's, the format of the REST API that turned it into a PDF, a Word file or the XML. It
is what this writes, and it is what `reader.read_json` reads back.

**One mapping, two directions.** Every field is put where `reader` looks for it, by the same
names: a work experience's ``Activities`` is what the reader makes its summary of, an
education's ``Level`` labelled ``EQF n`` is its level and any other label its grade, a skill
group named after one of `reader.SKILL_HEADINGS` goes under that heading, a language at
``native`` is a mother tongue. A CV written here and read back by Postulo's own importer
gives back what was written, as far as the format carries it, and a test holds that.

**What it has no place for is left out and named** (`leaves_out`), by the keys
`documents.structured.LEFT_OUT` words: a summary, the pronouns, the place of birth, the
names of skill groups the headings do not match. Nothing is squeezed into a field meant for
something else to keep it.

**Handed values, not records.** The writer is given `documents.structured.CVData` -- the CV
as it prints, in its own language -- and imports nothing of Postulo's: what it may write is
exactly what the person chose to print, and it cannot reach for more.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from html import escape

from .common import CEFR, CEFR_PARTS, _country, _heading
from .reader import SKILL_HEADINGS

CONTENT_TYPE = "application/json"

#: The schema version the document says it follows, as its own pattern writes one.
XSD_VERSION = "V3.3"

#: Postulo's kinds of entry that Europass keeps as an "achievement": a heading of its own
#: list (`Title.Code`) and a description in prose.
ACHIEVEMENTS = {
    "certification": "certifications",
    "honour": "honors_awards",
    "publication": "publications",
    "citation": "citations",
    "membership": "memberships",
    "course": "courses",
    "participation": "conferences",
    "reference": "references",
}

#: The licence categories Europass has a code for. It predates AM and A2 (Directive
#: 2006/126/EC's), and has no country.
DRIVING = frozenset({"A", "B", "C", "D", "A1", "B1", "C1", "D1", "BE", "CE", "DE", "C1E", "D1E"})

#: A photo Europass takes: the two kinds its schema names.
PHOTO = re.compile(r"^data:(?P<type>image/(?:png|jpeg));base64,(?P<data>[A-Za-z0-9+/=\s]+)$")

#: A date of birth as Postulo stores it (#679): a year, a year and month, or a whole date.
REDUCED_DATE = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?$")


# --------------------------------------------------------------------------- pieces


def _date(value) -> dict | None:
    """A date as Europass writes one: the year, month and day as numbers."""
    if value is None:
        return None
    return {"Year": value.year, "Month": value.month, "Day": value.day}


def _period(entry: dict) -> dict:
    """``From``, ``To`` and whether it is still going, where there is a start or an end."""
    start, end = entry.get("start_date"), entry.get("end_date")
    period: dict = {}
    if start:
        period["From"] = _date(start)
    if end:
        period["To"] = _date(end)
    if start and not end:
        period["Current"] = True
    return {"Period": period} if period else {}


def _lines(*parts) -> list[str]:
    """The lines that say something, in order, out of texts and lists of texts."""
    lines: list[str] = []
    for part in parts:
        for text in part if isinstance(part, (list, tuple)) else [part]:
            lines.extend(line.strip() for line in str(text or "").splitlines() if line.strip())
    return lines


def _prose(*parts, items=()) -> str:
    """A long field, as Europass writes one: HTML, a paragraph a line and a list of items.

    Europass's editors wrote these fields as HTML and `markup._plain` reads them as HTML, so
    what was typed is escaped here -- "under <4 minutes" is text, not a tag the reader would
    take apart -- and every line is a paragraph of its own, which is how the reader keeps
    them apart again.
    """
    paragraphs = "".join(f"<p>{escape(line, quote=False)}</p>" for line in _lines(*parts))
    listed = "".join(f"<li>{escape(line, quote=False)}</li>" for line in _lines(items))
    return paragraphs + (f"<ul>{listed}</ul>" if listed else "")


def _place(location: str) -> dict:
    """Where an employer or a school is: the reader puts the town back as the location."""
    if not location:
        return {}
    return {"ContactInfo": {"Address": {"Contact": {"Municipality": location}}}}


def _language_code(tag: str) -> str:
    """The two-letter code Europass lists a language under, or nothing."""
    primary = (tag or "").split("-", 1)[0].lower()
    return primary if len(primary) == 2 and primary.isalpha() else ""


def _locale(tag: str) -> str:
    """The document's language as the schema writes it: two lower-case letters."""
    return _language_code(tag)


# ------------------------------------------------------------------------ the person


def _identification(person) -> tuple[dict, set[str]]:
    """The person's block, and what of them it had no place for."""
    dropped: set[str] = set()
    identification: dict = {}

    name: dict = {}
    if person.first_name or person.last_name:
        name = {"FirstName": person.first_name, "Surname": person.last_name}
    elif person.name:
        name = {"FirstName": person.name}
    if person.form_of_address:
        name["Title"] = {"Label": person.form_of_address}
    if name:
        identification["PersonName"] = {key: value for key, value in name.items() if value}

    contact: dict = {}
    if person.location:
        # The line the CV prints, where the reader takes the location from.
        contact["Address"] = {"Contact": {"Municipality": person.location}}
    if person.email:
        contact["Email"] = {"Contact": person.email}
    if person.phone:
        contact["Telephone"] = [{"Contact": person.phone}]
    websites = []
    # The website first: the reader takes the first one listed as the person's own.
    for address, use in (
        (person.website, "personal"),
        (person.social, "business"),
        (person.repository, "portfolio"),
    ):
        if address:
            websites.append({"Contact": address, "Use": {"Code": use}})
    for _label, value, address in person.identifiers:
        # An ORCID is listed as its address, which is how the reader finds one again.
        if address:
            websites.append({"Contact": address})
        elif value:
            dropped.add("identifiers")
    if websites:
        contact["Website"] = websites
    if person.messaging:
        contact["InstantMessaging"] = [{"Contact": person.messaging}]
    if contact:
        identification["ContactInfo"] = contact

    demographics: dict = {}
    born = REDUCED_DATE.match(person.birth_date or "")
    if born:
        demographics["Birthdate"] = {
            key.title(): int(value) for key, value in born.groupdict().items() if value
        }
    elif person.birth_date:
        dropped.add("birth_date")
    if person.nationalities:
        demographics["Nationality"] = [
            {"Code": code} for code in (_country(code) for code in person.nationalities) if code
        ]
    elif person.nationality_scope:
        # "A citizen of the EU" is not a country, and a nationality here is one.
        dropped.add("nationality")
    if person.gender:
        # The person's own word, as a label: the schema's codes are two, and Postulo does not
        # turn somebody's word into one of them.
        demographics["Gender"] = {"Label": person.gender}
    if demographics:
        identification["Demographics"] = demographics

    if person.photo:
        found = PHOTO.match(person.photo)
        data = "".join(found.group("data").split()) if found else ""
        try:
            base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            data = ""
        if data:
            identification["Photo"] = {"MimeType": found.group("type"), "Data": data}
        else:
            dropped.add("photo")

    if person.pronouns:
        dropped.add("pronouns")
    if person.birth_place:
        dropped.add("birth_place")
    return identification, dropped


# ----------------------------------------------------------------------- the entries


def _experience(entry: dict) -> dict:
    out: dict = {**_period(entry)}
    if entry["role"]:
        out["Position"] = {"Label": entry["role"]}
    # One field of prose: the summary, and the highlights under it as a list.
    activities = _prose(entry["summary"], items=entry["highlights"])
    if activities:
        out["Activities"] = activities
    employer = {"Name": entry["organisation"], **_place(entry["location"])}
    out["Employer"] = {key: value for key, value in employer.items() if value}
    return out


def _education(entry: dict) -> tuple[dict, set[str]]:
    dropped: set[str] = set()
    out: dict = {**_period(entry)}
    if entry["qualification"]:
        out["Title"] = entry["qualification"]
    if entry["highlights"]:
        out["Activities"] = _prose(items=entry["highlights"])
    organisation = {"Name": entry["institution"], **_place(entry["location"])}
    out["Organisation"] = {key: value for key, value in organisation.items() if value}
    if entry["field_of_study"]:
        out["Field"] = {"Label": entry["field_of_study"]}
    # The reader takes a label of exactly "EQF n" for the level and any other for the grade;
    # there is one `Level`, so a grade beside a printed level has nowhere to go.
    if entry["eqf_level"]:
        out["Level"] = {"Code": str(entry["eqf_level"]), "Label": f"EQF {entry['eqf_level']}"}
        if entry["grade"]:
            dropped.add("grade")
    elif entry["grade"]:
        out["Level"] = {"Label": entry["grade"]}
    return out, dropped


def _skills(data) -> tuple[dict, set[str]]:
    """Languages, the skill groups under Europass's headings, and the driving licence."""
    dropped: set[str] = set()
    skills: dict = {}

    mothers, foreigners = [], []
    for entry in data.entries("languageskill"):
        description = {"Label": entry["name"]}
        code = _language_code(entry["code"])
        if code:
            description["Code"] = code
        if entry["proficiency"] == "native":
            mothers.append({"Description": description})
            continue
        foreign: dict = {"Description": description}
        level = entry["proficiency"].upper()
        if level in CEFR:
            # Postulo keeps one level and Europass five; the one is all five, and the reader
            # takes the lowest of five, which is that one again.
            foreign["ProficiencyLevel"] = dict.fromkeys(CEFR_PARTS, level)
        foreigners.append(foreign)
    linguistic: dict = {}
    if mothers:
        linguistic["MotherTongue"] = mothers
    if foreigners:
        linguistic["ForeignLanguage"] = foreigners
    if linguistic:
        skills["Linguistic"] = linguistic

    # A group named as one of Europass's headings is written under it -- the name the reader
    # gives that heading, in the CV's language. Every other group's skills go under "Other",
    # and its name is what is left out.
    headings = {_heading(label, data.language): tag for tag, label in SKILL_HEADINGS}
    under: dict[str, list[str]] = {}
    for entry in data.entries("skillgroup"):
        tag = headings.get(entry["name"], "Other")
        if tag == "Other" and entry["name"] and entry["name"] not in headings:
            dropped.add("skill_group_names")
        under.setdefault(tag, []).extend(skill for skill in entry["skills"] if skill)
    for tag, _label in SKILL_HEADINGS:
        if under.get(tag):
            # A skill a line, which is the boundary the reader splits them on.
            skills[tag] = {"Description": _prose(under[tag])}

    categories: list[str] = []
    for entry in data.entries("drivinglicence"):
        for code in entry["categories"]:
            if code in DRIVING and code not in categories:
                categories.append(code)
            elif code not in DRIVING:
                dropped.add("driving_details")
        if entry["other"] or entry["country"]:
            dropped.add("driving_details")
    if categories:
        skills["Driving"] = {"Description": categories}
    return skills, dropped


def _achievements(data) -> tuple[list[dict], set[str]]:
    """Projects one by one, as the reader reads them; the other kinds a heading each."""
    dropped: set[str] = set()
    found: list[dict] = []
    for entry in data.entries("project"):
        if entry["start_date"] or entry["end_date"]:
            dropped.add("project_dates")
        description = _prose(
            entry["role"], entry["summary"], entry["url"], items=entry["highlights"]
        )
        one: dict = {"Title": {"Code": "projects", "Label": entry["name"]}}
        if description:
            one["Description"] = description
        found.append(one)
    for section in data.sections:
        code = ACHIEVEMENTS.get(section.kind)
        if code is None:
            continue
        lines = list(section.lines)
        if section.kind == "reference" and data.references_on_request:
            lines.append(data.references_on_request)
        if lines:
            found.append(
                {"Title": {"Code": code, "Label": section.label}, "Description": _prose(lines)}
            )
    if data.references_on_request and data.section("reference") is None:
        found.append(
            {
                "Title": {"Code": "references", "Label": data.references_heading},
                "Description": _prose(data.references_on_request),
            }
        )
    return found, dropped


def _links(data) -> list[dict]:
    """The CV's links, beside the person's own websites: Europass has nowhere else for them."""
    return [
        {"Contact": entry["url"], "Use": {"Code": "portfolio"}}
        for entry in data.entries("link")
        if entry["url"]
    ]


# ------------------------------------------------------------------------- the file


def document(data) -> tuple[dict, set[str]]:
    """The CV as a Europass document, and the keys of what it left out."""
    dropped: set[str] = set()
    learner: dict = {}

    if data.person is not None:
        identification, left = _identification(data.person)
        dropped |= left
    else:
        identification = {}
    links = _links(data)
    if links:
        contact = identification.setdefault("ContactInfo", {})
        contact["Website"] = [*contact.get("Website", []), *links]
    if identification:
        learner["Identification"] = identification
    if data.headline:
        learner["Headline"] = {"Description": {"Label": data.headline}}
    if data.summary:
        dropped.add("summary")

    experience = [_experience(entry) for entry in data.entries("experience")]
    if experience:
        learner["WorkExperience"] = experience
    education = []
    for entry in data.entries("education"):
        written, left = _education(entry)
        education.append(written)
        dropped |= left
    if education:
        learner["Education"] = education
    skills, left = _skills(data)
    dropped |= left
    if skills:
        learner["Skills"] = skills
    achievements, left = _achievements(data)
    dropped |= left
    if achievements:
        learner["Achievement"] = achievements

    passport: dict = {}
    locale = _locale(data.language)
    if locale:
        passport["Locale"] = locale
    passport["DocumentInfo"] = {
        "DocumentType": "ECV",
        "XSDVersion": XSD_VERSION,
        "Generator": "Postulo",
    }
    passport["LearnerInfo"] = learner
    return {"SkillsPassport": passport}, dropped


def write(data) -> bytes:
    """The Europass file: UTF-8 JSON, the same CV the same bytes."""
    found, _dropped = document(data)
    return (json.dumps(found, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def leaves_out(data) -> list[str]:
    """What of this CV the file has no place for, by `documents.structured.LEFT_OUT`'s keys."""
    _found, dropped = document(data)
    order = (
        "summary",
        "pronouns",
        "identifiers",
        "birth_date",
        "birth_place",
        "nationality",
        "photo",
        "skill_group_names",
        "grade",
        "project_dates",
        "driving_details",
    )
    return [key for key in order if key in dropped]
