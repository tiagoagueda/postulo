"""The Europass europass.europa.eu writes today: the Candidate XML, and the PDF it travels in.

Until #244 Postulo read the two formats of the editor Cedefop ran until 2020, and told
anybody with a CV made since that their file was not a Europass file. The platform that
replaced it keeps a CV as an HR Open Standards ``Candidate`` and hands it over as a PDF
with that XML attached.

Three fixtures. ``tests/data/europass-candidate.xml`` was written by hand from the
Commission's published schema and describes the same invented career as the two older
fixtures, so a test can insist all three formats read one career.
``tests/data/europass-candidate-sample.xml`` is the Commission's own sample. Neither is a
download from a real account -- nobody here has one -- and the PDFs below are built by
:func:`europass_pdf`, laid out the way the one public description of the editor's output
says its PDFs are: an embedded file stream, Flate-compressed, reached from the catalogue's
``/EmbeddedFiles`` and its ``/AF``.
"""

from __future__ import annotations

import datetime as dt
import zlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from postulo.plugins import base
from postulo.plugins.europass import EuropassImporter, candidate, pdf
from postulo.plugins.europass import reader as europass
from postulo.resume import importing
from postulo.resume.models import Education, Experience, LanguageSkill, Project, Skill

pytestmark = pytest.mark.django_db

DATA = Path(__file__).parent / "data"
CANDIDATE = DATA / "europass-candidate.xml"
SAMPLE = DATA / "europass-candidate-sample.xml"
OLD_XML = DATA / "europass.xml"
OLD_JSON = DATA / "europass.json"

NS = (
    'xmlns="http://www.europass.eu/1.0" xmlns:hr="http://www.hr-xml.org/3" '
    'xmlns:eures="http://www.europass_eures.eu/1.0" '
    'xmlns:oa="http://www.openapplications.org/oagis/9"'
)


def candidate_xml(profile: str = "", person: str = "", language: str = "en") -> bytes:
    """A small Candidate, for the tests that want one thing in it and nothing else."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Candidate {NS}>'
        f"<CandidatePerson><PersonName><oa:GivenName>Alex</oa:GivenName>"
        f"<hr:FamilyName>Morgan</hr:FamilyName></PersonName>{person}</CandidatePerson>"
        f'<CandidateProfile languageCode="{language}">{profile}</CandidateProfile>'
        f"</Candidate>"
    ).encode()


def post(title: str, period: str, description: str = "") -> str:
    return (
        f"<EmploymentHistory><EmployerHistory><hr:OrganizationName>Initech</hr:OrganizationName>"
        f"<PositionHistory><PositionTitle>{title}</PositionTitle>{period}"
        f"<oa:Description>{description}</oa:Description></PositionHistory>"
        f"</EmployerHistory></EmploymentHistory>"
    )


def when(value: str) -> str:
    return f"<hr:FormattedDateTime>{value}</hr:FormattedDateTime>"


def language(code: str, *dimensions: tuple[str, str], extra: str = "") -> str:
    scored = "".join(
        f"<eures:CompetencyDimension><hr:CompetencyDimensionTypeCode>{kind}"
        f"</hr:CompetencyDimensionTypeCode><eures:Score><hr:ScoreText>{level}</hr:ScoreText>"
        f"</eures:Score></eures:CompetencyDimension>"
        for kind, level in dimensions
    )
    return (
        f"<PersonQualifications><PersonCompetency><CompetencyID>{code}</CompetencyID>"
        f"<hr:TaxonomyID>language</hr:TaxonomyID>{extra}{scored}</PersonCompetency>"
        f"</PersonQualifications>"
    )


# ---------------------------------------------------------------- a PDF, built here


@dataclass
class Attached:
    """One file a test PDF carries."""

    name: str
    data: bytes
    #: The filters the stream's dictionary names, in order.
    filters: tuple[str, ...] = ("FlateDecode",)
    #: ``direct``, ``elsewhere`` (an indirect reference) or ``absent``.
    length: str = "direct"
    #: The stream's bytes as written, for a test that wants them wrong on purpose.
    body: bytes | None = None
    subtype: str = "text#2Fxml"


def europass_pdf(*attached: Attached, object_streams: bool = False, encrypt: bool = False) -> bytes:
    """A one-page PDF carrying ``attached`` the way the Europass editor's PDFs carry the CV.

    After ISO 32000-1 and after the only public description of the editor's output (#244):
    each file is an ``/EmbeddedFile`` stream with its ``/Filespec``, named in the
    catalogue's ``/Names /EmbeddedFiles`` tree and listed in its ``/AF``. With
    ``object_streams`` everything that may be compressed is -- the dictionaries of the
    catalogue, the pages and the file specifications go into an object stream and the
    cross-reference table becomes a stream -- which is how pdf-lib, the library the
    editor's PDFs are reported to come from, saves by default. Not a copy of a real
    download, which nobody here has.
    """
    plain: dict[int, bytes] = {}
    streams: dict[int, bytes] = {}
    specs = []
    number = 4
    for one in attached:
        spec, stream = number, number + 1
        number += 2
        if one.body is not None:
            body = one.body
        elif one.filters == ("FlateDecode",):
            body = zlib.compress(one.data)
        else:
            body = one.data
        names = b" ".join(b"/" + name.encode() for name in one.filters)
        entries = [b"/Type /EmbeddedFile", b"/Subtype /" + one.subtype.encode()]
        entries.append(b"/Params << /Size %d >>" % len(one.data))
        if one.filters:
            entries.append(b"/Filter " + (names if len(one.filters) == 1 else b"[" + names + b"]"))
        if one.length == "direct":
            entries.append(b"/Length %d" % len(body))
        elif one.length == "elsewhere":
            plain[number] = b"%d" % len(body)
            entries.append(b"/Length %d 0 R" % number)
            number += 1
        # One entry a line, as some writers do: it is what would let a name ending in
        # "stream" pass for the keyword, were the keyword not anchored on the ">>".
        streams[stream] = b"<<\n" + b"\n".join(entries) + b"\n>>\nstream\n" + body + b"\nendstream"
        plain[spec] = (
            b"<< /Type /Filespec /F (%s) /UF (%s) /EF << /F %d 0 R >> /Desc (Europass) "
            b"/AFRelationship /Data >>" % (one.name.encode(), one.name.encode(), stream)
        )
        specs.append((one.name.encode(), spec))

    tree = b" ".join(b"(%s) %d 0 R" % (name, ref) for name, ref in specs)
    af = b" ".join(b"%d 0 R" % ref for _name, ref in specs)
    plain[1] = (
        b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [%s] >> >> "
        b"/AF [%s] >>" % (tree, af)
    )
    plain[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    plain[3] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>"
    encrypt_ref = b""
    if encrypt:
        plain[number] = b"<< /Filter /Standard /V 2 /R 3 /Length 128 /P -4 /O <%s> /U <%s> >>" % (
            b"00" * 32,
            b"00" * 32,
        )
        encrypt_ref = b" /Encrypt %d 0 R" % number
        number += 1

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, tuple[int, int, int]] = {}

    def write(object_number: int, body: bytes) -> None:
        offsets[object_number] = (1, len(out), 0)
        out.extend(b"%d 0 obj\n" % object_number + body + b"\nendobj\n")

    if not object_streams:
        for object_number in sorted({**plain, **streams}):
            write(object_number, plain.get(object_number) or streams[object_number])
        size = max(offsets) + 1
        xref = len(out)
        out.extend(b"xref\n0 %d\n0000000000 65535 f \n" % size)
        for object_number in range(1, size):
            out.extend(b"%010d 00000 n \n" % offsets[object_number][1])
        out.extend(b"trailer\n<< /Size %d /Root 1 0 R%s >>\n" % (size, encrypt_ref))
        out.extend(b"startxref\n%d\n%%%%EOF\n" % xref)
        return bytes(out)

    for object_number in sorted(streams):
        write(object_number, streams[object_number])
    packed_numbers = sorted(plain)
    heads, bodies, position = [], [], 0
    for index, object_number in enumerate(packed_numbers):
        heads.append(b"%d %d" % (object_number, position))
        bodies.append(plain[object_number])
        position += len(plain[object_number]) + 1
        offsets[object_number] = (2, 0, index)  # the container is filled in below
    head = b" ".join(heads) + b"\n"
    packed = zlib.compress(head + b"\n".join(bodies))
    container = number
    number += 1
    for object_number in packed_numbers:
        offsets[object_number] = (2, container, offsets[object_number][2])
    write(
        container,
        b"<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>\nstream\n"
        % (len(packed_numbers), len(head), len(packed))
        + packed
        + b"\nendstream",
    )
    xref_number = number
    size = xref_number + 1
    xref_offset = len(out)
    offsets[xref_number] = (1, xref_offset, 0)
    rows = b"".join(
        bytes([kind]) + second.to_bytes(4, "big") + third.to_bytes(2, "big")
        for kind, second, third in (
            offsets.get(object_number, (0, 0, 65535)) for object_number in range(size)
        )
    )
    packed_rows = zlib.compress(rows)
    out.extend(
        b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Root 1 0 R%s /Filter /FlateDecode "
        b"/Length %d >>\nstream\n"
        % (xref_number, size, encrypt_ref, len(packed_rows))
        + packed_rows
        + b"\nendstream\nendobj\n"
    )
    out.extend(b"startxref\n%d\n%%%%EOF\n" % xref_offset)
    return bytes(out)


def attachment(data: bytes | None = None, **options) -> Attached:
    return Attached("attachment.xml", CANDIDATE.read_bytes() if data is None else data, **options)


# ------------------------------------------------------ the format, as the schema has it


def test_the_format_europass_writes_today_reads():
    record = europass.read(CANDIDATE.read_bytes())

    assert record.source == "candidate"
    assert record.counts() == {
        "memberships": 0,
        "participations": 0,
        "experience": 2,
        "education": 1,
        "languages": 2,
        "skills": 5,
        "projects": 1,
        "references": 0,
    }
    assert record.person["first_name"] == "Alex"
    assert record.person["last_name"] == "Morgan"
    assert record.person["email"] == "alex@example.org"
    assert record.person["headline"] == "Backend engineer"
    assert record.locale == "en"
    assert record.skipped == []


def test_three_formats_read_one_career():
    """What #129 made the record for: another format is another front door.

    Where the formats genuinely differ the test says so rather than bending a fixture to
    pass. The Candidate writes a country as a code and a language as a code, so a place is
    its town alone and a language is named by Postulo -- the rest is the same career.
    """
    new = europass.read(CANDIDATE.read_bytes())

    for old in (europass.read(OLD_XML.read_bytes()), europass.read(OLD_JSON.read_bytes())):
        assert new.experience == [
            {**row, "location": row["location"].split(",")[0]} for row in old.experience
        ]
        assert new.education == [
            {**row, "location": row["location"].split(",")[0]} for row in old.education
        ]
        assert new.skill_groups == old.skill_groups
        assert new.projects == old.projects
        assert new.person == {**old.person, "location": "Lisboa"}
        assert [(row["proficiency"], row["levels"]) for row in new.languages] == [
            (row["proficiency"], row["levels"]) for row in old.languages
        ]


def test_dates_keep_their_precision_and_a_current_post_has_no_end():
    first, second = europass.read(CANDIDATE.read_bytes()).experience

    assert (first["start_date"], first["end_date"]) == (dt.date(2019, 3, 1), dt.date(2023, 6, 30))
    assert (second["start_date"], second["end_date"]) == (dt.date(2023, 7, 1), None)


def test_a_post_marked_current_has_no_end_even_where_one_is_written():
    period = (
        f"<eures:EmploymentPeriod><eures:StartDate>{when('2020')}</eures:StartDate>"
        f"<eures:EndDate>{when('2021')}</eures:EndDate>"
        f"<hr:CurrentIndicator>true</hr:CurrentIndicator></eures:EmploymentPeriod>"
    )

    row = europass.read(candidate_xml(post("Engineer", period))).experience[0]

    assert row["start_date"] == dt.date(2020, 1, 1)
    assert row["end_date"] is None


def test_the_employers_period_serves_a_post_that_has_none():
    """The schema puts one on the employer and one on each post; either is read."""
    data = candidate_xml(
        "<EmploymentHistory><EmployerHistory><hr:OrganizationName>Initech</hr:OrganizationName>"
        f"<eures:EmploymentPeriod><eures:StartDate>{when('2018-05')}</eures:StartDate>"
        "</eures:EmploymentPeriod>"
        "<PositionHistory><PositionTitle>Engineer</PositionTitle></PositionHistory>"
        "</EmployerHistory></EmploymentHistory>"
    )

    assert europass.read(data).experience[0]["start_date"] == dt.date(2018, 5, 1)


def test_a_date_that_is_only_words_is_not_made_into_one():
    period = (
        "<eures:EmploymentPeriod><eures:StartDate><hr:DateText>Two years ago</hr:DateText>"
        "</eures:StartDate></eures:EmploymentPeriod>"
    )

    assert (
        europass.read(candidate_xml(post("Engineer", period))).experience[0]["start_date"] is None
    )


def test_html_in_a_description_becomes_lines_of_text():
    """The long texts hold HTML, escaped in the XML; a career record holds text."""
    html = (
        "&lt;p&gt;Ran &lt;strong&gt;billing&lt;/strong&gt; &amp;amp; payroll.&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;Cut costs&lt;/li&gt;&lt;li&gt;Hired four&lt;/li&gt;&lt;/ul&gt;"
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )

    summary = europass.read(candidate_xml(post("Engineer", "", html))).experience[0]["summary"]

    assert summary == "Ran billing & payroll.\nCut costs\nHired four"


def test_education_reads_the_programme_its_grade_and_what_it_covered():
    row = europass.read(CANDIDATE.read_bytes()).education[0]

    assert row["qualification"] == "MSc Computer Science"
    assert row["institution"] == "Universidade de Lisboa"
    assert row["location"] == "Lisboa"
    assert row["grade"] == ""
    assert row["eqf_level"] == 7, "the level has a field of its own, and is not the grade"
    assert row["highlights"] == "Distributed systems."


def test_a_degree_name_serves_where_there_is_no_programme_name():
    data = candidate_xml(
        "<EducationHistory><EducationOrganizationAttendance>"
        "<hr:OrganizationName>ISEL</hr:OrganizationName>"
        "<AttendancePeriod><Ongoing>true</Ongoing></AttendancePeriod>"
        "<EducationDegree><hr:DegreeName>BSc Informatics</hr:DegreeName>"
        "<FinalGrade><hr:ScoreText>17/20</hr:ScoreText></FinalGrade></EducationDegree>"
        "<EducationLevelCode>6</EducationLevelCode>"
        "</EducationOrganizationAttendance></EducationHistory>"
    )

    row = europass.read(data).education[0]

    assert row["qualification"] == "BSc Informatics"
    assert row["grade"] == "17/20"
    assert row["end_date"] is None


def test_a_final_grade_and_an_eqf_code_are_both_kept():
    data = candidate_xml(
        "<EducationHistory><EducationOrganizationAttendance>"
        "<hr:OrganizationName>ISEL</hr:OrganizationName><hr:ProgramName>BSc</hr:ProgramName>"
        "<EducationDegree><FinalGrade><hr:ScoreText>17/20</hr:ScoreText></FinalGrade>"
        "</EducationDegree>"
        '<EducationLevelCode listName="EQF">6</EducationLevelCode>'
        "</EducationOrganizationAttendance></EducationHistory>"
    )

    row = europass.read(data).education[0]

    assert (row["grade"], row["eqf_level"]) == ("17/20", 6)


def test_a_level_that_does_not_say_it_is_eqf_is_not_called_one():
    """A bare 6 on the ISCED list is a different level from EQF 6."""
    data = candidate_xml(
        "<EducationHistory><EducationOrganizationAttendance>"
        "<hr:OrganizationName>ISEL</hr:OrganizationName><hr:ProgramName>BSc</hr:ProgramName>"
        '<EducationLevelCode listName="EURES_ISCEDEducationLevel">6</EducationLevelCode>'
        "</EducationOrganizationAttendance></EducationHistory>"
    )

    row = europass.read(data).education[0]
    assert (row["grade"], row["eqf_level"]) == ("", None), "nothing is read from an ISCED code"


# ------------------------------------------------------------------- the person


def test_the_person_is_the_candidate_and_not_whoever_supplied_the_file():
    """The Commission's sample names the supplier's contact too; he is not the candidate."""
    record = europass.read(SAMPLE.read_bytes())

    assert record.person["first_name"] == "John"
    assert record.person["last_name"] == "Smith"


def test_a_telephone_is_put_together_in_international_form():
    record = europass.read(SAMPLE.read_bytes())

    assert record.person["phone"] == "+3493554446776"


def test_a_telephone_with_no_country_is_kept_as_written():
    """A national number with a guessed country in front is a wrong number."""
    channel = (
        "<Communication><ChannelCode>Telephone</ChannelCode>"
        "<oa:DialNumber>21 123 4567</oa:DialNumber></Communication>"
    )

    assert europass.read(candidate_xml(person=channel)).person["phone"] == "21 123 4567"


def test_the_address_keeps_its_parts_and_a_two_letter_country():
    record = europass.read(CANDIDATE.read_bytes())

    assert record.person["address"] == {
        "street": "Rua do Exemplo 1",
        "postcode": "1000-001",
        "municipality": "Lisboa",
        "region": "",
        "country": "PT",
    }
    assert record.person["location"] == "Lisboa"


@pytest.mark.parametrize(
    "written,kept",
    [
        ("pt", "PT"),
        ("http://publications.europa.eu/resource/authority/country/PRT", ""),
        ("PRT", ""),
    ],
)
def test_only_a_two_letter_country_is_kept(written, kept):
    """Cutting PRT to two letters would make it Puerto Rico."""
    address = (
        "<Communication><Address><oa:CityName>Lisboa</oa:CityName>"
        f"<CountryCode>{written}</CountryCode></Address></Communication>"
    )

    assert europass.read(candidate_xml(person=address)).person["address"]["country"] == kept


def test_an_orcid_among_the_social_profiles_is_found_too():
    channels = (
        "<Communication><ChannelCode>Social Media</ChannelCode><UseCode>Other</UseCode>"
        "<oa:URI>https://orcid.org/0000-0002-1825-0097</oa:URI></Communication>"
    )

    record = europass.read(candidate_xml(person=channels))

    assert record.person["orcid"] == "0000-0002-1825-0097"
    assert "website" not in record.person, "a social profile is not the person's website"


# ------------------------------------------------------------------- languages


def test_languages_are_named_in_the_language_the_cv_is_written_in():
    """The old format wrote a label; this one writes a code, so Postulo names it --
    in the language of the CV, which is what the old label would have been in."""
    data = CANDIDATE.read_bytes()

    english = [row["name"] for row in europass.read(data).languages]
    portuguese = [
        row["name"]
        for row in europass.read(data.replace(b'languageCode="en"', b'languageCode="pt"')).languages
    ]

    assert english == ["Portuguese", "English"]
    assert portuguese == ["Português", "Inglês"]


@pytest.mark.parametrize(
    "written,name",
    [
        ("eng", "English"),
        ("fre", "French"),
        ("de", "German"),
        ("http://publications.europa.eu/resource/authority/language/MLT", "Malti"),
        ("http://publications.europa.eu/resource/authority/language/BFI", "BFI"),
    ],
)
def test_a_language_code_is_named_or_kept_as_written(written, name):
    """Two letters or three, a code or the official list's URI. A code nobody here can
    name -- a sign language, say -- is kept as the file's code, to be renamed."""
    person = f"<PrimaryLanguageCode>{written}</PrimaryLanguageCode>"

    assert europass.read(candidate_xml(person=person)).languages[0]["name"] == name


def test_a_language_written_as_free_text_is_its_own_name():
    person = '<PrimaryLanguageCode typeCode="FREETEXT">Mirandês</PrimaryLanguageCode>'

    assert europass.read(candidate_xml(person=person)).languages == [
        {"name": "Mirandês", "proficiency": "native", "levels": {}}
    ]


def test_the_lowest_of_the_levels_is_kept_and_both_writing_codes_are_writing():
    """The official list has two writing dimensions where the old format had one."""
    data = candidate_xml(
        language(
            "eng",
            ("CEF-Understanding-Listening", "C2"),
            ("CEF-Writing-Interaction", "B1"),
            ("CEF-Writing-Production", "B2"),
        )
    )

    row = europass.read(data).languages[0]

    assert row["levels"] == {"Listening": "C2", "Writing": "B1"}
    assert row["proficiency"] == "b1"


def test_one_level_for_the_whole_language_is_kept_where_there_are_no_dimensions():
    extra = "<eures:ProficiencyLevel><hr:ScoreText>B2</hr:ScoreText></eures:ProficiencyLevel>"

    row = europass.read(candidate_xml(language("deu", extra=extra))).languages[0]

    assert (row["name"], row["proficiency"], row["levels"]) == ("German", "b2", {})


def test_a_language_with_no_level_claims_none(user):
    """The rule #235 set for the old format holds for this one."""
    record = europass.read(candidate_xml(language("fra")))

    assert record.languages[0]["proficiency"] == ""
    importing.apply(user, record)
    assert LanguageSkill.objects.get(owner=user, name="French").proficiency == ""


# ------------------------------------------------------------------- skills and the rest


def test_skills_arrive_under_postulos_headings_or_the_persons_own():
    data = candidate_xml(
        "<PersonQualifications><hr:QualificationsSummary>Payroll; Audit</hr:QualificationsSummary>"
        "<PersonCompetency><CompetencyID>Welding</CompetencyID><hr:TaxonomyID>other</hr:TaxonomyID>"
        "</PersonCompetency></PersonQualifications>"
        "<ManagementAndLeadershipSkills><ManagementAndLeadershipSkill><Title>Budgets</Title>"
        "</ManagementAndLeadershipSkill></ManagementAndLeadershipSkills>"
        "<DigitalSkills><DigitalSkillsGroup><Title>Office</Title><DigitalSkill>Excel</DigitalSkill>"
        "</DigitalSkillsGroup></DigitalSkills>"
    )

    groups = {row["name"]: row["skills"] for row in europass.read(data).skill_groups}

    assert groups == {
        "Office": ["Excel"],
        "Management and leadership": ["Budgets"],
        "Job-related": ["Payroll", "Audit", "Welding"],
    }


def test_a_made_up_section_arrives_as_projects():
    """The crosswalk puts the old achievements in ``Others``; the old reader made those
    projects, and so does this one."""
    data = candidate_xml(
        "<Others><Title>Honours</Title><Other><Title>Best paper</Title>"
        "<Description>&lt;p&gt;At a conference.&lt;/p&gt;</Description></Other></Others>"
    )

    assert europass.read(data).projects == [{"name": "Best paper", "summary": "At a conference."}]


def test_a_section_with_nowhere_to_go_is_named_rather_than_dropped():
    """Exports carry sections the schema never defined; silence would hide them."""
    data = candidate_xml(
        "<hr:ExecutiveSummary>&lt;p&gt;About me.&lt;/p&gt;</hr:ExecutiveSummary>"
        "<PublicationHistory><Publication><Title>A paper</Title></Publication></PublicationHistory>"
        "<Certifications/>"
        "<Skills><PersonCompetency><hr:TaxonomyID>Digital_Skill</hr:TaxonomyID>"
        "<hr:CompetencyName>SQL</hr:CompetencyName></PersonCompetency></Skills>"
    )

    record = europass.read(data)

    assert record.skipped == [
        "Some sections of the file have nowhere to go in Postulo yet and were not read: "
        "ExecutiveSummary, PublicationHistory, Skills."
    ]


def test_a_skill_heading_follows_the_language_of_the_file_not_the_interface(monkeypatch):
    from django.utils import translation
    from django.utils.functional import lazy

    label = lazy(lambda: translation.get_language(), str)()
    monkeypatch.setattr(candidate, "DIGITAL", label)
    monkeypatch.setattr(candidate, "JOB_RELATED", label)
    monkeypatch.setattr(candidate, "SKILL_SECTIONS", (("Others", "Other", label),))
    data = CANDIDATE.read_bytes().replace(b'languageCode="en"', b'languageCode="pt"')
    data = data.replace(b"<DigitalSkillsGroup>", b"<DigitalSkillsGroup><Title></Title>", 1)

    with translation.override("en"):
        record = europass.read(data)

    names = [group["name"] for group in record.skill_groups]
    assert names
    assert set(names) == {"pt-pt"}


def test_a_career_in_two_languages_reads_the_first_and_says_so():
    data = CANDIDATE.read_bytes().replace(
        b"</Candidate>",
        b'<CandidateProfile languageCode="pt"><EducationHistory/></CandidateProfile></Candidate>',
    )

    record = europass.read(data)

    assert record.locale == "en"
    assert record.counts()["experience"] == 2
    assert any("more than one language" in note for note in record.skipped)


def test_the_commissions_own_sample_reads():
    record = europass.read(SAMPLE.read_bytes())

    assert record.source == "candidate"
    assert record.locale == "en"
    assert record.education == [
        {
            "qualification": "test",
            "institution": "UFR Amiens",
            "location": "",
            "start_date": dt.date(2005, 9, 1),
            "end_date": dt.date(2010, 7, 1),
            "grade": "",
            "eqf_level": 5,
            "highlights": "",
        }
    ]
    assert record.languages == [
        {"name": "French", "code": "fr", "proficiency": "native", "levels": {}}
    ]
    assert record.person["address"]["country"] == "BE"
    assert record.skipped == []


# ------------------------------------------------------------ which XML is which


def test_a_candidate_from_somewhere_other_than_europass_is_not_a_europass_cv():
    """``Candidate`` is an HR Open Standards noun; other systems write it too."""
    data = b'<Candidate xmlns="http://www.hr-xml.org/3"><CandidatePerson/></Candidate>'

    with pytest.raises(europass.EuropassError, match="not a Europass CV") as refused:
        europass.read(data)
    assert "Candidate" in str(refused.value) and "SkillsPassport" in str(refused.value)


def test_the_next_version_of_the_namespace_still_reads():
    """Matching the namespace exactly would read 1.0 and then quietly stop working."""
    data = CANDIDATE.read_bytes().replace(
        b"http://www.europass.eu/1.0", b"http://www.europass.eu/2.0"
    )

    assert europass.read(data).counts()["experience"] == 2


def test_a_doctype_in_a_candidate_is_refused_before_it_is_parsed():
    bomb = (
        b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
        b'<Candidate xmlns="http://www.europass.eu/1.0"><CandidatePerson>&x;</CandidatePerson>'
        b"</Candidate>"
    )

    with pytest.raises(base.ImportRefused, match="document type declaration"):
        europass.read(bomb)


def test_something_that_is_no_europass_format_names_every_one_that_is():
    with pytest.raises(europass.EuropassError) as refused:
        europass.read(b"Name,Role\nAlex,Engineer\n")

    message = str(refused.value)
    assert "PDF" in message and "XML" in message and "JSON" in message


@pytest.mark.parametrize(
    "data,claimed",
    [
        (CANDIDATE.read_bytes(), True),
        (b'<ep:Candidate xmlns:ep="http://www.europass.eu/1.0"><ep:Cand', True),
        (b'<Candidate xmlns="http://www.hr-xml.org/3"><CandidatePerson/></Candidate>', False),
        (b"%PDF-1.7 anything at all", True),
    ],
    ids=["candidate", "truncated-and-prefixed", "another-systems-candidate", "any-pdf"],
)
def test_what_europass_claims_now(data: bytes, claimed: bool):
    assert EuropassImporter().can_handle(data, "cv") is claimed


def test_the_candidate_is_found_under_its_root_and_not_by_luck():
    from xml.etree import ElementTree

    # The repository's own fixtures, which carry no DOCTYPE.
    root = ElementTree.fromstring(CANDIDATE.read_bytes())  # noqa: S314
    old = ElementTree.fromstring(OLD_XML.read_bytes())  # noqa: S314

    assert candidate.is_candidate(root)
    assert not candidate.is_candidate(old)


# ---------------------------------------------------------------- the PDF


def test_the_pdf_europass_gives_you_is_read_through_what_it_carries():
    record = europass.read(europass_pdf(attachment()))

    assert record.source == "pdf-candidate"
    direct = europass.read(CANDIDATE.read_bytes())
    assert (record.person, record.experience, record.languages) == (
        direct.person,
        direct.experience,
        direct.languages,
    )


def test_it_does_not_matter_that_everything_else_in_the_pdf_is_compressed():
    """A stream never lives in an object stream, so its dictionary is always readable."""
    data = europass_pdf(attachment(), object_streams=True)

    assert b"/Type /Catalog" not in data, "the catalogue really is compressed away"
    assert europass.read(data).counts()["experience"] == 2


def test_an_older_europass_pdf_reads_through_its_attachment_too():
    """Cedefop's editor attached its XML the same way, under another name."""
    data = europass_pdf(Attached("Europass-XML-Attachment.xml", OLD_XML.read_bytes()))

    record = europass.read(data)

    assert record.source == "pdf-xml"
    assert record.counts()["experience"] == 2


@pytest.mark.parametrize(
    "options",
    [{"filters": ()}, {"length": "elsewhere"}, {"length": "absent"}],
    ids=["not-compressed", "length-elsewhere", "no-length"],
)
def test_the_ways_a_stream_may_be_written_all_read(options):
    assert europass.read(europass_pdf(attachment(**options))).counts()["experience"] == 2


def test_a_name_ending_in_stream_does_not_end_a_dictionary_early():
    data = europass_pdf(
        Attached("photo.bin", b"\x00" * 64, subtype="application#2Foctet-stream"),
        attachment(),
    )

    assert europass.read(data).source == "pdf-candidate"


def test_a_doctype_after_a_long_comment_is_refused_inside_a_pdf_too():
    """#378: the refusal looked only at the first 4 KB of the attachment."""
    bomb = (
        b"<!--" + b" " * 5000 + b"-->"
        b'<!DOCTYPE r [<!ENTITY e "boom">]>'
        b"<SkillsPassport><LearnerInfo><Headline>&e;</Headline></LearnerInfo></SkillsPassport>"
    )
    for data in (bomb, bomb.decode().encode("utf-16-le")):
        with pytest.raises((europass.EuropassError, base.ImportRefused)):
            europass.read(europass_pdf(attachment(data)))
    with pytest.raises(base.ImportRefused, match="document type"):
        europass.read(europass_pdf(attachment(bomb)))


def test_a_pdf_with_nothing_attached_says_what_to_upload_instead():
    with pytest.raises(europass.EuropassError, match="nothing attached") as refused:
        europass.read(europass_pdf())

    assert "europass.europa.eu" in str(refused.value)


def test_a_pdf_whose_attachments_are_not_europass_says_so():
    data = europass_pdf(Attached("notes.txt", b"Remember the milk.", subtype="text#2Fplain"))

    with pytest.raises(europass.EuropassError, match="none of them is a Europass CV"):
        europass.read(data)


def test_a_pdf_attached_to_a_pdf_is_not_opened():
    inner = europass_pdf(attachment())

    with pytest.raises(europass.EuropassError, match="none of them is a Europass CV"):
        europass.read(europass_pdf(Attached("cv.pdf", inner, subtype="application#2Fpdf")))


def test_an_encrypted_pdf_is_refused_before_anything_is_unpacked():
    with pytest.raises(europass.EuropassError, match="encrypted"):
        europass.read(europass_pdf(attachment(), encrypt=True))


@pytest.mark.parametrize(
    "filters,body",
    [
        (("ASCIIHexDecode",), None),
        (("ASCII85Decode", "FlateDecode"), None),
    ],
    ids=["another-filter", "a-chain-of-filters"],
)
def test_an_attachment_packed_another_way_is_refused_by_name(filters, body):
    data = europass_pdf(attachment(filters=filters, body=b"3C3F786D6C>" if body is None else body))

    with pytest.raises(europass.EuropassError, match=filters[0]):
        europass.read(data)


def test_a_damaged_attachment_says_so():
    data = europass_pdf(attachment(body=b"this is not deflate at all"))

    with pytest.raises(europass.EuropassError, match="damaged"):
        europass.read(data)


def test_an_attachment_that_unpacks_past_the_cap_is_refused():
    """A few kilobytes of deflate that would unpack to more than the cap: the decompression
    bomb. The cap stops it at the first byte over, not after the last."""
    bomb = zlib.compress(b"\0" * (europass.MAX_BYTES + 1024), 9)
    assert len(bomb) < 64 * 1024

    with pytest.raises(europass.EuropassError, match="unpacks to more than 5 MB"):
        europass.read(europass_pdf(attachment(b"", body=bomb)))


def test_what_comes_out_of_a_pdf_meets_every_guard_an_upload_does():
    """The attachment is a second file, and a DOCTYPE in it is refused like one in the first."""
    bomb = (
        b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
        b'<Candidate xmlns="http://www.europass.eu/1.0"><CandidatePerson>&x;</CandidatePerson>'
        b"</Candidate>"
    )

    with pytest.raises(base.ImportRefused, match="document type declaration"):
        europass.read(europass_pdf(attachment(bomb)))


def test_a_file_made_of_markers_costs_only_so_many_looks():
    """Each marker costs a look either side of it; a file of nothing else must not cost
    that look for every one of them, or four megabytes of markers take minutes."""
    data = b"%PDF-1.7\n" + b"1 0 obj << /Type /EmbeddedFile " * 120_000

    with pytest.raises(europass.EuropassError, match="nothing attached"):
        europass.read(data)


def test_only_so_many_attachments_are_looked_at():
    """A Europass PDF carries one. A PDF made of hundreds is not allowed to cost hundreds."""
    notes = [
        Attached(f"n{i}.txt", b"note", subtype="text#2Fplain") for i in range(pdf.MAX_ATTACHMENTS)
    ]

    with pytest.raises(europass.EuropassError, match="none of them is a Europass CV"):
        europass.read(europass_pdf(*notes, attachment()))


# ------------------------------------------------------------ the page and the command


def upload(name: str, data: bytes, content_type: str = "application/pdf"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def test_the_page_asks_for_the_pdf(client, user):
    client.force_login(user)

    body = client.get(reverse("resume:europass_import")).content.decode()

    assert 'accept=".pdf,' in body
    assert "europass.europa.eu" in body


def test_the_pdf_reads_through_the_page_and_is_written_when_confirmed(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("Europass CV.pdf", europass_pdf(attachment()))})
    page = client.get(url).content.decode()

    assert "Read from the Europass XML attached to the PDF" in page
    assert "Senior Engineer" in page and "Aperture Science" in page
    assert not Experience.objects.filter(owner=user).exists()

    client.post(url, {"action": "confirm"})

    assert Experience.objects.filter(owner=user).count() == 2
    assert Education.objects.filter(owner=user).count() == 1
    assert Skill.objects.filter(owner=user).count() == 5
    assert Project.objects.filter(owner=user).count() == 1
    user.profile.refresh_from_db()
    assert user.profile.record_language == "en"


def test_the_candidate_xml_reads_through_the_page_and_says_which_it_was(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("cv.xml", CANDIDATE.read_bytes(), "text/xml")})

    assert (
        "the format europass.europa.eu has written since 2020" in client.get(url).content.decode()
    )


def test_a_pdf_the_page_cannot_read_says_why_and_keeps_the_page(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    response = client.post(url, {"file": upload("scan.pdf", europass_pdf())}, follow=True)

    assert "nothing attached" in response.content.decode()
    assert response.context["found"] is None


def test_the_command_reads_a_pdf(user, tmp_path, capsys):
    path = tmp_path / "europass.pdf"
    path.write_bytes(europass_pdf(attachment()))

    call_command("import_europass", str(path), f"--user={user.email}", "--dry-run")

    out = capsys.readouterr().out
    assert "PDF-CANDIDATE" in out
    assert "experience: 2" in out
    assert not Experience.objects.filter(owner=user).exists()


@pytest.mark.parametrize(
    ("country", "written", "stored"),
    [("44", "07911 123456", "+447911123456"), ("39", "06 6982 1234", "+390669821234")],
)
def test_a_number_typed_with_its_leading_zero_is_stored_as_dialled(user, country, written, stored):
    """The reader glues the code to the digits; the importer reads the result against the
    country's plan, so a trunk zero goes and Italy's stays (#644)."""
    from postulo.resume import importing

    channel = (
        "<Communication><ChannelCode>Telephone</ChannelCode>"
        f"<CountryDialing>{country}</CountryDialing>"
        f"<oa:DialNumber>{written}</oa:DialNumber></Communication>"
    )
    importing.apply(user, europass.read(candidate_xml(person=channel)))

    assert user.profile.phone_numbers.get().number == stored


def test_an_other_with_no_title_keeps_all_of_its_description():
    text = (
        "Wrote the migration plan for the billing platform and led the cut-over across "
        "three regions without a minute of downtime."
    )
    data = candidate_xml(f"<Others><Other><Description>{text}</Description></Other></Others>")

    (project,) = europass.read(data).projects

    assert project["summary"] == text
    assert len(project["name"]) <= 80


def test_every_skill_in_a_group_is_kept():
    skills = "".join(f"<DigitalSkill>Skill {number}</DigitalSkill>" for number in range(55))
    data = candidate_xml(
        f"<DigitalSkills><DigitalSkillsGroup>{skills}</DigitalSkillsGroup></DigitalSkills>"
    )

    (group,) = europass.read(data).skill_groups

    assert len(group["skills"]) == 55


# ------------------------------------------------------------ the organisation as a company

FROM_2020 = (
    f"<eures:EmploymentPeriod><eures:StartDate>{when('2020')}</eures:StartDate>"
    f"</eures:EmploymentPeriod>"
)


def test_a_europass_employer_is_linked_to_the_company_of_that_name_and_never_adds_one(user):
    from postulo.jobs.models import Company

    initech = Company.objects.create(owner=user, name="INITECH")
    importing.apply(user, europass.read(candidate_xml(post("Engineer", FROM_2020))))
    entry = Experience.objects.get(owner=user)
    assert entry.company == initech
    assert entry.organisation == "Initech"


def test_a_europass_employer_with_no_company_of_that_name_adds_none(user):
    from postulo.jobs.models import Company

    importing.apply(user, europass.read(candidate_xml(post("Engineer", FROM_2020))))
    assert Experience.objects.get(owner=user).company is None
    assert not Company.objects.filter(owner=user).exists()


def test_a_europass_institution_is_linked_to_an_existing_company_and_never_adds_one(user):
    from postulo.jobs.models import Company

    data = candidate_xml(
        "<EducationHistory><EducationOrganizationAttendance>"
        "<hr:OrganizationName>ISEL</hr:OrganizationName><hr:ProgramName>BSc</hr:ProgramName>"
        "<AttendancePeriod><Ongoing>true</Ongoing></AttendancePeriod>"
        "</EducationOrganizationAttendance></EducationHistory>"
    )
    importing.apply(user, europass.read(data))
    assert Education.objects.get(owner=user).company is None
    assert not Company.objects.filter(owner=user).exists()

    isel = Company.objects.create(owner=user, name="isel")
    Education.objects.all().delete()
    importing.apply(user, europass.read(data))
    assert Education.objects.get(owner=user).company == isel


@pytest.mark.parametrize(
    "written,code",
    [
        ("eng", "en"),
        ("de", "de"),
        ("http://publications.europa.eu/resource/authority/language/MLT", "mt"),
        ("http://publications.europa.eu/resource/authority/language/BFI", "bfi"),
    ],
)
def test_a_language_given_as_a_code_keeps_its_code(written, code):
    """A code Postulo cannot name, a sign language, is kept here and dropped by the importer
    (`resume.importing.language_code_of`), which is the one that knows what it can name."""
    person = f"<PrimaryLanguageCode>{written}</PrimaryLanguageCode>"

    assert europass.read(candidate_xml(person=person)).languages[0]["code"] == code


def test_free_text_is_a_name_and_no_code():
    person = '<PrimaryLanguageCode typeCode="FREETEXT">French</PrimaryLanguageCode>'

    assert "code" not in europass.read(candidate_xml(person=person)).languages[0]


# ------------------------------------------------------------ memberships (#693)

MEMBERSHIPS = (
    "<NetworksAndMemberships>"
    "<NetworkAndMembership><Title>Member of the University's Film-Making Society</Title>"
    "<Date><StartDate><hr:FormattedDateTime>2015-03</hr:FormattedDateTime></StartDate>"
    "<EndDate><hr:FormattedDateTime>2018-06-30</hr:FormattedDateTime></EndDate></Date>"
    "<oa:Description>&lt;p&gt;Shot two shorts.&lt;/p&gt;</oa:Description>"
    "<Link>https://films.example.org</Link></NetworkAndMembership>"
    "<NetworkAndMembership><Title>Chess club</Title>"
    "<Date><StartDate><hr:FormattedDateTime>2020</hr:FormattedDateTime></StartDate>"
    "<EndDate><hr:FormattedDateTime>2021</hr:FormattedDateTime></EndDate>"
    "<Ongoing>true</Ongoing></Date></NetworkAndMembership>"
    "</NetworksAndMemberships>"
    "<OrganizationAffiliations><OrganizationAffiliation>"
    '<hr:OrganizationName validFrom="2012-01-15" validTo="2014">Rowing Club</hr:OrganizationName>'
    "<Link>javascript:alert(1)</Link>"
    "</OrganizationAffiliation></OrganizationAffiliations>"
)


def test_the_two_membership_elements_are_read_as_memberships():
    record = europass.read(candidate_xml(MEMBERSHIPS))

    assert [row["organisation"] for row in record.memberships] == [
        "Member of the University's Film-Making Society",
        "Chess club",
        "Rowing Club",
    ]
    film, chess, rowing = record.memberships
    # A title is the organisation as written: splitting it into a role and a body is a guess.
    assert film["role"] == ""
    assert (film["start_date"], film["end_date"]) == (dt.date(2015, 3, 1), dt.date(2018, 6, 30))
    assert film["summary"] == "Shot two shorts."
    assert film["url"] == "https://films.example.org"
    assert (chess["start_date"], chess["end_date"]) == (dt.date(2020, 1, 1), None)
    assert (rowing["start_date"], rowing["end_date"]) == (dt.date(2012, 1, 15), dt.date(2014, 1, 1))
    assert record.counts()["memberships"] == 3
    assert not record.is_empty


def test_memberships_are_no_longer_named_as_having_nowhere_to_go():
    record = europass.read(candidate_xml(MEMBERSHIPS))

    assert not any("NetworksAndMemberships" in note for note in record.skipped)
    assert not any("OrganizationAffiliations" in note for note in record.skipped)


def test_nothing_is_read_as_an_honour():
    data = candidate_xml(
        "<NetworksAndMemberships><NetworkAndMembership><Title>Club</Title>"
        "<HonourAwardDate>2019</HonourAwardDate></NetworkAndMembership></NetworksAndMemberships>"
    )

    record = europass.read(data)

    assert [row["organisation"] for row in record.memberships] == ["Club"]
    assert not hasattr(record, "honours")


def test_an_element_with_no_name_is_not_a_membership():
    data = candidate_xml(
        "<NetworksAndMemberships><NetworkAndMembership><Description>x</Description>"
        "</NetworkAndMembership></NetworksAndMemberships>"
        "<OrganizationAffiliations><OrganizationAffiliation/></OrganizationAffiliations>"
    )

    assert europass.read(data).memberships == []


def test_the_review_page_lists_them_before_anything_is_saved_and_confirming_writes_them(
    client, user
):
    from postulo.resume.models import Membership

    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("cv.xml", candidate_xml(MEMBERSHIPS), "text/xml")})
    page = client.get(url).content.decode()

    assert "Memberships" in page and "Chess club" in page
    assert not Membership.objects.filter(owner=user).exists()

    client.post(url, {"action": "confirm"})

    kept = {m.organisation: m for m in Membership.objects.filter(owner=user)}
    assert set(kept) == {
        "Member of the University's Film-Making Society",
        "Chess club",
        "Rowing Club",
    }
    assert kept["Chess club"].end_date is None
    assert kept["Rowing Club"].url == "", "an address that is not a web address is not kept"
    assert kept["Member of the University's Film-Making Society"].url == "https://films.example.org"


def test_a_course_is_not_invented_from_an_education_entry(user):
    """The Candidate has no element for a course, and an attendance is filed as education:
    deciding which are courses would be a guess (#695)."""
    from postulo.resume.models import Course

    importing.apply(user, europass.read(CANDIDATE.read_bytes()))

    assert Education.objects.for_user(user).exists()
    assert not Course.objects.for_user(user).exists()


# ------------------------------------------------------------------ referees (#696)


def referee(name="Chell Johnson", *, role="Line manager", email="", phone="", extra="") -> str:
    channels = ""
    if email:
        channels += (
            f"<Communication><ChannelCode>Email</ChannelCode><URI>{email}</URI></Communication>"
        )
    if phone:
        channels += (
            "<Communication><ChannelCode>Telephone</ChannelCode>"
            f"<CountryDialing>351</CountryDialing><oa:DialNumber>{phone}</oa:DialNumber>"
            "</Communication>"
        )
    return (
        "<Referee><PersonName><oa:GivenName>"
        + name.split()[0]
        + "</oa:GivenName><hr:FamilyName>"
        + " ".join(name.split()[1:])
        + f"</hr:FamilyName></PersonName>{channels}<Role>{role}</Role>{extra}</Referee>"
    )


def referees(*rows: str) -> bytes:
    return candidate_xml(f"<EmploymentReferences>{''.join(rows)}</EmploymentReferences>")


def test_a_referee_is_read_as_a_person_how_to_reach_them_and_a_role():
    record = europass.read(referees(referee(email="chell@aperture.example", phone="912345678")))

    assert record.references == [
        {
            "name": "Chell Johnson",
            "relationship": "Line manager",
            "email": "chell@aperture.example",
            "phone": "+351912345678",
        }
    ]
    assert not record.is_empty
    assert record.counts()["references"] == 1
    assert not any("nowhere to go" in line for line in record.skipped)


def test_what_a_referee_says_that_is_not_kept_is_named_not_dropped():
    extra = (
        "<RefereeTypeCode>Professional</RefereeTypeCode><YearsKnownNumber>4</YearsKnownNumber>"
        "<Comment>Superb</Comment><Link>https://example.org</Link>"
    )
    record = europass.read(referees(referee(extra=extra)))

    (note,) = record.skipped
    for name in ("RefereeTypeCode", "YearsKnownNumber", "Comment", "Link"):
        assert name in note
    assert "not yet asked" in note
    assert record.references[0]["relationship"] == "Line manager"


def test_more_than_twenty_referees_are_capped_and_said():
    record = europass.read(referees(*(referee(f"Person {n}") for n in range(25))))

    assert len(record.references) == candidate.MAX_REFEREES == 20
    assert any("25 references" in line and "first 20" in line for line in record.skipped)


def test_the_review_page_lists_each_person_before_anything_is_written(client, user):
    from postulo.jobs.models import Contact
    from postulo.resume.models import Reference

    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload("cv.xml", referees(referee()), "text/xml")})

    page = client.get(url).content.decode()
    assert "Chell Johnson" in page and "Line manager" in page
    assert "none is printed on a CV until you say they agreed" in page
    assert not Contact.objects.exists() and not Reference.objects.exists()

    client.post(url, {"action": "confirm"})
    (mine,) = Reference.objects.for_user(user)
    assert mine.contact.name == "Chell Johnson"


def test_an_imported_referee_is_always_not_asked_and_prints_nothing_until_agreed(user):
    from postulo.documents import rendering
    from postulo.documents.models import CV, CVItem
    from postulo.resume.models import Reference

    importing.apply(user, europass.read(referees(referee(email="chell@aperture.example"))))

    (mine,) = Reference.objects.for_user(user)
    assert (mine.permission, mine.show_details, mine.relationship) == (
        "not_asked",
        False,
        "Line manager",
    )
    assert mine.contact.email == "chell@aperture.example"
    from django.contrib.contenttypes.models import ContentType

    cv = CV.objects.create(owner=user, name="Main", language="en-GB")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Reference),
        object_id=mine.pk,
    )
    assert "Chell" not in rendering.cv_text(cv)


def test_a_contact_with_the_same_name_and_address_is_reused_not_duplicated(user):
    from postulo.jobs.models import Contact
    from postulo.resume.models import Reference

    known = Contact.objects.create(owner=user, name="chell johnson", email="Chell@Aperture.example")
    importing.apply(user, europass.read(referees(referee(email="chell@aperture.example"))))

    assert Contact.objects.for_user(user).count() == 1
    assert Reference.objects.get(owner=user).contact == known

    report = importing.apply(user, europass.read(referees(referee(email="chell@aperture.example"))))
    assert Reference.objects.for_user(user).count() == 1
    assert any("already one of your references" in line for line in report.skipped)


def test_a_telephone_number_held_elsewhere_is_left_out_and_named_without_saying_why(
    user, other_user
):
    from postulo.core.models import PhoneNumber

    PhoneNumber.objects.create(
        owner=other_user, holder=other_user.profile, number="+351912345678", is_primary=True
    )
    report = importing.apply(user, europass.read(referees(referee(phone="912345678"))))

    assert not PhoneNumber.objects.filter(owner=user).exists()
    assert report.skipped == ["The telephone number of Chell Johnson was not added."]
    assert report.added["references"] == 1


def test_a_number_nobody_holds_is_kept_on_the_new_contact(user):
    from postulo.core import phone_numbers
    from postulo.jobs.models import Contact

    importing.apply(user, europass.read(referees(referee(phone="912345678"))))

    contact = Contact.objects.get(owner=user)
    assert phone_numbers.primary_for(contact).number == "+351912345678"


# --------------------------------------------------- conferences, seminars and speaking (#694)

EVENTS = (
    "<ConferencesAndSeminars>"
    "<ConferenceAndSeminar><Title>PyCon Portugal 2025</Title>"
    "<Date><StartDate><hr:FormattedDateTime>2025-10-17</hr:FormattedDateTime></StartDate>"
    "<EndDate><hr:FormattedDateTime>2025-10-18</hr:FormattedDateTime></EndDate></Date>"
    "<Location>Lisbon, Portugal</Location>"
    "<oa:Description>&lt;p&gt;Gave a talk on error budgets.&lt;/p&gt;</oa:Description>"
    "<Link>https://pycon.example/2025</Link><Link>https://pycon.example/slides</Link>"
    "</ConferenceAndSeminar>"
    "<ConferenceAndSeminar><Title>Annual seminar</Title>"
    "<Date><StartDate><hr:FormattedDateTime>2020</hr:FormattedDateTime></StartDate>"
    "<EndDate><hr:FormattedDateTime>2021</hr:FormattedDateTime></EndDate>"
    "<Ongoing>true</Ongoing></Date></ConferenceAndSeminar>"
    "<ConferenceAndSeminar><Description>No title</Description></ConferenceAndSeminar>"
    "</ConferencesAndSeminars>"
    "<SpeakingHistory><SpeakingEvent><EventName>Guest lecture, University of Porto</EventName>"
    "<oa:Description>Four lectures.</oa:Description></SpeakingEvent>"
    "<SpeakingEvent><Description>No name</Description></SpeakingEvent></SpeakingHistory>"
)


def test_a_conference_and_a_speaking_event_are_read_as_participations():
    record = europass.read(candidate_xml(EVENTS))

    assert [row["event"] for row in record.participations] == [
        "PyCon Portugal 2025",
        "Annual seminar",
        "Guest lecture, University of Porto",
    ]
    pycon, seminar, lecture = record.participations
    assert (pycon["start_date"], pycon["end_date"]) == (
        dt.date(2025, 10, 17),
        dt.date(2025, 10, 18),
    )
    assert pycon["place"] == "Lisbon, Portugal"
    assert pycon["summary"] == "Gave a talk on error budgets."
    assert pycon["url"] == "https://pycon.example/2025"
    # The element does not say what the person did: it is not guessed from the prose.
    assert (pycon["role"], pycon["kind"], pycon["title"]) == ("", "", "")
    assert seminar["end_date"] is None, "an ongoing flag leaves the end empty"
    assert (lecture["role"], lecture["summary"]) == ("speaker", "Four lectures.")
    assert record.counts()["participations"] == 3
    assert not record.is_empty


def test_a_second_link_is_named_as_not_read_and_the_sections_are_not_named_as_missed():
    record = europass.read(candidate_xml(EVENTS))

    assert any("links past the first" in note for note in record.skipped)
    assert not any("ConferencesAndSeminars" in note for note in record.skipped)
    assert not any("SpeakingHistory" in note for note in record.skipped)


def test_no_note_about_links_where_each_event_has_one():
    data = candidate_xml(
        "<ConferencesAndSeminars><ConferenceAndSeminar><Title>X</Title>"
        "<Link>https://x.example</Link></ConferenceAndSeminar></ConferencesAndSeminars>"
    )

    assert not any("links" in note for note in europass.read(data).skipped)


def test_the_review_page_lists_events_and_confirming_writes_them_unstated(client, user):
    from postulo.resume.models import Participation

    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("cv.xml", candidate_xml(EVENTS), "text/xml")})
    page = client.get(url).content.decode()

    assert "Presentations, conferences and seminars" in page and "PyCon Portugal 2025" in page
    assert not Participation.objects.filter(owner=user).exists()

    client.post(url, {"action": "confirm"})

    kept = {p.event: p for p in Participation.objects.filter(owner=user)}
    assert set(kept) == {
        "PyCon Portugal 2025",
        "Annual seminar",
        "Guest lecture, University of Porto",
    }
    assert kept["PyCon Portugal 2025"].role == "" and kept["PyCon Portugal 2025"].kind == ""
    assert kept["Guest lecture, University of Porto"].role == "speaker"
    assert kept["PyCon Portugal 2025"].start_date == dt.date(2025, 10, 17)
    assert kept["Annual seminar"].end_date is None
