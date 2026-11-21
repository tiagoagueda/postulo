"""Reading a JSON Resume file into a career record (#1260).

JSON Resume is the open résumé format published at jsonresume.org with a JSON Schema (MIT).
Postulo has written it since #1100; this reads it back, from any version of the schema
from 1.0.0 to 1.7.1: no field is required, extra fields are allowed everywhere, a date is
a year, a year and month or a whole day, and a location may be text or an object since
1.5.0.

**Tolerant, never validating.** A file is read for what it says. A value of the wrong type
loses that one value, and the review says so; the rest of the file is still read. Nothing
is guessed: a name is split into a first name and a surname only where it is two words, a
language's level is kept only where the file gives a CEFR level or says *native*, and what
the record has no place for is named under *What could not be read* rather than dropped.

No library: no maintained Python reader of the schema was found (2026-11; ``rezume`` pins
pydantic 1 and was last released in 2021), and the standard library's ``json`` is the parser.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from postulo.plugins.api import ImportRefused, refuse_unreadable

#: The top-level sections the schema names, any two of which say *this is a résumé*.
SECTIONS = (
    "basics",
    "objective",
    "work",
    "volunteer",
    "education",
    "awards",
    "certificates",
    "publications",
    "skills",
    "languages",
    "interests",
    "references",
    "projects",
    "meta",
    "custom",
)

#: How many entries of one section are read: a career is not this long, and the record is
#: kept in the session between reading it and adding it.
MAX_ENTRIES = 500

#: A date as the schema writes one: a year, a year and month, or a whole day.
REDUCED = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")

#: A level the file states as one of CEFR's, at the start of what it says: ``B2``,
#: ``C1 — advanced`` (what Postulo's own export writes).
CEFR = re.compile(r"\s*([ABCabc][12])(?![0-9A-Za-z])")

#: An ORCID iD inside an address or on its own.
ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dXx]")


def _parse(data: bytes):
    """The document, or ``None`` where the bytes are not JSON. Never raises."""
    try:
        text = data.decode("utf-8-sig")
        return json.loads(text, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _head(data: bytes) -> bytes:
    return data[:4096].lstrip(b"\xef\xbb\xbf").lstrip()


def recognises(data: bytes) -> bool:
    """Whether this is a JSON Resume file, by its shape rather than its name."""
    if not _head(data).startswith(b"{"):
        return False
    document = _parse(data)
    if document is None:
        # Cut short or damaged, and still plainly a résumé: `read` says it is not readable,
        # which is more use than "nothing here reads that".
        head = _head(data)
        return b'"basics"' in head or b"jsonresume" in head or b"resume-schema" in head
    if not isinstance(document, dict):
        return False
    # Postulo's own files, and Europass's JSON, are read elsewhere.
    if "postulo" in document or "SkillsPassport" in document or "LearnerInfo" in document:
        return False
    declared = document.get("$schema")
    if isinstance(declared, str) and ("jsonresume" in declared or "resume-schema" in declared):
        return True
    if isinstance(document.get("basics"), dict):
        return True
    return sum(1 for name in SECTIONS if name in document) >= 2


class _Reading:
    """One file being read: the record, and what is said about it on the way."""

    def __init__(self, record):
        self.record = record
        #: ``section.field`` the record has no place for, and how many entries had one.
        self.unmapped: Counter[str] = Counter()
        #: ``section.field`` that held something of the wrong kind.
        self.odd: Counter[str] = Counter()

    # ------------------------------------------------------------------- values

    def text(self, entry: dict, key: str, where: str) -> str:
        value = entry.get(key)
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        self.odd[f"{where}.{key}"] += 1
        return ""

    def lines(self, entry: dict, key: str, where: str) -> list[str]:
        value = entry.get(key)
        if value is None:
            return []
        if isinstance(value, list) and all(isinstance(one, str) for one in value):
            return [one.strip() for one in value if one.strip()]
        self.odd[f"{where}.{key}"] += 1
        return []

    def date(self, entry: dict, key: str, where: str) -> dt.date | None:
        """A reduced ISO 8601 date, its missing month or day the first; else none, said."""
        said = self.text(entry, key, where)
        if not said:
            return None
        found = REDUCED.fullmatch(said)
        if found is not None:
            year, month, day = (int(part) if part else 1 for part in found.groups())
            try:
                return dt.date(year, month, day)
            except ValueError:
                pass
        self.record.skipped.append(
            str(
                _("%(where)s: the date “%(date)s” is not one Postulo reads, so it was left out.")
                % {"where": where, "date": said[:40]}
            )
        )
        return None

    def place(self, value, where: str) -> str:
        """A location as text: what the file wrote, or the parts of an object joined."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            parts: list[str] = []
            for name in ("city", "region", "countryCode"):
                part = value.get(name)
                if isinstance(part, str) and part.strip() and part.strip() not in parts:
                    parts.append(part.strip())
            return ", ".join(parts)
        self.odd[f"{where}.location"] += 1
        return ""

    def entries(self, document: dict, section: str) -> list[tuple[str, dict]]:
        """The entries of a list section, each with where it is, as ``work[2]``."""
        value = document.get(section)
        if value is None:
            return []
        if not isinstance(value, list):
            self.odd[section] += 1
            return []
        found = []
        for place, entry in enumerate(value):
            if place >= MAX_ENTRIES:
                self.record.skipped.append(
                    str(
                        ngettext(
                            "%(section)s: only the first %(count)s entry was read.",
                            "%(section)s: only the first %(count)s entries were read.",
                            MAX_ENTRIES,
                        )
                        % {"section": section, "count": MAX_ENTRIES}
                    )
                )
                break
            if isinstance(entry, dict):
                found.append((f"{section}[{place}]", entry))
            else:
                self.odd[f"{section}[{place}]"] += 1
        return found

    def leftover(self, entry: dict, section: str, read: tuple[str, ...]) -> None:
        """Every field of an entry that is not read, counted under its section's name."""
        for key in entry:
            if key not in read and entry[key] not in (None, "", [], {}):
                self.unmapped[f"{section}.{key}"] += 1

    # ------------------------------------------------------------------ the end

    def say(self) -> None:
        if self.unmapped:
            listed = ", ".join(
                name if count == 1 else f"{name} ({count})"
                for name, count in sorted(self.unmapped.items())
            )
            self.record.skipped.append(
                str(
                    _(
                        "These were in the file and have no place in a career record in "
                        "Postulo, so they were not read: %(fields)s."
                    )
                    % {"fields": listed}
                )
            )
        if self.odd:
            listed = ", ".join(sorted(self.odd))
            self.record.skipped.append(
                str(
                    _(
                        "These held something JSON Resume does not put there, so they were "
                        "not read: %(fields)s."
                    )
                    % {"fields": listed}
                )
            )


def read(data: bytes):
    """Read a JSON Resume file. Raises `ImportRefused` with why, for the person."""
    from postulo.plugins.api import Record

    refuse_unreadable(data)
    document = _parse(data)
    if document is None:
        raise ImportRefused(
            _(
                "That file is not readable JSON, so it was not read. Export it again from "
                "the program that made it and upload that file as it is."
            )
        )
    if not isinstance(document, dict):
        raise ImportRefused(_("That JSON file is not a JSON Resume: it holds no résumé."))

    record = Record(
        source="json-resume",
        source_label=str(_("Read as JSON Resume, the open résumé format of jsonresume.org.")),
    )
    reading = _Reading(record)
    basics = document.get("basics")
    if isinstance(basics, dict):
        _read_basics(reading, basics)
    elif basics is not None:
        reading.odd["basics"] += 1
    _read_work(reading, document)
    _read_volunteering(reading, document)
    _read_education(reading, document)
    _read_awards(reading, document)
    _read_certificates(reading, document)
    _read_publications(reading, document)
    _read_skills(reading, document)
    _read_languages(reading, document)
    _read_references(reading, document)
    _read_projects(reading, document)
    for name in ("objective", "interests", "meta", "custom"):
        if document.get(name) not in (None, "", [], {}):
            reading.unmapped[name] += 1
    for name in document:
        if name not in SECTIONS and name != "$schema" and document[name] not in (None, "", [], {}):
            reading.unmapped[name] += 1
    reading.say()
    return record


# ---------------------------------------------------------------------- basics


def _read_basics(reading: _Reading, basics: dict) -> None:
    record, person = reading.record, {}
    name = " ".join(reading.text(basics, "name", "basics").split())
    words = name.split(" ") if name else []
    if len(words) == 2:
        person["first_name"], person["last_name"] = words
    elif name:
        # Which words are the surname is the person's to say, never a guess (#1260).
        record.skipped.append(
            str(
                _(
                    "The name “%(name)s” was not split into a first name and a surname, so it "
                    "was not added: add it under Your details."
                )
                % {"name": name[:150]}
            )
        )
    for key, field in (("label", "headline"), ("phone", "phone"), ("url", "website")):
        value = reading.text(basics, key, "basics")
        if value:
            person[field] = value
    email = reading.text(basics, "email", "basics")
    if email:
        # Shown on the review and never written: an address is the account's to verify.
        person["email"] = email
    location = basics.get("location")
    if isinstance(location, dict):
        address = {
            target: value.strip()
            for source, target in (
                ("address", "street"),
                ("postalCode", "postcode"),
                ("city", "municipality"),
                ("region", "region"),
            )
            if isinstance(value := location.get(source), str) and value.strip()
        }
        country = location.get("countryCode")
        if isinstance(country, str) and re.fullmatch(r"[A-Za-z]{2}", country.strip()):
            address["country"] = country.strip().upper()
        if address:
            person["address"] = address
        place = reading.place(location, "basics")
        if place:
            person["location"] = place
    elif isinstance(location, str) and location.strip():
        person["location"] = location.strip()
    elif location is not None:
        reading.odd["basics.location"] += 1
    others = []
    for where, profile in reading.entries(basics, "profiles"):
        network = reading.text(profile, "network", where)
        said = " ".join(
            filter(
                None,
                (reading.text(profile, "username", where), reading.text(profile, "url", where)),
            )
        )
        orcid = ORCID.search(said)
        if network.lower() == "orcid" and orcid and "orcid" not in person:
            person["orcid"] = orcid.group(0).upper()
        elif network or said:
            others.append(network or said[:60])
    if others:
        record.skipped.append(
            str(
                _(
                    "Profiles on other sites were not read: %(sites)s. Add the ones you want as "
                    "links."
                )
                % {"sites": ", ".join(others[:20])}
            )
        )
    reading.leftover(
        basics, "basics", ("name", "label", "phone", "url", "email", "location", "profiles")
    )
    record.person = person


# ----------------------------------------------------------------- the sections


def _read_work(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "work"):
        # `company` was the field's name before 1.0.0, and old files still say it.
        organisation = reading.text(entry, "name", where) or reading.text(entry, "company", where)
        role = reading.text(entry, "position", where)
        if not organisation and not role:
            reading.record.skipped.append(
                str(
                    _("%(where)s: a position with no employer and no title was not read.")
                    % {"where": where}
                )
            )
            continue
        reading.record.experience.append(
            {
                "role": role,
                "organisation": organisation,
                "location": reading.place(entry.get("location"), where),
                "start_date": reading.date(entry, "startDate", where),
                "end_date": reading.date(entry, "endDate", where),
                "summary": reading.text(entry, "summary", where),
                "highlights": "\n".join(reading.lines(entry, "highlights", where)),
            }
        )
        reading.leftover(
            entry,
            "work",
            (
                "name",
                "company",
                "position",
                "location",
                "startDate",
                "endDate",
                "summary",
                "highlights",
            ),
        )


def _read_volunteering(reading: _Reading, document: dict) -> None:
    found = reading.entries(document, "volunteer")
    for where, entry in found:
        organisation = reading.text(entry, "organization", where) or reading.text(
            entry, "organisation", where
        )
        if not organisation:
            reading.record.skipped.append(
                str(
                    _("%(where)s: volunteering with no organisation was not read.")
                    % {"where": where}
                )
            )
            continue
        url = reading.text(entry, "url", where)
        reading.record.memberships.append(
            {
                "organisation": organisation,
                "role": reading.text(entry, "position", where),
                "start_date": reading.date(entry, "startDate", where),
                "end_date": reading.date(entry, "endDate", where),
                "summary": "\n".join(
                    filter(
                        None,
                        (
                            reading.text(entry, "summary", where),
                            *reading.lines(entry, "highlights", where),
                        ),
                    )
                ),
                "url": url,
            }
        )
        reading.leftover(
            entry,
            "volunteer",
            (
                "organization",
                "organisation",
                "position",
                "startDate",
                "endDate",
                "summary",
                "highlights",
                "url",
            ),
        )
    if reading.record.memberships:
        # Postulo has no section for volunteering of its own (#1260, decision D5).
        reading.record.notes.append(
            str(
                _(
                    "Volunteering was read as memberships, since a career record in Postulo has "
                    "no section of its own for it."
                )
            )
        )


def _read_education(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "education"):
        institution = reading.text(entry, "institution", where)
        qualification = reading.text(entry, "studyType", where)
        area = reading.text(entry, "area", where)
        if not institution and not qualification and not area:
            reading.record.skipped.append(
                str(
                    _("%(where)s: education with no institution and no qualification was not read.")
                    % {"where": where}
                )
            )
            continue
        highlights = [
            reading.text(entry, "summary", where),
            *reading.lines(entry, "courses", where),
        ]
        reading.record.education.append(
            {
                "qualification": qualification,
                "institution": institution,
                "location": reading.place(entry.get("location"), where),
                "start_date": reading.date(entry, "startDate", where),
                "end_date": reading.date(entry, "endDate", where),
                "grade": reading.text(entry, "score", where),
                "highlights": "\n".join(filter(None, highlights)),
                "field_of_study": area,
            }
        )
        reading.leftover(
            entry,
            "education",
            (
                "institution",
                "studyType",
                "area",
                "location",
                "startDate",
                "endDate",
                "score",
                "summary",
                "courses",
            ),
        )


def _read_awards(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "awards"):
        title = reading.text(entry, "title", where)
        if not title:
            reading.record.skipped.append(
                str(_("%(where)s: an award with no title was not read.") % {"where": where})
            )
            continue
        reading.record.honours.append(
            {
                "title": title,
                "awarded_by": reading.text(entry, "awarder", where),
                "awarded_on": reading.date(entry, "date", where),
                "summary": reading.text(entry, "summary", where),
            }
        )
        reading.leftover(entry, "awards", ("title", "awarder", "date", "summary"))


def _read_certificates(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "certificates"):
        name = reading.text(entry, "name", where)
        if not name:
            reading.record.skipped.append(
                str(_("%(where)s: a certificate with no name was not read.") % {"where": where})
            )
            continue
        reading.record.certifications.append(
            {
                "name": name,
                "issuer": reading.text(entry, "issuer", where),
                "issued_on": reading.date(entry, "date", where),
                "expires_on": reading.date(entry, "expiryDate", where),
                "credential_url": reading.text(entry, "url", where),
            }
        )
        reading.leftover(entry, "certificates", ("name", "issuer", "date", "expiryDate", "url"))


def _read_publications(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "publications"):
        title = reading.text(entry, "name", where)
        if not title:
            reading.record.skipped.append(
                str(_("%(where)s: a publication with no title was not read.") % {"where": where})
            )
            continue
        url = reading.text(entry, "url", where)
        identifiers = entry.get("identifiers")
        doi = ""
        if isinstance(identifiers, dict):
            doi = reading.text(identifiers, "doi", f"{where}.identifiers")
        elif identifiers is not None:
            reading.odd[f"{where}.identifiers"] += 1
        if not doi and url.lower().startswith(("https://doi.org/", "http://doi.org/")):
            # Postulo's export writes the DOI's address where a paper has no link of its own.
            doi, url = url.split("doi.org/", 1)[1], ""
        date = reading.text(entry, "releaseDate", where)
        if date and REDUCED.fullmatch(date) is None:
            reading.record.skipped.append(
                str(
                    _(
                        "%(where)s: the date “%(date)s” is not one Postulo reads, so it was left "
                        "out."
                    )
                    % {"where": where, "date": date[:40]}
                )
            )
            date = ""
        reading.record.publications.append(
            {
                "title": title,
                "publisher": reading.text(entry, "publisher", where),
                "date": date,
                "url": url,
                "doi": doi,
            }
        )
        reading.leftover(
            entry, "publications", ("name", "publisher", "releaseDate", "url", "identifiers")
        )


def _read_skills(reading: _Reading, document: dict) -> None:
    loose: list[str] = []
    for where, entry in reading.entries(document, "skills"):
        name = reading.text(entry, "name", where)
        keywords = reading.lines(entry, "keywords", where)
        if keywords:
            reading.record.skill_groups.append(
                {"name": name or str(_("Skills")), "skills": keywords}
            )
        elif name:
            # A skill with no keywords is a skill, not a heading with nothing under it.
            loose.append(name)
        reading.leftover(entry, "skills", ("name", "keywords"))
    if loose:
        reading.record.skill_groups.append({"name": str(_("Skills")), "skills": loose})


def _read_languages(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "languages"):
        name = reading.text(entry, "language", where)
        if not name:
            reading.record.skipped.append(
                str(_("%(where)s: a language with no name was not read.") % {"where": where})
            )
            continue
        fluency = reading.text(entry, "fluency", where)
        cefr = CEFR.match(fluency)
        if cefr:
            proficiency, stated = cefr.group(1).lower(), ""
        elif "native" in fluency.lower():
            proficiency, stated = "native", ""
        else:
            # A level is a claim somebody will test: the file's words are shown, never
            # turned into one of Postulo's by a table (#235, #1260).
            proficiency, stated = "", fluency
        reading.record.languages.append(
            {"name": name, "proficiency": proficiency, "stated": stated}
        )
        reading.leftover(entry, "languages", ("language", "fluency"))


def _read_references(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "references"):
        name = reading.text(entry, "name", where)
        if not name:
            reading.record.skipped.append(
                str(_("%(where)s: a reference with no name was not read.") % {"where": where})
            )
            continue
        reading.record.references.append(
            {"name": name, "relationship": reading.text(entry, "relationship", where)}
        )
        # What a referee wrote is theirs, and a reference here is a person, not a quotation.
        reading.leftover(entry, "references", ("name", "relationship"))


def _read_projects(reading: _Reading, document: dict) -> None:
    for where, entry in reading.entries(document, "projects"):
        name = reading.text(entry, "name", where)
        if not name:
            reading.record.skipped.append(
                str(_("%(where)s: a project with no name was not read.") % {"where": where})
            )
            continue
        roles = reading.lines(entry, "roles", where)
        if len(roles) > 1:
            reading.unmapped["projects.roles"] += 1
        reading.record.projects.append(
            {
                "name": name,
                # `summary` since 1.6.0; `description` before, and still what Postulo writes.
                "summary": reading.text(entry, "summary", where)
                or reading.text(entry, "description", where),
                "url": reading.text(entry, "url", where),
                "role": roles[0] if roles else "",
                "start_date": reading.date(entry, "startDate", where),
                "end_date": reading.date(entry, "endDate", where),
                "highlights": "\n".join(reading.lines(entry, "highlights", where)),
            }
        )
        reading.leftover(
            entry,
            "projects",
            (
                "name",
                "summary",
                "description",
                "url",
                "roles",
                "startDate",
                "endDate",
                "highlights",
            ),
        )
