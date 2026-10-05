"""One person's own record as a file: taking it out, and putting one back (#181).

The archive is all or nothing, and nothing read it back through the interface. This is the
part of an account that is the person rather than the job search -- their details and their
career -- as one JSON file, and the page that reads one and adds what is new.

Four promises, and each has tests of its own below:

- **the same shapes** the archive writes, so there is one spelling of a career;
- **a review before anything is written**, saying what each part of the file would do;
- **a merge that never replaces**, so the same file twice is the same as once;
- **telling rather than refusing**, so an entry that cannot be added costs that entry.

What the file may not do -- name somebody else's record, outgrow its bound, carry a script
in an address -- is `tests/security/test_candidate_file.py`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import zipfile
from io import BytesIO

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from postulo.accounts import identifiers
from postulo.accounts.models import PersonIdentifier
from postulo.core import export, importer
from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.jobs.models import Company, Contact
from postulo.resume import candidate
from postulo.resume.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Link,
    Project,
    Skill,
    SkillGroup,
    Translation,
)

pytestmark = pytest.mark.django_db

PAGE = "resume:candidate_file"
DOWNLOAD = "resume:candidate_download"

CAREER = (
    Experience,
    Education,
    Project,
    Link,
    SkillGroup,
    Skill,
    Certification,
    LanguageSkill,
    Translation,
)


# ------------------------------------------------------------------------ what is used


def translate(entry, language: str, **texts) -> None:
    for name, text in texts.items():
        Translation.objects.create(
            owner=entry.owner,
            content_type=ContentType.objects.get_for_model(type(entry)),
            object_id=entry.pk,
            language=language,
            field=name,
            text=text,
        )


@pytest.fixture
def somebody(user):
    """One person with something of every kind the file carries."""
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    profile = user.profile
    profile.headline = "Backend engineer"
    profile.location = "Lisboa, Portugal"
    profile.record_language = "en-GB"
    # What the file must not carry: how Postulo behaves for this account.
    profile.theme = "dark"
    profile.time_zone = "Europe/Lisbon"
    profile.save()

    PhoneNumber.objects.create(
        owner=user, holder=profile, number="+351912345678", kind="mobile", is_primary=True
    )
    PhoneNumber.objects.create(owner=user, holder=profile, number="+351213456789", kind="home")
    PostalAddress.objects.create(
        owner=user,
        holder=profile,
        kind="home",
        street="Rua do Exemplo 1",
        postcode="1000-001",
        municipality="Lisboa",
        country="PT",
        is_primary=True,
    )
    WebLink.objects.create(
        owner=user,
        holder=profile,
        kind="social",
        label="LinkedIn",
        url="https://www.linkedin.com/in/alexmorgan",
        is_primary=True,
    )
    WebLink.objects.create(
        owner=user, holder=profile, kind="website", url="https://alex.example.org", is_primary=True
    )
    PersonIdentifier.objects.create(
        profile=profile, scheme=identifiers.ORCID, value="0000-0002-1825-0097"
    )

    current = Experience.objects.create(
        owner=user,
        organisation="Aperture Science",
        role="Senior Engineer",
        location="Lisbon",
        start_date=dt.date(2023, 7, 1),
        summary="Kept the services up.",
        highlights="Cut deploy time.\nMentored three engineers.",
        order=0,
    )
    Experience.objects.create(
        owner=user,
        organisation="Black Mesa",
        role="Engineer",
        start_date=dt.date(2019, 3, 1),
        end_date=dt.date(2023, 6, 30),
        order=1,
    )
    Education.objects.create(
        owner=user,
        institution="Universidade de Lisboa",
        qualification="MSc Computer Science",
        start_date=dt.date(2014, 9, 1),
        end_date=dt.date(2016, 7, 15),
    )
    Project.objects.create(
        owner=user, name="Postulo", role="Maintainer", url="https://example.org/postulo"
    )
    Link.objects.create(
        owner=user, title="Portfolio", url="https://alex.example.org/work", kind="portfolio"
    )
    group = SkillGroup.objects.create(owner=user, name="Languages", order=0)
    Skill.objects.create(owner=user, group=group, name="Python", order=0)
    skill = Skill.objects.create(owner=user, group=group, name="Go", order=1)
    Certification.objects.create(
        owner=user, name="Certified Kubernetes Administrator", issuer="CNCF"
    )
    LanguageSkill.objects.create(owner=user, name="português", proficiency="native")

    translate(current, "fr-FR", role="Ingénieur principal", summary="A maintenu les services.")
    translate(skill, "fr-FR", name="Go (langage)")
    return user


def the_file(person) -> bytes:
    return json.dumps(export.build_candidate_document(person)).encode()


def carried_elsewhere(person) -> bytes:
    """The person's file, on an instance that does not hold their telephone numbers.

    A number is unique across an instance (#90), so a file read back where it was written,
    into another account, cannot land its numbers: their owner still has them. Moving is
    between instances, and taking the numbers away here stands for the other one. What a
    number somebody else holds is told is `tests/security/test_candidate_file.py`.
    """
    data = the_file(person)
    person.profile.phone_numbers.all().delete()
    return data


def a_file(account=None, resume=None, **header) -> bytes:
    """A file written by hand, which is what a file from anywhere else is."""
    document: dict = {
        "postulo": {
            "candidate_format": export.CANDIDATE_FORMAT,
            "version": "0.5.0",
            "exported_at": "2026-09-28T10:00:00+00:00",
            **header,
        }
    }
    if account is not None:
        document["account"] = account
    if resume is not None:
        document["resume"] = resume
    return json.dumps(document).encode()


def a_role(**changes) -> dict:
    return {
        "id": 7,
        "organisation": "Initech",
        "role": "Developer",
        "location": "",
        "start_date": "2017-01-09",
        "end_date": "2019-02-28",
        "summary": "",
        "highlights": "",
        "order": 0,
        **changes,
    }


def drawn(person, data: bytes) -> candidate.Plan:
    return candidate.plan(person, candidate.read(data))


def add(person, data: bytes) -> candidate.Report:
    return candidate.apply(person, candidate.read(data))


def rows(plan: candidate.Plan, key: str) -> list[candidate.Row]:
    return [row for section in plan.sections if section.key == key for row in section.rows]


def outcomes(plan: candidate.Plan, key: str) -> list[str]:
    return [row.outcome for row in rows(plan, key)]


def held_by(person) -> dict[str, int]:
    """How many of everything the file carries this account holds."""
    found = {model.__name__: model.objects.for_user(person).count() for model in CAREER}
    found["numbers"] = person.profile.phone_numbers.count()
    found["addresses"] = person.profile.postal_addresses.count()
    found["links"] = person.profile.web_links.count()
    found["identifiers"] = person.profile.identifiers.count()
    return found


# ------------------------------------------------------------------------ the document


def test_the_file_is_the_person_and_nothing_about_the_job_search(somebody):
    Company.objects.create(owner=somebody, name="Umbrella Corporation")

    document = export.build_candidate_document(somebody)

    assert set(document) == {"postulo", "account", "resume"}
    assert "Umbrella" not in json.dumps(document)
    assert document["account"]["first_name"] == "Alex"
    assert document["account"]["profile"]["headline"] == "Backend engineer"
    assert document["resume"]["experience"][0]["role"] == "Senior Engineer"


def test_it_says_which_document_it_is_under_a_key_of_its_own(somebody):
    """The marker is the key: what reads an archive looks for `format` and finds none."""
    header = export.build_candidate_document(somebody)["postulo"]

    assert header["candidate_format"] == export.CANDIDATE_FORMAT
    assert "format" not in header
    assert header["version"] and header["exported_at"]
    assert "picture" in header["note"], "and the file itself says what it left out"


def test_every_block_is_the_block_the_archive_writes(somebody):
    """One spelling of a career, not two to keep in step.

    If this fails, the candidate document has grown a copy of a builder, and the two are
    free to drift apart -- which is what the issue asked not to happen.
    """
    whole = export.build_document(somebody)
    own = export.build_candidate_document(somebody)

    assert own["resume"] == whole["resume"]
    assert own["account"]["identifiers"] == whole["account"]["identifiers"]
    for name, value in own["account"]["profile"].items():
        assert value == whole["account"]["profile"][name], name
    for name in ("phone_numbers", "postal_addresses", "web_links"):
        assert own["account"]["profile"][name], f"{name} is there, and has something in it"
    assert len(own["resume"]["translations"]) == 3


def test_what_belongs_to_the_account_stays_with_the_account(somebody):
    """The picture, because JSON cannot carry one. The address and the username, because
    they are how somebody signs in here. The rest, because it is how Postulo behaves."""
    document = export.build_candidate_document(somebody)

    assert set(document["account"]) == {"first_name", "last_name", "profile", "identifiers"}
    assert set(document["account"]["profile"]) == {
        "form_of_address",
        "pronouns",
        "birth_date",
        "birth_place",
        "birth_country",
        "headline",
        "location",
        "record_language",
        "phone_numbers",
        "postal_addresses",
        "web_links",
    }
    written = json.dumps(document)
    assert somebody.email not in written and "avatar" not in written
    assert "Europe/Lisbon" not in written


def test_the_archive_is_written_as_it_was(somebody):
    """The builders moved; what they build did not, block for block.

    The format's number is pinned where a change to it is written down
    (`test_phone_verification.py::test_the_format_version_moved`); what this holds is that
    moving the builders out of `build_document` left every block where it was. `contacts`
    arrived with #239, after the move, `remembered_places` with #267 and `reminders` with #334.
    """
    document = export.build_document(somebody)

    assert document["postulo"]["format"] == export.FORMAT_VERSION
    assert list(document) == [
        "postulo",
        "plugins",
        "account",
        "tags",
        "resume",
        "contacts",
        "reminders",
        "companies",
        "documents",
        "captures",
        "remembered_places",
        "counts",
    ]
    assert list(document["account"]) == [
        "username",
        "email",
        "first_name",
        "last_name",
        "profile",
        "identifiers",
        "avatar_file",
    ]
    assert list(document["account"]["profile"]) == [
        *export.PROFILE_FIELDS,
        "phone_numbers",
        "postal_addresses",
        "web_links",
    ]
    assert list(document["resume"]) == [*export.RESUME_FIELDS, "translations"]
    for block, names in export.RESUME_FIELDS.items():
        for entry in document["resume"][block]:
            assert list(entry) == list(names), block


def fingerprint() -> str:
    """The shape of everything the candidate document carries, as one short string."""
    shape = {
        "profile": export.CANDIDATE_PROFILE_FIELDS,
        "phone_numbers": export.PHONE_NUMBER_FIELDS,
        "postal_addresses": export.POSTAL_ADDRESS_FIELDS,
        "web_links": export.WEB_LINK_FIELDS,
        "resume": export.RESUME_FIELDS,
        "translations": sorted(export.TRANSLATION_SECTIONS.items()),
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


#: What each version of the candidate document looked like. A new shape is a new line.
#: 2 added the ESCO skill a skill's name matches, written and never read back (#266). 3
#: added the form of address and the pronouns beside the name (#309). 4 added the service
#: a web link is on, read back where the importing side knows it and worked out from the
#: address in a file that does not say (#305). 5 added the date and place of birth (#679).
SHAPES = {
    1: "0941165cc7c21c64",
    2: "fe525ea84b2b6f93",
    3: "c1fc71fec061fadc",
    4: "bcb06c758a353ad3",
    5: "041a451ff678f708",
}


def test_a_block_that_changes_shape_is_a_new_version_of_the_file():
    """The candidate document borrows the archive's blocks, so a field added to one of them
    for the archive's sake changes this file too -- and its reader has to be told.

    Fails when a block changes and nobody has decided. Bump `CANDIDATE_FORMAT`, teach
    `resume.candidate` to read the older shape as well, and record the new one here.
    """
    assert SHAPES.get(export.CANDIDATE_FORMAT) == fingerprint(), (
        f"the candidate document's blocks changed shape (now {fingerprint()}) and "
        f"CANDIDATE_FORMAT is still {export.CANDIDATE_FORMAT}"
    )


def test_what_reads_the_file_knows_every_field_that_is_written_to_it():
    """The writer's list and the reader's, held together: a field added to a block is read
    back, or is written down in `NOT_READ` with the reason it is not."""
    assert set(candidate.KINDS_BY_BLOCK) == set(export.RESUME_FIELDS)
    for block, written in export.RESUME_FIELDS.items():
        kind = candidate.KINDS_BY_BLOCK[block]
        left = set(written) - set(kind.held)
        assert left == set(candidate.NOT_READ.get(block, ())), block
        assert set(kind.held) <= set(written), f"{block} reads what nothing writes"
        # And everything read is something the entry's own form takes, which is what
        # validates it: nothing reaches a column by another road.
        assert set(kind.fields) <= set(kind.form._meta.fields), block
        assert set(kind.names) | set(kind.dates) <= set(kind.fields), block

    wrote = {
        "phone_numbers": export.PHONE_NUMBER_FIELDS,
        "postal_addresses": export.POSTAL_ADDRESS_FIELDS,
        "web_links": export.WEB_LINK_FIELDS,
    }
    assert set(wrote) == set(candidate.CONTACT_BLOCKS)
    for block, written in wrote.items():
        # That a number was confirmed, and that it is a way back in, and nothing else.
        unsaid = set(candidate.NOT_THE_FILES_TO_SAY) if block == "phone_numbers" else set()
        assert set(written) - set(candidate.CONTACT_BLOCKS[block]) == unsaid, block
    assert set(export.TRANSLATION_SECTIONS.values()) <= set(candidate.KINDS_BY_BLOCK)
    assert set(candidate.DETAIL_FIELDS) == {
        "first_name",
        "last_name",
        *export.CANDIDATE_PROFILE_FIELDS,
    }


def test_what_reads_an_archive_does_not_take_this_for_one(somebody):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, the_file(somebody))
    buffer.seek(0)

    with pytest.raises(importer.ArchiveError, match="does not look like a Postulo export"):
        importer.read_manifest(zipfile.ZipFile(buffer))


def test_the_page_counts_what_the_file_would_carry(somebody):
    """Counted, as the archive's page counts (#220), and the two answers are one answer."""
    document = export.build_candidate_document(somebody)

    found = export.candidate_counts(somebody)

    for block in (*export.RESUME_FIELDS, "translations"):
        assert found[block] == len(document["resume"][block]), block
    for block in ("phone_numbers", "postal_addresses", "web_links"):
        assert found[block] == len(document["account"]["profile"][block]), block
    assert found["identifiers"] == len(document["account"]["identifiers"])


def test_a_translation_somebody_cleared_is_neither_written_nor_counted(somebody):
    Translation.objects.for_user(somebody).filter(field="summary").update(text="  ")

    document = export.build_candidate_document(somebody)

    assert len(document["resume"]["translations"]) == 2
    assert export.candidate_counts(somebody)["translations"] == 2


def test_nothing_of_anybody_elses_is_in_it(somebody, other_user):
    Experience.objects.create(
        owner=other_user,
        organisation="Somebody else's employer",
        role="Theirs",
        start_date=dt.date(2020, 1, 1),
    )
    PhoneNumber.objects.create(
        owner=other_user, holder=other_user.profile, number="+33612345678", is_primary=True
    )

    written = the_file(somebody).decode()

    assert "Somebody else" not in written and "+33612345678" not in written


# ---------------------------------------------------------------- what is not one of these


@pytest.mark.parametrize(
    "data",
    [
        b"Name,Role\nAlex,Engineer\n",
        b'{"postulo": ',
        b"[1, 2, 3]",
        b'{"hello": "world"}',
        b'{"postulo": "yes"}',
        b'{"postulo": {"candidate_format": "one"}}',
        b'{"postulo": {"candidate_format": true}}',
        b'{"postulo": {"candidate_format": 0}}',
        b'{"postulo": {"candidate_format": 1, "weight": NaN}}',
        "￾ not text at all".encode("utf-16"),
        ("[" * 100_000).encode(),
    ],
    ids=[
        "a spreadsheet",
        "half a file",
        "a list",
        "no header",
        "a header that is a word",
        "a version that is a word",
        "a version that is a yes",
        "a version before the first",
        "a number that is not one",
        "another encoding",
        "nothing but depth",
    ],
)
def test_a_file_that_is_not_one_of_these_is_refused_and_told_what_is(data):
    with pytest.raises(candidate.Refused) as refused:
        candidate.read(data)

    assert "one person's own record" in str(refused.value), "it names what is read here"


def test_an_archive_is_refused_in_words_of_its_own(somebody):
    """The likeliest wrong file there is, so it is told what it is as well as what is not."""
    whole = json.dumps(export.build_document(somebody), default=str).encode()

    with pytest.raises(candidate.Refused, match="the export of a whole account"):
        candidate.read(whole)


def test_an_empty_file_says_so():
    with pytest.raises(candidate.Refused, match="empty"):
        candidate.read(b"")


def test_a_header_and_nothing_else_is_a_file_with_nothing_in_it():
    held = candidate.read(a_file())

    assert candidate.is_empty(held)
    assert not candidate.is_empty(candidate.read(a_file(account={"first_name": "Alex"})))


# ------------------------------------------------------------------------ the round trip


def test_everything_arrives_in_an_account_that_had_nothing(somebody, other_user):
    left = held_by(somebody)
    data = carried_elsewhere(somebody)
    promised = drawn(other_user, data)

    report = add(other_user, data)

    assert held_by(other_user) == left
    assert {row.outcome for row in promised.rows()} == {candidate.ADD}
    assert report.total == promised.adds, "what the page said would be added is what was"
    other_user.refresh_from_db()
    profile = other_user.profile
    profile.refresh_from_db()
    assert (other_user.first_name, other_user.last_name) == ("Alex", "Morgan")
    assert profile.headline == "Backend engineer"
    assert profile.location == "Lisboa, Portugal"
    assert profile.record_language == "en-GB"


def test_what_arrives_is_what_left(somebody, other_user):
    """Written out again from where it landed, the file says what it said."""

    def content(person) -> dict:
        document = export.build_candidate_document(person)

        def without_ids(value):
            if isinstance(value, list):
                return [without_ids(item) for item in value]
            if isinstance(value, dict):
                return {
                    name: without_ids(item)
                    for name, item in value.items()
                    if name not in ("id", "group_id", "ref")
                }
            return value

        # The header says when, and the ids are each file's own.
        document.pop("postulo")
        return without_ids(document)

    left = content(somebody)

    add(other_user, carried_elsewhere(somebody))

    assert content(other_user) == left


def test_a_skill_still_belongs_to_its_group(somebody, other_user):
    add(other_user, the_file(somebody))

    group = SkillGroup.objects.for_user(other_user).get()

    assert group.name == "Languages"
    assert [skill.name for skill in group.skills.all()] == ["Python", "Go"]
    assert group.pk != SkillGroup.objects.for_user(somebody).get().pk


def test_a_translation_still_belongs_to_its_entry(somebody, other_user):
    add(other_user, the_file(somebody))

    role = Experience.objects.for_user(other_user).get(organisation="Aperture Science")
    skill = Skill.objects.for_user(other_user).get(name="Go")

    assert {row.field: row.text for row in role.translations.all()} == {
        "role": "Ingénieur principal",
        "summary": "A maintenu les services.",
    }
    assert skill.translations.get().text == "Go (langage)"
    assert all(row.owner == other_user for row in role.translations.all())


def test_the_primary_of_each_kind_is_still_the_primary(somebody, other_user):
    add(other_user, carried_elsewhere(somebody))
    profile = other_user.profile

    assert {row.number: row.is_primary for row in profile.phone_numbers.all()} == {
        "+351912345678": True,
        "+351213456789": False,
    }
    assert profile.postal_addresses.get().is_primary
    assert {row.kind: row.is_primary for row in profile.web_links.all()} == {
        "social": True,
        "website": True,
    }


def test_nothing_about_a_number_is_carried_but_the_number(somebody, other_user):
    """That a number was confirmed, and that it gets somebody back into their account, are
    facts about the instance that checked. The file says them; this one did not check."""
    number = somebody.profile.phone_numbers.get(is_primary=True)
    number.record_verified()
    PhoneNumber.objects.filter(pk=number.pk).update(is_recovery=True)
    written = json.loads(the_file(somebody))
    assert written["account"]["profile"]["phone_numbers"][0]["verified_at"]
    assert written["account"]["profile"]["phone_numbers"][0]["is_recovery"] is True

    add(other_user, carried_elsewhere(somebody))

    for row in other_user.profile.phone_numbers.all():
        assert row.verified_at is None and not row.is_recovery


# ------------------------------------------------------------- it merges and never replaces


def test_the_same_file_twice_adds_nothing_the_second_time(somebody, other_user):
    data = the_file(somebody)
    add(other_user, data)
    after_the_first = held_by(other_user)

    again = drawn(other_user, data)
    report = add(other_user, data)

    assert again.adds == 0
    assert report.total == 0
    assert held_by(other_user) == after_the_first


def test_a_persons_own_file_adds_nothing_to_their_own_account(somebody):
    before = held_by(somebody)

    plan = drawn(somebody, the_file(somebody))
    add(somebody, the_file(somebody))

    assert {row.outcome for row in plan.rows()} == {candidate.PRESENT}
    assert held_by(somebody) == before


def test_what_is_new_is_added_beside_what_was_there(somebody):
    data = a_file(
        resume={
            "experience": [
                a_role(),
                {
                    "id": 8,
                    "organisation": "Aperture Science",
                    "role": "Senior Engineer",
                    "start_date": "2023-07-01",
                    "end_date": None,
                    "summary": "Something else entirely.",
                },
            ]
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "experience") == [candidate.ADD, candidate.PRESENT]
    assert Experience.objects.for_user(somebody).count() == 3
    assert Experience.objects.for_user(somebody).filter(organisation="Initech").exists()


def test_what_was_there_is_left_exactly_as_it_was(somebody):
    """The file says something else about a role the account already holds. Yours stays,
    and the page says that the two differ rather than letting it pass in silence."""
    data = a_file(
        resume={
            "experience": [
                {
                    "organisation": "Aperture Science",
                    "role": "Senior Engineer",
                    "start_date": "2023-07-01",
                    "summary": "Something else entirely.",
                    "highlights": "",
                }
            ]
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    (row,) = rows(plan, "experience")
    assert row.outcome == candidate.PRESENT
    assert any("words it differently" in note for note in row.notes)
    role = Experience.objects.for_user(somebody).get(organisation="Aperture Science")
    assert role.summary == "Kept the services up."
    assert role.highlights.startswith("Cut deploy time.")


def test_a_role_is_its_organisation_its_title_and_its_dates(somebody):
    """The three together. Any one of them different is another entry."""
    same = {
        "organisation": "Black Mesa",
        "role": "Engineer",
        "start_date": "2019-03-01",
        "end_date": "2023-06-30",
    }
    data = a_file(
        resume={
            "experience": [
                same,
                {**same, "organisation": "Black Mesa Research"},
                {**same, "role": "Lead Engineer"},
                {**same, "start_date": "2019-04-01"},
                {**same, "end_date": None},
            ]
        }
    )

    plan = drawn(somebody, data)

    assert outcomes(plan, "experience") == [
        candidate.PRESENT,
        candidate.ADD,
        candidate.ADD,
        candidate.ADD,
        candidate.ADD,
    ]


def test_the_same_post_with_other_dates_is_added_and_said_to_be_alike(somebody):
    """Told, not refused (#178): it may be the same job with a date corrected, and it may
    be a second stint. The person knows which, and is told so that they can say."""
    data = a_file(
        resume={
            "experience": [
                {
                    "organisation": "Black Mesa",
                    "role": "Engineer",
                    "start_date": "2019-03-01",
                    "end_date": "2024-01-31",
                }
            ]
        }
    )

    (row,) = rows(drawn(somebody, data), "experience")

    assert row.outcome == candidate.ADD
    assert any("with other dates" in note for note in row.notes)


def test_how_it_was_typed_does_not_make_it_another_entry(somebody):
    """Case, spacing and the way a letter is composed vary in what people type."""
    data = a_file(
        resume={
            "experience": [
                {
                    "organisation": "  aperture   SCIENCE ",
                    "role": "senior engineer",
                    "start_date": "2023-07-01",
                }
            ],
            "languages": [{"name": "português".upper(), "proficiency": "native"}],
            "links": [
                {
                    "title": "My work",
                    "url": "http://www.alex.example.org/work/",
                    "kind": "site",
                }
            ],
        }
    )

    plan = drawn(somebody, data)

    assert outcomes(plan, "experience") == [candidate.PRESENT]
    assert outcomes(plan, "languages") == [candidate.PRESENT]
    assert outcomes(plan, "links") == [candidate.PRESENT], "one page, written two ways"


@pytest.mark.parametrize(
    "block,entry,alike",
    [
        (
            "education",
            {
                "institution": "Universidade de Lisboa",
                "qualification": "MSc Computer Science",
                "start_date": "2014-09-01",
                "end_date": "2016-07-15",
            },
            {"end_date": "2017-07-15"},
        ),
        ("projects", {"name": "Postulo"}, {"start_date": "2024-01-01"}),
        (
            "certifications",
            {"name": "Certified Kubernetes Administrator", "issuer": "CNCF"},
            {"issued_on": "2022-05-01"},
        ),
    ],
)
def test_every_dated_kind_is_its_names_and_its_dates(somebody, block, entry, alike):
    data = a_file(resume={block: [entry, {**entry, **alike}]})

    first, second = rows(drawn(somebody, data), block)

    assert first.outcome == candidate.PRESENT
    assert second.outcome == candidate.ADD
    assert any("with other dates" in note for note in second.notes)


def test_a_skill_is_its_name_under_its_group(somebody):
    """*Python* under *Languages* and *Python* under *Tools* are two skills."""
    data = a_file(
        resume={
            "skill_groups": [{"id": 1, "name": "languages"}, {"id": 2, "name": "Tools"}],
            "skills": [
                {"id": 1, "name": "python", "group_id": 1},
                {"id": 2, "name": "Python", "group_id": 2},
                {"id": 3, "name": "Rust", "group_id": 1},
            ],
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "skill_groups") == [candidate.PRESENT, candidate.ADD]
    assert outcomes(plan, "skills") == [candidate.PRESENT, candidate.ADD, candidate.ADD]
    languages = SkillGroup.objects.for_user(somebody).get(name="Languages")
    assert [skill.name for skill in languages.skills.all()] == ["Python", "Go", "Rust"]
    assert SkillGroup.objects.for_user(somebody).get(name="Tools").skill_names == ["Python"]


def test_an_entry_the_file_has_twice_is_added_once(user):
    data = a_file(resume={"experience": [a_role(id=1), a_role(id=2, summary="Again.")]})

    plan = drawn(user, data)
    add(user, data)

    assert outcomes(plan, "experience") == [candidate.ADD, candidate.REPEATED]
    assert Experience.objects.for_user(user).count() == 1


# ------------------------------------------------------------------- the ids in the file


def test_an_id_in_the_file_is_never_an_id_in_the_database(somebody):
    """The file names a role by the number the account's own role has. It is another role,
    and the account's own is not the one a translation beside it is given to."""
    mine = Experience.objects.for_user(somebody).get(organisation="Black Mesa")
    data = a_file(
        resume={
            "experience": [a_role(id=mine.pk)],
            "translations": [
                {
                    "section": "experience",
                    "ref": mine.pk,
                    "language": "fr-FR",
                    "field": "role",
                    "text": "Développeur",
                }
            ],
        }
    )

    add(somebody, data)

    mine.refresh_from_db()
    assert mine.role == "Engineer" and mine.organisation == "Black Mesa"
    assert not mine.translations.exists()
    added = Experience.objects.for_user(somebody).get(organisation="Initech")
    assert added.pk != mine.pk
    assert added.translations.get().text == "Développeur"


def test_an_id_that_names_two_entries_names_neither(user):
    data = a_file(
        resume={
            "experience": [a_role(id=1), a_role(id=1, organisation="Initrode")],
            "translations": [
                {
                    "section": "experience",
                    "ref": 1,
                    "language": "fr-FR",
                    "field": "role",
                    "text": "Développeur",
                }
            ],
        }
    )

    plan = drawn(user, data)
    add(user, data)

    assert outcomes(plan, "experience") == [candidate.ADD, candidate.ADD]
    assert outcomes(plan, "translations") == [candidate.REFUSED]
    assert not Translation.objects.for_user(user).exists()


# ------------------------------------------------------------------ where an entry lands


def test_into_an_empty_section_the_order_is_the_files(user):
    """Their owner put them in that order somewhere else, and it is theirs (#203)."""
    data = a_file(
        resume={
            "experience": [
                a_role(id=1, organisation="Third", order=2, start_date="2018-01-01"),
                a_role(id=2, organisation="First", order=0, start_date="2010-01-01"),
                a_role(id=3, organisation="Second", order=1, start_date="2016-01-01"),
            ]
        }
    )

    add(user, data)

    found = Experience.objects.for_user(user)
    assert [role.organisation for role in found] == ["First", "Second", "Third"]
    assert [role.order for role in found] == [0, 1, 2]


def test_into_a_section_with_entries_a_dated_one_lands_by_its_date(somebody):
    """As one typed by hand would: adding the latest job still puts it first."""
    data = a_file(
        resume={
            "experience": [
                a_role(id=1, organisation="The oldest", start_date="2001-01-01", end_date=None),
                a_role(id=2, organisation="Between", start_date="2021-01-01", end_date=None),
                a_role(id=3, organisation="The newest", start_date="2025-01-01", end_date=None),
            ]
        }
    )

    add(somebody, data)

    found = Experience.objects.for_user(somebody)
    assert [role.organisation for role in found] == [
        "The newest",
        "Aperture Science",
        "Between",
        "Black Mesa",
        "The oldest",
    ]
    assert [role.order for role in found] == [0, 1, 2, 3, 4]


def test_into_a_section_with_no_dates_a_new_one_goes_last(somebody):
    data = a_file(resume={"languages": [{"name": "English", "proficiency": "c1", "order": 0}]})

    add(somebody, data)

    assert [row.name for row in LanguageSkill.objects.for_user(somebody)] == [
        "português",
        "English",
    ]


# ------------------------------------------------------------------------- your details


def test_a_blank_is_filled_and_an_answer_is_kept(user):
    """A blank is not an opinion. What somebody said about themselves is."""
    user.first_name, user.last_name = "Alexandra", ""
    user.save()
    profile = user.profile
    profile.headline = "Staff engineer, mostly Python"
    profile.save()
    data = a_file(
        account={
            "first_name": "Alex",
            "last_name": "Morgan",
            "profile": {
                "headline": "Backend engineer",
                "location": "Lisboa, Portugal",
                "record_language": "pt_PT",
            },
        }
    )

    plan = drawn(user, data)
    add(user, data)

    assert outcomes(plan, "details") == [
        candidate.KEPT,
        candidate.ADD,
        candidate.KEPT,
        candidate.ADD,
        candidate.ADD,
    ]
    user.refresh_from_db()
    profile.refresh_from_db()
    assert (user.first_name, user.last_name) == ("Alexandra", "Morgan")
    assert profile.headline == "Staff engineer, mostly Python"
    assert profile.location == "Lisboa, Portugal"
    assert profile.record_language == "pt-PT", "written the way Postulo writes a language"


def test_a_detail_that_says_what_yours_says_is_already_there(somebody):
    data = a_file(account={"first_name": "ALEX", "profile": {"headline": "backend  engineer"}})

    assert outcomes(drawn(somebody, data), "details") == [candidate.PRESENT, candidate.PRESENT]


# ------------------------------------------------------ numbers, addresses, links, identifiers


def test_a_number_the_account_has_is_the_same_number_however_it_is_written(somebody):
    data = a_file(
        account={
            "profile": {
                "phone_numbers": [
                    {"kind": "mobile", "number": "+351 912 345 678", "is_primary": True},
                    {"kind": "work", "number": "00351 217 654 321", "is_primary": True},
                ]
            }
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "phone_numbers") == [candidate.PRESENT, candidate.ADD]
    numbers = {row.number: row for row in somebody.profile.phone_numbers.all()}
    assert set(numbers) == {"+351912345678", "+351213456789", "+351217654321"}
    assert numbers["+351912345678"].is_primary, "which number a CV prints is not the file's"
    assert not numbers["+351217654321"].is_primary


def test_the_first_number_an_account_gets_is_its_primary(user):
    data = a_file(
        account={
            "profile": {
                "phone_numbers": [
                    {"kind": "home", "number": "+351213456700"},
                    {"kind": "mobile", "number": "+351912345600", "is_primary": True},
                ]
            }
        }
    )

    add(user, data)

    assert user.profile.phone_numbers.get(is_primary=True).number == "+351912345600"

    # And where the file names none, one is chosen: a holder with numbers and no primary
    # shows none at all once *Several telephone numbers* is switched off.
    other = a_file(account={"profile": {"postal_addresses": [{"street": "1 Example Street"}]}})
    add(user, other)
    assert user.profile.postal_addresses.get().is_primary


def test_an_address_is_the_same_address_however_it_is_typed(somebody):
    data = a_file(
        account={
            "profile": {
                "postal_addresses": [
                    {
                        "kind": "home",
                        "street": "rua do exemplo  1",
                        "postcode": "1000-001",
                        "municipality": "LISBOA",
                        "country": "pt",
                    },
                    {"kind": "work", "street": "Avenida da Liberdade 100", "country": "PT"},
                    {"kind": "other", "label": "", "street": "Somewhere"},
                    {"kind": "home"},
                ]
            }
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "postal_addresses") == [
        candidate.PRESENT,
        candidate.ADD,
        candidate.REFUSED,
    ], "and one with nothing in it is not mentioned at all"
    assert somebody.profile.postal_addresses.count() == 2


def test_an_address_listed_for_a_contact_cannot_be_listed_again(somebody):
    """One address once per account, which the database holds to (#92). Said, not raised."""
    company = Company.objects.create(owner=somebody, name="Aperture Science")
    contact = Contact.objects.create(owner=somebody, company=company, name="Cave Johnson")
    PostalAddress.objects.create(owner=somebody, holder=contact, street="1 Enrichment Way")
    data = a_file(account={"profile": {"postal_addresses": [{"street": "1 enrichment way"}]}})

    plan = drawn(somebody, data)
    add(somebody, data)

    (row,) = rows(plan, "postal_addresses")
    assert row.outcome == candidate.REFUSED
    assert "somebody you deal with" in row.notes[0]
    assert somebody.profile.postal_addresses.count() == 1


def test_links_are_sorted_by_what_they_are(somebody):
    data = a_file(
        account={
            "profile": {
                "web_links": [
                    {"kind": "social", "url": "http://linkedin.com/in/alexmorgan/"},
                    {"kind": "repository", "label": "Codeberg", "url": "https://codeberg.org/alex"},
                    {"kind": "blog", "url": "https://blog.example.org"},
                ]
            }
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "web_links") == [candidate.PRESENT, candidate.ADD, candidate.REFUSED]
    assert [str(section.title) for section in plan.sections] == [
        "Social profiles",
        "Code repositories",
        "Web links",
    ]
    added = somebody.profile.web_links.get(kind="repository")
    # The address says which service it is on (#305), and a name that said only that is
    # the service's to say now: the row is Codeberg's, and is still shown as Codeberg.
    assert (added.service, added.label) == ("codeberg", "")
    assert added.display == "Codeberg alex"
    assert added.is_primary, "the first of its kind, so the one to show"


def test_an_identifier_somebody_has_is_theirs(somebody):
    """One of each kind per person, and the one they have is the one they keep.

    The third is an ORCID whose check digit is wrong. It used to be refused; a value its
    kind does not accept here is added as *Other*, named by the kind, as an archive's is
    (#311): a file is not refused an identifier, and the row says what was done with it.
    """
    data = a_file(
        account={
            "identifiers": [
                {"scheme": "orcid", "value": "https://orcid.org/0000-0002-1825-0097"},
                {"scheme": "orcid", "value": "0000-0001-5109-3700"},
                {"scheme": "orcid", "value": "0000-0002-1825-0098"},
                {"scheme": "lei", "value": "5493001KJTIIGC8Y1R12"},
                {"scheme": "wikidata", "value": "Q42"},
                {"scheme": "", "value": ""},
            ]
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "identifiers") == [
        candidate.PRESENT,
        candidate.KEPT,
        candidate.ADD,
        candidate.REFUSED,
        candidate.ADD,
    ]
    assert rows(plan, "identifiers")[2].notes == [
        "This is not a value ORCID accepts here, so it is added as Other, named “orcid”."
    ]
    held = {(row.scheme, row.label): row.value for row in somebody.profile.identifiers.all()}
    assert held == {
        ("orcid", ""): "0000-0002-1825-0097",
        ("wikidata", ""): "Q42",
        ("other", "orcid"): "0000-0002-1825-0098",
    }


def test_a_file_cannot_give_somebody_two_of_a_kind_either(user):
    """The database would refuse the second, half way through. It is said on the page."""
    data = a_file(
        account={
            "identifiers": [
                {"scheme": "orcid", "value": "0000-0002-1825-0097"},
                {"scheme": "orcid", "value": "0000 0002 1825 0097"},
                {"scheme": "orcid", "value": "0000-0001-5109-3700"},
                {"scheme": "other", "value": "A-1", "label": "Staff number"},
                {"scheme": "other", "value": "B-2", "label": "Library card"},
                {"scheme": "other", "value": "C-3"},
            ]
        }
    )

    plan = drawn(user, data)
    add(user, data)

    assert outcomes(plan, "identifiers") == [
        candidate.ADD,
        candidate.REPEATED,
        candidate.REFUSED,
        candidate.ADD,
        candidate.ADD,
        candidate.REFUSED,
    ]
    assert "has another of this type" in rows(plan, "identifiers")[2].notes[0]
    held = sorted((row.scheme, row.value) for row in user.profile.identifiers.all())
    assert held == [("orcid", "0000-0002-1825-0097"), ("other", "A-1"), ("other", "B-2")]


# ------------------------------------------------------------------------- translations


def fr(section: str, ref: int, name: str, text: str, language: str = "fr-FR") -> dict:
    return {"section": section, "ref": ref, "language": language, "field": name, "text": text}


def test_an_entry_already_there_gains_what_it_did_not_say(somebody):
    """Adding, not replacing: the role was translated and keeps its translation; the place
    was not, and gets one."""
    role = {
        "id": 1,
        "organisation": "Aperture Science",
        "role": "Senior Engineer",
        "location": "Lisbon",
        "start_date": "2023-07-01",
        "summary": "Kept the services up.",
        "highlights": "Cut deploy time.\nMentored three engineers.",
    }
    data = a_file(
        resume={
            "experience": [role],
            "translations": [
                fr("experience", 1, "role", "Ingénieur principal"),
                fr("experience", 1, "summary", "Autre chose."),
                fr("experience", 1, "location", "Lisbonne"),
                fr("experience", 1, "location", "Lisboa", language="pt-PT"),
            ],
        }
    )

    plan = drawn(somebody, data)
    add(somebody, data)

    assert outcomes(plan, "translations") == [
        candidate.PRESENT,
        candidate.KEPT,
        candidate.ADD,
        candidate.ADD,
    ]
    entry = Experience.objects.for_user(somebody).get(organisation="Aperture Science")
    said = {(row.language, row.field): row.text for row in entry.translations.all()}
    assert said == {
        ("fr-FR", "role"): "Ingénieur principal",
        ("fr-FR", "summary"): "A maintenu les services.",
        ("fr-FR", "location"): "Lisbonne",
        ("pt-PT", "location"): "Lisboa",
    }


def test_a_translation_somebody_cleared_is_a_blank_and_a_blank_is_filled(somebody):
    entry = Experience.objects.for_user(somebody).get(organisation="Aperture Science")
    entry.translations.filter(field="role").update(text="")
    data = a_file(
        resume={
            "experience": [
                {
                    "id": 1,
                    "organisation": "Aperture Science",
                    "role": "Senior Engineer",
                    "start_date": "2023-07-01",
                }
            ],
            "translations": [fr("experience", 1, "role", "Ingénieure principale")],
        }
    )

    add(somebody, data)

    assert entry.translations.get(field="role").text == "Ingénieure principale"
    assert entry.translations.count() == 2, "the row that was there, not one beside it"


def test_one_row_on_the_page_for_an_entry_in_a_language(user):
    data = a_file(
        resume={
            "experience": [a_role(id=1)],
            "translations": [
                fr("experience", 1, "role", "Développeur"),
                fr("experience", 1, "summary", "Quelque chose."),
            ],
        }
    )

    (row,) = rows(drawn(user, data), "translations")

    assert row.label == "Developer"
    assert row.sub == "français (France): role, summary"


@pytest.mark.parametrize(
    "row,why",
    [
        (fr("experience", 9, "role", "x"), "not in the file"),
        (fr("experience", 2, "role", "x"), "cannot be added"),
        (fr("experience", 1, "organisation", "x"), "Postulo translates"),
        (fr("certifications", 1, "name", "x"), "Postulo translates"),
        (fr("hobbies", 1, "name", "x"), "Postulo translates"),
        (fr("experience", 1, "role", "x", language="../../etc"), "not a language code"),
        (fr("experience", 1, "role", ["x"]), "not text"),
    ],
    ids=[
        "an entry that is not there",
        "an entry that cannot be added",
        "a name that is somebody else's",
        "a kind that translates nothing",
        "a kind there is not",
        "a language that is not one",
        "a text that is not text",
    ],
)
def test_a_translation_that_cannot_be_added_says_why(user, row, why):
    data = a_file(
        resume={
            "experience": [a_role(id=1), a_role(id=2, organisation="Initrode", role="")],
            "certifications": [{"id": 1, "name": "A certificate"}],
            "translations": [row],
        }
    )

    plan = drawn(user, data)
    add(user, data)

    (found,) = rows(plan, "translations")
    assert found.outcome == candidate.REFUSED
    assert why in " ".join(found.notes)
    assert not Translation.objects.for_user(user).exists()
    assert Experience.objects.for_user(user).filter(organisation="Initech").exists()


# --------------------------------------------------------------- it tells, and does not refuse


@pytest.mark.parametrize(
    "changes,why",
    [
        ({"start_date": "09/01/2017"}, "year, month and day"),
        ({"start_date": "2017-02-31"}, "year, month and day"),
        ({"start_date": None}, "required"),
        ({"role": ""}, "required"),
        ({"role": "x" * 201}, "at most 200"),
        ({"role": ["Developer"]}, "not text"),
        ({"role": 12345}, "not text"),
        ({"end_date": "2016-01-01"}, "before the start"),
        ({"summary": "nul\x00l"}, "Null characters"),
    ],
    ids=[
        "a date written another way",
        "a date there is not",
        "no start",
        "no title",
        "a title longer than a title",
        "a title that is a list",
        "a title that is a number",
        "an end before its start",
        "a character that is not one",
    ],
)
def test_an_entry_that_cannot_be_added_costs_that_entry(user, changes, why):
    """And the page says which, and why, in the words its own form would have used."""
    data = a_file(
        resume={"experience": [a_role(id=1, **changes), a_role(id=2, organisation="Initrode")]}
    )

    plan = drawn(user, data)
    add(user, data)

    refused, added = rows(plan, "experience")
    assert refused.outcome == candidate.REFUSED
    assert why in " ".join(refused.notes)
    assert added.outcome == candidate.ADD
    assert [role.organisation for role in Experience.objects.for_user(user)] == ["Initrode"]


def test_a_reason_names_the_field_it_is_about(user):
    data = a_file(resume={"experience": [a_role(role="", start_date="soon")]})

    (row,) = rows(drawn(user, data), "experience")

    assert sorted(note.partition(":")[0] for note in row.notes) == ["From", "Role"]


def test_nothing_is_cut_to_fit(user):
    """The Europass reader trims what is too long. This does not: a title that loses its
    end is a title somebody did not write."""
    data = a_file(resume={"skills": [{"name": "x" * 101}]})

    add(user, data)

    assert not Skill.objects.for_user(user).exists()


def test_something_in_a_list_that_is_not_an_entry_is_said_to_be(user):
    data = a_file(resume={"experience": ["see attached", a_role(), 42]})

    plan = drawn(user, data)

    assert outcomes(plan, "experience") == [candidate.REFUSED, candidate.ADD, candidate.REFUSED]
    assert "not written the way an entry is" in rows(plan, "experience")[0].notes[0]


def test_a_skill_without_its_group_is_not_added_loose(user):
    """A skill is printed under its group and nowhere else, so one that lost its group on
    the way in would be a row nobody could see."""
    data = a_file(
        resume={
            "skill_groups": [{"id": 1, "name": "x" * 101}],
            "skills": [
                {"name": "Python", "group_id": 1},
                {"name": "Go", "group_id": 9},
                {"name": "Rust", "group_id": None},
            ],
        }
    )

    plan = drawn(user, data)
    add(user, data)

    python, go, rust = rows(plan, "skills")
    assert python.outcome == candidate.REFUSED and "cannot be added" in python.notes[0]
    assert go.outcome == candidate.REFUSED and "not in the file" in go.notes[0]
    assert rust.outcome == candidate.ADD, "one that never had a group is as it was"
    assert [skill.name for skill in Skill.objects.for_user(user)] == ["Rust"]


def test_a_level_the_file_does_not_state_is_not_stated(user):
    """A level is a claim somebody will be tested on, so none is made for them (#235)."""
    data = a_file(
        resume={
            "languages": [
                {"name": "Deutsch", "proficiency": ""},
                {"name": "Italiano", "proficiency": "fluent"},
            ]
        }
    )

    plan = drawn(user, data)
    add(user, data)

    assert outcomes(plan, "languages") == [candidate.ADD, candidate.REFUSED]
    assert LanguageSkill.objects.for_user(user).get().proficiency == ""


def test_a_links_last_check_is_this_instances_to_make(user):
    """What another instance found when it asked is not something this one knows."""
    data = a_file(
        resume={
            "links": [
                {
                    "title": "Portfolio",
                    "url": "https://alex.example.org",
                    "kind": "portfolio",
                    "check_status": "ok",
                    "check_detail": "Answered 200",
                    "checked_at": "2026-01-01T10:00:00+00:00",
                }
            ]
        }
    )

    add(user, data)

    link = Link.objects.for_user(user).get()
    assert (link.check_status, link.check_detail, link.checked_at) == ("", "", None)


def test_what_is_odd_about_the_file_is_said_once_at_the_top(user):
    document = json.loads(a_file(resume={"experience": [a_role()], "education": "see attached"}))
    document["postulo"]["candidate_format"] = export.CANDIDATE_FORMAT + 1
    document["companies"] = [{"name": "Umbrella Corporation"}]
    document["account"] = "Alex"

    plan = drawn(user, json.dumps(document).encode())

    said = " ".join(plan.notes)
    assert "a newer Postulo" in said
    assert "also holds companies" in said
    assert "“education”" in said and "“account”" in said
    assert outcomes(plan, "experience") == [candidate.ADD], "and the rest is read"


def test_a_block_with_more_rows_than_anybody_has_is_read_so_far_and_no_further(user):
    data = a_file(
        resume={"skills": [{"name": f"Skill {number}"} for number in range(candidate.MAX_ROWS + 3)]}
    )

    plan = drawn(user, data)

    assert len(rows(plan, "skills")) == candidate.MAX_ROWS
    assert f"{candidate.MAX_ROWS + 3} rows" in " ".join(plan.notes)


# ------------------------------------------------------------------------------ the page


def upload(data: bytes, name: str = "postulo-candidate.json"):
    return SimpleUploadedFile(name, data, content_type="application/json")


def test_the_page_offers_the_file_and_says_what_is_not_in_it(client, somebody):
    client.force_login(somebody)

    page = client.get(reverse(PAGE))

    body = page.content.decode()
    assert page.status_code == 200
    assert reverse(DOWNLOAD) in body
    assert "Your picture is left out" in body
    assert export.candidate_filename(somebody) in body
    counted = {str(row["label"]): row["total"] for row in page.context["counts"]}
    assert counted["Experience"] == 2 and counted["Translations"] == 3
    assert counted["Web links"] == 2


def test_the_page_is_named_after_the_instance_like_any_other(client, user):
    client.force_login(user)

    body = client.get(reverse(PAGE)).content.decode()

    assert "<title>Postulo &gt; Your record as a file</title>" in " ".join(body.split())


def test_the_download_is_the_file(client, somebody):
    client.force_login(somebody)

    response = client.get(reverse(DOWNLOAD))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response["Content-Disposition"] == (
        f'attachment; filename="{export.candidate_filename(somebody)}"'
    )
    assert "no-store" in response["Cache-Control"]
    document = json.loads(response.content)
    assert document["postulo"]["candidate_format"] == export.CANDIDATE_FORMAT
    assert document["resume"]["experience"][0]["organisation"] == "Aperture Science"
    assert "Ingénieur principal" in response.content.decode(), "written as it reads"


def test_what_is_downloaded_is_what_the_page_reads_back(client, somebody, other_user):
    """The whole of it through the interface: out of one account, into another."""
    client.force_login(somebody)
    data = client.get(reverse(DOWNLOAD)).content

    client.force_login(other_user)
    client.post(reverse(PAGE), {"file": upload(data)})
    client.post(reverse(PAGE), {"action": "confirm"})

    assert Experience.objects.for_user(other_user).count() == 2
    assert Skill.objects.for_user(other_user).count() == 2
    assert Translation.objects.for_user(other_user).count() == 3


def test_reading_a_file_writes_nothing_until_it_is_confirmed(client, somebody, other_user):
    client.force_login(other_user)
    before = held_by(other_user)

    response = client.post(reverse(PAGE), {"file": upload(the_file(somebody))}, follow=True)

    assert held_by(other_user) == before
    other_user.profile.refresh_from_db()
    assert other_user.profile.headline == ""
    body = response.content.decode()
    assert "What is in the file" in body and "Add what is new" in body
    assert "Senior Engineer" in body and "Aperture Science" in body
    assert "Will be added" in body
    assert response.context["review"].adds


def test_confirming_adds_it_and_forgets_the_file(client, somebody, other_user):
    client.force_login(other_user)
    client.post(reverse(PAGE), {"file": upload(the_file(somebody))})

    response = client.post(reverse(PAGE), {"action": "confirm"}, follow=True)

    assert Experience.objects.for_user(other_user).count() == 2
    assert client.session.get("candidate_file") is None
    assert response.redirect_chain[-1][0] == reverse("resume:overview")
    assert "entries were added to your record" in response.content.decode()


def test_confirming_twice_adds_once(client, somebody, other_user):
    """The button pressed again, or the page left open in another tab."""
    client.force_login(other_user)
    client.post(reverse(PAGE), {"file": upload(the_file(somebody))})
    held = client.session["candidate_file"]
    client.post(reverse(PAGE), {"action": "confirm"})
    after_the_first = held_by(other_user)

    session = client.session
    session["candidate_file"] = held
    session.save()
    response = client.post(reverse(PAGE), {"action": "confirm"}, follow=True)

    assert held_by(other_user) == after_the_first
    assert "Nothing was added" in response.content.decode()


def test_the_review_is_of_the_account_as_it_stands(client, somebody, other_user):
    """Worked out when the page is drawn, not when the file was read."""
    client.force_login(other_user)
    client.post(reverse(PAGE), {"file": upload(the_file(somebody))})
    first = client.get(reverse(PAGE)).context["review"]

    Experience.objects.create(
        owner=other_user,
        organisation="Black Mesa",
        role="Engineer",
        start_date=dt.date(2019, 3, 1),
        end_date=dt.date(2023, 6, 30),
    )
    second = client.get(reverse(PAGE)).context["review"]

    assert outcomes(first, "experience") == [candidate.ADD, candidate.ADD]
    assert outcomes(second, "experience") == [candidate.ADD, candidate.PRESENT]


def test_a_file_with_nothing_new_in_it_offers_nothing_to_confirm(client, somebody):
    client.force_login(somebody)

    response = client.post(reverse(PAGE), {"file": upload(the_file(somebody))}, follow=True)

    body = response.content.decode()
    assert "There is nothing in this file to add" in body
    assert "Add what is new" not in body
    assert "Start again" in body


def test_starting_again_drops_what_was_read(client, somebody, other_user):
    client.force_login(other_user)
    client.post(reverse(PAGE), {"file": upload(the_file(somebody))})

    response = client.post(reverse(PAGE), {"action": "forget"}, follow=True)

    assert client.session.get("candidate_file") is None
    assert response.context["review"] is None
    assert not Experience.objects.for_user(other_user).exists()


def test_confirming_with_nothing_held_writes_nothing(client, user):
    client.force_login(user)

    response = client.post(reverse(PAGE), {"action": "confirm"}, follow=True)

    assert "nothing waiting to be imported" in response.content.decode()
    assert not Experience.objects.for_user(user).exists()


def test_a_file_that_is_refused_says_why_and_keeps_the_page(client, user):
    client.force_login(user)

    response = client.post(reverse(PAGE), {"file": upload(b'{"hello": "world"}')}, follow=True)

    assert "one person&#x27;s own record" in response.content.decode()
    assert response.context["review"] is None
    assert client.session.get("candidate_file") is None


def test_no_file_at_all_says_so(client, user):
    client.force_login(user)

    response = client.post(reverse(PAGE), {}, follow=True)

    assert "Choose a file first." in response.content.decode()


def test_a_file_with_nothing_in_it_says_so(client, user):
    client.force_login(user)

    response = client.post(reverse(PAGE), {"file": upload(a_file())}, follow=True)

    assert "there was nothing in it to import" in response.content.decode()
    assert client.session.get("candidate_file") is None


def test_a_file_whose_career_cannot_be_read_is_told_so_and_not_called_empty(client, user):
    """Told, not refused, and not passed over either: a part that is there in a shape this
    page does not read is something to say, so the review says it."""
    document = json.loads(a_file())
    document["resume"] = "see attached"
    client.force_login(user)

    response = client.post(
        reverse(PAGE), {"file": upload(json.dumps(document).encode())}, follow=True
    )

    review = response.context["review"]
    assert review is not None and not review.sections and not review.adds
    body = response.content.decode()
    assert "“resume” is in the file" in body
    assert "There is nothing in this file to add" in body
    assert "Add what is new" not in body and "Start again" in body
    assert "data-summary" not in body, "and no list of counts with nothing to count"


@pytest.mark.parametrize(
    "when",
    ["2026-09-28T10:00:00", "2026-13-45T10:00:00", "yesterday", "", 42, None, ["x"]],
    ids=["no zone", "no such day", "a word", "blank", "a number", "nothing", "a list"],
)
def test_when_a_file_says_it_was_written_is_said_only_if_it_is_a_time(client, user, when):
    """The header is the file's word for itself, and is read as carefully as the rest."""
    document = json.loads(a_file(resume={"experience": [a_role()]}))
    document["postulo"]["exported_at"] = when
    client.force_login(user)

    response = client.post(
        reverse(PAGE), {"file": upload(json.dumps(document).encode())}, follow=True
    )

    assert response.status_code == 200
    assert response.context["review"].adds == 1
    said = "Written by Postulo 0.5.0 on" in response.content.decode()
    assert said == (when == "2026-09-28T10:00:00")


def test_the_review_says_what_happens_to_each_part_in_words(client, somebody):
    data = a_file(
        account={"profile": {"headline": "Something else"}},
        resume={
            "experience": [
                a_role(),
                a_role(id=2, role=""),
                {
                    "organisation": "Black Mesa",
                    "role": "Engineer",
                    "start_date": "2019-03-01",
                    "end_date": "2023-06-30",
                },
            ]
        },
    )
    client.force_login(somebody)

    response = client.post(reverse(PAGE), {"file": upload(data)}, follow=True)

    body = " ".join(response.content.decode().split())
    for words in ("Will be added", "Already in your record", "Yours is kept", "Cannot be added"):
        assert words in body
    assert "Written by Postulo 0.5.0 on" in body
    assert dict(response.context["review"].summary) == {
        "Will be added": 1,
        "Already in your record": 1,
        "Yours is kept": 1,
        "Cannot be added": 1,
    }
    # The keys the file is written in are the file's; the page has words of its own.
    assert "start_date" not in body and "candidate_format" not in body


def test_what_is_held_is_what_was_read_and_never_a_sentence_about_it(client, user):
    """The session holds facts, and the page says them when it is drawn, in the language it
    is drawn in: a language changed between reading a file and confirming it reads properly.
    And what is held is flat, whatever the file put where a title should have been."""
    document = json.loads(
        a_file(
            resume={
                "experience": [a_role(role={"deep": [[["er"]]]}, salary=1e9)],
                "education": "see attached",
            }
        )
    )
    document["companies"] = [{"name": "Umbrella Corporation"}]
    client.force_login(user)

    client.post(reverse(PAGE), {"file": upload(json.dumps(document).encode())})

    held = client.session["candidate_file"]
    assert held["unreadable"] == ["education"]
    assert held["also"] == ["companies"]
    (role,) = held["experience"]
    assert role["role"] == [], "something was there, and it was not text"
    assert "salary" not in role, "and what nothing reads is not kept"
    written = json.dumps(held)
    assert "Umbrella" not in written and "deep" not in written
    for words in ("Cannot be added", "not text", "does not read"):
        assert words not in written


def test_a_session_that_holds_something_else_is_a_session_that_holds_nothing(client, user):
    """Written by an older Postulo, say. Not worth an error page on every visit."""
    client.force_login(user)
    session = client.session
    session["candidate_file"] = ["not", "what", "is", "kept", "here"]
    session.save()

    page = client.get(reverse(PAGE))

    assert page.status_code == 200 and page.context["review"] is None
    assert "candidate_file" not in client.session


def test_both_pages_about_the_person_lead_here(client, user):
    client.force_login(user)

    for name in ("resume:overview", "accounts:profile", "core:export", "resume:europass_import"):
        assert reverse(PAGE) in client.get(reverse(name)).content.decode(), name


@override_settings(POSTULO_NUMBER_RATE="20/h")
def test_the_page_draws_what_a_whole_career_holds(client, somebody, other_user):
    """Every kind of row at once, which is the page nobody sees while testing one kind."""
    client.force_login(other_user)

    response = client.post(reverse(PAGE), {"file": upload(the_file(somebody))}, follow=True)

    titles = [str(section.title) for section in response.context["review"].sections]
    assert titles == [
        "Your details",
        "Telephone numbers",
        "Postal addresses",
        "Social profiles",
        "Websites",
        "Identifiers",
        "Experience",
        "Education",
        "Projects",
        "Links",
        "Skill groups",
        "Skills",
        "Certifications",
        "Languages",
        "Translations",
    ]
    body = response.content.decode()
    for title in titles:
        assert title in body
