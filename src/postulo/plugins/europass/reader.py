"""Reading a career record out of a Europass file.

Anybody who has applied to an EU institution, or through a national employment service,
already has a Europass CV. Typing that career record into Postulo a second time is exactly
the work Postulo exists to remove, and the format is published, stable and free of licence
questions.

**Three formats, one record.** Europass has been written down three ways, and this module
used to believe the wrong one was current (#244):

* the **Candidate XML** -- what europass.europa.eu has written since the platform moved in
  July 2020: an HR Open Standards ``Candidate`` in ``http://www.europass.eu/1.0``. The
  editor's only download is a PDF with this attached to it, so that is the file a person
  is most likely to arrive with. ``candidate.py`` reads it and ``pdf.py`` reaches it;
* the **SkillsPassport XML** -- Cedefop's editor, 2004 to 2020, downloaded as XML or
  attached to a PDF in the same way. Nothing produces it any more; importing it is about
  rescuing what people already have on their disks;
* the **SkillsPassport JSON** -- the same format as Cedefop's REST API wrote it. It was
  never a download of either editor, and it was not what europass.europa.eu exports, which
  is what this paragraph said until the claim was checked.

They describe the same career, so they share everything after the parse: :func:`read`
works out which it has been handed and fills in a
:class:`~postulo.resume.importing.Record`, and the review, the writing and the refusal to
overwrite all work on that. A second copy of the mapping would be a second place for them
to drift apart; the Candidate reader follows the Commission's own crosswalk from the old
format so that a career arrives the same way from either.

The record is **Postulo's** shape rather than Europass's, which is why it is Postulo's to
define: this package reads a file into it, and the next importer somebody writes reads a
different file into the same one (#129).

**Which XML is decided by its root.** The first byte cannot tell two XML formats apart, so
:func:`read_xml` looks at the root element: a ``Candidate`` in a Europass namespace is the
current format, and a ``SkillsPassport`` or ``LearnerInfo`` is the old one. The old format's
namespace is ignored -- it has been through several (``urn:europass:xml:2.0``,
``http://europass.cedefop.europa.eu/Europass``, others), and a file on somebody's disk may
carry any of them. The new one's is not: a ``Candidate`` is an HR Open Standards noun that
other systems write too, and only Europass's is read as a Europass CV.

**Parsed defensively.** It is a file from somewhere else:

* a size cap, checked before parsing;
* **any ``DOCTYPE`` is refused outright**. That is where entity expansion lives — the
  billion-laughs attack and external entity fetches both need one — and a Europass file has
  no legitimate use for a document type declaration. Refusing it is a complete answer to
  both. It is decided by an XML parser, not by looking for bytes, so a comment before
  it or UTF-16 hides nothing (#378);
* a nesting cap on the JSON. A career record is not forty levels deep, so anything that is
  is not one;
* **no key is assumed to be present, and no value is assumed to have the type it should**.
  A file that is half right imports the half that is right and says what it skipped, which
  is more use to somebody than refusing the lot;
* **what a PDF carries is a second file**, and goes through every one of these guards
  again, after it has been unpacked under a cap of its own;
* nothing is fetched. No schema is resolved, no network is touched.

**Nothing is written by reading**, and that is the whole line between this package and
Postulo. :func:`read` returns what it found and touches no database; ``resume.importing``
writes it, and only ever *adds* -- an import never overwrites a career record, because
duplicates are the person's to delete and are far better than something lost. Which is why
an importer needs no ownership scoping of its own, here or in anybody else's package (#129).
"""

from __future__ import annotations

import datetime as dt
import json
from xml.etree import ElementTree

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as parse_xml
from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import MAX_IMPORT_BYTES, ImportRefused, Record, refuse_unreadable

from . import pdf
from .common import (
    CEFR_PARTS,
    _all,
    _country,
    _find,
    _heading,
    _local,
    _locale,
    _lowest,
    _make_date,
    _orcid_from,
    _project_from,
    _split_skills,
    _text,
)
from .markup import _plain

#: Kept as a name because the interface and the tests use it, but the number belongs to
#: the importer kind now: every importer gets the same cap, not just this one.
MAX_BYTES = MAX_IMPORT_BYTES

#: A career record is not this deep. Anything that is, is not one.
MAX_DEPTH = 40

#: The Europass headings for free-prose skills, and what Postulo calls each group.
#:
#: The heading on the left is the tag, which Europass writes in English whatever language
#: the file is in. The name on the right is a **heading on somebody's CV**, so it is
#: translated: a Portuguese record used to arrive with a section called "Job-related" in it,
#: which is not a word its owner would have written (#235). Europass ships its own
#: catalogues, because core never translates a plugin's strings.
SKILL_HEADINGS = (
    ("Computer", _("Digital")),
    ("Organisational", _("Organisational")),
    ("Communication", _("Communication")),
    ("JobRelated", _("Job-related")),
    ("Other", _("Other")),
)


class EuropassError(ImportRefused):
    """The file could not be read, and the message says why in plain words."""


# ------------------------------------------------------------- what was found


# ------------------------------------------------------- shared by both formats


def _country_code(address) -> str:
    """The two-letter code Europass puts on a country, or empty.

    The code rather than the label: `PostalAddress.country` is ISO 3166-1 alpha-2, and the
    label is a name in whatever language the file was written in.
    """
    country = _find(address, "Country")
    if country is None:
        return ""
    return _country(_text(country, "Code") or "")


def _json_country_code(address) -> str:
    country = (_obj(address.get("Country")) if isinstance(address, dict) else None) or {}
    return _country(str(country.get("Code") or ""))


def _place(municipality: str, country: str) -> str:
    return ", ".join(part for part in (municipality, country) if part)


# --------------------------------------------------------------- which format


def read(data: bytes) -> Record:
    """Read a Europass file, in whichever of its formats it is.

    A person has *a Europass file*; they should not have to know which one it is, so the
    file decides -- a PDF by its header, XML and JSON by their first character, and which
    XML by its root -- and the record says which was found.
    """
    # Empty, oversized and DOCTYPE are the importer kind's refusals now rather than this
    # module's. An importer is handed a file somebody uploaded, and that is a threat every
    # importer faces rather than one Europass happened to think about.
    refuse_unreadable(data)

    if pdf.is_pdf(data):
        return read_pdf(data)
    return _read_document(data)


def _read_document(data: bytes) -> Record:
    """XML or JSON: what a file can be, and what a PDF's attachment can be.

    Never a PDF. A PDF attached to a PDF is not a Europass CV, and reading one would make
    the attachment's guards the only thing standing between a nest of them and the server.
    """
    head = data.lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<"):
        return read_xml(data)
    if head.startswith(b"{"):
        return read_json(data)
    raise EuropassError(
        _(
            "That is not a Europass file. Postulo reads the PDF europass.europa.eu gives you, "
            "the XML attached to it, and the older Europass XML and JSON; this is none of "
            "them."
        )
    )


# -------------------------------------------------------------------- the PDF

#: What to say when a PDF's attachment cannot be reached, by the reason `pdf` gives.
#: Each says what happened and what to upload instead.
PDF_REFUSALS = {
    pdf.ENCRYPTED: _(
        "That PDF is encrypted, so the Europass CV attached to it cannot be read. Download "
        "the CV again from europass.europa.eu and upload that file as it is."
    ),
    pdf.FILTER: _(
        "The file attached to that PDF is packed in a way Postulo does not read "
        "(%(filter)s). Download the CV again from europass.europa.eu and upload that file "
        "as it is."
    ),
    pdf.DAMAGED: _(
        "The file attached to that PDF is damaged and could not be unpacked. Download the "
        "CV again from europass.europa.eu and upload that file as it is."
    ),
    pdf.TOO_LARGE: _(
        "The file attached to that PDF unpacks to more than %(limit)s MB, so it was not read."
    ),
}


def read_pdf(data: bytes) -> Record:
    """Read the Europass CV a PDF carries attached. Raises :class:`EuropassError` with why.

    What comes out of the PDF is a second file and is treated as one: it goes through
    ``refuse_unreadable`` -- the size cap, the refusal of a DOCTYPE -- and then through the
    same readers an upload does.
    """
    try:
        found = pdf.attachments(data, limit=MAX_BYTES)
    except pdf.Refused as error:
        raise EuropassError(
            PDF_REFUSALS[error.reason]
            % {"filter": error.detail, "limit": MAX_BYTES // (1024 * 1024)}
        ) from error

    if not found:
        raise EuropassError(
            _(
                "That PDF has nothing attached to it, so there is no Europass CV inside it to "
                "read. The PDF europass.europa.eu gives you carries the CV as an attached "
                "file, and a PDF printed or saved again by another program loses it: download "
                "the CV again from europass.europa.eu and upload that file as it is."
            )
        )
    attached = next((one for one in found if _is_europass_xml(one)), None)
    if attached is None:
        raise EuropassError(
            _(
                "That PDF has files attached to it, and none of them is a Europass CV. Upload "
                "the PDF europass.europa.eu gives you, as it is."
            )
        )

    refuse_unreadable(attached)
    record = _read_document(attached)
    record.source = f"pdf-{record.source}"
    return record


def _is_europass_xml(data: bytes) -> bool:
    """Whether an attachment is a Europass CV, by what it says before anything parses it."""
    head = data.lstrip(b"\xef\xbb\xbf").lstrip()
    if not head.startswith(b"<"):
        return False
    return (b"Candidate" in data and b"www.europass.eu/" in data) or (
        b"SkillsPassport" in data or b"LearnerInfo" in data
    )


# -------------------------------------------------------------------- the XML


def _gregorian(value):
    """A month or a day as the schema types them (``xs:gMonth``, ``xs:gDay``).

    Those are written with leading hyphens -- ``--08`` and ``---01`` -- which is what
    Cedefop's own example has, and ``int()`` reads neither (#632). Older files wrote plain
    numbers, which have none to strip.
    """
    return value.lstrip("-") if isinstance(value, str) else value


def _date(period, which: str) -> dt.date | None:
    """A Europass date, which carries year, month and day as attributes."""
    node = _find(period, which)
    if node is None:
        return None
    return _make_date(node.get("year"), _gregorian(node.get("month")), _gregorian(node.get("day")))


def _entries(parent, name: str) -> list:
    """The entries of a list in either of the shapes the old format was written in.

    The schema wraps each list -- ``WorkExperienceList`` holding ``WorkExperience`` -- and
    that is what Cedefop's editor wrote (#632). The reader was written to a file that
    nested a same-named element instead, or put the entries straight under the parent, or
    had a single entry with no inner element at all; all of those are still read.
    """
    found: list = []
    wrapper = _find(parent, f"{name}List")
    if wrapper is not None:
        found = _all(wrapper, name)
    if found:
        return found
    for node in _all(parent, name):
        found.extend(_all(node, name) or [node])
    return found


def _unread(record: Record, entries: list, added: int, what) -> None:
    """A note where a section was in the file and nothing of it could be read.

    As the JSON reader says it: silence would read as a short record, and an unknown shape
    is exactly what a person rescuing an old file cannot see for themselves.
    """
    if entries and not added:
        record.skipped.append(
            str(_("%(what)s was in the file but could not be read.") % {"what": what})
        )


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
    """Read Europass XML, in either format. Raises :class:`EuropassError` with the reason.

    A DOCTYPE is refused by ``refuse_unreadable`` before this is called, and again here:
    the parser is ``defusedxml``'s, which forbids a document type declaration whatever
    its position or encoding, so calling this directly on such a document raises rather
    than expanding an entity (#378).

    Which format is decided by the root element, because both are XML: a ``Candidate`` in
    a Europass namespace is what europass.europa.eu writes today, and anything carrying a
    ``LearnerInfo`` is the format of the editor before it.
    """
    try:
        root = parse_xml(data, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException) as error:
        raise EuropassError(
            _("That file is not readable XML: %(reason)s") % {"reason": error}
        ) from error

    # Imported when an XML file arrives, which is the only time it is needed.
    from . import candidate

    if candidate.is_candidate(root):
        return candidate.read(root)

    learner = None if _local(root.tag) == "Candidate" else _find(root, "LearnerInfo")
    if learner is None and _local(root.tag) == "LearnerInfo":
        learner = root
    if learner is None:
        raise EuropassError(
            _(
                "That XML is not a Europass CV. Postulo reads the XML europass.europa.eu "
                "writes (a Candidate) and that of the Europass editor before it (a "
                "SkillsPassport); this is neither."
            )
        )

    # On the wrapper, not on LearnerInfo -- and a file handed over as a bare LearnerInfo has
    # no wrapper to carry it, which is a blank rather than a guess.
    record = Record(source="xml", locale=_locale(root.get("locale")))
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
        phones = _entries(contact, "Telephone")
        person["phone"] = next(filter(None, (_text(tel, "Contact") for tel in phones)), "")
        websites = [_text(site, "Contact") for site in _entries(contact, "Website")]
        websites = [site for site in websites if site]
        person["website"] = next(iter(websites), "")
        person["orcid"] = _orcid_from(websites)
        address = _find(contact, "Address", "Contact")
        if address is not None:
            person["location"] = _place(
                _text(address, "Municipality"), _text(address, "Country", "Label")
            )
            # And the rest of it, which used to go in the bin. A Europass file carries a
            # street and a postcode, and until #92 there was nowhere in Postulo to put
            # them -- so somebody exported their CV, imported it here, and the two lines
            # that make an address an address were silently gone.
            person["address"] = {
                "street": _text(address, "AddressLine"),
                "postcode": _text(address, "PostalCode"),
                "municipality": _text(address, "Municipality"),
                "region": _text(address, "Region"),
                "country": _country_code(address),
            }

    headline = _find(learner, "Headline", "Description", "Label")
    if headline is not None:
        person["headline"] = _text(headline)

    record.person = {key: value for key, value in person.items() if value}


def _read_experience(learner, record: Record) -> None:
    entries = _entries(learner, "WorkExperience")
    before = len(record.experience)
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
                "summary": _plain(_text(entry, "Activities", keep_lines=True)),
            }
        )
    _unread(record, entries, len(record.experience) - before, _("Work experience"))


def _read_education(learner, record: Record) -> None:
    entries = _entries(learner, "Education")
    before = len(record.education)
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
                "highlights": _plain(_text(entry, "Activities", keep_lines=True)),
            }
        )
    _unread(record, entries, len(record.education) - before, _("Education"))


def _read_skills(learner, record: Record) -> None:
    skills = _find(learner, "Skills")
    if skills is None:
        return

    linguistic = _find(skills, "Linguistic")
    if linguistic is not None:
        mothers = _entries(linguistic, "MotherTongue")
        foreigners = _entries(linguistic, "ForeignLanguage")
        before = len(record.languages)
        for mother in mothers:
            name = _text(mother, "Description", "Label")
            if name:
                record.languages.append({"name": name, "proficiency": "native", "levels": {}})
        for foreign in foreigners:
            name = _text(foreign, "Description", "Label")
            if not name:
                continue
            levels = _levels(_find(foreign, "ProficiencyLevel"))
            record.languages.append(
                {"name": name, "proficiency": _lowest(levels), "levels": levels}
            )
        _unread(record, [*mothers, *foreigners], len(record.languages) - before, _("Languages"))

    # Europass keeps these as free prose under a handful of headings. Each heading becomes
    # a skill group and each line becomes a skill, which is as close as the two shapes get.
    for heading, label in SKILL_HEADINGS:
        block = _find(skills, heading)
        if block is None:
            continue
        prose = _text(block, "Description", "Label", keep_lines=True) or _text(
            block, "Description", keep_lines=True
        )
        lines = _split_skills(_plain(prose))
        if lines:
            # `str()` now rather than a lazy string: what is read is held in the session
            # between the review page and the confirmation, and a session is JSON.
            record.skill_groups.append({"name": _heading(label, record.locale), "skills": lines})


def _read_achievements(learner, record: Record) -> None:
    entries = _entries(learner, "Achievement")
    before = len(record.projects)
    for entry in entries:
        project = _project_from(
            _text(entry, "Title", "Label") or _text(entry, "Title"),
            _plain(
                _text(entry, "Description", "Label", keep_lines=True)
                or _text(entry, "Description", keep_lines=True)
            ),
        )
        if project:
            record.projects.append(project)
    _unread(record, entries, len(record.projects) - before, _("Achievements"))


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
    except (ValueError, RecursionError) as error:  # ValueError: bad JSON, or a number too long
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

    record = Record(
        source="json",
        locale=_locale(_dig(document, "SkillsPassport", "Locale") or _dig(document, "Locale")),
    )
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
        person["address"] = {
            "street": _json_text(address, "AddressLine"),
            "postcode": _json_text(address, "PostalCode"),
            "municipality": _json_text(address, "Municipality"),
            "region": _json_text(address, "Region"),
            "country": _json_country_code(address),
        }

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
                "summary": _plain(_json_text(entry, "Activities", keep_lines=True)),
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
                "highlights": _plain(_json_text(entry, "Activities", keep_lines=True)),
            }
        )


def _read_json_skills(learner: dict, record: Record) -> None:
    block = learner.get("Skills")
    if not _readable(record, block, _("Skills")):
        return
    skills = _obj(block)
    if skills is None:
        return

    linguistic = skills.get("Linguistic")
    if not _readable(record, linguistic, _("Skills")):
        linguistic = None
    linguistic = _obj(linguistic) or {}
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
        if not _readable(record, skills.get(heading), label):
            continue
        block = _obj(skills.get(heading))
        if block is None:
            continue
        lines = _split_skills(_plain(_json_text(block, "Description", keep_lines=True)))
        if lines:
            record.skill_groups.append({"name": _heading(label, record.locale), "skills": lines})


def _read_json_achievements(learner: dict, record: Record) -> None:
    block = learner.get("Achievement")
    if not _readable(record, block, _("Achievements")):
        return
    for entry in _rows(block):
        project = _project_from(
            _json_text(entry, "Title"), _plain(_json_text(entry, "Description", keep_lines=True))
        )
        if project:
            record.projects.append(project)


# ------------------------------------------------------------------- writing
