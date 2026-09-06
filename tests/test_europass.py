"""Reading a Europass CV: what the file says, what gets written, and what is refused.

The fixture at ``tests/data/europass.xml`` is a real file's shape — nested
``WorkExperience``, dates as attributes, CEFR split five ways, prose under each skill
heading — because that is what this reader has to survive.
"""

import datetime as dt
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from postulo.resume import europass
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


def test_it_reads_the_shape_a_real_export_has():
    record = europass.read(FIXTURE.read_bytes())

    assert record.counts() == {
        "experience": 2,
        "education": 1,
        "languages": 2,
        "skills": 5,
        "projects": 1,
    }
    assert record.person["first_name"] == "Alex"
    assert record.person["email"] == "alex@example.org"
    assert record.person["headline"] == "Backend engineer"
    assert record.person["location"] == "Lisboa, Portugal"


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

    with pytest.raises(europass.EuropassError, match="document type declaration"):
        europass.read(bomb)


def test_an_external_entity_cannot_reach_the_disk():
    xxe = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
        b"<SkillsPassport><LearnerInfo><Headline>&x;</Headline></LearnerInfo></SkillsPassport>"
    )

    with pytest.raises(europass.EuropassError, match="document type declaration"):
        europass.read(xxe)


def test_something_that_is_not_xml_says_so():
    with pytest.raises(europass.EuropassError, match="not readable XML"):
        europass.read(b"this is a PDF, honestly")


def test_xml_that_is_not_europass_says_so():
    with pytest.raises(europass.EuropassError, match="LearnerInfo"):
        europass.read(b"<html><body>Hello</body></html>")


def test_an_empty_file_says_so():
    with pytest.raises(europass.EuropassError, match="empty"):
        europass.read(b"")


def test_a_file_over_the_cap_is_not_parsed():
    with pytest.raises(europass.EuropassError, match="larger than"):
        europass.read(b"<a/>" + b" " * europass.MAX_BYTES)


# ---------------------------------------------------------------- the writing


def test_applying_writes_everything_it_found(user):
    record = europass.read(FIXTURE.read_bytes())

    report = europass.apply(user, record)

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

    europass.apply(user, europass.read(FIXTURE.read_bytes()))

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.first_name == "Alexandra"
    assert profile.headline == "Staff engineer, mostly Python"
    # A blank is not an opinion, so the blank ones were filled.
    assert profile.location == "Lisboa, Portugal"
    assert user.last_name == "Morgan"


def test_a_heading_that_already_exists_is_used_rather_than_repeated(user):
    SkillGroup.objects.create(owner=user, name="Digital")

    europass.apply(user, europass.read(FIXTURE.read_bytes()))

    assert SkillGroup.objects.filter(owner=user, name="Digital").count() == 1
    assert Skill.objects.filter(owner=user, group__name="Digital").count() == 3


def test_experience_without_a_start_is_left_out(user):
    data = MINIMAL.replace(b'<Period><From year="2020"/></Period>', b"")

    europass.apply(user, europass.read(data))

    assert not Experience.objects.filter(owner=user).exists()


def test_one_persons_import_does_not_touch_another(user, other_user):
    europass.apply(user, europass.read(FIXTURE.read_bytes()))

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

    response = client.post(url, {"file": upload(data=b"not xml at all")}, follow=True)

    assert b"not readable XML" in response.content
    assert response.context["found"] is None


def test_what_is_held_between_the_two_steps_is_the_record_and_not_the_file(client, user):
    """No reason to keep somebody's CV on the server longer than it takes to read it."""
    client.force_login(user)

    client.post(reverse("resume:europass_import"), {"file": upload()})

    held = client.session["europass_import_data"]
    assert set(held) == {
        "person",
        "experience",
        "education",
        "languages",
        "skill_groups",
        "projects",
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
