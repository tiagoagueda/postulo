"""Reading a career record out of a Europass file.

Anybody who has applied to an EU institution, or through a national employment service,
already has a Europass CV. Typing that career record into Postulo a second time is exactly
the work Postulo exists to remove, and the format is published, stable and free of licence
questions.

**Two formats, one mapping.** Europass has been written down two ways:

* the **XML** — ``SkillsPassport``, from the 2004 CV editor and from every export before
  the platform moved on. Nobody is producing it any more, and importing it is about
  rescuing what people already have on their disks;
* the **JSON** — what europass.europa.eu exports today, and so the one a person is most
  likely to arrive with.

They describe the same career in the same words, so they share everything after the parse:
:func:`read` sniffs which it has been handed and returns a :class:`Record`, and the review,
the writing and the refusal to overwrite all work on that. A second copy of the mapping
would be a second place for the two to drift apart.

**Namespaces are ignored.** Europass XML has been through several namespaces
(``urn:europass:xml:2.0``, ``http://europass.cedefop.europa.eu/Europass``, others), and a
file that a person has on their disk may carry any of them. Matching on the local tag name
reads all of them; matching on the namespace reads whichever one was current when this was
written and then quietly stops working.

**Parsed defensively.** It is a file from somewhere else:

* a size cap, checked before parsing;
* **any ``DOCTYPE`` is refused outright**. That is where entity expansion lives — the
  billion-laughs attack and external entity fetches both need one — and a Europass file has
  no legitimate use for a document type declaration. Refusing it is a complete answer to
  both, and needs no dependency;
* a nesting cap on the JSON. A career record is not forty levels deep, so anything that is
  is not one;
* **no key is assumed to be present, and no value is assumed to have the type it should**.
  A file that is half right imports the half that is right and says what it skipped, which
  is more use to somebody than refusing the lot;
* nothing is fetched. No schema is resolved, no network is touched.

**Nothing is written by reading.** :func:`read` returns what it found; :func:`apply` writes
it, and only ever *adds*. An import never overwrites a career record: duplicates are the
person's to delete and are far better than something lost.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from postulo.accounts import identifiers

#: Generous for a CV, mean for anything that is not one.
MAX_BYTES = 5 * 1024 * 1024

#: A career record is not this deep. Anything that is, is not one.
MAX_DEPTH = 40

#: CEFR levels as Europass writes them, mapped onto Postulo's own.
CEFR = {"A1": "a1", "A2": "a2", "B1": "b1", "B2": "b2", "C1": "c1", "C2": "c2"}

#: The five skills Europass records separately for each foreign language.
CEFR_PARTS = ("Listening", "Reading", "SpokenInteraction", "SpokenProduction", "Writing")

#: The Europass headings for free-prose skills, and what Postulo calls each group.
SKILL_HEADINGS = (
    ("Computer", "Digital"),
    ("Organisational", "Organisational"),
    ("Communication", "Communication"),
    ("JobRelated", "Job-related"),
    ("Other", "Other"),
)

_ORDER = list(CEFR.values())


class EuropassError(Exception):
    """The file could not be read, and the message says why in plain words."""


# ------------------------------------------------------------- what was found


@dataclass
class Record:
    """A career record, in Postulo's terms rather than Europass's.

    The intermediate shape both readers produce. Everything downstream works on this, so
    the JSON reader is a second front door and not a second mapping.
    """

    #: Personal details, to fill blanks on the profile and never to overwrite.
    person: dict = field(default_factory=dict)
    experience: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    skill_groups: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    #: Which of the two formats this came out of: ``"xml"`` or ``"json"``.
    source: str = ""
    #: What was in the file and could not be read, in words, to be shown before importing.
    skipped: list[str] = field(default_factory=list)

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


# ------------------------------------------------------- shared by both formats


def _split_skills(prose: str) -> list[str]:
    """Europass keeps skills as free prose; a line or a semicolon is a boundary."""
    return [line.strip(" -•\t") for line in re.split(r"[\n;]+", prose) if line.strip(" -•\t")]


def _lowest(levels: dict[str, str]) -> str:
    """One CEFR level out of the five Europass records for a language.

    Europass keeps listening, reading, spoken interaction, spoken production and writing
    apart, and a person is rarely the same at all five. Postulo keeps one, so this takes
    the **lowest**: claiming the highest of five on a CV is the kind of thing that gets
    found out in an interview, and the review page shows all five so it can be corrected.
    """
    found = [CEFR[value] for value in levels.values() if value in CEFR]
    return min(found, key=_ORDER.index) if found else ""


def _make_date(year, month, day) -> dt.date | None:
    """A date out of three numbers that arrived from somewhere else.

    A month or a day may be missing — people write "2019" and mean it — so what is
    **absent** becomes the first of the period rather than the import failing. What is
    **present and unreadable** is a different thing: no date is produced at all, because
    turning a nonsense month into January could misdate a job by eleven months, and an
    entry with no start is named in the report rather than written.

    The day is kept as written and only pulled back to the end of the month when the month
    does not have it: clamping every date to the 28th would silently move a perfectly good
    30 June, which is worse than the impossible date it was guarding against.
    """
    try:
        year = int(year)
        month = max(1, min(12, int(month or 1)))
        day = max(1, int(day or 1))
    except (TypeError, ValueError):
        return None
    try:
        return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))
    except (ValueError, TypeError):
        return None


def _place(municipality: str, country: str) -> str:
    return ", ".join(part for part in (municipality, country) if part)


def _orcid_from(addresses: list[str]) -> str:
    """An ORCID out of the websites a Europass file lists, if one of them is one.

    Neither format has a field for it, and everybody who has one puts it among their
    websites. It is worth lifting out because in the places Postulo is aimed at first it is
    the identifier an application form asks for by name (#46). The checksum decides: a
    wrong one is dropped rather than saved, and orcid.org is asked nothing.
    """
    for address in addresses:
        if "orcid.org" not in address:
            continue
        try:
            return identifiers.clean(identifiers.ORCID, address)
        except ValidationError:
            continue
    return ""


def _project_from(title: str, description: str) -> dict | None:
    if not title and not description:
        return None
    return {"name": title or description[:80], "summary": description if title else ""}


# --------------------------------------------------------------- which format


def read(data: bytes) -> Record:
    """Read a Europass file, in whichever of the two formats it is.

    A person has *a Europass file*; they should not have to know which one it is, so the
    first character decides and the record says which was found.
    """
    if not data:
        raise EuropassError(_("That file is empty."))
    if len(data) > MAX_BYTES:
        raise EuropassError(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // (1024 * 1024)}
        )

    head = data.lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<"):
        return read_xml(data)
    if head.startswith(b"{"):
        return read_json(data)
    raise EuropassError(
        _(
            "That is not a Europass file. Postulo reads the XML the CV editor produced and "
            "the JSON europass.europa.eu exports; this is neither."
        )
    )


# -------------------------------------------------------------------- the XML


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
    """A Europass date, which carries year, month and day as attributes."""
    node = _find(period, which)
    if node is None:
        return None
    return _make_date(node.get("year"), node.get("month"), node.get("day"))


def _levels(level) -> dict[str, str]:
    """All five, so the review page can show what was set aside."""
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


def read_xml(data: bytes) -> Record:
    """Read the Europass XML. Raises :class:`EuropassError` with the reason."""
    # Before parsing, not after: a DOCTYPE is where entity expansion lives, and the point
    # is to refuse it rather than to hand it to a parser and hope.
    head = data[:4096].lstrip()
    if re.search(rb"<!DOCTYPE", head, re.I):
        raise EuropassError(
            _(
                "That file carries a document type declaration, which Postulo will not "
                "read. A Europass export does not have one."
            )
        )

    try:
        root = ElementTree.fromstring(data)  # noqa: S314 - no DOCTYPE, and nothing is fetched
    except ElementTree.ParseError as error:
        raise EuropassError(
            _("That file is not readable XML: %(reason)s") % {"reason": error}
        ) from error

    learner = _find(root, "LearnerInfo")
    if learner is None and _local(root.tag) == "LearnerInfo":
        learner = root
    if learner is None:
        raise EuropassError(
            _("That does not look like a Europass file: it has no LearnerInfo section.")
        )

    record = Record(source="xml")
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
        websites = [_text(site, "Contact") for site in _all(contact, "Website")]
        websites = [site for site in websites if site]
        person["website"] = next(iter(websites), "")
        person["orcid"] = _orcid_from(websites)
        address = _find(contact, "Address", "Contact")
        if address is not None:
            person["location"] = _place(
                _text(address, "Municipality"), _text(address, "Country", "Label")
            )

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
                location = _place(
                    _text(address, "Municipality"), _text(address, "Country", "Label")
                )
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
                location = _place(
                    _text(address, "Municipality"), _text(address, "Country", "Label")
                )
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
            levels = _levels(_find(foreign, "ProficiencyLevel"))
            record.languages.append(
                {"name": name, "proficiency": _lowest(levels), "levels": levels}
            )

    # Europass keeps these as free prose under a handful of headings. Each heading becomes
    # a skill group and each line becomes a skill, which is as close as the two shapes get.
    for heading, label in SKILL_HEADINGS:
        block = _find(skills, heading)
        if block is None:
            continue
        prose = _text(block, "Description", "Label", keep_lines=True) or _text(
            block, "Description", keep_lines=True
        )
        lines = _split_skills(prose)
        if lines:
            record.skill_groups.append({"name": label, "skills": lines[:40]})


def _read_achievements(learner, record: Record) -> None:
    block = _find(learner, "Achievement")
    if block is None:
        return
    entries = _all(block, "Achievement") or [block]
    for entry in entries:
        project = _project_from(
            _text(entry, "Title", "Label") or _text(entry, "Title"),
            _text(entry, "Description", "Label") or _text(entry, "Description"),
        )
        if project:
            record.projects.append(project)


# ------------------------------------------------------------------- the JSON


def _obj(value):
    """A mapping, or nothing. An absent block is written as ``null`` or left out."""
    return value if isinstance(value, dict) else None


def _rows(value) -> list[dict]:
    """A list of mappings.

    Europass writes one entry as an object and several as an array, and exports differ on
    which they do when there is exactly one. Both read the same here.
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _string(value, *, keep_lines: bool = False) -> str:
    """A string out of whatever is there: a string, a number, or an object's ``Label``."""
    if isinstance(value, str):
        if keep_lines:
            return "\n".join(" ".join(line.split()) for line in value.splitlines()).strip()
        return " ".join(value.split())
    if isinstance(value, dict):
        return _string(value.get("Label"), keep_lines=keep_lines)
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _dig(node, *names: str):
    """Follow a path of keys, stopping at the first thing that is not a mapping."""
    current = node
    for name in names:
        current = _obj(current)
        if current is None:
            return None
        current = current.get(name)
    return current


def _json_text(node, *names: str, keep_lines: bool = False) -> str:
    return _string(_dig(node, *names) if names else node, keep_lines=keep_lines)


def _contacts(value) -> list[str]:
    """The addresses under an Email, Telephone or Website block, in the order given."""
    found = [_string(row.get("Contact")) for row in _rows(value)]
    if not found:
        found = [_string(value)]
    return [item for item in found if item]


def _json_date(period, which: str) -> dt.date | None:
    node = _obj(_dig(period, which))
    if node is None:
        return None
    return _make_date(node.get("Year"), node.get("Month"), node.get("Day"))


def _json_place(node) -> str:
    address = _dig(node, "ContactInfo", "Address", "Contact")
    if address is None:
        return ""
    return _place(_json_text(address, "Municipality"), _json_text(address, "Country", "Label"))


def _depth(value) -> int:
    """How deeply the parsed document nests, measured without recursing into it.

    Iterative on purpose: measuring recursion with recursion is how the guard becomes the
    thing it was guarding against.
    """
    worst = 0
    stack = [(value, 1)]
    while stack:
        node, level = stack.pop()
        worst = max(worst, level)
        if level > MAX_DEPTH:
            return level
        if isinstance(node, dict):
            stack.extend((child, level + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, level + 1) for child in node)
    return worst


def read_json(data: bytes) -> Record:
    """Read the Europass JSON. Raises :class:`EuropassError` with the reason."""
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise EuropassError(_("That file is not UTF-8, so it is not a Europass export.")) from error
    except (json.JSONDecodeError, RecursionError) as error:
        raise EuropassError(
            _("That file is not readable JSON: %(reason)s") % {"reason": error}
        ) from error

    if _depth(document) > MAX_DEPTH:
        raise EuropassError(
            _("That file nests more than %(limit)s levels deep, so it was not read.")
            % {"limit": MAX_DEPTH}
        )

    # The wrapper has been written both ways: an export may put LearnerInfo inside a
    # SkillsPassport object, or hand it over on its own.
    learner = _obj(_dig(document, "SkillsPassport", "LearnerInfo")) or _obj(
        _dig(document, "LearnerInfo")
    )
    if learner is None:
        raise EuropassError(
            _("That does not look like a Europass file: it has no LearnerInfo section.")
        )

    record = Record(source="json")
    _read_json_person(learner, record)
    _read_json_experience(learner, record)
    _read_json_education(learner, record)
    _read_json_skills(learner, record)
    _read_json_achievements(learner, record)
    return record


def _readable(record: Record, value, what) -> bool:
    """Whether a block is the shape it should be, and a note in the record if it is not.

    Silence would be worse. An export whose ``WorkExperience`` is a string rather than a
    list would otherwise import as an empty career and look like a file with nothing in it.
    """
    if value is None or _rows(value):
        return True
    record.skipped.append(
        str(_("%(what)s was in the file but could not be read.") % {"what": what})
    )
    return False


def _read_json_person(learner: dict, record: Record) -> None:
    identification = _obj(learner.get("Identification"))
    if identification is None:
        return
    person: dict = {}
    name = _obj(identification.get("PersonName")) or {}
    person["first_name"] = _string(name.get("FirstName"))
    person["last_name"] = _string(name.get("Surname"))

    contact = _obj(identification.get("ContactInfo")) or {}
    person["email"] = next(iter(_contacts(contact.get("Email"))), "")
    person["phone"] = next(iter(_contacts(contact.get("Telephone"))), "")
    websites = _contacts(contact.get("Website"))
    person["website"] = next(iter(websites), "")
    person["orcid"] = _orcid_from(websites)
    address = _dig(contact, "Address", "Contact")
    if address is not None:
        person["location"] = _place(
            _json_text(address, "Municipality"), _json_text(address, "Country", "Label")
        )

    person["headline"] = _json_text(learner, "Headline", "Description")
    record.person = {key: value for key, value in person.items() if value}


def _read_json_experience(learner: dict, record: Record) -> None:
    block = learner.get("WorkExperience")
    if not _readable(record, block, _("Work experience")):
        return
    for entry in _rows(block):
        period = _obj(entry.get("Period"))
        employer = _obj(entry.get("Employer")) or {}
        role = _json_text(entry, "Position")
        organisation = _string(employer.get("Name"))
        if not role and not organisation:
            continue
        record.experience.append(
            {
                "role": role,
                "organisation": organisation,
                "location": _json_place(employer),
                "start_date": _json_date(period, "From"),
                "end_date": _json_date(period, "To"),
                "summary": _json_text(entry, "Activities"),
            }
        )


def _read_json_education(learner: dict, record: Record) -> None:
    block = learner.get("Education")
    if not _readable(record, block, _("Education")):
        return
    for entry in _rows(block):
        period = _obj(entry.get("Period"))
        organisation = _obj(entry.get("Organisation")) or {}
        qualification = _json_text(entry, "Title")
        institution = _string(organisation.get("Name"))
        if not qualification and not institution:
            continue
        record.education.append(
            {
                "qualification": qualification,
                "institution": institution,
                "location": _json_place(organisation),
                "start_date": _json_date(period, "From"),
                "end_date": _json_date(period, "To"),
                "grade": _json_text(entry, "Level"),
                "highlights": _json_text(entry, "Activities"),
            }
        )


def _read_json_skills(learner: dict, record: Record) -> None:
    skills = _obj(learner.get("Skills"))
    if skills is None:
        return

    linguistic = _obj(skills.get("Linguistic")) or {}
    for mother in _rows(linguistic.get("MotherTongue")):
        name = _json_text(mother, "Description")
        if name:
            record.languages.append({"name": name, "proficiency": "native", "levels": {}})
    for foreign in _rows(linguistic.get("ForeignLanguage")):
        name = _json_text(foreign, "Description")
        if not name:
            continue
        node = _obj(foreign.get("ProficiencyLevel")) or {}
        levels = {}
        for part in CEFR_PARTS:
            value = _string(node.get(part)).strip().upper()
            if value:
                levels[part] = value
        record.languages.append({"name": name, "proficiency": _lowest(levels), "levels": levels})

    for heading, label in SKILL_HEADINGS:
        block = _obj(skills.get(heading))
        if block is None:
            continue
        lines = _split_skills(_json_text(block, "Description", keep_lines=True))
        if lines:
            record.skill_groups.append({"name": label, "skills": lines[:40]})


def _read_json_achievements(learner: dict, record: Record) -> None:
    block = learner.get("Achievement")
    if not _readable(record, block, _("Achievements")):
        return
    for entry in _rows(block):
        project = _project_from(_json_text(entry, "Title"), _json_text(entry, "Description"))
        if project:
            record.projects.append(project)


# ------------------------------------------------------------------- writing


@dataclass
class Report:
    """What an import actually added."""

    added: dict[str, int] = field(default_factory=dict)
    profile_filled: list[str] = field(default_factory=list)
    #: Entries that were read but not written, in words, so nothing goes missing quietly.
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.added.values())


def apply(owner, record: Record) -> Report:
    """Write what was found. Only ever adds; nothing existing is changed or removed.

    Blank fields on the profile are filled, because a blank is not an opinion. A field
    that already says something is left exactly as it is — somebody's own words about
    themselves beat a form they filled in years ago.
    """
    from postulo.accounts.models import PersonIdentifier

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

        # An ORCID somebody already has is theirs; a second one is not an improvement.
        orcid = record.person.get("orcid")
        if orcid and not profile.identifiers.filter(scheme=identifiers.ORCID).exists():
            PersonIdentifier.objects.create(profile=profile, scheme=identifiers.ORCID, value=orcid)
            report.profile_filled.append("orcid")

    for field_name in ("first_name", "last_name"):
        value = record.person.get(field_name)
        if value and not getattr(owner, field_name, ""):
            setattr(owner, field_name, value[:150])
            owner.save(update_fields=[field_name])
            report.profile_filled.append(field_name)

    for entry in record.experience:
        if not entry.get("start_date"):
            # Experience needs a start; without one there is nothing to order it by. Said
            # out loud rather than dropped, so a half-dated file does not lose a job
            # quietly.
            report.skipped.append(
                str(
                    _("%(role)s: no start date, so it was not added.")
                    % {"role": entry["role"] or entry["organisation"]}
                )
            )
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
