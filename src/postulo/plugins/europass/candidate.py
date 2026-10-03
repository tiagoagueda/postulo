"""Reading the Europass CV europass.europa.eu has written since 2020.

**What it is.** The platform that replaced Cedefop's editor in July 2020 keeps a CV as an HR
Open Standards 3.2 ``Candidate`` document with the Commission's own extensions: the root is
``Candidate`` in ``http://www.europass.eu/1.0``, with HR Open Standards (``hr:``), OAGIS
(``oa:``) and EURES (``eures:``) elements beside Europass's own. The Commission published it
as *Europass XML schema definition v4.0.1* -- a zip whose ``Candidate.xsd`` says "Europass2
1.0.1, 02 June 2020" -- with a 160-page *EUROPASS2 CV XML Schema Documentation* (v3.0.0)
and a crosswalk from the old format, all still at europass.europa.eu/system/files/. The
editor hands it over attached to a PDF, which is ``pdf.py``'s business, not this module's.

**Read by local name, and nothing is required.** Exports do not follow the published schema
to the letter. The two public accounts of real downloads (2025 and 2026) describe elements it
does not define -- a ``RenderingInformation`` block, a ``Skills`` block -- and children in
another order than it prescribes. So nothing here depends on order or on an element being
present, and a section this reader has no place for, but which holds something, is *named*
in the record rather than dropped in silence: the person sees it on the review page, and
the owner verifying a real download sees what is left to map (#244).

**Mapped the way the Commission mapped the old format onto it** (the crosswalk in the
schema zip), so a career read from either arrives in the same shape: a qualification is
``ProgramName``, a job's text is its ``Description``, a foreign language is a
``PersonCompetency`` whose taxonomy is ``language`` with a CEFR score per dimension,
computer skills are ``DigitalSkills``, an achievement is a ``Project`` or an ``Others``
entry.

**Codes rather than words.** Where the old format wrote a label -- "English", "Portugal" --
this one writes a code. A language code becomes the language's name in the language the CV
is written in, which is what the old file would have said; a code nobody here can name is
kept as the code, for the person to rename. A country code goes where Postulo keeps
one, on the postal address, and a place is otherwise the city alone: there is no country
name to put beside it without guessing one.
"""

from __future__ import annotations

import re

from django.utils import translation
from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import Record

from .common import (
    _ORDER,
    CEFR,
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

#: The namespace a Europass Candidate is in, less its version. Matched as a prefix, so the
#: next version reads too; a ``Candidate`` from anywhere else is not a Europass CV.
NAMESPACE = "http://www.europass.eu/"

#: The EURES codes for the CEFR dimensions, onto the five parts the old format kept apart.
#: The official list has two writing dimensions where the old format had one; either is
#: writing, and where a file gives both the lower is the one kept, as everywhere else here.
DIMENSIONS = {
    "CEF-Understanding-Listening": "Listening",
    "CEF-Understanding-Reading": "Reading",
    "CEF-Speaking-Interaction": "SpokenInteraction",
    "CEF-Speaking-Production": "SpokenProduction",
    "CEF-Writing-Production": "Writing",
    "CEF-Writing-Interaction": "Writing",
}

#: The spoken languages of the official list (ECV06, in the schema's CodeLists.xsd, where
#: each is a Publications Office URI ending in a three-letter code), onto the two-letter
#: code Django names languages by. Bibliographic variants too, because files written by
#: hand use either. Sign languages and language families have no two-letter code and are
#: kept as written.
TWO_LETTER = {
    "alb": "sq", "ara": "ar", "arm": "hy", "aze": "az", "baq": "eu", "bel": "be",
    "ben": "bn", "bos": "bs", "bul": "bg", "cat": "ca", "ces": "cs", "chi": "zh",
    "cym": "cy", "cze": "cs", "dan": "da", "deu": "de", "dut": "nl", "ell": "el",
    "eng": "en", "est": "et", "eus": "eu", "fas": "fa", "fin": "fi", "fra": "fr",
    "fre": "fr", "geo": "ka", "ger": "de", "gle": "ga", "glg": "gl", "gre": "el",
    "guj": "gu", "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "hye": "hy",
    "ice": "is", "isl": "is", "ita": "it", "jav": "jv", "jpn": "ja", "kat": "ka",
    "kaz": "kk", "kor": "ko", "kur": "ku", "lat": "la", "lav": "lv", "lim": "li",
    "lit": "lt", "mac": "mk", "mar": "mr", "may": "ms", "mkd": "mk", "mlt": "mt",
    "msa": "ms", "nld": "nl", "nor": "no", "oci": "oc", "pan": "pa", "per": "fa",
    "pol": "pl", "por": "pt", "ron": "ro", "rum": "ro", "rus": "ru", "san": "sa",
    "slk": "sk", "slo": "sk", "slv": "sl", "spa": "es", "sqi": "sq", "srd": "sc",
    "srp": "sr", "swe": "sv", "tam": "ta", "tel": "te", "tur": "tr", "ukr": "uk",
    "urd": "ur", "vie": "vi", "vol": "vo", "wel": "cy", "wln": "wa", "yid": "yi",
    "zho": "zh",
}  # fmt: skip

#: The languages of that list Django has no name for, by the name each gives itself.
AUTONYMS = {
    "gu": "ગુજરાતી",
    "jv": "Basa Jawa",
    "ku": "Kurdî",
    "la": "Latina",
    "li": "Limburgs",
    "mt": "Malti",
    "oc": "occitan",
    "sa": "संस्कृतम्",
    "sc": "sardu",
    "vo": "Volapük",
    "wa": "walon",
    "yi": "ייִדיש",
    "zh": "中文",
}

#: The Candidate's own skill sections, with what Postulo calls each group. The labels the
#: old format shares are the old reader's msgids, so they are translated once.
SKILL_SECTIONS = (
    ("OrganisationalSkills", "OrganisationalSkill", _("Organisational")),
    (
        "CommunicationAndInterpersonalSkills",
        "CommunicationAndInterpersonalSkill",
        _("Communication"),
    ),
    (
        "ManagementAndLeadershipSkills",
        "ManagementAndLeadershipSkill",
        _("Management and leadership"),
    ),
)
DIGITAL = _("Digital")
JOB_RELATED = _("Job-related")

#: The parts of a ``CandidateProfile`` this reader takes something from.
READ = frozenset(
    {
        "CandidatePositionPreferences",
        "EmploymentHistory",
        "EducationHistory",
        "PersonQualifications",
        "Projects",
        "Others",
        "DigitalSkills",
        *(section for section, _item, _label in SKILL_SECTIONS),
    }
)

#: And the parts left alone without a word: identifiers, and the photograph, which the old
#: reader never took either.
QUIET = frozenset({"ID", "ProfileName", "Attachment"})

_DATE = re.compile(r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")
_EQF = re.compile(r"^(?:EQF[\s_-]*)?([1-8])$", re.IGNORECASE)


def is_candidate(root) -> bool:
    """Whether a parsed document is a Europass Candidate: its root, and the root's namespace."""
    return _local(root.tag) == "Candidate" and _namespace(root.tag).startswith(NAMESPACE)


def read(root) -> Record:
    """A career record out of a parsed Candidate document."""
    profiles = _all(root, "CandidateProfile")
    profile = profiles[0] if profiles else None
    record = Record(
        source="candidate",
        locale=_locale(_language_code(profile.get("languageCode") if profile is not None else "")),
    )
    person = _find(root, "CandidatePerson")
    if person is not None:
        _read_person(person, profile, record)
        _read_mother_tongues(person, record)
    if profile is not None:
        _read_experience(profile, record)
        _read_education(profile, record)
        _read_languages(profile, record)
        _read_skills(profile, record)
        _read_projects(profile, record)
        _name_what_was_not_read(profile, record)
    if len(profiles) > 1:
        # A profile per language is allowed (BR-COM-02), and one career is one language
        # here: reading two as one is the work `resume.translating` leaves for later.
        record.skipped.append(
            str(
                _(
                    "The file holds this career in more than one language, and only the "
                    "first was read."
                )
            )
        )
    return record


# ------------------------------------------------------------------- helpers


def _namespace(tag: str) -> str:
    return tag[1:].partition("}")[0] if tag.startswith("{") else ""


def _first(*nodes):
    """The first that exists. Not ``or``: an element with no children is falsy."""
    return next((node for node in nodes if node is not None), None)


def _raw(element, *names: str) -> str:
    """A node's text as written, before any tidying, so markup in it can be read as such."""
    if element is None:
        return ""
    found = _find(element, *names) if names else element
    return (found.text or "") if found is not None else ""


def _date(period, which: str):
    """``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` (BR-COM-06), under ``FormattedDateTime``.

    A date given only as ``DateText`` -- "two years ago" -- is not a date, and is not made
    into one.
    """
    if period is None:
        return None
    match = _DATE.match(_text(period, which, "FormattedDateTime"))
    return _make_date(*match.groups()) if match else None


def _true(element, name: str) -> bool:
    return element is not None and _text(element, name).lower() == "true"


def _city(holder) -> str:
    """An organisation's town, from its contact details."""
    if holder is None:
        return ""
    for contact in _all(holder, "OrganizationContact"):
        for channel in _all(contact, "Communication"):
            city = _text(channel, "Address", "CityName")
            if city:
                return city
    return ""


def _language_code(value) -> str:
    """A language as a code this module can look up, from a code or a Publications Office URI."""
    code = str(value or "").strip().rsplit("/", 1)[-1].lower()
    return TWO_LETTER.get(code, code)


def _language_name(node, cv_language: str) -> str:
    """What to call a language the file gives as a code, or as words.

    In the language the CV is written in, because that is what the old format's label said
    and what the CV will print. Free text -- the editor's way of recording a language that
    has no code -- is the name already. A code nobody here can name is kept as the file's
    code, without the URI around it: renaming ``BFI`` is a moment's work, and a guess at
    what it stands for would be a claim on somebody's CV.
    """
    if node is None:
        return ""
    written = _text(node)
    if not written:
        return ""
    kind = " ".join(node.get(key, "") for key in ("typeCode", "name", "schemeName"))
    if "FREETEXT" in kind.upper().replace("_", "").replace(" ", ""):
        return written
    code = _language_code(written)
    try:
        info = translation.get_language_info(code)
    except KeyError:
        return AUTONYMS.get(code, written.rsplit("/", 1)[-1])
    if cv_language:
        with translation.override(cv_language):
            return str(info["name_translated"])
    return str(info["name_translated"])


def _has_text(element) -> bool:
    return any((node.text or "").strip() for node in element.iter())


# ------------------------------------------------------------------- the person


def _read_person(person_node, profile, record: Record) -> None:
    person: dict = {}
    name = _find(person_node, "PersonName")
    if name is not None:
        person["first_name"] = _text(name, "GivenName")
        person["last_name"] = " ".join(
            text for text in (_text(family) for family in _all(name, "FamilyName")) if text
        )

    emails: list[str] = []
    websites: list[str] = []
    elsewhere: list[str] = []
    phones: list[str] = []
    address = None
    for channel in _all(person_node, "Communication"):
        found_address = _find(channel, "Address")
        if found_address is not None:
            address = _first(address, found_address)
            continue
        kind = _text(channel, "ChannelCode").casefold().replace(" ", "")
        uri = _text(channel, "URI")
        if kind == "email" and uri:
            emails.append(uri)
        elif kind == "web" and uri:
            websites.append(uri)
        elif uri:
            elsewhere.append(uri)
        elif _find(channel, "DialNumber") is not None:
            number = _phone(channel)
            if number:
                phones.append(number)

    person["email"] = next(iter(emails), "")
    person["phone"] = next(iter(phones), "")
    person["website"] = next(iter(websites), "")
    # Wherever it was put: a website, or a profile among the social ones.
    person["orcid"] = _orcid_from(websites + elsewhere)
    if address is not None:
        city = _text(address, "CityName")
        person["location"] = city
        person["address"] = {
            "street": _street(address),
            "postcode": _text(address, "PostalCode"),
            "municipality": city,
            # CountrySubDivisionCode is a NUTS code, not a name anybody writes on a letter.
            "region": "",
            "country": _country(_text(address, "CountryCode"))
            or _country(_text(person_node, "ResidencyCountryCode")),
        }

    headline = _headline(profile)
    if headline:
        person["headline"] = headline
    record.person = {key: value for key, value in person.items() if value}


def _street(address) -> str:
    lines = [_text(line) for line in _all(address, "AddressLine")]
    lines = [line for line in lines if line]
    if lines:
        return ", ".join(lines)
    parts = (_text(address, "StreetName"), _text(address, "BuildingNumber"), _text(address, "Unit"))
    return " ".join(part for part in parts if part)


def _phone(channel) -> str:
    """A number in international form where the file says which country it is for.

    The dialling code is its own element, digits and no plus; the rest is area and number.
    Without a dialling code the number is kept as written, because a national number with
    a guessed country in front is a wrong number. A trunk zero typed after the code stays
    in what is returned; the importer reads the result against the country's numbering
    plan, which drops it where the plan has one and keeps Italy's (#644).
    """
    country = re.sub(r"\D", "", _text(channel, "CountryDialing"))
    parts = (_text(channel, "AreaDialing"), _text(channel, "DialNumber"))
    local = " ".join(part for part in parts if part)
    if not local:
        return ""
    if not country:
        return local
    digits = re.sub(r"\D", "", local)
    return f"+{country}{digits}"


def _headline(profile) -> str:
    """The crosswalk puts the old headline in the position the person wants."""
    if profile is None:
        return ""
    wanted = _find(profile, "CandidatePositionPreferences")
    if wanted is None:
        return ""
    return " ".join(_plain(_raw(wanted, "Description")).split()) or _text(wanted, "PositionTitle")


# ------------------------------------------------------------------- the career


def _read_experience(profile, record: Record) -> None:
    history = _find(profile, "EmploymentHistory")
    if history is None:
        return
    for employer in _all(history, "EmployerHistory"):
        organisation = _text(employer, "OrganizationName")
        # The schema puts a period on the employer and another on each post; the crosswalk
        # and exports use the post's. Either is read, the post's first.
        employer_period = _find(employer, "EmploymentPeriod")
        for position in _all(employer, "PositionHistory") or [None]:
            role = _text(position, "PositionTitle") if position is not None else ""
            if not role and not organisation:
                continue
            period = _first(
                _find(position, "EmploymentPeriod") if position is not None else None,
                employer_period,
            )
            place = _text(position, "City") if position is not None else ""
            record.experience.append(
                {
                    "role": role,
                    "organisation": organisation,
                    "location": place or _city(employer),
                    "start_date": _date(period, "StartDate"),
                    "end_date": None
                    if _true(period, "CurrentIndicator")
                    else _date(period, "EndDate"),
                    "summary": _plain(_raw(position, "Description")),
                }
            )


def _read_education(profile, record: Record) -> None:
    history = _find(profile, "EducationHistory")
    if history is None:
        return
    for attendance in _all(history, "EducationOrganizationAttendance"):
        degree = _find(attendance, "EducationDegree")
        qualification = _text(attendance, "ProgramName") or (
            _text(degree, "DegreeName") if degree is not None else ""
        )
        institution = _text(attendance, "OrganizationName")
        if not qualification and not institution:
            continue
        period = _find(attendance, "AttendancePeriod")
        record.education.append(
            {
                "qualification": qualification,
                "institution": institution,
                "location": _city(attendance),
                "start_date": _date(period, "StartDate"),
                "end_date": None if _true(period, "Ongoing") else _date(period, "EndDate"),
                "grade": _grade(attendance, degree),
                "highlights": _plain(_raw(degree, "OccupationalSkillsCovered"))
                or _plain(_raw(period, "Description")),
            }
        )


def _grade(attendance, degree) -> str:
    """The final grade where the file gives one, and otherwise the EQF level, as before.

    The old reader filled ``grade`` from the old format's level, which was the EQF label;
    a level is written the same way here so that the two formats agree. Only where the file
    says it is EQF: a bare 6 on the ISCED list is a different level.
    """
    if degree is not None:
        final = _find(degree, "FinalGrade")
        if final is not None:
            text = _text(final, "ScoreText") or _text(final, "ScoreNumeric")
            if text:
                return text
    for level in _all(attendance, "EducationLevelCode"):
        value = _text(level)
        match = _EQF.match(value)
        if match and (
            "EQF" in (level.get("listName") or "").upper() or value.upper().startswith("EQF")
        ):
            return f"EQF {match.group(1)}"
    return ""


# ------------------------------------------------------------------- languages


def _read_mother_tongues(person_node, record: Record) -> None:
    for tongue in _all(person_node, "PrimaryLanguageCode"):
        name = _language_name(tongue, record.locale)
        if name:
            record.languages.append({"name": name, "proficiency": "native", "levels": {}})


def _read_languages(profile, record: Record) -> None:
    qualifications = _find(profile, "PersonQualifications")
    if qualifications is None:
        return
    for competency in _all(qualifications, "PersonCompetency"):
        if _text(competency, "TaxonomyID").casefold() != "language":
            continue
        name = _language_name(_find(competency, "CompetencyID"), record.locale) or _text(
            competency, "CompetencyName"
        )
        if not name:
            continue
        found: dict[str, str] = {}
        for dimension in _all(competency, "CompetencyDimension"):
            part = DIMENSIONS.get(_text(dimension, "CompetencyDimensionTypeCode"))
            level = _text(dimension, "Score", "ScoreText").upper()
            if part is None or level not in CEFR:
                continue
            if part not in found or _ORDER.index(CEFR[level]) < _ORDER.index(CEFR[found[part]]):
                found[part] = level
        levels = {part: found[part] for part in CEFR_PARTS if part in found}
        # One level for the whole language, where the file gives no dimensions.
        overall = _text(competency, "ProficiencyLevel", "ScoreText").upper()
        proficiency = _lowest(levels) if levels else CEFR.get(overall, "")
        record.languages.append({"name": name, "proficiency": proficiency, "levels": levels})


# ------------------------------------------------------------------- skills and the rest


def _read_skills(profile, record: Record) -> None:
    digital = _find(profile, "DigitalSkills")
    if digital is not None:
        for group in _all(digital, "DigitalSkillsGroup"):
            skills = [
                skill
                for node in _all(group, "DigitalSkill")
                for skill in _split_skills(_text(node, keep_lines=True))
            ]
            if skills:
                # A group the person titled keeps its title: it is their heading.
                name = _text(group, "Title") or _heading(DIGITAL, record.locale)
                record.skill_groups.append({"name": name, "skills": skills})

    for section, item, label in SKILL_SECTIONS:
        block = _find(profile, section)
        if block is None:
            continue
        skills = []
        for entry in _all(block, item):
            title = _text(entry, "Title")
            skills.extend([title] if title else _split_skills(_plain(_raw(entry, "Description"))))
        if skills:
            record.skill_groups.append({"name": _heading(label, record.locale), "skills": skills})

    # The crosswalk puts the old job-related skills here: prose in the summary, and any
    # competency that is not a language.
    qualifications = _find(profile, "PersonQualifications")
    if qualifications is not None:
        skills = _split_skills(_plain(_raw(qualifications, "QualificationsSummary")))
        for competency in _all(qualifications, "PersonCompetency"):
            if _text(competency, "TaxonomyID").casefold() == "language":
                continue
            name = _text(competency, "CompetencyName") or _text(competency, "CompetencyID")
            if name:
                skills.append(name)
        if skills:
            record.skill_groups.append(
                {"name": _heading(JOB_RELATED, record.locale), "skills": skills}
            )


def _read_projects(profile, record: Record) -> None:
    projects = _find(profile, "Projects")
    if projects is not None:
        for project in _all(projects, "Project"):
            entry = _project_from(_text(project, "Title"), _plain(_raw(project, "Description")))
            if entry:
                record.projects.append(entry)
    # A section the person made up. The crosswalk puts the old format's achievements here,
    # and the old reader made those projects.
    for others in _all(profile, "Others"):
        heading = _text(others, "Title")
        for other in _all(others, "Other"):
            entry = _project_from(
                _text(other, "Title") or heading, _plain(_raw(other, "Description"))
            )
            if entry:
                record.projects.append(entry)


def _name_what_was_not_read(profile, record: Record) -> None:
    """Say which parts of the profile held something and have nowhere to go.

    The schema has more sections than a career record has places, and exports carry some
    the schema never defined. Silence would make a file that imported half its contents
    look like a file with half the contents.
    """
    missed: list[str] = []
    for child in profile:
        name = _local(child.tag)
        if name in READ or name in QUIET or name in missed or not _has_text(child):
            continue
        missed.append(name)
    if missed:
        record.skipped.append(
            str(
                _(
                    "Some sections of the file have nowhere to go in Postulo yet and were "
                    "not read: %(sections)s."
                )
                % {"sections": ", ".join(missed)}
            )
        )
