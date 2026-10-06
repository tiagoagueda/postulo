"""Reading a Europass CV in the format of the editor before 2020: what the file says, what
gets written, and what is refused.

Two fixtures, one career. ``tests/data/europass.xml`` is the legacy format's real shape —
nested ``WorkExperience``, dates as attributes, CEFR split five ways, prose under each
skill heading — and ``tests/data/europass.json`` is the same person in that format's JSON,
as Cedefop's web service wrote it. They describe the same career on purpose: a test insists
the two readers produce the same record, which is what keeps the mapping in one place
instead of two. The format europass.europa.eu has written since 2020, and the PDF it comes
in, are ``test_europass_candidate.py`` (#244).
"""

import datetime as dt
import json
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from postulo.accounts import identifiers
from postulo.accounts.models import PersonIdentifier
from postulo.core import postal
from postulo.core.models import PostalAddress, WebLink
from postulo.jobs.models import Company, Contact
from postulo.plugins import base
from postulo.plugins.europass import reader as europass
from postulo.resume import importing
from postulo.resume.models import (
    Education,
    Experience,
    LanguageSkill,
    Project,
    Skill,
    SkillGroup,
)

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).parent / "data" / "europass.xml"
JSON_FIXTURE = Path(__file__).parent / "data" / "europass.json"
CEDEFOP_FIXTURE = Path(__file__).parent / "data" / "europass-cedefop-shape.xml"

MINIMAL = b"""<?xml version="1.0"?>
<SkillsPassport xmlns="http://europass.cedefop.europa.eu/Europass">
  <LearnerInfo>
    <WorkExperience>
      <Period><From year="2020"/></Period>
      <Position><Label>Engineer</Label></Position>
      <Employer><Name>Initech</Name></Employer>
    </WorkExperience>
  </LearnerInfo>
</SkillsPassport>
"""


# ----------------------------------------------------------------- the reader


def test_it_reads_the_nested_shape_the_reader_was_first_written_to():
    record = europass.read(FIXTURE.read_bytes())

    assert record.counts() == {
        "experience": 2,
        "education": 1,
        "languages": 2,
        "skills": 5,
        "projects": 1,
        "memberships": 0,
    }
    assert record.person["first_name"] == "Alex"
    assert record.person["email"] == "alex@example.org"
    assert record.person["headline"] == "Backend engineer"
    assert record.person["location"] == "Lisboa, Portugal"


def test_it_reads_the_shape_the_schema_gives_a_cv(user):
    """`*List` wrappers and months written as xs:gMonth, which read as nothing before (#632)."""
    record = europass.read(CEDEFOP_FIXTURE.read_bytes())

    assert record.counts() == {
        "experience": 2,
        "education": 1,
        "languages": 2,
        "skills": 1,
        "projects": 1,
        "memberships": 0,
    }
    assert record.person["phone"] == "+3225551234"
    assert record.person["website"] == "https://sam.example.org"
    first, second = record.experience
    assert first["role"] == "Independent consultant"
    assert first["start_date"] == dt.date(2016, 8, 1)
    assert first["end_date"] == dt.date(2020, 12, 31)
    assert second["start_date"] == dt.date(2020, 9, 14)
    assert record.education[0]["start_date"] == dt.date(2010, 9, 1)
    assert record.skipped == []
    # And it is written, where an entry with no start date would have been left out.
    importing.apply(user, record)
    assert Experience.objects.filter(owner=user).count() == 2


def test_a_list_that_holds_nothing_readable_is_named_rather_than_dropped():
    data = (
        b'<SkillsPassport xmlns="http://europass.cedefop.europa.eu/Europass"><LearnerInfo>'
        b"<WorkExperienceList><WorkExperience><Odd/></WorkExperience></WorkExperienceList>"
        b"</LearnerInfo></SkillsPassport>"
    )

    record = europass.read(data)

    assert record.experience == []
    assert record.skipped == ["Work experience was in the file but could not be read."]


def test_a_single_entry_file_has_no_inner_element():
    """Europass nests WorkExperience inside WorkExperience — except when it does not."""
    record = europass.read(MINIMAL)

    assert len(record.experience) == 1
    assert record.experience[0]["role"] == "Engineer"
    assert record.experience[0]["organisation"] == "Initech"


def test_dates_come_off_the_attributes_and_keep_their_day():
    record = europass.read(FIXTURE.read_bytes())
    first, second = record.experience

    assert first["start_date"] == dt.date(2019, 3, 1)
    # 30 June, not 28: clamping every date to a safe day silently moves a real one.
    assert first["end_date"] == dt.date(2023, 6, 30)
    # A current position has no end, and a period without a day starts the month.
    assert second["start_date"] == dt.date(2023, 7, 1)
    assert second["end_date"] is None


def test_a_year_on_its_own_is_the_first_of_january():
    record = europass.read(MINIMAL)

    assert record.experience[0]["start_date"] == dt.date(2020, 1, 1)


def test_an_impossible_day_is_pulled_back_to_the_end_of_its_month():
    data = MINIMAL.replace(b'<From year="2020"/>', b'<From year="2021" month="02" day="31"/>')

    record = europass.read(data)

    assert record.experience[0]["start_date"] == dt.date(2021, 2, 28)


def test_the_lowest_of_the_five_levels_is_the_one_kept():
    """Claiming the best of five on a CV is what gets found out in an interview."""
    record = europass.read(FIXTURE.read_bytes())
    languages = {row["name"]: row for row in record.languages}

    assert languages["português"]["proficiency"] == "native"
    assert languages["English"]["proficiency"] == "b2"
    # All five are kept so the review page can show what was set aside.
    assert languages["English"]["levels"] == {
        "Listening": "C2",
        "Reading": "C2",
        "SpokenInteraction": "C1",
        "SpokenProduction": "C1",
        "Writing": "B2",
    }


def test_skills_are_split_on_both_semicolons_and_lines():
    record = europass.read(FIXTURE.read_bytes())
    groups = {group["name"]: group["skills"] for group in record.skill_groups}

    # The Europass heading "Computer" becomes a word somebody would write on a CV.
    assert groups["Digital"] == ["Python", "PostgreSQL", "Docker"]
    # A newline is a skill boundary too; collapsing it made "Mentoring Planning".
    assert groups["Organisational"] == ["Mentoring", "Planning"]


MARKUP = (
    "<p>Evaluation of support measures<br />and young people</p>"
    "<ul><li>Mentoring</li><li>Planning</li></ul>"
)
LIST = "<ul><li>Mentoring</li><li>Planning</li></ul>"


def _assert_plain(record):
    """What the editor wrote as HTML arrives as lines, and a listed block as one skill each."""
    assert record.experience[0]["summary"] == (
        "Evaluation of support measures\nand young people\nMentoring\nPlanning"
    )
    assert record.education[0]["highlights"] == record.experience[0]["summary"]
    assert record.projects[0]["summary"] == record.experience[0]["summary"]
    groups = {group["name"]: group["skills"] for group in record.skill_groups}
    assert groups["Organisational"] == ["Mentoring", "Planning"]
    assert groups["Communication"] == ["Mentoring", "Planning"]
    for text in (
        record.experience[0]["summary"],
        record.education[0]["highlights"],
        record.projects[0]["summary"],
        *(skill for group in groups.values() for skill in group),
    ):
        assert "<" not in text and ">" not in text


def test_the_xml_reader_turns_the_editors_markup_into_lines():
    """Cedefop's editor wrote escaped HTML into these fields, as the Candidate one does (#563)."""
    escaped = MARKUP.replace("<", "&lt;").replace(">", "&gt;")
    listed = LIST.replace("<", "&lt;").replace(">", "&gt;")
    data = (
        FIXTURE.read_text(encoding="utf-8")
        .replace("Built the thing. Kept it running.", escaped)
        .replace("Distributed systems.", escaped)
        .replace("Maintainer of a thing.", escaped)
        .replace("<Label>Mentoring\nPlanning</Label>", f"<Label>{listed}</Label>")
        .replace(
            "</Organisational>",
            f"</Organisational><Communication><Description><Label>{listed}</Label>"
            "</Description></Communication>",
        )
    )

    _assert_plain(europass.read(data.encode()))


def test_the_json_reader_turns_the_editors_markup_into_lines():
    document = json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))
    learner = document["SkillsPassport"]["LearnerInfo"]
    learner["WorkExperience"][0]["Activities"] = MARKUP
    learner["Education"][0]["Activities"] = MARKUP
    learner["Achievement"][0]["Description"] = MARKUP
    learner["Skills"]["Organisational"] = {"Description": LIST}
    learner["Skills"]["Communication"] = {"Description": LIST}

    _assert_plain(europass.read(json.dumps(document).encode()))


def test_a_namespace_nobody_has_seen_still_reads():
    """Europass has been through several. Matching the namespace reads exactly one."""
    data = FIXTURE.read_bytes().replace(
        b"http://europass.cedefop.europa.eu/Europass", b"urn:europass:xml:9.9"
    )

    assert europass.read(data).counts()["experience"] == 2


# --------------------------------------------------------------- what it refuses


def test_a_doctype_is_refused_before_anything_is_parsed():
    """Where entity expansion lives. A Europass export has no use for one."""
    bomb = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>\n'
        b"<SkillsPassport><LearnerInfo><Headline>&lol2;</Headline></LearnerInfo></SkillsPassport>"
    )

    with pytest.raises(base.ImportRefused, match="document type declaration"):
        europass.read(bomb)


def test_read_xml_itself_will_not_expand_an_entity():
    """#378: not safe because a refusal ran first, but because the parser forbids it."""
    padded = (
        b"<!--" + b" " * 5000 + b"-->"
        b'<!DOCTYPE r [<!ENTITY e "boom">]>'
        b"<SkillsPassport><LearnerInfo><Headline>&e;</Headline></LearnerInfo></SkillsPassport>"
    )
    for data in (padded, padded.decode().encode("utf-16-le")):
        with pytest.raises(europass.EuropassError, match="not readable XML"):
            europass.read_xml(data)


def test_an_external_entity_cannot_reach_the_disk():
    xxe = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
        b"<SkillsPassport><LearnerInfo><Headline>&x;</Headline></LearnerInfo></SkillsPassport>"
    )

    with pytest.raises(base.ImportRefused, match="document type declaration"):
        europass.read(xxe)


def test_xml_that_does_not_parse_says_so():
    with pytest.raises(europass.EuropassError, match="not readable XML"):
        europass.read(b"<SkillsPassport><LearnerInfo>")


def test_xml_that_is_not_europass_says_so():
    """And names both XML formats it does read, since the first byte cannot tell them apart."""
    with pytest.raises(europass.EuropassError, match="not a Europass CV") as refused:
        europass.read(b"<html><body>Hello</body></html>")
    assert "Candidate" in str(refused.value) and "SkillsPassport" in str(refused.value)


def test_an_empty_file_says_so():
    with pytest.raises(base.ImportRefused, match="empty"):
        europass.read(b"")


def test_a_file_over_the_cap_is_not_parsed():
    with pytest.raises(base.ImportRefused, match="larger than"):
        europass.read(b"<a/>" + b" " * europass.MAX_BYTES)


# ---------------------------------------------------------------- the writing


def test_applying_writes_everything_it_found(user):
    record = europass.read(FIXTURE.read_bytes())

    report = importing.apply(user, record)

    assert Experience.objects.filter(owner=user).count() == 2
    assert Education.objects.filter(owner=user).count() == 1
    assert LanguageSkill.objects.filter(owner=user).count() == 2
    assert Skill.objects.filter(owner=user).count() == 5
    assert Project.objects.filter(owner=user).count() == 1
    assert report.total == sum(report.added.values())


def test_an_import_never_overwrites_what_is_already_there(user):
    """Somebody's own words about themselves beat a form they filled in years ago."""
    user.first_name = "Alexandra"
    user.save(update_fields=["first_name"])
    profile = user.profile
    profile.headline = "Staff engineer, mostly Python"
    profile.save(update_fields=["headline"])

    importing.apply(user, europass.read(FIXTURE.read_bytes()))

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.first_name == "Alexandra"
    assert profile.headline == "Staff engineer, mostly Python"
    # A blank is not an opinion, so the blank ones were filled.
    assert user.last_name == "Morgan"
    # Except the location, where a blank is an answer since #309: the file brought an
    # address, and a blank location prints that address's town and country.
    assert profile.location == ""
    assert postal.printed_location(profile) == "Lisboa, Portugal"


def test_a_heading_that_already_exists_is_used_rather_than_repeated(user):
    SkillGroup.objects.create(owner=user, name="Digital")

    importing.apply(user, europass.read(FIXTURE.read_bytes()))

    assert SkillGroup.objects.filter(owner=user, name="Digital").count() == 1
    assert Skill.objects.filter(owner=user, group__name="Digital").count() == 3


def test_experience_without_a_start_is_left_out(user):
    data = MINIMAL.replace(b'<Period><From year="2020"/></Period>', b"")

    importing.apply(user, europass.read(data))

    assert not Experience.objects.filter(owner=user).exists()


def test_one_persons_import_does_not_touch_another(user, other_user):
    importing.apply(user, europass.read(FIXTURE.read_bytes()))

    assert not Experience.objects.filter(owner=other_user).exists()
    assert not SkillGroup.objects.filter(owner=other_user).exists()


# ------------------------------------------------------------------- the page


def upload(name="cv.xml", data=None):
    return SimpleUploadedFile(name, data or FIXTURE.read_bytes(), content_type="text/xml")


def test_the_page_needs_an_account(client):
    response = client.get(reverse("resume:europass_import"))

    assert response.status_code == 302
    assert "login" in response["Location"]


def test_reading_a_file_writes_nothing_until_it_is_confirmed(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload()})

    assert not Experience.objects.filter(owner=user).exists()
    page = client.get(url)
    assert page.context["found"]["counts"]
    assert b"Add all of this" in page.content


def test_confirming_writes_it_and_forgets_the_file(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload()})

    response = client.post(url, {"action": "confirm"}, follow=True)

    assert Experience.objects.filter(owner=user).count() == 2
    assert client.session.get("europass_import") is None
    assert response.redirect_chain[-1][0] == reverse("resume:overview")


def test_starting_again_drops_what_was_read(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload()})

    client.post(url, {"action": "forget"})

    assert client.session.get("europass_import") is None
    assert not Experience.objects.filter(owner=user).exists()


def test_confirming_with_nothing_held_writes_nothing(client, user):
    client.force_login(user)

    client.post(reverse("resume:europass_import"), {"action": "confirm"})

    assert not Experience.objects.filter(owner=user).exists()


def test_a_file_that_cannot_be_read_says_why_and_keeps_the_page(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    response = client.post(url, {"file": upload(data=b"<SkillsPassport><Learner")}, follow=True)

    assert b"not readable XML" in response.content
    assert response.context["found"] is None


def _confirm(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload()})
    return client.post(url, {"action": "confirm"}, follow=True)


def test_a_website_the_account_keeps_as_a_social_profile_is_left_out_and_said(client, user):
    """A link is unique on its holder and address whatever its kind (#616)."""
    WebLink.objects.create(
        owner=user,
        holder=user.profile,
        kind="social",
        url="https://alex.example.org",
        is_primary=True,
    )

    response = _confirm(client, user)

    assert response.redirect_chain[-1][0] == reverse("resume:overview")
    assert Experience.objects.filter(owner=user).count() == 2
    assert user.profile.web_links.count() == 1
    assert "is already among your links" in response.content.decode()


def test_an_address_listed_for_a_contact_is_left_out_and_said(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    PostalAddress.objects.create(
        owner=user,
        holder=contact,
        street="rua do exemplo 1",
        postcode="1000-001",
        municipality="Lisboa",
        country="PT",
    )

    response = _confirm(client, user)

    assert response.redirect_chain[-1][0] == reverse("resume:overview")
    assert Experience.objects.filter(owner=user).count() == 2
    assert not user.profile.postal_addresses.exists()
    assert "is already listed for somebody you deal with" in response.content.decode()


def test_what_is_held_between_the_two_steps_is_the_record_and_not_the_file(client, user):
    """No reason to keep somebody's CV on the server longer than it takes to read it."""
    client.force_login(user)

    client.post(reverse("resume:europass_import"), {"file": upload()})

    held = client.session["europass_import_data"]
    assert set(held) == {
        "person",
        "locale",
        "experience",
        "education",
        "languages",
        "skill_groups",
        "projects",
        "memberships",
    }
    # Dates survive the round trip through JSON.
    assert held["experience"][0]["start_date"] == "2019-03-01"


def test_the_review_page_says_what_it_found_in_words(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload()})

    page = client.get(url)

    body = page.content.decode()
    assert "Senior Engineer" in body and "Aperture Science" in body
    assert "MSc Computer Science" in body
    # The keys the session holds are Europass's; the page shows Postulo's words.
    assert "first_name" not in body and "SpokenInteraction" not in body
    assert "Positions" in body


def test_the_overview_offers_the_import(client, user):
    client.force_login(user)

    page = client.get(reverse("resume:overview"))

    assert reverse("resume:europass_import").encode() in page.content


def test_confirming_one_entry_says_so_in_the_singular(client, user):
    """ "Added 1 entries." was said at one (#391)."""
    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload(data=MINIMAL)})

    response = client.post(url, {"action": "confirm"}, follow=True)

    body = response.content.decode()
    assert "Added 1 entry. Nothing was overwritten" in body
    assert "1 entries" not in body


def test_confirming_several_says_so_in_the_plural(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    client.post(url, {"file": upload()})

    response = client.post(url, {"action": "confirm"}, follow=True)

    assert "entries. Nothing was overwritten" in response.content.decode()


# ------------------------------------------------------------ the command line


def test_a_dry_run_says_what_it_found_and_writes_nothing(user, capsys):
    call_command("import_europass", str(FIXTURE), f"--user={user.email}", "--dry-run")

    out = capsys.readouterr().out
    assert "experience: 2" in out
    assert not Experience.objects.filter(owner=user).exists()


def test_the_command_imports_for_the_account_it_is_given(user, other_user, capsys):
    call_command("import_europass", str(FIXTURE), f"--user={user.email}")

    assert Experience.objects.filter(owner=user).count() == 2
    assert not Experience.objects.filter(owner=other_user).exists()
    assert "Nothing was overwritten" in capsys.readouterr().out


# ------------------------------------------------------------- the JSON format


MINIMAL_JSON = b"""{
  "SkillsPassport": {"LearnerInfo": {"WorkExperience": {
    "Period": {"From": {"Year": 2020}},
    "Position": {"Label": "Engineer"},
    "Employer": {"Name": "Initech"}
  }}}
}"""


def test_both_formats_read_the_same_career():
    """The point of the split: two front doors, one mapping.

    If this ever fails, the JSON reader has grown its own copy of the mapping and the two
    are free to drift apart, which is exactly what the intermediate record is for.
    """
    from_xml = europass.read(FIXTURE.read_bytes())
    from_json = europass.read(JSON_FIXTURE.read_bytes())

    assert from_xml.source == "xml"
    assert from_json.source == "json"
    assert from_json.person == from_xml.person
    assert from_json.experience == from_xml.experience
    assert from_json.education == from_xml.education
    assert from_json.languages == from_xml.languages
    assert from_json.skill_groups == from_xml.skill_groups
    assert from_json.projects == from_xml.projects


def test_both_readers_move_exactly_eqf_n_into_the_level_and_keep_any_other_label():
    """The older formats copied the `Level` label into the grade; an EQF one is a level (#684)."""
    for read in (europass.read(FIXTURE.read_bytes()), europass.read(JSON_FIXTURE.read_bytes())):
        row = read.education[0]
        assert (row["grade"], row["eqf_level"]) == ("", 7)

    def json_with(label: str) -> dict:
        data = {"LearnerInfo": {"Education": [{"Title": "MSc", "Level": {"Label": label}}]}}
        return europass.read(json.dumps(data).encode()).education[0]

    assert json_with("EQF 6")["eqf_level"] == 6
    for label in ("EQF 9", "EQF 6 or equivalent", "eqf 6", "Master 2", "6.0 GPA"):
        row = json_with(label)
        assert (row["grade"], row["eqf_level"]) == (label, None), label


def test_the_format_is_sniffed_so_nobody_has_to_know_which_they_have():
    assert europass.read(FIXTURE.read_bytes()).source == "xml"
    assert europass.read(JSON_FIXTURE.read_bytes()).source == "json"
    # A byte order mark and leading whitespace do not change the answer.
    assert europass.read(b"\xef\xbb\xbf\n  " + JSON_FIXTURE.read_bytes()).source == "json"


def test_something_that_is_neither_format_says_so():
    with pytest.raises(europass.EuropassError, match="not a Europass file"):
        europass.read(b"Name,Role\nAlex,Engineer\n")


def test_one_entry_written_as_an_object_reads_like_a_list_of_one():
    """Exports differ on whether a single entry is wrapped in an array."""
    record = europass.read(MINIMAL_JSON)

    assert len(record.experience) == 1
    assert record.experience[0]["role"] == "Engineer"
    assert record.experience[0]["start_date"] == dt.date(2020, 1, 1)


def test_a_block_of_the_wrong_type_is_skipped_and_said_out_loud():
    """Half a file is worth importing; a silently empty career is not."""
    data = b"""{"LearnerInfo": {
      "WorkExperience": "see attached",
      "Education": [{"Title": "MSc", "Period": {"From": {"Year": 2014}}}]
    }}"""

    record = europass.read(data)

    assert record.experience == []
    assert len(record.education) == 1
    assert any("Work experience" in note for note in record.skipped)


def test_values_of_the_wrong_type_do_not_stop_the_read():
    data = b"""{"LearnerInfo": {
      "Identification": {"PersonName": {"FirstName": ["Alex"], "Surname": "Morgan"}},
      "WorkExperience": [
        {"Position": {"Label": "Engineer"}, "Employer": {"Name": 12345},
         "Period": {"From": {"Year": "2020", "Month": "not a month"}}}
      ]
    }}"""

    record = europass.read(data)

    assert record.person == {"last_name": "Morgan"}
    assert record.experience[0]["organisation"] == "12345"
    # A month that is missing becomes January; a month that is nonsense is not guessed at,
    # because inventing one could misdate the job by eleven months.
    assert record.experience[0]["start_date"] is None


def test_json_that_is_not_europass_says_so():
    with pytest.raises(europass.EuropassError, match="LearnerInfo"):
        europass.read(b'{"hello": "world"}')


def test_json_that_does_not_parse_says_so():
    with pytest.raises(europass.EuropassError, match="not readable JSON"):
        europass.read(b'{"LearnerInfo": ')


def test_a_document_that_nests_too_deep_is_refused():
    """A career record is not forty levels deep, so anything that is, is not one."""
    data = ('{"LearnerInfo":' * 60) + "null" + ("}" * 60)

    with pytest.raises(europass.EuropassError, match="nests more than"):
        europass.read(data.encode())


def test_a_json_file_over_the_cap_is_not_parsed():
    with pytest.raises(base.ImportRefused, match="larger than"):
        europass.read(b"{" + b" " * europass.MAX_BYTES)


def test_the_json_reads_through_the_page_as_well(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("cv.json", JSON_FIXTURE.read_bytes())})
    client.post(url, {"action": "confirm"})

    assert Experience.objects.filter(owner=user).count() == 2
    assert Education.objects.filter(owner=user).count() == 1


# ------------------------------------------------------------------- an ORCID


def test_an_orcid_among_the_websites_becomes_an_identifier(user):
    """Neither format has a field for it, and everybody who has one lists it as a site."""
    record = europass.read(FIXTURE.read_bytes())

    assert record.person["orcid"] == "0000-0002-1825-0097"
    # The first website is still the website; the ORCID does not take its place.
    assert record.person["website"] == "https://alex.example.org"

    importing.apply(user, record)

    identifier = PersonIdentifier.objects.get(profile=user.profile)
    assert identifier.scheme == identifiers.ORCID
    assert identifier.value == "0000-0002-1825-0097"


def test_an_orcid_that_fails_its_checksum_is_dropped_rather_than_saved(user):
    data = FIXTURE.read_bytes().replace(b"0000-0002-1825-0097", b"0000-0002-1825-0098")

    record = europass.read(data)

    assert "orcid" not in record.person
    importing.apply(user, record)
    assert not PersonIdentifier.objects.filter(profile=user.profile).exists()


def test_an_orcid_somebody_already_has_is_left_alone(user):
    PersonIdentifier.objects.create(
        profile=user.profile, scheme=identifiers.ORCID, value="0000-0001-5109-3700"
    )

    importing.apply(user, europass.read(FIXTURE.read_bytes()))

    identifiers_held = PersonIdentifier.objects.filter(profile=user.profile)
    assert identifiers_held.count() == 1
    assert identifiers_held.get().value == "0000-0001-5109-3700"


# ------------------------------------------------ saying what was not written


def test_experience_without_a_start_is_named_rather_than_dropped_quietly(user):
    data = MINIMAL.replace(b'<Period><From year="2020"/></Period>', b"")

    report = importing.apply(user, europass.read(data))

    assert not Experience.objects.filter(owner=user).exists()
    assert report.skipped == ["Engineer: no start date, so it was not added."]


def test_the_review_page_says_which_format_it_read(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("cv.json", JSON_FIXTURE.read_bytes())})

    assert b"Read as Europass JSON" in client.get(url).content


def test_the_review_page_lists_what_it_could_not_read(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    half = b'{"LearnerInfo": {"WorkExperience": "see attached", "Education": [{"Title": "MSc"}]}}'

    client.post(url, {"file": upload("cv.json", half)})

    page = client.get(url)
    assert b"What could not be read" in page.content
    assert b"Work experience was in the file but could not be read." in page.content


def test_what_could_not_be_written_is_said_after_the_import(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")
    undated = MINIMAL.replace(b'<Period><From year="2020"/></Period>', b"")
    client.post(url, {"file": upload(data=undated)})

    response = client.post(url, {"action": "confirm"}, follow=True)

    assert b"no start date, so it was not added" in response.content


# ------------------------------------------ what the file says, and what it does not (#235)


NO_LEVELS = b"""<?xml version="1.0"?>
<SkillsPassport xmlns="http://europass.cedefop.europa.eu/Europass" locale="pt-PT">
  <LearnerInfo>
    <Skills>
      <Linguistic>
        <ForeignLanguage><Description><Label>Deutsch</Label></Description></ForeignLanguage>
      </Linguistic>
      <JobRelated><Description><Label>Gestao de projeto</Label></Description></JobRelated>
    </Skills>
  </LearnerInfo>
</SkillsPassport>
"""


def test_a_language_with_no_level_in_the_file_claims_none(user):
    """`or "b1"` put a claim on somebody's CV that they had never made."""
    record = europass.read(NO_LEVELS)
    assert record.languages[0]["proficiency"] == ""

    importing.apply(user, record)

    assert LanguageSkill.objects.get(owner=user, name="Deutsch").proficiency == ""


def test_a_level_that_was_never_stated_prints_nothing_at_all(user):
    """ "Not stated" on a CV is worse than silence, and a dangling dash is worse than either."""
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CV, CVItem
    from postulo.documents.rendering import render_cv_html

    importing.apply(user, europass.read(NO_LEVELS))
    language = LanguageSkill.objects.get(owner=user, name="Deutsch")
    cv = CV.objects.create(owner=user, name="Backend")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(LanguageSkill),
        object_id=language.pk,
        order=0,
    )

    html = render_cv_html(cv)

    # The file calls it Deutsch and the importer found the language, so a CV in English
    # prints its name in English (#689); what the test is for is the dash.
    assert "German" in html
    assert "German —" not in html


def test_the_language_of_the_record_comes_from_the_file(user):
    """Every export says which language it is in, and until now nothing here read it."""
    importing.apply(user, europass.read(NO_LEVELS))

    user.profile.refresh_from_db()
    assert user.profile.record_language == "pt-PT"


def test_a_language_of_the_record_already_chosen_is_left_alone(user):
    """An import fills blanks, and somebody's own answer about themselves is not a blank."""
    user.profile.record_language = "fr"
    user.profile.save(update_fields=["record_language"])

    importing.apply(user, europass.read(NO_LEVELS))

    user.profile.refresh_from_db()
    assert user.profile.record_language == "fr"


@pytest.mark.parametrize("value", [b"", b"not a language at all", b"../../etc/passwd"])
def test_a_locale_that_is_not_a_language_tag_is_dropped(value):
    """It is a file from somewhere else, and this one ends up in a column."""
    data = NO_LEVELS.replace(b'locale="pt-PT"', b'locale="' + value + b'"')

    assert europass.read(data).locale == ""


def test_the_json_carries_its_locale_too():
    assert europass.read(JSON_FIXTURE.read_bytes()).locale == "en"


def test_a_skill_heading_is_written_in_the_language_being_read():
    """ "Job-related" is a heading on somebody's CV, and a Portuguese record had it in English.

    The .mo files are built at packaging time and the suite runs without them, so the two
    properties that make the translation work are what is checked here rather than the
    Portuguese words: each label is lazy, so it is resolved in the language the file is being
    read in rather than the one the server started in, and what reaches the record is a plain
    string, because the session holding it between the review page and the confirmation is
    JSON and a lazy string is not JSON.
    """
    from django.utils.functional import Promise

    assert all(isinstance(label, Promise) for _heading, label in europass.SKILL_HEADINGS)

    record = europass.read(NO_LEVELS)

    assert type(record.skill_groups[0]["name"]) is str


def _headings_that_name_their_language(monkeypatch):
    """Swap the labels for ones that answer with the language they are resolved in.

    The .mo files are built at packaging time and the suite runs without them, so the
    Portuguese words cannot be asserted; which language was active when the heading was
    resolved can.
    """
    from django.utils import translation
    from django.utils.functional import lazy

    label = lazy(lambda: translation.get_language(), str)()
    monkeypatch.setattr(europass, "SKILL_HEADINGS", (("JobRelated", label),))


def test_a_skill_heading_follows_the_language_of_the_file_not_the_interface(monkeypatch):
    from django.utils import translation

    _headings_that_name_their_language(monkeypatch)
    data = NO_LEVELS.replace(b'locale="pt-PT"', b'locale="pt"')

    with translation.override("en"):
        record = europass.read(data)

    assert record.skill_groups[0]["name"] == "pt-pt"


def test_a_skill_heading_is_in_the_active_language_when_the_file_states_none(monkeypatch):
    from django.utils import translation

    _headings_that_name_their_language(monkeypatch)
    data = NO_LEVELS.replace(b' locale="pt-PT"', b"")

    with translation.override("fr-FR"):
        record = europass.read(data)

    assert record.locale == ""
    assert record.skill_groups[0]["name"] == "fr-fr"


def test_the_review_page_says_that_no_level_will_be_claimed(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": upload("cv.xml", NO_LEVELS)})

    assert "no level in the file" in client.get(url).content.decode()


def test_an_achievement_with_no_title_keeps_all_of_its_description():
    """The name is a first line cut at a word; the rest of the text must not vanish."""
    text = (
        "Wrote the migration plan for the billing platform and led the cut-over across "
        "three regions without a minute of downtime."
    )
    data = (
        b'<SkillsPassport xmlns="http://europass.cedefop.europa.eu/Europass"><LearnerInfo>'
        b"<Achievement><Achievement><Description><Label>"
        + text.encode()
        + b"</Label></Description>"
        b"</Achievement></Achievement></LearnerInfo></SkillsPassport>"
    )

    (project,) = europass.read(data).projects

    assert project["summary"] == text
    assert text.startswith(project["name"].rstrip("…"))
    assert len(project["name"]) <= 80
    assert not project["name"].rstrip("…").endswith(("th", "ac"))


def test_every_skill_under_one_heading_is_kept():
    lines = "\n".join(f"Skill {number}" for number in range(55))
    document = {"LearnerInfo": {"Skills": {"Computer": {"Description": lines}}}}
    data = json.dumps(document).encode()

    (group,) = europass.read(data).skill_groups

    assert len(group["skills"]) == 55


@pytest.mark.parametrize(
    "skills",
    ['"a string"', '{"Computer": "Python; Django"}', '{"Computer": ["Python"]}'],
)
def test_json_skills_of_the_wrong_shape_are_said_out_loud(skills):
    record = europass.read(('{"LearnerInfo": {"Skills": ' + skills + "}}").encode())

    assert record.skill_groups == []
    assert any("could not be read" in note for note in record.skipped)


def _address_xml(code: str) -> bytes:
    return (
        b'<SkillsPassport xmlns="http://europass.cedefop.europa.eu/Europass"><LearnerInfo>'
        b"<Identification><ContactInfo><Address><Contact><Municipality>London</Municipality>"
        b"<Country><Code>" + code.encode() + b"</Code><Label>Somewhere</Label></Country>"
        b"</Contact></Address></ContactInfo></Identification></LearnerInfo></SkillsPassport>"
    )


def _address_json(code: str) -> bytes:
    import json

    return json.dumps(
        {
            "SkillsPassport": {
                "LearnerInfo": {
                    "Identification": {
                        "ContactInfo": {
                            "Address": {
                                "Contact": {
                                    "Municipality": "London",
                                    "Country": {"Code": code, "Label": "Somewhere"},
                                }
                            }
                        }
                    }
                }
            }
        }
    ).encode()


@pytest.mark.parametrize("make", [_address_xml, _address_json])
@pytest.mark.parametrize(
    ("written", "stored"),
    [("UK", "GB"), ("EL", "GR"), ("pt", "PT"), ("PRT", ""), ("", "")],
)
def test_the_old_formats_store_a_country_as_iso(make, written, stored):
    """Cedefop wrote UK and EL; a three-letter code is not cut to another country."""
    record = europass.read(make(written))

    assert record.person["address"]["country"] == stored


# ------------------------------------------------- input that is half right (#634)


def _json_with(learner: str) -> bytes:
    return ('{"LearnerInfo": ' + learner + "}").encode()


def test_an_address_without_a_country_still_reads():
    record = europass.read(
        _json_with(
            '{"Identification": {"ContactInfo": {"Address": '
            '{"Contact": {"Municipality": "Lisboa"}}}}}'
        )
    )

    assert record.person["address"]["municipality"] == "Lisboa"
    assert "country" not in record.person["address"] or not record.person["address"]["country"]


def test_a_number_over_the_integer_limit_is_refused_with_a_sentence():
    data = _json_with('{"WorkExperience": [{"Period": {"From": {"Year": ' + "9" * 5000 + "}}}]}")

    with pytest.raises(europass.EuropassError, match="not readable JSON"):
        europass.read(data)


@pytest.mark.parametrize("year", ["Infinity", "1e400", "99999999999"])
def test_a_year_no_date_can_hold_is_left_without_a_date(year):
    data = _json_with(
        '{"WorkExperience": [{"Position": {"Label": "Engineer"}, '
        '"Period": {"From": {"Year": ' + year + "}}}]}"
    )

    record = europass.read(data)

    assert record.experience == [] or record.experience[0]["start_date"] is None


def test_a_year_no_date_can_hold_in_xml_is_left_without_a_date():
    data = MINIMAL.replace(b'year="2020"', b'year="99999999999"')

    record = europass.read(data)

    assert record.experience == [] or record.experience[0]["start_date"] is None


@pytest.mark.parametrize("fmt", ["json", "xml"])
def test_a_website_urlsplit_rejects_does_not_stop_the_read(fmt):
    site = "http://orcid.org[/0000"
    if fmt == "json":
        data = _json_with(
            '{"Identification": {"PersonName": {"Surname": "Morgan"}, "ContactInfo": '
            '{"Website": [{"Contact": "' + site + '"}]}}}'
        )
    else:
        data = (
            b'<SkillsPassport xmlns="http://europass.cedefop.europa.eu/Europass"><LearnerInfo>'
            b"<Identification><PersonName><Surname>Morgan</Surname></PersonName><ContactInfo>"
            b"<Website><Contact>" + site.encode() + b"</Contact></Website>"
            b"</ContactInfo></Identification></LearnerInfo></SkillsPassport>"
        )

    record = europass.read(data)

    assert record.person["last_name"] == "Morgan"
    assert not record.person.get("orcid")


def test_the_older_reader_has_names_and_the_importer_gives_each_language_its_code(user):
    """The older formats write whatever the file says, so the code is found from the name
    when it is written, and a name that is more than one language is left as it was (#689)."""
    record = europass.read(FIXTURE.read_bytes())
    assert all("code" not in row for row in record.languages)
    record.languages.append({"name": "Norwegian", "proficiency": "b1", "levels": {}})

    importing.apply(user, record)

    codes = {row.name: row.code for row in LanguageSkill.objects.filter(owner=user)}
    assert codes["English"] == "en"
    assert codes["português"] == "", "Portuguese is two countries', and the person chooses"
    assert codes["Norwegian"] == ""


def test_a_language_code_from_a_reader_that_has_one_is_kept_where_postulo_can_name_it(user):
    record = europass.read(FIXTURE.read_bytes())
    record.languages[:] = [
        {"name": "Whatever", "code": "fr", "proficiency": "b2", "levels": {}},
        {"name": "BSL", "code": "bfi", "proficiency": "b2", "levels": {}},
        {"name": "Not a code", "code": "not a code", "proficiency": "b2", "levels": {}},
    ]

    importing.apply(user, record)

    codes = {row.name: row.code for row in LanguageSkill.objects.filter(owner=user)}
    assert codes == {"Whatever": "fr", "BSL": "", "Not a code": ""}
