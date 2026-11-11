"""Writing a CV as JSON Resume (#1100).

JSON Resume is the open résumé format many tools read -- themes that print it, sites that
host it, editors and converters -- published at jsonresume.org with a JSON Schema
(`resume-schema`, MIT). This writes schema 1.2.1's fields from the CV as it prints
(`structured.CVData`): its chosen entries in its order and language, and the personal details
it prints, nothing where its contact block is off.

**Its own fields, used for what they say.** A job is ``work``, a qualification is
``education`` with the qualification as its ``studyType``, a skill group keeps its name and
its skills as ``keywords``, an honour is an ``award``. A date is written in the reduced
ISO 8601 form the schema asks for.

**What it has no place for is left out and named** (`leaves_out`), by the keys
`structured.LEFT_OUT` words: the personal details beyond a name and how to reach somebody,
memberships, courses, events, citation counts, links and driving licences. Nothing is
squeezed into a field that means something else to keep it.

No library: the format is a JSON document and the standard library writes one. The writers
on PyPI found for it (2026-11) build a résumé from a Python object of their own, which is a
second model of a CV to keep in step for no gain here.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Handed one, never building one: `formats` registers this writer and `structured` reads
    # `rendering`, which reads `formats`.
    from .structured import CVData

CONTENT_TYPE = "application/json"

#: The version of the schema the file is written to, and where it is published.
SCHEMA = "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.2.1/schema.json"

#: A date as the schema's ``iso8601`` takes one: a year, a year and month, or a whole date.
REDUCED_DATE = re.compile(r"^[1-2][0-9]{3}(-[0-1][0-9](-[0-3][0-9])?)?$")

#: Kinds of entry with no field of their own in the format.
NO_PLACE = ("membership", "course", "participation", "citation", "link", "drivinglicence")


def _date(value) -> str:
    return value.isoformat() if value else ""


def _reduced(text: str) -> str:
    """A date that was typed as text, kept only where it is already the schema's shape."""
    return text if REDUCED_DATE.match(text or "") else ""


def _clean(mapping: dict) -> dict:
    """Without the fields that say nothing: the schema has no required ones."""
    return {key: value for key, value in mapping.items() if value not in ("", None, [], {})}


def _basics(data: CVData) -> tuple[dict, set[str]]:
    dropped: set[str] = set()
    basics: dict = {"label": data.headline, "summary": data.summary}
    person = data.person
    if person is None:
        return _clean(basics), dropped
    profiles = []
    for address, network in (
        (person.social, person.social_service),
        (person.repository, person.repository_service),
    ):
        if address:
            profiles.append({"url": address, "network": network})
    for label, value, address in person.identifiers:
        profiles.append({"network": label, "username": value, "url": address})
    basics.update(
        {
            "name": person.name,
            "email": person.email,
            "phone": person.phone,
            "url": person.website,
            "image": person.photo,
            "location": _clean({"address": person.location}),
            "profiles": [_clean(profile) for profile in profiles],
        }
    )
    for key, said in (
        ("form_of_address", person.form_of_address),
        ("pronouns", person.pronouns),
        ("messaging", person.messaging),
        ("birth_date", person.birth_date),
        ("birth_place", person.birth_place),
        ("nationality", person.nationalities or person.nationality_scope),
        ("gender", person.gender),
    ):
        if said:
            dropped.add(key)
    return _clean(basics), dropped


def document(data: CVData) -> tuple[dict, set[str]]:
    """The CV as a JSON Resume document, and the keys of what it left out."""
    basics, dropped = _basics(data)
    resume: dict = {"$schema": SCHEMA, "basics": basics}

    resume["work"] = [
        _clean(
            {
                "name": entry["organisation"],
                "position": entry["role"],
                "location": entry["location"],
                "startDate": _date(entry["start_date"]),
                "endDate": _date(entry["end_date"]),
                "summary": entry["summary"],
                "highlights": entry["highlights"],
            }
        )
        for entry in data.entries("experience")
    ]
    education = []
    for entry in data.entries("education"):
        if entry["highlights"]:
            dropped.add("education_highlights")
        if entry["eqf_level"]:
            dropped.add("eqf_level")
        education.append(
            _clean(
                {
                    "institution": entry["institution"],
                    "area": entry["field_of_study"],
                    "studyType": entry["qualification"],
                    "startDate": _date(entry["start_date"]),
                    "endDate": _date(entry["end_date"]),
                    "score": entry["grade"],
                }
            )
        )
    resume["education"] = education
    resume["awards"] = [
        _clean(
            {
                "title": entry["title"],
                "date": _date(entry["awarded_on"]),
                "awarder": entry["awarded_by"],
                "summary": entry["summary"],
            }
        )
        for entry in data.entries("honour")
    ]
    resume["certificates"] = [
        _clean(
            {
                "name": entry["name"],
                "date": _date(entry["issued_on"]),
                "issuer": entry["issuer"],
                "url": entry["url"],
            }
        )
        for entry in data.entries("certification")
    ]
    resume["publications"] = [
        _clean(
            {
                "name": entry["title"],
                "publisher": entry["publisher"],
                "releaseDate": _reduced(entry["date"]),
                "url": entry["url"] or (f"https://doi.org/{entry['doi']}" if entry["doi"] else ""),
            }
        )
        for entry in data.entries("publication")
    ]
    resume["skills"] = [
        _clean({"name": entry["name"], "keywords": entry["skills"]})
        for entry in data.entries("skillgroup")
    ]
    resume["languages"] = [
        _clean({"language": entry["name"], "fluency": entry["proficiency_label"]})
        for entry in data.entries("languageskill")
    ]
    references = [
        _clean({"name": entry["name"], "reference": entry["line"]})
        for entry in data.entries("reference")
    ]
    if data.references_on_request:
        references.append({"reference": data.references_on_request})
    resume["references"] = references
    resume["projects"] = [
        _clean(
            {
                "name": entry["name"],
                "description": entry["summary"],
                "highlights": entry["highlights"],
                "startDate": _date(entry["start_date"]),
                "endDate": _date(entry["end_date"]),
                "url": entry["url"],
                "roles": [entry["role"]] if entry["role"] else [],
            }
        )
        for entry in data.entries("project")
    ]
    for kind in NO_PLACE:
        if data.entries(kind):
            dropped.add(kind)
    return {key: value for key, value in resume.items() if value}, dropped


def write(data: CVData) -> bytes:
    """The JSON Resume file: UTF-8 JSON, the same CV the same bytes."""
    found, _dropped = document(data)
    return (json.dumps(found, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def leaves_out(data: CVData) -> list[str]:
    """What of this CV the file has no place for, by `structured.LEFT_OUT`'s keys."""
    _found, dropped = document(data)
    order = (
        "form_of_address",
        "pronouns",
        "messaging",
        "birth_date",
        "birth_place",
        "nationality",
        "gender",
        "education_highlights",
        "eqf_level",
        *NO_PLACE,
    )
    return [key for key in order if key in dropped]
