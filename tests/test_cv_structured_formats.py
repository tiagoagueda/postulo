"""A CV leaves as Europass and as JSON Resume, as well as in words (#1100).

Job portals across Europe take Europass, and many tools read JSON Resume; a CV that left only
as a PDF or a Word file was retyped into each of them by hand. Held here:

- each file is the **variant**: its chosen entries in its order and language, the details it
  prints, nobody where its contact block is off -- never the whole record;
- the Europass file **reads back** through Postulo's own Europass importer as what was
  written, as far as the format carries it, so the reader and the writer share one mapping;
- each file is valid against its format's published schema: JSON Resume's own (vendored,
  MIT, `tests/data/json-resume/`), and for Europass the constraints of Cedefop's schema 3.3.0
  this writer relies on, written out below because no licence lets that schema be copied
  here; nothing is fetched;
- what a format has no place for is left out of the file and named on the CV's page;
- both follow their switch under *Extend file formats*, and a letter is offered neither.

The people and careers here are made up by hand: no real person's file is used.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import fastjsonschema
import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.accounts.models import PersonIdentifier
from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.documents import formats, jsonresume, rendering, structured
from postulo.documents.models import CV, CoverLetter, CVItem
from postulo.plugins.europass import reader
from postulo.plugins.europass import writer as europass
from postulo.plugins.models import PluginPolicy
from postulo.resume.models import (
    Certification,
    Course,
    DrivingLicence,
    Education,
    Experience,
    Honour,
    LanguageSkill,
    Link,
    Membership,
    Project,
    SkillGroup,
)

pytestmark = pytest.mark.django_db

DATA = Path(__file__).resolve().parent / "data"

MOBILE = "+351912345678"
MOBILE_PRINTED = "+351 912 345 678"
LINKEDIN = "https://www.linkedin.com/in/alex-morgan"
FORGE = "https://codeberg.org/alex"
SITE = "https://alex.example"
ORCID = "0000-0002-1825-0097"


def put_on(cv, entry, order: int = 0, **fields):
    return CVItem.objects.create(
        owner=cv.owner,
        cv=cv,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        order=order,
        **fields,
    )


@pytest.fixture
def person(user):
    """Somebody made up, with a detail of most kinds a CV can print."""
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    profile = user.profile
    profile.headline = "Backend engineer"
    profile.pronouns = "they"
    profile.birth_date = "1990-05"
    profile.birth_place = "Coimbra"
    profile.nationalities = ["PT", "FR"]
    profile.save()
    PhoneNumber.objects.create(
        owner=user, holder=profile, number=MOBILE, kind="mobile", is_primary=True
    )
    for kind, url in (("social", LINKEDIN), ("repository", FORGE), ("website", SITE)):
        WebLink.objects.create(owner=user, holder=profile, kind=kind, url=url, is_primary=True)
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value=ORCID)
    PostalAddress.objects.create(
        owner=user, holder=profile, municipality="Lisboa", country="PT", is_primary=True
    )
    return user


@pytest.fixture
def cv(person):
    """One of most kinds of entry, by hand, in the order somebody might put them."""
    variant = CV.objects.create(
        owner=person,
        name="Backend EN",
        headline="Backend engineer",
        summary="Ten years of keeping services up.",
        language="en-GB",
        show_eqf_level=True,
        show_pronouns=True,
        show_birth_date=True,
        show_birth_place=True,
        show_nationality=True,
    )
    put_on(
        variant,
        Experience.objects.create(
            owner=person,
            organisation="Aperture Science",
            role="Senior Engineer",
            location="Cambridge",
            start_date=datetime.date(2021, 3, 15),
            summary="Kept the portal up.",
            highlights="Cut deploys to <4 minutes & kept them there.\nMentored three engineers.",
        ),
        0,
    )
    put_on(
        variant,
        Experience.objects.create(
            owner=person,
            organisation="Black Mesa",
            role="Engineer",
            start_date=datetime.date(2016, 1, 4),
            end_date=datetime.date(2021, 2, 26),
        ),
        1,
    )
    put_on(
        variant,
        Education.objects.create(
            owner=person,
            institution="Universidade de Lisboa",
            qualification="BSc Computer Science",
            field_of_study="Distributed systems",
            location="Lisboa",
            eqf_level=6,
            grade="17/20",
            start_date=datetime.date(2010, 9, 1),
            end_date=datetime.date(2013, 7, 1),
            highlights="Thesis on consensus.",
        ),
        2,
    )
    put_on(
        variant,
        Education.objects.create(
            owner=person,
            institution="Escola Secundária",
            qualification="Secondary school",
            grade="Very good",
        ),
        3,
    )
    put_on(
        variant,
        Project.objects.create(
            owner=person,
            name="Turret firmware",
            role="Maintainer",
            url="https://example.org/turrets",
            summary="Open-source firmware.",
            highlights="Adopted by three labs.",
            start_date=datetime.date(2019, 1, 1),
        ),
        4,
    )
    digital = SkillGroup.objects.create(owner=person, name="Digital")
    digital.skills.create(owner=person, name="Python")
    digital.skills.create(owner=person, name="Go")
    put_on(variant, digital, 5)
    cooking = SkillGroup.objects.create(owner=person, name="Cooking")
    cooking.skills.create(owner=person, name="Bread")
    put_on(variant, cooking, 6)
    put_on(
        variant,
        LanguageSkill.objects.create(
            owner=person, name="Portuguese", code="pt", proficiency="native"
        ),
        7,
    )
    put_on(
        variant,
        LanguageSkill.objects.create(owner=person, name="French", code="fr", proficiency="c1"),
        8,
    )
    put_on(variant, LanguageSkill.objects.create(owner=person, name="Basque", proficiency=""), 9)
    put_on(
        variant,
        Certification.objects.create(
            owner=person,
            name="Kubernetes Administrator",
            issuer="CNCF",
            issued_on=datetime.date(2022, 5, 1),
        ),
        10,
    )
    put_on(
        variant,
        Honour.objects.create(owner=person, title="Best paper", awarded_by="SOSP"),
        11,
    )
    put_on(
        variant,
        Membership.objects.create(owner=person, organisation="ACM", role="Member"),
        12,
    )
    put_on(variant, Course.objects.create(owner=person, title="Rust in a week"), 13)
    put_on(
        variant,
        DrivingLicence.objects.create(owner=person, categories=["B", "AM"], country="PT"),
        14,
    )
    put_on(
        variant,
        Link.objects.create(owner=person, title="Portfolio", url="https://alex.example/work"),
        15,
    )
    # On the record and not on this CV, and on it and hidden: neither is in a file.
    Experience.objects.create(
        owner=person,
        organisation="Not on it",
        role="Elsewhere",
        start_date=datetime.date(2010, 1, 1),
    )
    put_on(
        variant,
        Experience.objects.create(
            owner=person,
            organisation="Hidden Ltd",
            role="Hidden",
            start_date=datetime.date(2012, 1, 1),
        ),
        16,
        is_included=False,
    )
    return variant


def europass_of(cv) -> dict:
    return json.loads(europass.write(structured.cv_data(cv)))


def json_resume_of(cv) -> dict:
    return json.loads(jsonresume.write(structured.cv_data(cv)))


# ------------------------------------------------- Europass, read back by Postulo's own reader


def test_a_cv_written_as_europass_reads_back_as_what_was_written(cv, person):
    """The writer puts every field where the importer looks for it (#1100). A career that
    left as Europass and came back through Postulo's own Europass reader is the same career,
    as far as the format carries it."""
    data = structured.cv_data(cv)
    record = reader.read(europass.write(data))

    with_contact = rendering.contact_details(person, cv)
    assert record.source == "json"
    assert record.locale == "en"
    assert {
        key: record.person[key]
        for key in ("first_name", "last_name", "email", "phone", "website", "orcid", "headline")
    } == {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": person.email,
        "phone": MOBILE_PRINTED,
        "website": SITE,
        "orcid": ORCID,
        "headline": "Backend engineer",
    }
    assert record.person["location"] == with_contact["location"]
    assert record.experience == [
        {
            "role": "Senior Engineer",
            "organisation": "Aperture Science",
            "location": "Cambridge",
            "start_date": datetime.date(2021, 3, 15),
            "end_date": None,
            # Europass has one field of prose for a job: the summary and the highlights.
            "summary": "Kept the portal up.\n"
            "Cut deploys to <4 minutes & kept them there.\n"
            "Mentored three engineers.",
        },
        {
            "role": "Engineer",
            "organisation": "Black Mesa",
            "location": "",
            "start_date": datetime.date(2016, 1, 4),
            "end_date": datetime.date(2021, 2, 26),
            "summary": "",
        },
    ]
    assert record.education == [
        {
            "qualification": "BSc Computer Science",
            "institution": "Universidade de Lisboa",
            "location": "Lisboa",
            "start_date": datetime.date(2010, 9, 1),
            "end_date": datetime.date(2013, 7, 1),
            "grade": "",
            "eqf_level": 6,
            "highlights": "Thesis on consensus.",
        },
        {
            "qualification": "Secondary school",
            "institution": "Escola Secundária",
            "location": "",
            "start_date": None,
            "end_date": None,
            "grade": "Very good",
            "eqf_level": None,
            "highlights": "",
        },
    ]
    assert record.languages == [
        {"name": "Portuguese", "proficiency": "native", "levels": {}},
        {
            "name": "French",
            "proficiency": "c1",
            "levels": dict.fromkeys(
                ("Listening", "Reading", "SpokenInteraction", "SpokenProduction", "Writing"), "C1"
            ),
        },
        {"name": "Basque", "proficiency": "", "levels": {}},
    ]
    # A group named as a Europass heading comes back under its name; any other group's
    # skills come back under "Other", which is what the page says is left out.
    assert record.skill_groups == [
        {"name": "Digital", "skills": ["Python", "Go"]},
        {"name": "Other", "skills": ["Bread"]},
    ]
    assert record.projects[0] == {
        "name": "Turret firmware",
        "summary": "Maintainer\nOpen-source firmware.\nhttps://example.org/turrets\n"
        "Adopted by three labs.",
    }
    assert not record.skipped


def test_nothing_off_the_cv_is_in_either_file(cv):
    """The variant, not the record: an entry on the record and not on this CV, and one on it
    and hidden, are in neither file (#1100)."""
    for written in (europass.write, jsonresume.write):
        text = written(structured.cv_data(cv)).decode()
        assert "Aperture Science" in text
        assert "Not on it" not in text
        assert "Hidden Ltd" not in text


def test_a_cv_with_its_contact_block_off_names_nobody_in_either_file(cv, person):
    """The switch an anonymous copy is made with holds for these files as for the PDF (#512)."""
    cv.show_contact_details = False
    cv.save()

    passport = europass_of(cv)["SkillsPassport"]["LearnerInfo"]
    resume = json_resume_of(cv)

    assert "PersonName" not in passport.get("Identification", {})
    assert "Alex" not in json.dumps(passport) and person.email not in json.dumps(passport)
    assert "name" not in resume["basics"] and "email" not in resume["basics"]
    assert "Alex" not in json.dumps(resume)


def test_a_detail_the_cv_does_not_print_is_in_neither_file(cv, person):
    """A format with a field for a detail does not put back one the CV keeps off it."""
    cv.show_birth_date = cv.show_nationality = cv.show_pronouns = False
    cv.save()

    identification = europass_of(cv)["SkillsPassport"]["LearnerInfo"]["Identification"]

    assert "Birthdate" not in identification.get("Demographics", {})
    assert "Nationality" not in identification.get("Demographics", {})
    assert "pronouns" not in europass.leaves_out(structured.cv_data(cv))


def test_the_personal_details_europass_has_a_place_for_are_there(cv):
    identification = europass_of(cv)["SkillsPassport"]["LearnerInfo"]["Identification"]

    assert identification["Demographics"]["Birthdate"] == {"Year": 1990, "Month": 5}
    assert identification["Demographics"]["Nationality"] == [{"Code": "PT"}, {"Code": "FR"}]


def test_what_was_typed_is_text_and_not_markup_in_the_file(cv):
    """Europass's long fields are HTML; "<4 minutes" is escaped, and reads back as typed."""
    passport = europass_of(cv)["SkillsPassport"]["LearnerInfo"]
    activities = passport["WorkExperience"][0]["Activities"]

    assert "&lt;4 minutes &amp; kept" in activities
    assert "<4" not in activities


def test_the_same_cv_is_the_same_bytes(cv):
    data = structured.cv_data(cv)
    assert europass.write(data) == europass.write(structured.cv_data(cv))
    assert jsonresume.write(data) == jsonresume.write(structured.cv_data(cv))


def test_the_file_is_in_the_documents_language(cv):
    """A skill heading Europass writes is in the CV's language, as the reader reads it."""
    cv.language = "pt-PT"
    cv.save()

    passport = europass_of(cv)["SkillsPassport"]

    assert passport["Locale"] == "pt"


# ------------------------------------------------------------- valid against the schemas

#: The constraints of the Europass JSON Schema 3.3.0 (Cedefop, `europass-json-schema-v3.3.0.json`,
#: published at interop.europass.cedefop.europa.eu/documentation/data-model/json-resources)
#: that the writer relies on, written out for this test: the schema itself states no licence
#: that lets it be copied into the repository. Every object the writer makes is closed here
#: (`additionalProperties: false`) where the official one is, and the codes, patterns and
#: types are the official ones. Draft 4, as the official schema is.
_DATE = {
    "type": "object",
    "properties": {
        "Year": {"type": "number"},
        "Month": {"type": "number"},
        "Day": {"type": "number"},
    },
    "additionalProperties": False,
}
_PERIOD = {
    "type": "object",
    "properties": {"From": _DATE, "To": _DATE, "Current": {"type": "boolean"}},
    "additionalProperties": False,
}
_COUNTRY = {
    "type": "object",
    "properties": {
        "Code": {"type": "string", "pattern": "^[A-Z]{2}$"},
        "Label": {"type": "string"},
    },
    "additionalProperties": False,
}
_ADDRESS = {
    "type": "object",
    "properties": {
        "AddressLine": {"type": "string"},
        "AddressLine2": {"type": "string"},
        "PostalCode": {"type": "string"},
        "Municipality": {"type": "string"},
        "Country": _COUNTRY,
    },
    "additionalProperties": False,
}
_ORGANISATION = {
    "type": "object",
    "properties": {
        "Name": {"type": "string"},
        "ContactInfo": {
            "type": "object",
            "properties": {
                "Address": {
                    "type": "object",
                    "properties": {"Contact": _ADDRESS},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
_CEFR = {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"]}
_LANGUAGE_NAME = {
    "type": "object",
    "properties": {"Code": {"type": "string"}, "Label": {"type": "string"}},
    "additionalProperties": False,
}
_PROSE = {
    "type": "object",
    "properties": {"Description": {"type": "string"}},
    "additionalProperties": False,
}
EUROPASS_CONSTRAINTS = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "type": "object",
    "required": ["SkillsPassport"],
    "additionalProperties": False,
    "properties": {
        "SkillsPassport": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "Locale": {"type": "string", "pattern": "^[a-z]{2}$"},
                "DocumentInfo": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "DocumentType": {"enum": ["ECV_ESP", "ESP", "ECV", "ELP", "ECL", "EX"]},
                        "XSDVersion": {"type": "string", "pattern": "^V[0-9]\\.[0-9]$"},
                        "Generator": {"type": "string"},
                    },
                },
                "LearnerInfo": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "Identification": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "PersonName": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "Title": {
                                            "type": "object",
                                            "properties": {
                                                "Code": {"enum": ["mr", "ms", "mrs", "miss", "dr"]},
                                                "Label": {"type": "string"},
                                            },
                                            "additionalProperties": False,
                                        },
                                        "FirstName": {"type": "string"},
                                        "Surname": {"type": "string"},
                                    },
                                },
                                "ContactInfo": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "Address": {
                                            "type": "object",
                                            "properties": {"Contact": _ADDRESS},
                                            "additionalProperties": False,
                                        },
                                        "Email": {
                                            "type": "object",
                                            "properties": {"Contact": {"type": "string"}},
                                            "additionalProperties": False,
                                        },
                                        "Telephone": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {"Contact": {"type": "string"}},
                                                "additionalProperties": False,
                                            },
                                        },
                                        "Website": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "Contact": {"type": "string"},
                                                    "Use": {
                                                        "type": "object",
                                                        "properties": {
                                                            "Code": {
                                                                "enum": [
                                                                    "personal",
                                                                    "business",
                                                                    "blog",
                                                                    "portfolio",
                                                                ]
                                                            },
                                                        },
                                                        "additionalProperties": False,
                                                    },
                                                },
                                                "additionalProperties": False,
                                            },
                                        },
                                        "InstantMessaging": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {"Contact": {"type": "string"}},
                                                "additionalProperties": False,
                                            },
                                        },
                                    },
                                },
                                "Demographics": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "Birthdate": _DATE,
                                        "Gender": {
                                            "type": "object",
                                            "properties": {
                                                "Code": {"enum": ["M", "F"]},
                                                "Label": {"type": "string"},
                                            },
                                            "additionalProperties": False,
                                        },
                                        "Nationality": {"type": "array", "items": _COUNTRY},
                                    },
                                },
                                "Photo": {
                                    "type": "object",
                                    "properties": {
                                        "MimeType": {
                                            "enum": [
                                                "image/jpeg",
                                                "image/pjpeg",
                                                "image/png",
                                                "image/x-png",
                                            ]
                                        },
                                        "Data": {"type": "string"},
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "Headline": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "Description": {
                                    "type": "object",
                                    "properties": {"Label": {"type": "string"}},
                                    "additionalProperties": False,
                                }
                            },
                        },
                        "WorkExperience": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "Period": _PERIOD,
                                    "Position": {
                                        "type": "object",
                                        "properties": {"Label": {"type": "string"}},
                                        "additionalProperties": False,
                                    },
                                    "Activities": {"type": "string"},
                                    "Employer": _ORGANISATION,
                                },
                            },
                        },
                        "Education": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "Period": _PERIOD,
                                    "Title": {"type": "string"},
                                    "Activities": {"type": "string"},
                                    "Organisation": _ORGANISATION,
                                    "Field": {
                                        "type": "object",
                                        "properties": {
                                            "Code": {"type": "string", "pattern": "^[0-9]{2}$"},
                                            "Label": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                    "Level": {
                                        "type": "object",
                                        "properties": {
                                            "Code": {"type": "string", "pattern": "^[1-8]{1}$"},
                                            "Label": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                            },
                        },
                        "Skills": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "Linguistic": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "MotherTongue": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {"Description": _LANGUAGE_NAME},
                                                "additionalProperties": False,
                                            },
                                        },
                                        "ForeignLanguage": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "Description": _LANGUAGE_NAME,
                                                    "ProficiencyLevel": {
                                                        "type": "object",
                                                        "properties": {
                                                            "Listening": _CEFR,
                                                            "Reading": _CEFR,
                                                            "SpokenInteraction": _CEFR,
                                                            "SpokenProduction": _CEFR,
                                                            "Writing": _CEFR,
                                                        },
                                                        "additionalProperties": False,
                                                    },
                                                },
                                                "additionalProperties": False,
                                            },
                                        },
                                    },
                                },
                                "Communication": _PROSE,
                                "Organisational": _PROSE,
                                "JobRelated": _PROSE,
                                "Computer": _PROSE,
                                "Other": _PROSE,
                                "Driving": {
                                    "type": "object",
                                    "properties": {
                                        "Description": {
                                            "type": "array",
                                            "items": {
                                                "enum": [
                                                    "A",
                                                    "B",
                                                    "C",
                                                    "D",
                                                    "A1",
                                                    "B1",
                                                    "C1",
                                                    "D1",
                                                    "BE",
                                                    "CE",
                                                    "DE",
                                                    "C1E",
                                                    "D1E",
                                                ]
                                            },
                                        }
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "Achievement": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "Title": {
                                        "type": "object",
                                        "properties": {
                                            "Code": {
                                                "enum": [
                                                    "honors_awards",
                                                    "publications",
                                                    "presentations",
                                                    "projects",
                                                    "citations",
                                                    "memberships",
                                                    "conferences",
                                                    "seminars",
                                                    "workshops",
                                                    "references",
                                                    "signature_equivalent",
                                                    "courses",
                                                    "certifications",
                                                ]
                                            },
                                            "Label": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                    "Description": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


def test_the_europass_file_keeps_to_the_europass_schema(cv):
    """Within schema 3.3.0's own codes, patterns and closed objects (#1100)."""
    check = fastjsonschema.compile(EUROPASS_CONSTRAINTS)

    check(europass_of(cv))


def test_so_does_a_cv_with_nothing_on_it(person):
    empty = CV.objects.create(owner=person, name="Empty")

    fastjsonschema.compile(EUROPASS_CONSTRAINTS)(europass_of(empty))
    _json_resume_schema()(json_resume_of(empty))


def _closed(schema):
    """The published schema with every object closed: no field it does not name."""
    if isinstance(schema, dict):
        out = {key: _closed(value) for key, value in schema.items()}
        if "properties" in out:
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_closed(value) for value in schema]
    return schema


def _json_resume_schema(*, closed: bool = False):
    published = json.loads((DATA / "json-resume" / "schema.json").read_text(encoding="utf-8"))
    return fastjsonschema.compile(_closed(published) if closed else published)


def test_the_json_resume_file_keeps_to_its_published_schema(cv):
    """JSON Resume 1.2.1, as published (`tests/data/json-resume/`, MIT) (#1100)."""
    _json_resume_schema()(json_resume_of(cv))


def test_and_uses_no_field_the_schema_does_not_name(cv):
    """The published schema leaves its objects open; nothing here relies on that."""
    _json_resume_schema(closed=True)(json_resume_of(cv))


def test_the_json_resume_file_carries_the_entries_in_its_own_fields(cv, person):
    resume = json_resume_of(cv)

    assert resume["basics"]["name"] == "Alex Morgan"
    assert resume["basics"]["label"] == "Backend engineer"
    assert resume["basics"]["summary"] == "Ten years of keeping services up."
    assert resume["basics"]["phone"] == MOBILE_PRINTED
    assert resume["basics"]["url"] == SITE
    assert {"url": LINKEDIN, "network": "LinkedIn"} in resume["basics"]["profiles"]
    assert [job["name"] for job in resume["work"]] == ["Aperture Science", "Black Mesa"]
    assert resume["work"][0]["startDate"] == "2021-03-15" and "endDate" not in resume["work"][0]
    assert resume["work"][0]["highlights"] == [
        "Cut deploys to <4 minutes & kept them there.",
        "Mentored three engineers.",
    ]
    assert resume["education"][0] == {
        "institution": "Universidade de Lisboa",
        "area": "Distributed systems",
        "studyType": "BSc Computer Science",
        "startDate": "2010-09-01",
        "endDate": "2013-07-01",
        "score": "17/20",
    }
    assert resume["skills"] == [
        {"name": "Digital", "keywords": ["Python", "Go"]},
        {"name": "Cooking", "keywords": ["Bread"]},
    ]
    assert [one["language"] for one in resume["languages"]] == ["Portuguese", "French", "Basque"]
    assert "fluency" not in resume["languages"][2], "a level nobody stated says nothing"
    assert resume["projects"][0]["roles"] == ["Maintainer"]
    assert resume["awards"] == [{"title": "Best paper", "awarder": "SOSP"}]
    assert resume["certificates"][0]["issuer"] == "CNCF"


# ------------------------------------------------------------------- what is left out


def test_each_format_names_what_of_this_cv_it_has_no_place_for(cv):
    data = structured.cv_data(cv)

    assert structured.left_out(formats.get("europass"), data) == [
        "Summary",
        "Pronouns",
        "Place of birth",
        "The names of skill groups",
        "Grade",
        "Dates of projects",
        "Some driving licence details",
    ]
    assert structured.left_out(formats.get("json-resume"), data) == [
        "Pronouns",
        "Date of birth",
        "Place of birth",
        "Nationality",
        "Highlights of education",
        "EQF level",
        "Memberships",
        "Courses",
        "Links",
        "Driving licence",
    ]


def test_the_cvs_page_offers_both_and_says_what_each_leaves_out(client, person, cv):
    client.force_login(person)

    page = client.get(cv.get_absolute_url()).content.decode()

    for key in ("europass", "json-resume"):
        assert reverse("documents:cv_download", args=[cv.pk, key]) in page
        assert f'data-left-out-of="{key}"' in page
    assert "Europass" in page and "JSON Resume" in page
    assert "Left out, because this format has no place for them:" in page
    assert "The names of skill groups" in page and "Memberships" in page


def test_a_cv_with_nothing_to_leave_out_says_nothing(client, person):
    plain = CV.objects.create(owner=person, name="Plain")
    put_on(
        plain,
        Experience.objects.create(
            owner=person,
            organisation="Aperture",
            role="Engineer",
            start_date=datetime.date(2021, 1, 1),
        ),
    )
    client.force_login(person)

    page = client.get(plain.get_absolute_url()).content.decode()

    assert "data-left-out-of=" not in page


# ---------------------------------------------------------------------- the download


@pytest.mark.parametrize("key", ["europass", "json-resume"])
def test_each_downloads_as_a_json_file_and_files_nothing(client, person, cv, key):
    client.force_login(person)

    response = client.post(reverse("documents:cv_download", args=[cv.pk, key]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert ".json" in response["Content-Disposition"]
    assert json.loads(b"".join(response)) == (
        europass_of(cv) if key == "europass" else json_resume_of(cv)
    )
    assert not cv.renders.exists()


@pytest.mark.parametrize(
    ("key", "module"), [("europass", "format-europass"), ("json-resume", "format-json-resume")]
)
def test_each_follows_its_switch(client, person, cv, key, module):
    client.force_login(person)
    url = reverse("documents:cv_download", args=[cv.pk, key])
    PluginPolicy.objects.create(plugin=module, state=PluginPolicy.State.FORCED_OFF)

    assert client.get(url).status_code == 404
    assert (
        reverse("documents:cv_download", args=[cv.pk, key])
        not in client.get(cv.get_absolute_url()).content.decode()
    )


def test_somebody_elses_cv_is_not_found(client, other_user, cv):
    client.force_login(other_user)

    assert client.get(reverse("documents:cv_download", args=[cv.pk, "europass"])).status_code == 404


def test_a_letter_is_offered_neither(client, person):
    letter = CoverLetter.objects.create(owner=person, name="Letter", body="Dear reader,")
    client.force_login(person)

    page = client.get(letter.get_absolute_url()).content.decode()

    for key in ("europass", "json-resume"):
        url = reverse("documents:letter_download", args=[letter.pk, key])
        assert url not in page
        assert client.get(url).status_code == 404
    assert reverse("documents:letter_download", args=[letter.pk, "docx"]) in page
