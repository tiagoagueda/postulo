"""Reading a career record out of a Europass file.

Anybody who has applied to an EU institution, or through a national employment service,
already has a Europass CV. Typing that career record into Postulo a second time is exactly
the work Postulo exists to remove, and the format is published, stable and free of licence
questions.

**What this reads.** The Europass XML — ``SkillsPassport``, from the CV editor and from
every export before the platform moved on. The current platform exports JSON instead
(#69); this module is deliberately shaped so that reader is a second front door and not a
second mapping: :func:`read` produces a :class:`Record`, and everything after it — the
review, the writing, the refusal to overwrite — works on that.

**Namespaces are ignored.** Europass XML has been through several namespaces
(``urn:europass:xml:2.0``, ``http://europass.cedefop.europa.eu/Europass``, others), and a
file that a person has on their disk may carry any of them. Matching on the local tag name
reads all of them; matching on the namespace reads whichever one was current when this was
written and then quietly stops working.

**Parsed defensively.** It is XML from somewhere else:

* a size cap, checked before parsing;
* **any ``DOCTYPE`` is refused outright**. That is where entity expansion lives — the
  billion-laughs attack and external entity fetches both need one — and a Europass file has
  no legitimate use for a document type declaration. Refusing it is a complete answer to
  both, and needs no dependency;
* nothing is fetched. No schema is resolved, no network is touched.

**Nothing is written by reading.** :func:`read` returns what it found; :func:`apply` writes
it, and only ever *adds*. An import never overwrites a career record: duplicates are the
person's to delete and are far better than something lost.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

#: Generous for a CV, mean for anything that is not one.
MAX_BYTES = 5 * 1024 * 1024

#: CEFR levels as Europass writes them, mapped onto Postulo's own.
CEFR = {"A1": "a1", "A2": "a2", "B1": "b1", "B2": "b2", "C1": "c1", "C2": "c2"}

#: The five skills Europass records separately for each foreign language.
CEFR_PARTS = ("Listening", "Reading", "SpokenInteraction", "SpokenProduction", "Writing")

_ORDER = list(CEFR.values())


class EuropassError(Exception):
    """The file could not be read, and the message says why in plain words."""


# ------------------------------------------------------------- what was found


@dataclass
class Record:
    """A career record, in Postulo's terms rather than Europass's.

    The intermediate shape both readers produce. Everything downstream works on this, so
    adding the JSON reader (#69) adds a front door and not a second mapping.
    """

    #: Personal details, to fill blanks on the profile and never to overwrite.
    person: dict = field(default_factory=dict)
    experience: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    skill_groups: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.experience, self.education, self.languages, self.skill_groups, self.projects)
        )

    def counts(self) -> dict[str, int]:
        return {
            "experience": len(self.experience),
            "education": len(self.education),
            "languages": len(self.languages),
            "skills": sum(len(group["skills"]) for group in self.skill_groups),
            "projects": len(self.projects),
        }


# ------------------------------------------------------------------ the parse


def _local(tag: str) -> str:
    """The tag without its namespace, which is how everything here is matched."""
    return tag.rsplit("}", 1)[-1]


def _find(element, *names: str):
    """The first descendant whose local name is ``names`` in order, or nothing."""
    current = element
    for name in names:
        found = None
        for child in current:
            if _local(child.tag) == name:
                found = child
                break
        if found is None:
            return None
        current = found
    return current


def _all(element, name: str) -> list:
    return [child for child in element if _local(child.tag) == name]


def _text(element, *names: str, keep_lines: bool = False) -> str:
    """The text of a node, tidied.

    ``keep_lines`` matters where the prose is a list: Europass puts skills in one block
    with a line each, so collapsing the newlines turns three skills into one long one.
    """
    found = _find(element, *names) if names else element
    if found is None or found.text is None:
        return ""
    if keep_lines:
        return "\n".join(" ".join(line.split()) for line in found.text.splitlines()).strip()
    return " ".join(found.text.split())


def _date(period, which: str) -> dt.date | None:
    """A Europass date, which carries year, month and day as attributes.

    A month or a day may be missing — people write "2019" and mean it — so what is absent
    becomes the first of the period rather than the import failing.
    """
    node = _find(period, which)
    if node is None:
        return None
    try:
        year = int(node.get("year") or "")
    except ValueError:
        return None
    try:
        month = max(1, min(12, int(node.get("month") or 1)))
        day = max(1, int(node.get("day") or 1))
    except (ValueError, TypeError):
        return None
    # The day is kept as written and only pulled back to the end of the month when the
    # month does not have it. Clamping every date to the 28th would silently move a
    # perfectly good 30 June, which is worse than the impossible date it was guarding
    # against.
    try:
        return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))
    except (ValueError, TypeError):
        return None


def _proficiency(level) -> str:
    """One CEFR level from the five Europass records for a language.

    Europass keeps listening, reading, spoken interaction, spoken production and writing
    apart, and a person is rarely the same at all five. Postulo keeps one, so this takes
    the **lowest**: claiming the highest of five on a CV is the kind of thing that gets
    found out in an interview, and the review page shows all five so it can be corrected.
    """
    if level is None:
        return ""
    found = []
    for part in CEFR_PARTS:
        node = _find(level, part)
        value = (node.get("level") if node is not None else "") or _text(level, part)
        value = (value or "").strip().upper()
        if value in CEFR:
            found.append(CEFR[value])
    if not found:
        return ""
    return min(found, key=_ORDER.index)


def _levels(level) -> dict[str, str]:
    """All five, so the review page can show what was thrown away."""
    if level is None:
        return {}
    out = {}
    for part in CEFR_PARTS:
        node = _find(level, part)
        value = (node.get("level") if node is not None else "") or _text(level, part)
        value = (value or "").strip().upper()
        if value:
            out[part] = value
    return out


def read(data: bytes) -> Record:
    """Read a Europass XML file. Raises :class:`EuropassError` with the reason."""
    if not data:
        raise EuropassError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise EuropassError(
            f"That file is larger than {MAX_BYTES // (1024 * 1024)} MB, so it was not read."
        )
    # Before parsing, not after: a DOCTYPE is where entity expansion lives, and the point
    # is to refuse it rather than to hand it to a parser and hope.
    head = data[:4096].lstrip()
    if re.search(rb"<!DOCTYPE", head, re.I):
        raise EuropassError(
            "That file carries a document type declaration, which Postulo will not read. "
            "A Europass export does not have one."
        )

    try:
        root = ElementTree.fromstring(data)  # noqa: S314 - no DOCTYPE, and nothing is fetched
    except ElementTree.ParseError as error:
        raise EuropassError(f"That file is not readable XML: {error}") from error

    learner = _find(root, "LearnerInfo")
    if learner is None and _local(root.tag) == "LearnerInfo":
        learner = root
    if learner is None:
        raise EuropassError(
            "That does not look like a Europass file: it has no LearnerInfo section."
        )

    record = Record()
    _read_person(learner, record)
    _read_experience(learner, record)
    _read_education(learner, record)
    _read_skills(learner, record)
    _read_achievements(learner, record)
    return record


def _read_person(learner, record: Record) -> None:
    identification = _find(learner, "Identification")
    if identification is None:
        return
    person: dict = {}
    name = _find(identification, "PersonName")
    if name is not None:
        person["first_name"] = _text(name, "FirstName")
        person["last_name"] = _text(name, "Surname")

    contact = _find(identification, "ContactInfo")
    if contact is not None:
        person["email"] = _text(contact, "Email", "Contact")
        person["phone"] = _text(contact, "Telephone", "Contact")
        person["website"] = _text(contact, "Website", "Contact")
        address = _find(contact, "Address", "Contact")
        if address is not None:
            municipality = _text(address, "Municipality")
            country = _text(address, "Country", "Label")
            person["location"] = ", ".join(part for part in (municipality, country) if part)

    headline = _find(learner, "Headline", "Description", "Label")
    if headline is not None:
        person["headline"] = _text(headline)

    record.person = {key: value for key, value in person.items() if value}


def _read_experience(learner, record: Record) -> None:
    block = _find(learner, "WorkExperience")
    if block is None:
        return
    # Europass nests one WorkExperience inside another; a file with a single entry
    # sometimes has only the outer one.
    entries = _all(block, "WorkExperience") or [block]
    for entry in entries:
        period = _find(entry, "Period")
        employer = _find(entry, "Employer")
        location = ""
        if employer is not None:
            address = _find(employer, "ContactInfo", "Address", "Contact")
            if address is not None:
                municipality = _text(address, "Municipality")
                country = _text(address, "Country", "Label")
                location = ", ".join(part for part in (municipality, country) if part)
        role = _text(entry, "Position", "Label") or _text(entry, "Position")
        organisation = _text(employer, "Name") if employer is not None else ""
        if not role and not organisation:
            continue
        record.experience.append(
            {
                "role": role,
                "organisation": organisation,
                "location": location,
                "start_date": _date(period, "From") if period is not None else None,
                "end_date": _date(period, "To") if period is not None else None,
                "summary": _text(entry, "Activities"),
            }
        )


def _read_education(learner, record: Record) -> None:
    block = _find(learner, "Education")
    if block is None:
        return
    entries = _all(block, "Education") or [block]
    for entry in entries:
        period = _find(entry, "Period")
        organisation = _find(entry, "Organisation")
        location = ""
        if organisation is not None:
            address = _find(organisation, "ContactInfo", "Address", "Contact")
            if address is not None:
                municipality = _text(address, "Municipality")
                country = _text(address, "Country", "Label")
                location = ", ".join(part for part in (municipality, country) if part)
        qualification = _text(entry, "Title")
        institution = _text(organisation, "Name") if organisation is not None else ""
        if not qualification and not institution:
            continue
        record.education.append(
            {
                "qualification": qualification,
                "institution": institution,
                "location": location,
                "start_date": _date(period, "From") if period is not None else None,
                "end_date": _date(period, "To") if period is not None else None,
                "grade": _text(entry, "Level", "Label"),
                "highlights": _text(entry, "Activities"),
            }
        )


def _read_skills(learner, record: Record) -> None:
    skills = _find(learner, "Skills")
    if skills is None:
        return

    linguistic = _find(skills, "Linguistic")
    if linguistic is not None:
        for mother in _all(linguistic, "MotherTongue"):
            name = _text(mother, "Description", "Label")
            if name:
                record.languages.append({"name": name, "proficiency": "native", "levels": {}})
        for foreign in _all(linguistic, "ForeignLanguage"):
            name = _text(foreign, "Description", "Label")
            if not name:
                continue
            level = _find(foreign, "ProficiencyLevel")
            record.languages.append(
                {
                    "name": name,
                    "proficiency": _proficiency(level),
                    "levels": _levels(level),
                }
            )

    # Europass keeps these as free prose under a handful of headings. Each heading becomes
    # a skill group and each line becomes a skill, which is as close as the two shapes get.
    for heading, label in (
        ("Computer", "Digital"),
        ("Organisational", "Organisational"),
        ("Communication", "Communication"),
        ("JobRelated", "Job-related"),
        ("Other", "Other"),
    ):
        block = _find(skills, heading)
        if block is None:
            continue
        prose = _text(block, "Description", "Label", keep_lines=True) or _text(
            block, "Description", keep_lines=True
        )
        lines = [line.strip(" -•\t") for line in re.split(r"[\n;]+", prose) if line.strip(" -•\t")]
        if lines:
            record.skill_groups.append({"name": label, "skills": lines[:40]})


def _read_achievements(learner, record: Record) -> None:
    block = _find(learner, "Achievement")
    if block is None:
        return
    entries = _all(block, "Achievement") or [block]
    for entry in entries:
        title = _text(entry, "Title", "Label") or _text(entry, "Title")
        description = _text(entry, "Description", "Label") or _text(entry, "Description")
        if not title and not description:
            continue
        record.projects.append(
            {"name": title or description[:80], "summary": description if title else ""}
        )


# ------------------------------------------------------------------- writing


@dataclass
class Report:
    """What an import actually added."""

    added: dict[str, int] = field(default_factory=dict)
    profile_filled: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.added.values())


def apply(owner, record: Record) -> Report:
    """Write what was found. Only ever adds; nothing existing is changed or removed.

    Blank fields on the profile are filled, because a blank is not an opinion. A field
    that already says something is left exactly as it is — somebody's own words about
    themselves beat a form they filled in years ago.
    """
    from .models import Education, Experience, LanguageSkill, Project, Skill, SkillGroup

    report = Report()

    profile = getattr(owner, "profile", None)
    if profile is not None and record.person:
        changed = []
        for field_name in ("headline", "phone", "location", "website"):
            value = record.person.get(field_name)
            if value and not getattr(profile, field_name, ""):
                setattr(profile, field_name, value[:200])
                changed.append(field_name)
        if changed:
            fields = [*changed, "updated_at"] if hasattr(profile, "updated_at") else changed
            profile.save(update_fields=fields)
            report.profile_filled = changed
    for field_name in ("first_name", "last_name"):
        value = record.person.get(field_name)
        if value and not getattr(owner, field_name, ""):
            setattr(owner, field_name, value[:150])
            owner.save(update_fields=[field_name])
            report.profile_filled.append(field_name)

    for entry in record.experience:
        if not entry.get("start_date"):
            # Experience needs a start; without one there is nothing to order it by.
            continue
        Experience.objects.create(
            owner=owner,
            role=entry["role"][:200],
            organisation=entry["organisation"][:200],
            location=entry["location"][:200],
            start_date=entry["start_date"],
            end_date=entry["end_date"],
            summary=entry["summary"],
        )
        report.added["experience"] = report.added.get("experience", 0) + 1

    for entry in record.education:
        Education.objects.create(
            owner=owner,
            qualification=entry["qualification"][:200],
            institution=entry["institution"][:200],
            location=entry["location"][:200],
            start_date=entry["start_date"],
            end_date=entry["end_date"],
            grade=entry["grade"][:100],
            highlights=entry["highlights"],
        )
        report.added["education"] = report.added.get("education", 0) + 1

    for entry in record.languages:
        LanguageSkill.objects.create(
            owner=owner,
            name=entry["name"][:100],
            proficiency=entry["proficiency"] or "b1",
        )
        report.added["languages"] = report.added.get("languages", 0) + 1

    for group in record.skill_groups:
        group_name = group["name"][:100]
        # A heading somebody already has is reused. Two "Languages" headings on one CV is
        # a mess they then have to tidy, and adding the skills under the existing one is
        # still only ever adding.
        made = SkillGroup.objects.filter(owner=owner, name=group_name).first()
        if made is None:
            made = SkillGroup.objects.create(owner=owner, name=group_name)
            report.added["skill_groups"] = report.added.get("skill_groups", 0) + 1
        for name in group["skills"]:
            Skill.objects.create(owner=owner, group=made, name=name[:100])
            report.added["skills"] = report.added.get("skills", 0) + 1

    for entry in record.projects:
        Project.objects.create(
            owner=owner, name=entry["name"][:200], summary=entry.get("summary", "")
        )
        report.added["projects"] = report.added.get("projects", 0) + 1

    return report
